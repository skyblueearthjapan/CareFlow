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
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const unsentMutateAsync = vi.fn();
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

vi.mock('@/lib/queries/cockpit', () => ({
  useUnsentSummary: () => ({ mutateAsync: unsentMutateAsync }),
}));
vi.mock('@/lib/queries/integrations', () => ({
  useStartApply: () => ({ mutateAsync: startApplyMutateAsync }),
  useSendEventsOutbound: () => ({ mutateAsync: sendEventsMutateAsync }),
}));
vi.mock('../useKaipokeReconcile', () => ({
  useKaipokeReconcile: () => reconcileStub,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { SyncBar } from '../SyncBar';

const SHEET_ID = '00000000-0000-4000-8000-00000000beef';
const ITEM_FUTURE = '00000000-0000-4000-8000-00000000a001';
const ITEM_PAST = '00000000-0000-4000-8000-00000000a002';

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
});
