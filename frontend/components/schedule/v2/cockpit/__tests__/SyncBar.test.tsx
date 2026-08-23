/**
 * SyncBar — カイポケ同期ストリップ (方向性A・docs/mockups/sync-strip-mock.html)。
 *
 * ① ストリップは常時 1 行: 状態バッジ + 件数チップ + 3 ボタン
 * ② パネルは押した時だけ直下に開く (同時に 1 つ)
 * ③ 行を選ぶと「何から何へ」表 + 盤面ゴースト (onSelectDiff)
 * ④ 全件は 2 段クリック / RPA 未対応の行は送れない / 当日以前は非表示
 * ⑤ 未確認 (phase=idle) のまま ⇩ を開くと同期確認を自動で始める
 *
 * 過去日判定は実時計基準のため、送れる分は常に「来週」を使う。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const unsentMutateAsync = vi.fn();
const serviceOverrideMutateAsync = vi.fn();
const updateStaffMutateAsync = vi.fn();
const startApplyMutateAsync = vi.fn();
const sendEventsMutateAsync = vi.fn();
const reconcileStub = {
  phase: 'idle' as string,
  error: null as string | null,
  fetchedAt: null as Date | null,
  stale: false,
  rpaRunning: false,
  busyKey: null as string | null,
  diffs: [] as unknown[],
  eventChanges: [] as unknown[],
  visitItems: [] as unknown[],
  visitSheetId: null as string | null,
  inSheetId: null as string | null,
  visitsPlan: null as unknown,
  masterResult: null as unknown,
  runFetch: vi.fn(),
  fetchInboundDiff: vi.fn(),
  runMasterReconcile: vi.fn(),
  applyDiff: vi.fn(),
  applyAllDiffs: vi.fn(),
  overwriteDiff: vi.fn(),
  overwriteAllDiffs: vi.fn(),
};
const RECONCILE_DEFAULTS = { ...reconcileStub };
/** SyncBar が useKaipokeReconcile に渡した onReady (= 同期確認完了コールバック)。 */
let reconcileOnReady: (() => void) | null = null;

vi.mock('@/lib/queries/cockpit', () => ({
  useUnsentSummary: () => ({ mutateAsync: unsentMutateAsync }),
  useVisitServiceOverride: () => ({ mutateAsync: serviceOverrideMutateAsync }),
}));
vi.mock('@/lib/queries/staff', () => ({
  useUpdateStaff: () => ({ mutateAsync: updateStaffMutateAsync }),
}));
vi.mock('@/lib/queries/integrations', () => ({
  useStartApply: () => ({ mutateAsync: startApplyMutateAsync }),
  useSendEventsOutbound: () => ({ mutateAsync: sendEventsMutateAsync }),
}));
vi.mock('../useKaipokeReconcile', () => ({
  useKaipokeReconcile: (opts: { onReady?: () => void }) => {
    // 「同期確認が終わった」を後からテストで起こせるよう控えておく。
    reconcileOnReady = opts?.onReady ?? null;
    return reconcileStub;
  },
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SyncBar } from '../SyncBar';

const SHEET_ID = '00000000-0000-4000-8000-00000000beef';
const ITEM_FUTURE = '00000000-0000-4000-8000-00000000a001';
const ITEM_PAST = '00000000-0000-4000-8000-00000000a002';
const ITEM_RPA = '00000000-0000-4000-8000-00000000a003';
const ITEM_SVC_ADD = '00000000-0000-4000-8000-00000000a004';
const ITEM_SVC_DELETE = '00000000-0000-4000-8000-00000000a005';
const STAFF_MISSING = '00000000-0000-4000-8000-0000000000c3';
const DIFF_VISIT = '00000000-0000-4000-8000-00000000d001';
const DIFF_EVENT = '00000000-0000-4000-8000-00000000d002';

const today = new Date();
const nextMonday = new Date(today);
nextMonday.setDate(today.getDate() + ((8 - today.getDay()) % 7 || 7));
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const WEEK_START = iso(nextMonday);
const FUTURE_DAY = iso(
  new Date(nextMonday.getFullYear(), nextMonday.getMonth(), nextMonday.getDate() + 2),
);

function item(id: string, dateIso: string) {
  const dom = String(Number.parseInt(dateIso.slice(8, 10), 10));
  return {
    id,
    sheet_id: SHEET_ID,
    patient_id: null,
    visit_id: null,
    action: 'edit',
    before: {
      user_name: '田中',
      date: dom,
      start_time: '10:00',
      end_time: '11:00',
      staff1: '川名',
    },
    after: { user_name: '田中', date: dom, start_time: '10:30', end_time: '11:30', staff1: '川名' },
    include: true,
    comment: null,
    created_at: '',
    updated_at: '',
    date_iso: dateIso,
  };
}

/**
 * BE の実形状に合わせた空側 (`diff/engine.py` の add/delete 生成)。
 *
 * 差分エンジンは **反対側を空文字で埋める**: delete の `after` は
 * `{date:'', start_time:'', ...}`、add の `before` も同様 (null ではない)。
 * `user_name` / `service_type` / `business_type` / `remarks` だけは
 * `correction_before_after` が両側に同じ値を入れる。
 */
function _empty(overrides: Record<string, string> = {}) {
  return {
    user_name: '田中',
    date: '',
    start_time: '',
    end_time: '',
    staff1: '',
    staff2: '',
    service_type: '',
    business_type: '医療保険',
    remarks: '',
    ...overrides,
  };
}

function _filled(dateIso: string, overrides: Record<string, string> = {}) {
  return _empty({
    date: String(Number.parseInt(dateIso.slice(8, 10), 10)),
    start_time: '10:00',
    end_time: '11:00',
    staff1: '川名',
    ...overrides,
  });
}

/** RPA が登録できない add (准看/一般のサービス内容)。BE が rpa_unsupported を立てる。 */
function rpaUnsupportedItem(id: string, dateIso: string) {
  const base = item(id, dateIso);
  const after = _filled(dateIso, { service_type: '基本療養費Ⅰ・正看' });
  return {
    ...base,
    action: 'add',
    before: _empty({ service_type: '基本療養費Ⅰ・正看' }),
    after,
    rpa_unsupported: true,
  };
}

/**
 * サービス内容だけが違うペア (設計 §3-1): カイポケの編集ではサービス内容を
 * 直せないため、差分は必ず delete (カイポケの行) + add (らく助の行) で出る。
 * 日付・時刻・担当は同じで `service_type` だけが違う。
 *
 * BE は **ペアの両方** に `rpa_unsupported` を立てる (add だけ落として delete を
 * 送るとカイポケの行だけ消えるため — 2026-08-23 レビュー H1)。
 */
function servicePairAdd(overrides: Record<string, string> = {}) {
  const base = item(ITEM_SVC_ADD, FUTURE_DAY);
  const svc = '基本療養費Ⅰ・正看';
  return {
    ...base,
    action: 'add',
    // add は before が空側 (ここを読むと日付も時刻も空文字になる)。
    before: _empty({ service_type: svc }),
    after: _filled(FUTURE_DAY, { service_type: svc, ...overrides }),
    rpa_unsupported: true,
  };
}

function servicePairDelete(overrides: Record<string, string> = {}) {
  const base = item(ITEM_SVC_DELETE, FUTURE_DAY);
  const svc = '精神基本療養費Ⅰ・准看';
  return {
    ...base,
    action: 'delete',
    before: _filled(FUTURE_DAY, { service_type: svc, ...overrides }),
    // delete は after が空側。
    after: _empty({ service_type: svc }),
    rpa_unsupported: true,
  };
}

/** 🔄同期確認で見つかった訪問差分 (⇩取り込む パネルの 1 行)。 */
function visitDiff() {
  return {
    id: DIFF_VISIT,
    kind: 'visit',
    item: { id: DIFF_VISIT },
    marker: {
      kind: 'visit',
      action: 'update',
      externalId: DIFF_VISIT,
      title: '久須見',
      patient_name: '久須見',
      start: '10:30',
      end: '11:15',
      beforeStart: '10:00',
      beforeEnd: '10:45',
      before: {
        staff_id: null,
        staff_name: '熊澤',
        date: FUTURE_DAY,
        start: '10:00',
        end: '10:45',
        course_label: '都賀A',
      },
      after: {
        staff_id: null,
        staff_name: '佐藤',
        date: FUTURE_DAY,
        start: '10:30',
        end: '11:15',
        course_label: '都賀B',
      },
    },
  };
}

/** 🔄同期確認で見つかったイベント差分 (カイポケにだけある)。 */
function eventDiff() {
  return {
    id: DIFF_EVENT,
    kind: 'event',
    change: { externalId: DIFF_EVENT },
    marker: {
      kind: 'event',
      action: 'add',
      externalId: DIFF_EVENT,
      title: '会議',
      start: '13:00',
      end: '14:00',
      beforeStart: null,
      beforeEnd: null,
      after: {
        staff_id: null,
        staff_name: '髙梨',
        date: FUTURE_DAY,
        start: '13:00',
        end: '14:00',
      },
    },
  };
}

const EMPTY_SUMMARY = {
  week_start: WEEK_START,
  snapshot: {
    fetched_at: '2026-08-21T09:12:00+09:00',
    month: WEEK_START.slice(0, 7),
    row_count: 10,
  },
  sheet_id: SHEET_ID,
  items: [],
  events: [],
  sendable_count: 0,
  past_count: 0,
};

function renderBar() {
  const onSelectDiff = vi.fn();
  const view = render(<SyncBar weekStartIso={WEEK_START} canEdit onSelectDiff={onSelectDiff} />);
  return {
    onSelectDiff,
    /** stub の中身を書き換えたあと画面を作り直す (hook は再描画で読み直される)。 */
    refresh: () =>
      view.rerender(<SyncBar weekStartIso={WEEK_START} canEdit onSelectDiff={onSelectDiff} />),
  };
}

/** `reloadKey` を変えて「盤面を触った直後の数え直し」を再現する。 */
function renderBarWithReload() {
  const onSelectDiff = vi.fn();
  const view = render(
    <SyncBar weekStartIso={WEEK_START} canEdit reloadKey={0} onSelectDiff={onSelectDiff} />,
  );
  return (next: number) =>
    view.rerender(
      <SyncBar weekStartIso={WEEK_START} canEdit reloadKey={next} onSelectDiff={onSelectDiff} />,
    );
}

/** ⇧ 送る パネルを開く (未送信の読み込み完了を待ってから)。 */
async function openOutPanel() {
  await waitFor(() => expect(screen.getByTestId('sync-open-out')).toBeEnabled());
  fireEvent.click(screen.getByTestId('sync-open-out'));
  return screen.getByTestId('sync-panel-out');
}

/** ⇩ 取り込む パネルを開く。 */
async function openInPanel() {
  await waitFor(() => expect(screen.getByTestId('sync-open-in')).toBeEnabled());
  fireEvent.click(screen.getByTestId('sync-open-in'));
  return screen.getByTestId('sync-panel-in');
}

/** 🔄 同期確認 パネルを開く。 */
async function openCheckPanel() {
  await waitFor(() => expect(screen.getByTestId('sync-open-check')).toBeEnabled());
  fireEvent.click(screen.getByTestId('sync-open-check'));
  return screen.getByTestId('sync-panel-check');
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(reconcileStub, RECONCILE_DEFAULTS);
  reconcileStub.diffs = [];
  startApplyMutateAsync.mockResolvedValue({ jobId: 'j1' });
});

describe('SyncBar — ストリップ (常時1行)', () => {
  it('差分ゼロなら ✓ カイポケと同じ・件数はすべて 0', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalledWith({ week_start: WEEK_START }));
    await waitFor(() =>
      expect(screen.getByTestId('sync-status')).toHaveTextContent('✓ カイポケと同じ'),
    );
    const counts = screen.getByTestId('sync-counts');
    expect(counts).toHaveTextContent('カイポケから 0');
    expect(counts).toHaveTextContent('らく助から 0');
    expect(counts).toHaveTextContent('要確認 0');
  });

  it('差分があれば ⚠ 差分あり + 件数チップ + ボタンの件数が揃う', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY), item(ITEM_PAST, '2020-01-06')],
      sendable_count: 1,
      past_count: 1,
    });
    reconcileStub.diffs = [visitDiff(), eventDiff()];
    renderBar();
    await waitFor(() => expect(screen.getByTestId('sync-status')).toHaveTextContent('⚠ 差分あり'));
    const counts = screen.getByTestId('sync-counts');
    // カイポケから = 取込差分 / らく助から = 送れる未送信 (当日以前は数えない)
    expect(counts).toHaveTextContent('カイポケから 2');
    expect(counts).toHaveTextContent('らく助から 1');
    expect(screen.getByTestId('sync-open-in')).toHaveTextContent('⇩ カイポケから取り込む 2');
    expect(screen.getByTestId('sync-open-out')).toHaveTextContent('⇧ カイポケへ送る 1');
  });

  it('カイポケ側の控えが無ければ ? 未確認', async () => {
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, snapshot: null, sheet_id: null });
    renderBar();
    await waitFor(() =>
      expect(screen.getByTestId('sync-status')).toHaveTextContent(
        '? 未確認（カイポケ側の控えがありません）',
      ),
    );
  });

  it('らく助が作業中はバッジが変わり 3 ボタンとも押せない', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.busyKey = '__in_diff__';
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    expect(screen.getByTestId('sync-status')).toHaveTextContent('らく助が確認中');
    for (const id of ['sync-open-in', 'sync-open-out', 'sync-open-check']) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
  });

  it('RPA 実行中も 3 ボタンとも押せない', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.rpaRunning = true;
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    for (const id of ['sync-open-in', 'sync-open-out', 'sync-open-check']) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
  });

  it('パネルは同時に 1 つだけ開く (もう一度押すと閉じる)', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    renderBar();

    await openInPanel();
    expect(screen.queryByTestId('sync-panel-out')).toBeNull();
    expect(screen.queryByTestId('sync-panel-check')).toBeNull();

    fireEvent.click(screen.getByTestId('sync-open-out'));
    expect(screen.queryByTestId('sync-panel-in')).toBeNull();
    expect(screen.getByTestId('sync-panel-out')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('sync-open-out'));
    expect(screen.queryByTestId('sync-panel-out')).toBeNull();
  });
});

describe('SyncBar — ⇩ カイポケから取り込む', () => {
  it('差分をカード行で並べ、行を選ぶと詳細表 + 盤面ゴーストが出る', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.diffs = [visitDiff(), eventDiff()];
    const { onSelectDiff } = renderBar();
    await openInPanel();

    const rows = screen.getAllByTestId('sync-in-row');
    expect(rows).toHaveLength(2);
    // 「誰が・何が・どう変わる」の 1 文
    expect(rows[0]).toHaveTextContent('久須見 様 10:30');
    expect(rows[0]).toHaveTextContent('担当 熊澤 → 佐藤');
    expect(rows[0]).toHaveTextContent('カイポケ側で変わっている');
    expect(rows[1]).toHaveTextContent('カイポケにだけある');

    // 選ぶまで詳細は出さない
    expect(screen.queryByTestId('diff-detail-card')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /久須見/ }));
    expect(await screen.findByTestId('diff-detail-card')).toBeInTheDocument();
    await waitFor(() =>
      expect(onSelectDiff).toHaveBeenCalledWith(expect.objectContaining({ kind: 'visit' })),
    );
  });

  it('訪問だけ「らく助を正にして上書き」が出る / 1件取込は applyDiff を呼ぶ', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.diffs = [visitDiff(), eventDiff()];
    renderBar();
    await openInPanel();

    const rows = screen.getAllByTestId('sync-in-row');
    expect(within(rows[0]!).getByTestId('sync-in-over')).toBeInTheDocument();
    expect(within(rows[1]!).queryByTestId('sync-in-over')).toBeNull();

    fireEvent.click(within(rows[0]!).getByTestId('sync-in-apply'));
    expect(reconcileStub.applyDiff).toHaveBeenCalledWith(
      expect.objectContaining({ id: DIFF_VISIT }),
    );
    fireEvent.click(within(rows[0]!).getByTestId('sync-in-over'));
    expect(reconcileStub.overwriteDiff).toHaveBeenCalledWith(
      expect.objectContaining({ id: DIFF_VISIT }),
    );
  });

  it('「全件取り込む」は 2 段クリック (1回目は確認・2回目で実行)', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.diffs = [visitDiff(), eventDiff()];
    renderBar();
    await openInPanel();

    const all = screen.getByTestId('sync-in-apply-all');
    expect(all).toHaveTextContent('⇩ 2件すべて取り込む');
    fireEvent.click(all);
    expect(all).toHaveTextContent('2件すべて取り込む？もう一度押す');
    expect(reconcileStub.applyAllDiffs).not.toHaveBeenCalled();
    fireEvent.click(all);
    expect(reconcileStub.applyAllDiffs).toHaveBeenCalled();
  });

  it('同期確認がまだ (phase=idle) なら開いた時に自動で始める', async () => {
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, snapshot: null, sheet_id: null });
    const { refresh } = renderBar();
    await openInPanel();
    expect(reconcileStub.runFetch).toHaveBeenCalled();
    // 作業中は取り込むパネルにも らく助の演出 + 進捗チップを出す (PO要望 2026-08-23)
    reconcileStub.phase = 'events';
    refresh();
    expect(screen.getByTestId('sync-working')).toBeInTheDocument();
    expect(screen.getByTestId('sync-progress')).toBeInTheDocument();
  });

  it('同期確認済みで差分ゼロなら「変わっている予定はありません」', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    renderBar();
    await openInPanel();
    expect(reconcileStub.runFetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('sync-in-empty')).toHaveTextContent(
      'カイポケ側で変わっている予定はありません',
    );
  });
});

describe('SyncBar — ⇧ カイポケへ送る', () => {
  it('行の「送る」が既存 apply の部分適用を呼ぶ / 当日以前は非表示', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY), item(ITEM_PAST, '2020-01-06')],
      sendable_count: 1,
      past_count: 1,
    });
    renderBar();
    await openOutPanel();

    const rows = screen.getAllByTestId('sync-out-row');
    expect(rows).toHaveLength(1);
    // 変更行は「今こうなっている」側 (before) を主語にし、変化点を右に添える。
    expect(rows[0]).toHaveTextContent('田中 様 10:00');
    expect(rows[0]).toHaveTextContent('時刻 10:00〜11:00 → 10:30〜11:30');
    expect(screen.getByTestId('sync-out-past-note')).toHaveTextContent(
      '当日以前の予定は実績保護のため送れません（1件・非表示）',
    );

    fireEvent.click(within(rows[0]!).getByTestId('sync-out-send'));
    await waitFor(() =>
      expect(startApplyMutateAsync).toHaveBeenCalledWith({
        sheetId: SHEET_ID,
        dryRun: false,
        itemIds: [ITEM_FUTURE],
      }),
    );
  });

  it('行を選ぶと詳細表 + 盤面ゴーストが出る', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    const { onSelectDiff } = renderBar();
    await openOutPanel();
    expect(screen.queryByTestId('diff-detail-card')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /田中/ }));
    expect(await screen.findByTestId('diff-detail-card')).toBeInTheDocument();
    await waitFor(() =>
      expect(onSelectDiff).toHaveBeenCalledWith(expect.objectContaining({ kind: 'visit' })),
    );
  });

  it('RPA 未対応 (准看/一般) の行は薄く出して送れない', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY), rpaUnsupportedItem(ITEM_RPA, FUTURE_DAY)],
      sendable_count: 1,
      rpa_unsupported_count: 1,
    });
    renderBar();
    await openOutPanel();

    // 「らく助から」は送れる分だけ数える
    expect(screen.getByTestId('sync-counts')).toHaveTextContent('らく助から 1');
    const rows = screen.getAllByTestId('sync-out-row');
    expect(rows).toHaveLength(2);
    const blocked = rows.find((r) => r.textContent?.includes('自動送信不可'))!;
    expect(blocked).toHaveTextContent('准看護師／一般の登録はRPAが未対応です');
    expect(within(blocked).getByTestId('sync-out-send')).toBeDisabled();
    const ok = rows.find((r) => r !== blocked)!;
    expect(within(ok).getByTestId('sync-out-send')).toBeEnabled();
  });

  it('RPA 未対応しか無ければ「全件送る」も押せない', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [rpaUnsupportedItem(ITEM_RPA, FUTURE_DAY)],
      sendable_count: 0,
      rpa_unsupported_count: 1,
    });
    renderBar();
    await openOutPanel();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
  });

  it('「全件送る」は 2 段クリック (1回目は確認・2回目で実行)', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    renderBar();
    await openOutPanel();

    const all = screen.getByTestId('sync-unsent-send-all');
    expect(all).toHaveTextContent('⇧ 1件すべて送る');
    fireEvent.click(all);
    expect(all).toHaveTextContent('1件すべて送信？もう一度押す');
    expect(startApplyMutateAsync).not.toHaveBeenCalled();

    fireEvent.click(all);
    await waitFor(() =>
      expect(startApplyMutateAsync).toHaveBeenCalledWith({
        sheetId: SHEET_ID,
        dryRun: false,
        itemIds: [ITEM_FUTURE],
      }),
    );
  });

  it('控えが無いときは「🔄 同期確認で最新を読み込んで」と案内する', async () => {
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, snapshot: null, sheet_id: null });
    renderBar();
    await openOutPanel();
    expect(screen.getByTestId('sync-out-empty')).toHaveTextContent('🔄 同期確認');
  });

  // ── 送信済みの印は訪問キー基準 / 再計算では消えない (M1) ──

  it('送信後に数え直しても、送った行は復活しない（item.id が変わっても）', async () => {
    const row = item(ITEM_FUTURE, FUTURE_DAY);
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, items: [row], sendable_count: 1 });
    const reload = renderBarWithReload();
    await openOutPanel();
    fireEvent.click(screen.getByTestId('sync-out-send'));
    await waitFor(() => expect(startApplyMutateAsync).toHaveBeenCalled());

    // RPA は非同期なので、直後の再計算でも同じ訪問が返る。●未送信は毎回シートを
    // 作り直すので item.id は変わる — キーで覚えていないと復活してしまう。
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [{ ...row, id: '00000000-0000-4000-8000-0000000000ff' }],
      sendable_count: 1,
    });
    reload(1);
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryAllByTestId('sync-out-row')).toHaveLength(0));
  });

  it('🔄同期確認が終わったときだけ送信済みの印を捨てる', async () => {
    const row = item(ITEM_FUTURE, FUTURE_DAY);
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, items: [row], sendable_count: 1 });
    renderBar();
    await openOutPanel();
    fireEvent.click(screen.getByTestId('sync-out-send'));
    await waitFor(() => expect(screen.queryAllByTestId('sync-out-row')).toHaveLength(0));

    // 同期確認完了 = カイポケの現況を見直した後。まだ残る行は本当に未送信。
    await act(async () => {
      await reconcileOnReady?.();
    });
    await waitFor(() => expect(screen.getAllByTestId('sync-out-row')).toHaveLength(1));
  });
});

describe('SyncBar — 🔄 同期確認', () => {
  it('結果がある状態では 🔄同期確認 は再実行せず開閉だけ (たたんでも結果は残る)。再実行は「再確認」', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    renderBar();
    await openCheckPanel();
    expect(reconcileStub.runFetch).not.toHaveBeenCalled();
    // たたむ → 再び開いても再実行しない
    fireEvent.click(screen.getByTestId('sync-panel-close'));
    expect(screen.queryByTestId('sync-panel-check')).not.toBeInTheDocument();
    await openCheckPanel();
    expect(reconcileStub.runFetch).not.toHaveBeenCalled();
    // 再確認ボタンで実行
    fireEvent.click(screen.getByTestId('sync-check-rerun'));
    expect(reconcileStub.runFetch).toHaveBeenCalledTimes(1);
  });

  it('押すと実行し、作業中はらく助の演出と進捗チップを出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    const { refresh } = renderBar();
    await openCheckPanel();
    expect(reconcileStub.runFetch).toHaveBeenCalled();

    reconcileStub.phase = 'visits';
    refresh();
    expect(screen.getByTestId('sync-working')).toBeInTheDocument();
    const steps = screen.getByTestId('sync-progress');
    expect(steps).toHaveTextContent('ログイン');
    expect(steps).toHaveTextContent('差分計算');
    // 作業中はサマリを出さない
    expect(screen.queryByTestId('sync-summary')).toBeNull();
  });

  it('完了後はサマリ 3 カードを出す', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    reconcileStub.phase = 'ready';
    reconcileStub.diffs = [visitDiff(), eventDiff()];
    renderBar();
    await openCheckPanel();
    const summary = screen.getByTestId('sync-summary');
    expect(summary).toHaveTextContent('カイポケ側で変わっている');
    expect(summary).toHaveTextContent('らく助で変えた（未送信）');
    expect(summary).toHaveTextContent('要確認');
  });

  it('実績のない日があれば「取込差分を計算」の案内を出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.visitsPlan = { replaceDays: ['2026-08-19'], replace: { inserted: 4 } };
    renderBar();
    await openCheckPanel();
    expect(screen.getByTestId('sync-replace-days')).toHaveTextContent('実績のない日');
    fireEvent.click(screen.getByTestId('sync-in-diff-button'));
    expect(reconcileStub.fetchInboundDiff).toHaveBeenCalled();
  });

  // ── 要確認: サービス内容のズレ (kaipoke-service-content-design.md §2 / §3-1) ──

  it('サービス内容だけが違う delete+add を 1 行に束ね、合わせるボタンで上書きする', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [servicePairAdd(), servicePairDelete()],
      sendable_count: 2,
    });
    serviceOverrideMutateAsync.mockResolvedValue({
      id: 'v1',
      kaipoke_service_override: '精神基本療養費Ⅰ・准看',
    });
    reconcileStub.phase = 'ready';
    renderBar();
    await openCheckPanel();

    const row = screen.getByTestId('sync-service-mismatch-row');
    expect(row).toHaveTextContent('田中');
    expect(row).toHaveTextContent('らく助: 基本療養費Ⅰ・正看');
    expect(row).toHaveTextContent('カイポケ: 精神基本療養費Ⅰ・准看');

    fireEvent.click(screen.getByTestId('sync-service-mismatch-apply'));
    // visit の特定は BE 側 = delete 側 item の id を渡す。
    await waitFor(() =>
      expect(serviceOverrideMutateAsync).toHaveBeenCalledWith({
        item_id: ITEM_SVC_DELETE,
        service_content: '精神基本療養費Ⅰ・准看',
      }),
    );
    // 適用後は未送信を数え直す (初回ロード + 再計算 = 2 回)。
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalledTimes(2));
  });

  it('担当も違うなら束ねない（本当の予定変更まで「サービス内容のズレ」にしない）', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [servicePairAdd(), servicePairDelete({ staff1: '別人' })],
      sendable_count: 2,
    });
    reconcileStub.phase = 'ready';
    renderBar();
    await openCheckPanel();
    expect(screen.queryByTestId('sync-service-mismatch-row')).toBeNull();
  });

  it('ペアの両方が BE のフラグどおり送信対象から外れる（片肺送信の防止）', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [servicePairAdd(), servicePairDelete()],
      // BE はペアの両方を rpa_unsupported に数える。
      sendable_count: 0,
      rpa_unsupported_count: 2,
    });
    renderBar();
    await openOutPanel();
    // 2 行とも「自動送信不可」= FE がサービス内容を自前で判定していない証拠。
    const sends = screen.getAllByTestId('sync-out-send');
    expect(sends).toHaveLength(2);
    for (const b of sends) expect(b).toBeDisabled();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
  });

  // ── 要確認: 資格のズレ / 未設定 (§1-2) ──

  it('資格のズレは表示のみ・未設定は「カイポケの職種を採用」で埋められる', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    updateStaffMutateAsync.mockResolvedValue({ id: STAFF_MISSING });
    reconcileStub.phase = 'ready';
    reconcileStub.masterResult = {
      month: '2026-08',
      patients: { matched: 0, notationDiff: [], kaipokeOnly: [], rakusukeOnly: [] },
      staff: { matched: 0, notationDiff: [], kaipokeOnly: [], rakusukeOnly: [] },
      staffQualifications: [
        {
          staffId: '00000000-0000-4000-8000-0000000000c1',
          name: '一致 太郎',
          kaipokeQualification: '看護師',
          rakusukeQualification: '看護師',
          status: 'match',
        },
        {
          staffId: '00000000-0000-4000-8000-0000000000c2',
          name: 'ズレ 花子',
          kaipokeQualification: '准看護師',
          rakusukeQualification: '看護師',
          status: 'mismatch',
        },
        {
          staffId: STAFF_MISSING,
          name: '未設定 次郎',
          kaipokeQualification: '准看護師',
          rakusukeQualification: null,
          status: 'missing_in_rakusuke',
        },
      ],
    };
    renderBar();
    await openCheckPanel();

    // 一致は出さない
    expect(screen.getByTestId('sync-needs-check')).not.toHaveTextContent('一致 太郎');
    // ズレはボタン無し (どちらが正かは人が判断する)
    expect(screen.getByTestId('sync-master-qual-mismatch')).toHaveTextContent(
      'カイポケ「准看護師」⇔ らく助「看護師」',
    );

    // 採用は確認ダイアログを挟む (マスタ更新 = その職員の全訪問に効く)。
    fireEvent.click(screen.getByTestId('sync-master-qual-adopt'));
    expect(updateStaffMutateAsync).not.toHaveBeenCalled();
    const dialog = await screen.findByTestId('sync-master-qual-confirm-dialog');
    expect(dialog).toHaveTextContent(
      '未設定 次郎さんの資格を「准看護師」にします。この職員の全訪問のサービス内容が変わります。',
    );

    fireEvent.click(screen.getByTestId('sync-master-qual-confirm'));
    await waitFor(() =>
      expect(updateStaffMutateAsync).toHaveBeenCalledWith({
        id: STAFF_MISSING,
        payload: { qualification: '准看護師' },
      }),
    );
    // 採用済みは行が消える (👥突合は ~1 分かかるので取り直さない)
    await waitFor(() => expect(screen.queryByTestId('sync-master-qual-missing')).toBeNull());
  });

  it('同名で判別できない資格は採用ボタンを出さず、注意だけ出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.masterResult = {
      month: '2026-08',
      patients: { matched: 0, notationDiff: [], kaipokeOnly: [], rakusukeOnly: [] },
      staff: { matched: 0, notationDiff: [], kaipokeOnly: [], rakusukeOnly: [] },
      staffQualifications: [
        {
          staffId: null,
          name: '川名　千恵',
          kaipokeQualification: '准看護師',
          rakusukeQualification: null,
          status: 'ambiguous',
        },
      ],
    };
    renderBar();
    await openCheckPanel();
    expect(screen.getByTestId('sync-master-qual-ambiguous')).toHaveTextContent(
      '同じ名前の在職スタッフが複数います',
    );
    expect(screen.queryByTestId('sync-master-qual-adopt')).toBeNull();
  });

  // ── 👥 名簿の詳細 (折りたたみ) ──

  it('👥名簿の詳細を開くと突合結果を出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.masterResult = {
      month: '2026-08',
      patients: {
        matched: 12,
        notationDiff: [{ kaipoke: '高橋', rakusuke: '髙橋' }],
        kaipokeOnly: ['新井'],
        rakusukeOnly: [],
      },
      staff: { matched: 5, notationDiff: [], kaipokeOnly: [], rakusukeOnly: ['熊澤'] },
    };
    renderBar();
    await openCheckPanel();

    // 畳んでいる間は一覧を出さない
    expect(screen.queryByTestId('sync-master-result')).toBeNull();
    fireEvent.click(screen.getByTestId('sync-master-toggle'));

    const res = screen.getByTestId('sync-master-result');
    expect(res).toHaveTextContent('患者: 一致 12');
    expect(res).toHaveTextContent('カイポケ「高橋」⇔らく助「髙橋」');
    expect(res).toHaveTextContent('らく助のみ（当月のカイポケスケジュールに未出現）: 熊澤');
    fireEvent.click(screen.getByTestId('sync-master-button'));
    expect(reconcileStub.runMasterReconcile).toHaveBeenCalled();
  });

  it('らく助未登録のスタッフはカイポケの職種を氏名に添えて出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.phase = 'ready';
    reconcileStub.masterResult = {
      month: '2026-08',
      patients: { matched: 0, notationDiff: [], kaipokeOnly: ['新井'], rakusukeOnly: [] },
      staff: { matched: 0, notationDiff: [], kaipokeOnly: ['新人　太郎'], rakusukeOnly: [] },
      staffQualifications: [
        {
          staffId: null,
          name: '新人　太郎',
          kaipokeQualification: '准看護師',
          rakusukeQualification: null,
          status: 'unknown_staff',
        },
      ],
    };
    renderBar();
    await openCheckPanel();
    fireEvent.click(screen.getByTestId('sync-master-toggle'));

    const res = screen.getByTestId('sync-master-result');
    // スタッフ側だけ職種を添える (患者側はそのまま)。
    // toHaveTextContent は全角スペースを半角へ潰すので、氏名は正規表現で見る。
    expect(res).toHaveTextContent(/新人\s*太郎（准看護師）/);
    expect(res).toHaveTextContent('カイポケのみ（らく助未登録）: 新井');
    // 要確認には二重に出さない。
    expect(screen.queryByTestId('sync-master-qual-missing')).toBeNull();
  });
});
