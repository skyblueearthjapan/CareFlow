/**
 * KaipokeReconcilePanel — カイポケ突合ビュー (C1・weekly-space-design.md §7-3)。
 *
 * ① 取得フロー: イベント→訪問の直列取得後に差分一覧が出る + 盤面マーカー供給
 * ② イベント差分 1 件の「⇩取り込む」が changes 部分配列で apply される
 * ③ 訪問差分 1 件の取込 = include 排他 (bulk false→対象true) + 当日 days 指定 apply
 * ④ RPA 実行中 (live.running) は取得ボタンが無効
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const eventsPreviewMutateAsync = vi.fn();
const applyEventsMutateAsync = vi.fn();
const smartPreviewMutateAsync = vi.fn();
const applyInboundMutateAsync = vi.fn();
const updateItemMutateAsync = vi.fn();
const bulkItemsMutateAsync = vi.fn();
const diffLocalMutateAsync = vi.fn();
const startApplyMutateAsync = vi.fn();
const diffInboundMutateAsync = vi.fn();
const masterReconcileMutateAsync = vi.fn();
let liveRunning = false;
let correctionItems: unknown[] = [];
let outCorrectionItems: unknown[] = [];

const OUT_SHEET_ID = '00000000-0000-4000-8000-00000000beef';

vi.mock('@/lib/queries/integrations', () => ({
  useEventsInboundPreview: () => ({ mutateAsync: eventsPreviewMutateAsync }),
  useApplyEventsInbound: () => ({ mutateAsync: applyEventsMutateAsync }),
  useSmartInboundPreview: () => ({ mutateAsync: smartPreviewMutateAsync }),
  useApplyInbound: () => ({ mutateAsync: applyInboundMutateAsync }),
  useUpdateCorrectionItem: () => ({ mutateAsync: updateItemMutateAsync }),
  useBulkUpdateItems: () => ({ mutateAsync: bulkItemsMutateAsync }),
  useKaipokeLive: () => ({ data: { running: liveRunning } }),
  useCorrectionItems: (sheetId?: string) => ({
    data: { items: sheetId === OUT_SHEET_ID ? outCorrectionItems : correctionItems },
  }),
  useStartDiffLocal: () => ({ mutateAsync: diffLocalMutateAsync }),
  useStartApply: () => ({ mutateAsync: startApplyMutateAsync }),
  useStartDiffInbound: () => ({ mutateAsync: diffInboundMutateAsync }),
  useMasterReconcile: () => ({ mutateAsync: masterReconcileMutateAsync }),
}));

import { addDays, format } from 'date-fns';

import { KaipokeReconcilePanel } from '../KaipokeReconcilePanel';
import type { StaffRead } from '@/lib/schemas/staff';

const STAFF_1 = '00000000-0000-4000-8000-000000000001';
const SHEET_ID = '00000000-0000-4000-8000-00000000feed';
const ITEM_1 = '00000000-0000-4000-8000-00000000a001';
const ITEM_2 = '00000000-0000-4000-8000-00000000a002';

// 過去日フィルタ (実績保護) が実時計基準のため、対象週は常に「来週」を使う
// (固定日付だとテストが時間経過で腐る)。
const _today = new Date();
const _nextMonday = addDays(_today, ((8 - _today.getDay()) % 7) + (_today.getDay() === 1 ? 7 : 0));
const WEEK_START = format(_nextMonday, 'yyyy-MM-dd');
const WEEK_END = format(addDays(_nextMonday, 6), 'yyyy-MM-dd');
const DAY2_ISO = format(addDays(_nextMonday, 1), 'yyyy-MM-dd'); // 火曜 (weekday 1)
const DAY1_DOM = String(_nextMonday.getDate()); // 月曜の「日」(1-31)
const MONTH = WEEK_START.slice(0, 7);

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

const EVENTS_PLAN = {
  weekStart: WEEK_START,
  weekEnd: WEEK_END,
  fetchedTotal: 3,
  sundaySkipped: 0,
  memoCount: 0,
  adds: 1,
  updates: 0,
  deletes: 0,
  changes: [CHANGE_ADD],
  unmatched: [],
  conflicts: [],
};

const VISITS_PLAN = {
  weekStart: WEEK_START,
  weekEnd: WEEK_END,
  protectedDays: [WEEK_START],
  replaceDays: [format(addDays(_nextMonday, 2), 'yyyy-MM-dd')],
  sheetId: SHEET_ID,
  diffSummary: { edit: 1 },
  replace: null,
};

const VISIT_ITEM = {
  id: ITEM_1,
  sheet_id: SHEET_ID,
  patient_id: null,
  visit_id: null,
  action: 'edit',
  before: { user_name: '田中', date: DAY1_DOM, start_time: '10:00', staff1: '川名' },
  after: { user_name: '田中', date: DAY1_DOM, start_time: '10:30', staff1: '川名' },
  include: true,
  comment: null,
  created_at: '',
  updated_at: '',
};
const VISIT_ITEM_OTHER = { ...VISIT_ITEM, id: ITEM_2 };

const staffMap = new Map<string, StaffRead>([
  [STAFF_1, { id: STAFF_1, name: '川名', status: 'active' } as unknown as StaffRead],
]);

function renderPanel(onEventMarkersChange = vi.fn()) {
  return {
    onEventMarkersChange,
    ...render(
      <KaipokeReconcilePanel
        weekStartIso={WEEK_START}
        canEdit
        staffMap={staffMap}
        onClose={vi.fn()}
        onEventMarkersChange={onEventMarkersChange}
      />,
    ),
  };
}

const OUT_ITEM_1 = '00000000-0000-4000-8000-00000000b001';
const OUT_ITEM_2 = '00000000-0000-4000-8000-00000000b002';
const OUT_ITEM = {
  ...VISIT_ITEM,
  id: OUT_ITEM_1,
  sheet_id: OUT_SHEET_ID,
  action: 'edit',
};
const OUT_ITEM_B = { ...OUT_ITEM, id: OUT_ITEM_2 };

beforeEach(() => {
  vi.clearAllMocks();
  liveRunning = false;
  correctionItems = [VISIT_ITEM, VISIT_ITEM_OTHER];
  outCorrectionItems = [OUT_ITEM, OUT_ITEM_B];
  diffLocalMutateAsync.mockResolvedValue({
    jobId: '00000000-0000-4000-8000-00000000c9f0',
    sheetId: OUT_SHEET_ID,
    summary: { total: 2 },
  });
  startApplyMutateAsync.mockResolvedValue({ jobId: 'x', status: 'pending' });
  diffInboundMutateAsync.mockResolvedValue({
    jobId: '00000000-0000-4000-8000-00000000c9f1',
    sheetId: SHEET_ID,
    summary: { total: 2 },
  });
  eventsPreviewMutateAsync.mockResolvedValue(EVENTS_PLAN);
  smartPreviewMutateAsync.mockResolvedValue(VISITS_PLAN);
  applyEventsMutateAsync.mockResolvedValue({
    dryRun: false,
    added: 1,
    updated: 0,
    deleted: 0,
    skipped: 0,
    failed: 0,
    results: [],
    conflicts: [],
  });
  applyInboundMutateAsync.mockResolvedValue({ dryRun: false, failed: 0, results: [] });
  bulkItemsMutateAsync.mockResolvedValue({ updated: 1 });
  updateItemMutateAsync.mockResolvedValue(VISIT_ITEM);
});

describe('KaipokeReconcilePanel', () => {
  it('① 取得フロー: イベント→訪問の直列取得後に差分一覧 + 盤面マーカー供給', async () => {
    const { onEventMarkersChange } = renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-events-list')).toBeInTheDocument(),
    );
    expect(eventsPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: WEEK_START });
    expect(smartPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: WEEK_START });
    // イベント差分行 + 訪問差分行 + 置換日案内
    expect(screen.getByTestId(`reconcile-event-${CHANGE_ADD.externalId}`)).toBeInTheDocument();
    expect(screen.getByTestId(`reconcile-visit-${ITEM_1}`)).toBeInTheDocument();
    expect(screen.getByTestId('reconcile-replace-days')).toBeInTheDocument();
    // 盤面マーカー: 2026-08-18 = 火曜 (weekday 1)
    const lastCall = onEventMarkersChange.mock.calls.at(-1)![0] as Map<string, unknown[]>;
    expect(lastCall.get(`${STAFF_1}:1`)).toHaveLength(1);
  });

  it('② イベント1件の⇩取込 = changes 部分配列で apply・行が消える', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(
        screen.getByTestId(`reconcile-apply-event-${CHANGE_ADD.externalId}`),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId(`reconcile-apply-event-${CHANGE_ADD.externalId}`));
    await waitFor(() =>
      expect(applyEventsMutateAsync).toHaveBeenCalledWith({
        weekStart: WEEK_START,
        dryRun: false,
        changes: [CHANGE_ADD],
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId(`reconcile-event-${CHANGE_ADD.externalId}`),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('reconcile-events-empty')).toBeInTheDocument();
  });

  it('③ 訪問差分1件の⇩取込 = include排他 + 当日days指定で apply', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(screen.getByTestId(`reconcile-apply-visit-${ITEM_1}`)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId(`reconcile-apply-visit-${ITEM_1}`));
    await waitFor(() => expect(applyInboundMutateAsync).toHaveBeenCalled());
    // 他項目を include=false に
    expect(bulkItemsMutateAsync).toHaveBeenCalledWith({
      sheetId: SHEET_ID,
      ids: [ITEM_2],
      patch: { include: false },
    });
    // 対象を include=true に
    expect(updateItemMutateAsync).toHaveBeenCalledWith({
      id: ITEM_1,
      patch: { include: true },
    });
    // 対象日 (週の月曜) の日単位適用
    expect(applyInboundMutateAsync).toHaveBeenCalledWith({
      sheetId: SHEET_ID,
      dryRun: false,
      days: [WEEK_START],
    });
  });

  it('④ RPA 実行中は取得ボタンが無効', () => {
    liveRunning = true;
    renderPanel();
    expect(screen.getByTestId('reconcile-fetch-button')).toBeDisabled();
  });

  it('⑤ ⇧送信 (C2): 差分計算→1件送信は itemIds 部分適用・2段クリック確認', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-outbound-diff-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('reconcile-outbound-diff-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-outbound-list')).toBeInTheDocument(),
    );
    expect(diffLocalMutateAsync).toHaveBeenCalledWith({
      month: MONTH,
      weekStart: WEEK_START,
      weekEnd: WEEK_END,
    });
    // 計算時刻の表示 (再突合リセットとセットの改善)
    expect(screen.getByTestId('reconcile-outbound-fetched-at')).toBeInTheDocument();
    // 1回目クリック = 確認状態 (まだ送信しない)
    const sendBtn = screen.getByTestId(`reconcile-send-outbound-${OUT_ITEM_1}`);
    fireEvent.click(sendBtn);
    expect(startApplyMutateAsync).not.toHaveBeenCalled();
    expect(sendBtn).toHaveTextContent('本当に送信？');
    // 2回目クリック = 実行 — itemIds 部分適用 (includeいじりはしない・シート非ロック)
    fireEvent.click(sendBtn);
    await waitFor(() => expect(startApplyMutateAsync).toHaveBeenCalled());
    expect(bulkItemsMutateAsync).not.toHaveBeenCalled();
    expect(startApplyMutateAsync).toHaveBeenCalledWith({
      sheetId: OUT_SHEET_ID,
      dryRun: false,
      itemIds: [OUT_ITEM_1],
    });
    // 送信済み項目は一覧から消える
    await waitFor(() =>
      expect(
        screen.queryByTestId(`reconcile-outbound-${OUT_ITEM_1}`),
      ).not.toBeInTheDocument(),
    );
  });

  it('⑥ ⇩取込差分 (全曜日・#7): 計算ボタン→diff-inbound→全曜日の項目が一覧に出る', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-inbound-diff-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('reconcile-inbound-diff-button'));
    await waitFor(() =>
      expect(diffInboundMutateAsync).toHaveBeenCalledWith({
        month: MONTH,
        weekStart: WEEK_START,
      }),
    );
    // 見出しが「全曜日」へ変わり、置換日の案内は消える
    await waitFor(() => expect(screen.getByText(/訪問差分（全曜日）/)).toBeInTheDocument());
    expect(screen.queryByTestId('reconcile-replace-days')).not.toBeInTheDocument();
  });

  it('⑦ マスタ突合 (Phase M): 実行→3分類の結果が表示される', async () => {
    masterReconcileMutateAsync.mockResolvedValue({
      month: MONTH,
      patients: {
        matched: 100,
        kaipokeOnly: ['カイポケのみ患者'],
        rakusukeOnly: [],
        notationDiff: [],
      },
      staff: {
        matched: 6,
        kaipokeOnly: [],
        rakusukeOnly: [],
        notationDiff: [{ kaipoke: '髙梨　桂子', rakusuke: '髙梨桂子' }],
      },
    });
    renderPanel();
    fireEvent.click(screen.getByTestId('reconcile-fetch-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-master-button')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId('reconcile-master-button'));
    await waitFor(() =>
      expect(screen.getByTestId('reconcile-master-result')).toBeInTheDocument(),
    );
    expect(masterReconcileMutateAsync).toHaveBeenCalledWith({ month: MONTH });
    const box = screen.getByTestId('reconcile-master-result');
    expect(box.textContent).toContain('カイポケのみ患者');
    expect(box.textContent).toContain('髙梨　桂子');
    expect(box.textContent).toContain('表記ズレ');
  });
});
