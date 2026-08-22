/**
 * useKaipokeReconcile — 🔄突合ロジック (週空間 Phase E)。
 * 移植元 `KaipokeReconcilePanel.test.tsx` のうち hook に移せるケースを移植。
 *
 * ① 取得フロー: イベント→訪問の直列 + 全曜日差分 (diff-inbound) の自動継続
 * ② イベント差分 1 件が changes 部分配列で apply される
 * ③ 訪問差分 1 件の取込 = include 排他 (他を false → 対象を true) + days 指定 apply
 * ④ RPA 実行中 (live.running) が rpaRunning に出る
 * ⑤ 失敗 (例外) は phase='error' + error に出る (握り潰さない)
 * ⑥ res.failed>0 は「一部の取込に失敗しました」+ 失敗項目は適用済みにしない
 * ⑦ ⇧上書きは reverse → apply の順で、訪問差分のみ
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const eventsPreviewMutateAsync = vi.fn();
const applyEventsMutateAsync = vi.fn();
const smartPreviewMutateAsync = vi.fn();
const applyInboundMutateAsync = vi.fn();
const updateItemMutateAsync = vi.fn();
const bulkItemsMutateAsync = vi.fn();
const diffInboundMutateAsync = vi.fn();
const startApplyMutateAsync = vi.fn();
const masterReconcileMutateAsync = vi.fn();
const reverseSheetMutateAsync = vi.fn();
const toastError = vi.fn();
const toastSuccess = vi.fn();
let liveRunning = false;
let correctionItems: unknown[] = [];

const SHEET_ID = '00000000-0000-4000-8000-00000000feed';
const IN_SHEET_ID = '00000000-0000-4000-8000-00000000cafe';
const OUT_SHEET_ID = '00000000-0000-4000-8000-00000000beef';
const ITEM_1 = '00000000-0000-4000-8000-00000000a001';
const ITEM_2 = '00000000-0000-4000-8000-00000000a002';
const STAFF_1 = '00000000-0000-4000-8000-000000000001';

vi.mock('@/lib/queries/integrations', () => ({
  useEventsInboundPreview: () => ({ mutateAsync: eventsPreviewMutateAsync }),
  useApplyEventsInbound: () => ({ mutateAsync: applyEventsMutateAsync }),
  useSmartInboundPreview: () => ({ mutateAsync: smartPreviewMutateAsync }),
  useApplyInbound: () => ({ mutateAsync: applyInboundMutateAsync }),
  useUpdateCorrectionItem: () => ({ mutateAsync: updateItemMutateAsync }),
  useBulkUpdateItems: () => ({ mutateAsync: bulkItemsMutateAsync }),
  useKaipokeLive: () => ({ data: { running: liveRunning } }),
  useCorrectionItems: (sheetId?: string) => ({
    data: { items: sheetId ? correctionItems : [] },
  }),
  useStartApply: () => ({ mutateAsync: startApplyMutateAsync }),
  useStartDiffInbound: () => ({ mutateAsync: diffInboundMutateAsync }),
  useMasterReconcile: () => ({ mutateAsync: masterReconcileMutateAsync }),
}));
vi.mock('@/lib/queries/cockpit', () => ({
  useReverseSheet: () => ({ mutateAsync: reverseSheetMutateAsync }),
}));
// vi.mock は巻き上げられるため、外側の const は「呼ばれた時に」参照する。
vi.mock('sonner', () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: (...args: unknown[]) => toastSuccess(...args),
  },
}));

import { useKaipokeReconcile } from '../useKaipokeReconcile';

// 過去日フィルタが実時計基準のため、対象週は常に「来週」を使う。
const today = new Date();
const nextMonday = new Date(today);
nextMonday.setDate(today.getDate() + ((8 - today.getDay()) % 7 || 7));
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const WEEK_START = iso(nextMonday);
const DAY2 = new Date(nextMonday.getFullYear(), nextMonday.getMonth(), nextMonday.getDate() + 1);
const DAY2_ISO = iso(DAY2);
const DAY1_DOM = String(nextMonday.getDate());

const CHANGE_ADD = {
  action: 'add' as const,
  externalId: `111:22:${DAY2_ISO}`,
  staffId: STAFF_1,
  staffName: '川名',
  date: DAY2_ISO,
  start: '09:00',
  end: '09:30',
  title: '朝会',
  isMemo: false,
  beforeStart: null,
  beforeEnd: null,
  beforeTitle: null,
};

const EVENTS_PLAN = { weekStart: WEEK_START, changes: [CHANGE_ADD], conflicts: [], unmatched: [] };
const VISITS_PLAN = {
  weekStart: WEEK_START,
  sheetId: SHEET_ID,
  protectedDays: [WEEK_START],
  replaceDays: [DAY2_ISO],
  replace: null,
};

const VISIT_ITEM = {
  id: ITEM_1,
  sheet_id: SHEET_ID,
  patient_id: null,
  visit_id: null,
  action: 'edit',
  before: {
    user_name: '田中',
    date: DAY1_DOM,
    start_time: '10:00',
    end_time: '11:00',
    staff1: '川名',
  },
  after: {
    user_name: '田中',
    date: DAY1_DOM,
    start_time: '10:30',
    end_time: '11:30',
    staff1: '川名',
  },
  include: true,
  comment: null,
  created_at: '',
  updated_at: '',
  date_iso: WEEK_START,
};
const VISIT_ITEM_OTHER = { ...VISIT_ITEM, id: ITEM_2 };

function render() {
  return renderHook(() => useKaipokeReconcile({ weekStartIso: WEEK_START, canEdit: true }));
}

async function renderReady() {
  const h = render();
  await act(async () => {
    await h.result.current.runFetch();
  });
  await waitFor(() => expect(h.result.current.phase).toBe('ready'));
  return h;
}

beforeEach(() => {
  vi.clearAllMocks();
  liveRunning = false;
  correctionItems = [VISIT_ITEM, VISIT_ITEM_OTHER];
  eventsPreviewMutateAsync.mockResolvedValue(EVENTS_PLAN);
  smartPreviewMutateAsync.mockResolvedValue(VISITS_PLAN);
  diffInboundMutateAsync.mockResolvedValue({ sheetId: IN_SHEET_ID, summary: { total: 2 } });
  applyEventsMutateAsync.mockResolvedValue({
    added: 1,
    updated: 0,
    deleted: 0,
    skipped: 0,
    failed: 0,
    results: [{ action: 'add', externalId: CHANGE_ADD.externalId, outcome: 'added' }],
  });
  applyInboundMutateAsync.mockResolvedValue({
    cancelled: 0,
    updated: 1,
    added: 0,
    skipped: 0,
    failed: 0,
    results: [{ itemId: ITEM_1, action: 'edit', outcome: 'updated' }],
  });
  reverseSheetMutateAsync.mockResolvedValue({ sheet_id: OUT_SHEET_ID, item_count: 1 });
  startApplyMutateAsync.mockResolvedValue({ jobId: 'j1' });
});

describe('useKaipokeReconcile', () => {
  it('イベント→訪問→全曜日差分の順に取得し、差分を統合して返す', async () => {
    const h = await renderReady();
    expect(eventsPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: WEEK_START });
    expect(smartPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: WEEK_START });
    // 実績のない日も 1 件ずつ取り込めるよう diff-inbound まで自動で続く
    expect(diffInboundMutateAsync).toHaveBeenCalledWith({
      month: WEEK_START.slice(0, 7),
      weekStart: WEEK_START,
    });
    expect(eventsPreviewMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      smartPreviewMutateAsync.mock.invocationCallOrder[0]!,
    );
    expect(smartPreviewMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      diffInboundMutateAsync.mock.invocationCallOrder[0]!,
    );

    await waitFor(() => expect(h.result.current.inSheetId).toBe(IN_SHEET_ID));
    // イベント1件 + 訪問2件
    expect(h.result.current.diffs).toHaveLength(3);
    expect(h.result.current.diffs[0]!.kind).toBe('event');
    expect(h.result.current.visitsPlan?.replaceDays).toEqual([DAY2_ISO]);
  });

  it('イベント差分 1 件は changes 部分配列で apply される', async () => {
    const h = await renderReady();
    const diff = h.result.current.diffs.find((d) => d.kind === 'event')!;
    await act(async () => {
      await h.result.current.applyDiff(diff);
    });
    expect(applyEventsMutateAsync).toHaveBeenCalledWith({
      weekStart: WEEK_START,
      dryRun: false,
      changes: [CHANGE_ADD],
    });
    await waitFor(() => expect(h.result.current.diffs.some((d) => d.kind === 'event')).toBe(false));
  });

  it('訪問差分 1 件は include 排他 (他=false → 対象=true) → days 指定 apply', async () => {
    const h = await renderReady();
    const diff = h.result.current.diffs.find((d) => d.id === ITEM_1)!;
    await act(async () => {
      await h.result.current.applyDiff(diff);
    });
    expect(bulkItemsMutateAsync).toHaveBeenCalledWith({
      sheetId: IN_SHEET_ID,
      ids: [ITEM_2],
      patch: { include: false },
    });
    expect(updateItemMutateAsync).toHaveBeenCalledWith({
      id: ITEM_1,
      patch: { include: true },
    });
    expect(applyInboundMutateAsync).toHaveBeenCalledWith({
      sheetId: IN_SHEET_ID,
      dryRun: false,
      days: [WEEK_START],
    });
    // 呼び順: bulk(false) → update(true) → apply
    expect(bulkItemsMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      updateItemMutateAsync.mock.invocationCallOrder[0]!,
    );
    expect(updateItemMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      applyInboundMutateAsync.mock.invocationCallOrder[0]!,
    );
    await waitFor(() => expect(h.result.current.diffs.some((d) => d.id === ITEM_1)).toBe(false));
  });

  it('failed>0 は「一部の取込に失敗しました」を出し、失敗項目を適用済みにしない', async () => {
    applyInboundMutateAsync.mockResolvedValue({
      cancelled: 0,
      updated: 0,
      added: 0,
      skipped: 0,
      failed: 1,
      results: [{ itemId: ITEM_1, action: 'edit', outcome: 'failed', detail: 'NG' }],
    });
    const h = await renderReady();
    const diff = h.result.current.diffs.find((d) => d.id === ITEM_1)!;
    await act(async () => {
      await h.result.current.applyDiff(diff);
    });
    expect(h.result.current.error).toContain('一部の取込に失敗しました');
    expect(toastError).toHaveBeenCalled();
    // 失敗した項目はリストに残る (再試行できる)
    expect(h.result.current.diffs.some((d) => d.id === ITEM_1)).toBe(true);
  });

  it('取得が失敗したら phase=error + error にメッセージが出る', async () => {
    smartPreviewMutateAsync.mockRejectedValue(new Error('カイポケに繋がりません'));
    const h = render();
    await act(async () => {
      await h.result.current.runFetch();
    });
    expect(h.result.current.phase).toBe('error');
    expect(h.result.current.error).toContain('カイポケに繋がりません');
    expect(toastError).toHaveBeenCalled();
  });

  it('取込の例外は握り潰さず error + toast に出る', async () => {
    applyInboundMutateAsync.mockRejectedValue(new Error('500'));
    const h = await renderReady();
    const diff = h.result.current.diffs.find((d) => d.id === ITEM_1)!;
    await act(async () => {
      await h.result.current.applyDiff(diff);
    });
    expect(h.result.current.error).toContain('取込に失敗しました');
    expect(h.result.current.busyKey).toBeNull();
  });

  it('⇧上書きは reverse → apply の順 (訪問のみ・イベントは何もしない)', async () => {
    const h = await renderReady();
    const eventDiff = h.result.current.diffs.find((d) => d.kind === 'event')!;
    await act(async () => {
      await h.result.current.overwriteDiff(eventDiff);
    });
    expect(reverseSheetMutateAsync).not.toHaveBeenCalled();

    const visitDiff = h.result.current.diffs.find((d) => d.id === ITEM_1)!;
    await act(async () => {
      await h.result.current.overwriteDiff(visitDiff);
    });
    expect(reverseSheetMutateAsync).toHaveBeenCalledWith({ sheet_id: IN_SHEET_ID });
    expect(startApplyMutateAsync).toHaveBeenCalledWith({
      sheetId: OUT_SHEET_ID,
      dryRun: false,
    });
    expect(reverseSheetMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      startApplyMutateAsync.mock.invocationCallOrder[0]!,
    );
  });

  it('RPA 実行中は rpaRunning が立つ', () => {
    liveRunning = true;
    const h = render();
    expect(h.result.current.rpaRunning).toBe(true);
  });

  it('全曜日差分の失敗は突合結果を捨てずに error だけ出す', async () => {
    diffInboundMutateAsync.mockRejectedValue(new Error('タイムアウト'));
    const h = render();
    await act(async () => {
      await h.result.current.runFetch();
    });
    expect(h.result.current.phase).toBe('ready'); // ①② の結果は残る
    expect(h.result.current.error).toContain('取込差分');
  });
});
