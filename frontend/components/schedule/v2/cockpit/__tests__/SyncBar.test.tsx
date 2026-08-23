/**
 * SyncBar — 同期バー (週空間 Phase E)。
 *
 * ① 未送信 0 件: 「なし（カイポケと同じ）」+ 送信ボタン無効 + ✓ 表示
 * ② 未送信あり: 件数 (送れる N) が出て、⇧1件送信 が既存 apply の部分適用を呼ぶ
 * ③ 当日以前 (JST) は送信対象外 (option disabled・全件からも除外)
 * ④ ⇧全件 は 2 段クリック (1回目は確認表示のみ・2回目で実行)
 *
 * 過去日判定は実時計基準のため、送れる分は常に「来週」を使う。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

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
/** SyncBar が useKaipokeReconcile に渡した onReady (= 突合完了コールバック)。 */
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
    // 「突合が終わった」を後からテストで起こせるよう控えておく。
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
  render(<SyncBar weekStartIso={WEEK_START} canEdit onSelectDiff={onSelectDiff} />);
  return onSelectDiff;
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

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(reconcileStub, RECONCILE_DEFAULTS);
  startApplyMutateAsync.mockResolvedValue({ jobId: 'j1' });
});

describe('SyncBar', () => {
  it('未送信 0 件のときは「なし（カイポケと同じ）」で送信できない', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalledWith({ week_start: WEEK_START }));
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-select')).toHaveTextContent('なし（カイポケと同じ）'),
    );
    expect(screen.getByTestId('sync-unsent-send')).toBeDisabled();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
    expect(screen.getByTestId('sync-meta')).toHaveTextContent('カイポケと同じ状態です');
  });

  it('未送信ありのとき件数が出て ⇧1件送信 が部分適用を呼ぶ', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY), item(ITEM_PAST, '2020-01-06')],
      sendable_count: 1,
      past_count: 1,
    });
    renderBar();
    // 件数は BE の past_count を正として表示する
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-count')).toHaveTextContent(
        '2件（送れる 1・当日以前 1）',
      ),
    );
    // 当日以前は選べない
    const opts = screen
      .getByTestId('sync-unsent-select')
      .querySelectorAll('option') as NodeListOf<HTMLOptionElement>;
    expect([...opts].find((o) => o.value === ITEM_PAST)?.disabled).toBe(true);
    expect([...opts].find((o) => o.value === ITEM_PAST)?.textContent).toContain(
      '当日以前=送信対象外',
    );

    fireEvent.click(screen.getByTestId('sync-unsent-send'));
    await waitFor(() =>
      expect(startApplyMutateAsync).toHaveBeenCalledWith({
        sheetId: SHEET_ID,
        dryRun: false,
        itemIds: [ITEM_FUTURE],
      }),
    );
  });

  it('⇧全件 は 2 段クリック (1回目は確認・2回目で実行)', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    renderBar();
    const all = await screen.findByTestId('sync-unsent-send-all');
    await waitFor(() => expect(all).toBeEnabled());

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

  it('らく助が作業中 (rec.busyKey) は突合系も送信系も押せない', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    reconcileStub.busyKey = '__in_diff__';
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    expect(screen.getByTestId('sync-working')).toBeInTheDocument();
    for (const id of [
      'sync-reconcile-run',
      'sync-in-apply',
      'sync-in-apply-all',
      'sync-in-over',
      'sync-in-over-all',
      'sync-unsent-send',
      'sync-unsent-send-all',
      'sync-master-button',
    ]) {
      expect(screen.getByTestId(id)).toBeDisabled();
    }
  });

  it('RPA 未対応 (准看/一般) の行は選べず「送れる」から外れる', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY), rpaUnsupportedItem(ITEM_RPA, FUTURE_DAY)],
      sendable_count: 1,
      past_count: 0,
      rpa_unsupported_count: 1,
    });
    renderBar();

    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-count')).toHaveTextContent(
        '2件（送れる 1・RPA未対応 1）',
      ),
    );

    const opts = screen
      .getByTestId('sync-unsent-select')
      .querySelectorAll('option') as NodeListOf<HTMLOptionElement>;
    const blocked = [...opts].find((o) => o.value === ITEM_RPA);
    expect(blocked?.disabled).toBe(true);
    expect(blocked?.textContent).toContain('RPAが准看/一般の登録に未対応');
    // 対応済みの行は従来どおり選べる
    expect([...opts].find((o) => o.value === ITEM_FUTURE)?.disabled).toBe(false);

    // ⇧1件送信 / ⇧全件 のどちらも RPA 未対応を含めない
    fireEvent.click(screen.getByTestId('sync-unsent-send'));
    await waitFor(() =>
      expect(startApplyMutateAsync).toHaveBeenCalledWith({
        sheetId: SHEET_ID,
        dryRun: false,
        itemIds: [ITEM_FUTURE],
      }),
    );
  });

  it('RPA 未対応しか無ければ送信ボタンが無効', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [rpaUnsupportedItem(ITEM_RPA, FUTURE_DAY)],
      sendable_count: 0,
      rpa_unsupported_count: 1,
    });
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    expect(screen.getByTestId('sync-unsent-send')).toBeDisabled();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
  });

  it('RPA 実行中は未送信の送信ボタンも押せない', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    reconcileStub.rpaRunning = true;
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    expect(screen.getByTestId('sync-unsent-send')).toBeDisabled();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
  });

  it('未送信を選ばなくても差分カードが出る (既定=先頭の送れる分)', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [item(ITEM_FUTURE, FUTURE_DAY)],
      sendable_count: 1,
    });
    const onSelectDiff = renderBar();
    expect(await screen.findByTestId('diff-detail-card')).toBeInTheDocument();
    // 盤面ゴーストにも同じマーカーが渡る
    await waitFor(() =>
      expect(onSelectDiff).toHaveBeenCalledWith(expect.objectContaining({ kind: 'visit' })),
    );
  });

  it('実績のない日があれば「取込差分を計算」の案内を出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    reconcileStub.visitsPlan = { replaceDays: ['2026-08-19'], replace: { inserted: 4 } };
    renderBar();
    const box = await screen.findByTestId('sync-replace-days');
    expect(box).toHaveTextContent('実績のない日');
    fireEvent.click(screen.getByTestId('sync-in-diff-button'));
    expect(reconcileStub.fetchInboundDiff).toHaveBeenCalled();
  });

  it('👥マスタ突合の結果を表示する', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
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
    const res = await screen.findByTestId('sync-master-result');
    expect(res).toHaveTextContent('患者: 一致 12');
    expect(res).toHaveTextContent('カイポケ「高橋」⇔らく助「髙橋」');
    expect(res).toHaveTextContent('らく助のみ（当月のカイポケスケジュールに未出現）: 熊澤');
  });

  it('カイポケ側の控えが無ければ突合を促す', async () => {
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, snapshot: null, sheet_id: null });
    renderBar();
    expect(await screen.findByTestId('sync-no-snapshot')).toHaveTextContent(
      '🔄突合でカイポケ現況を取得してください',
    );
  });

  // ── サービス内容のズレ (kaipoke-service-content-design.md §2 / §3-1) ──
  // カイポケの編集はサービス内容を触れないため、差分は必ず delete + add で出る。
  // 生のまま見せると「取消して新規登録する」に読めるので 1 行に束ねる。

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
    renderBar();

    const row = await screen.findByTestId('sync-service-mismatch-row');
    expect(row).toHaveTextContent('田中');
    expect(row).toHaveTextContent('らく助: 基本療養費Ⅰ・正看');
    expect(row).toHaveTextContent('カイポケ: 精神基本療養費Ⅰ・准看');
    // 束ねても各 item は一覧に残る = 個別の ⇧ 送信は引き続きできる。
    const opts = screen
      .getByTestId('sync-unsent-select')
      .querySelectorAll('option') as NodeListOf<HTMLOptionElement>;
    expect([...opts].map((o) => o.value)).toEqual(
      expect.arrayContaining([ITEM_SVC_ADD, ITEM_SVC_DELETE]),
    );

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
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    expect(screen.queryByTestId('sync-service-mismatch')).toBeNull();
  });

  it('ペアの delete は BE のフラグどおり送信対象から外れる（片肺送信の防止）', async () => {
    unsentMutateAsync.mockResolvedValue({
      ...EMPTY_SUMMARY,
      items: [servicePairAdd(), servicePairDelete()],
      // BE はペアの両方を rpa_unsupported に数える。
      sendable_count: 0,
      rpa_unsupported_count: 2,
    });
    renderBar();
    await screen.findByTestId('sync-service-mismatch-row');

    expect(screen.getByTestId('sync-unsent-count')).toHaveTextContent(
      '2件（送れる 0・RPA未対応 2）',
    );
    const opts = screen
      .getByTestId('sync-unsent-select')
      .querySelectorAll('option') as NodeListOf<HTMLOptionElement>;
    // delete 側も選べない = FE がサービス内容を自前で判定していない証拠。
    expect([...opts].find((o) => o.value === ITEM_SVC_DELETE)?.disabled).toBe(true);
    expect([...opts].find((o) => o.value === ITEM_SVC_ADD)?.disabled).toBe(true);
    expect(screen.getByTestId('sync-unsent-send')).toBeDisabled();
    expect(screen.getByTestId('sync-unsent-send-all')).toBeDisabled();
  });

  // ── 資格のズレ / 未設定 (§1-2) ──

  it('資格のズレは表示のみ・未設定は「カイポケの職種を採用」で埋められる', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
    updateStaffMutateAsync.mockResolvedValue({ id: STAFF_MISSING });
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

    const box = await screen.findByTestId('sync-master-qualifications');
    expect(box).toHaveTextContent('資格: ズレ 1 ・ 未設定 1');
    // 一致は出さない
    expect(box).not.toHaveTextContent('一致 太郎');
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
    const row = await screen.findByTestId('sync-master-qual-ambiguous');
    expect(row).toHaveTextContent('同じ名前の在職スタッフが複数います');
    expect(screen.queryByTestId('sync-master-qual-adopt')).toBeNull();
    expect(screen.getByTestId('sync-master-qualifications')).toHaveTextContent('同名で判別不可 1');
  });

  it('らく助未登録のスタッフはカイポケの職種を氏名に添えて出す', async () => {
    unsentMutateAsync.mockResolvedValue(EMPTY_SUMMARY);
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
    const res = await screen.findByTestId('sync-master-result');
    // スタッフ側だけ職種を添える (患者側はそのまま)。
    // toHaveTextContent は全角スペースを半角へ潰すので、氏名は正規表現で見る。
    expect(res).toHaveTextContent(/新人\s*太郎（准看護師）/);
    expect(res).toHaveTextContent('カイポケのみ（らく助未登録）: 新井');
    // 資格セクションには二重に出さない。
    expect(screen.queryByTestId('sync-master-qual-missing')).toBeNull();
  });

  // ── 送信済みの印は訪問キー基準 / 再計算では消えない (M1) ──

  it('送信後に数え直しても、送った行は復活しない（item.id が変わっても）', async () => {
    const row = item(ITEM_FUTURE, FUTURE_DAY);
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, items: [row], sendable_count: 1 });
    const reload = renderBarWithReload();
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-count')).toHaveTextContent('1件（送れる 1）'),
    );

    fireEvent.click(screen.getByTestId('sync-unsent-send'));
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
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-select')).toHaveTextContent('なし（カイポケと同じ）'),
    );
  });

  it('🔄突合が終わったときだけ送信済みの印を捨てる', async () => {
    const row = item(ITEM_FUTURE, FUTURE_DAY);
    unsentMutateAsync.mockResolvedValue({ ...EMPTY_SUMMARY, items: [row], sendable_count: 1 });
    renderBar();
    await waitFor(() => expect(unsentMutateAsync).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('sync-unsent-send'));
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-select')).toHaveTextContent('なし（カイポケと同じ）'),
    );

    // 突合完了 = カイポケの現況を見直した後。まだ残る行は本当に未送信。
    await act(async () => {
      await reconcileOnReady?.();
    });
    await waitFor(() =>
      expect(screen.getByTestId('sync-unsent-count')).toHaveTextContent('1件（送れる 1）'),
    );
  });
});
