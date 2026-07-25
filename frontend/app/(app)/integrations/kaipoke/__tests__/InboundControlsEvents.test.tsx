/**
 * InboundControls — イベント（個別業務）取り込みセクションの描画契約 (E-2)。
 *
 * ① イベント差分（追加/変更/削除・📝メモ・未登録職員）が表示される
 * ② イベント差分のみでも ❸ dry-run が押せる（訪問差分ゼロでも取り込める）
 * ③ イベント dry-run 結果テーブルが表示される
 * ④ イベント取得失敗 (eventsError) が Alert で明示される
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { InboundControls } from '../_components/InboundControls';
import type { InboundVm } from '../_components/useInbound';
import type {
  EventsInboundApplyResult,
  EventsInboundPreview,
} from '@/lib/schemas/integration';

const idleMutation = {
  mutateAsync: vi.fn(),
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
};
const eligQuery = { data: { eligible: true }, isLoading: false };

const MONDAY = new Date(2026, 6, 20); // 2026-07-20 (月)

const PLAN: EventsInboundPreview = {
  weekStart: '2026-07-20',
  weekEnd: '2026-07-25',
  fetchedTotal: 4,
  sundaySkipped: 1,
  memoCount: 1,
  adds: 2,
  updates: 1,
  deletes: 0,
  changes: [
    {
      action: 'add',
      externalId: '690499216:4601519:2026-07-20',
      staffId: '11111111-1111-4111-8111-111111111111',
      staffName: '宇田川　優莉',
      date: '2026-07-20',
      start: '09:00',
      end: '18:00',
      title: '休み',
      isMemo: false,
    },
    {
      action: 'add',
      externalId: '674969993:4465191:2026-07-25',
      staffId: '22222222-2222-4222-8222-222222222222',
      staffName: '川名　千恵',
      date: '2026-07-25',
      start: '00:00',
      end: '00:00',
      title: '清水様：歯科薬お渡し',
      isMemo: true,
    },
    {
      action: 'update',
      externalId: '695430472:4465191:2026-07-21',
      staffId: '22222222-2222-4222-8222-222222222222',
      staffName: '川名　千恵',
      date: '2026-07-21',
      start: '10:00',
      end: '11:00',
      title: 'ケア会議：青栁あい様',
      isMemo: false,
      beforeStart: '09:30',
      beforeEnd: '10:30',
    },
  ],
  unmatched: [{ staffName: '菅野　頼子', count: 2 }],
};

const APPLY_RESULT: EventsInboundApplyResult = {
  jobId: null,
  dryRun: true,
  added: 2,
  updated: 1,
  deleted: 0,
  skipped: 0,
  failed: 0,
  results: [
    {
      action: 'add',
      externalId: '690499216:4601519:2026-07-20',
      staffName: '宇田川　優莉',
      date: '2026-07-20',
      title: '休み',
      outcome: 'added',
      detail: '',
    },
  ],
};

function makeVm(overrides: Partial<InboundVm> = {}): InboundVm {
  return {
    busy: false,
    credentialsConfigured: true,
    thisMonday: MONDAY,
    nextMonday: new Date(2026, 6, 27),
    selectedWeek: 'this',
    weekStart: MONDAY,
    thisElig: eligQuery,
    nextElig: eligQuery,
    currentElig: eligQuery,
    eligible: true,
    diffInbound: { ...idleMutation },
    applyInbound: { ...idleMutation },
    itemsQuery: { data: { items: [] } },
    items: [],
    weekDays: [
      '2026-07-20',
      '2026-07-21',
      '2026-07-22',
      '2026-07-23',
      '2026-07-24',
      '2026-07-25',
    ],
    daysWithDiff: new Set<string>(),
    sheetId: null,
    summary: null,
    selectedDays: new Set<string>(),
    setSelectedDays: vi.fn(),
    dryRunResult: null,
    setDryRunResult: vi.fn(),
    confirm: false,
    setConfirm: vi.fn(),
    handleWeekChange: vi.fn(),
    runDiff: vi.fn(),
    runApply: vi.fn(),
    hasSelectedDays: false,
    selectedDayLabels: '',
    massCancelWarning: false,
    eventsPreview: { ...idleMutation },
    applyEvents: { ...idleMutation },
    eventsPlan: null,
    eventsError: null,
    eventsDryRunResult: null,
    hasEventChanges: false,
    fetching: false,
    ...overrides,
  } as unknown as InboundVm;
}

describe('InboundControls — イベント取り込みセクション', () => {
  it('① イベント差分（チップ・行・📝メモ・未登録職員）が表示される', () => {
    render(<InboundControls vm={makeVm({ eventsPlan: PLAN, hasEventChanges: true })} />);
    const section = screen.getByTestId('events-plan-section');
    expect(section).toBeInTheDocument();
    expect(screen.getByText('休み')).toBeInTheDocument();
    expect(screen.getByText('ケア会議：青栁あい様')).toBeInTheDocument();
    // update 行は before → after の時刻を併記
    expect(screen.getByText(/09:30〜10:30 → 10:00〜11:00/)).toBeInTheDocument();
    // メモ系は 📝 表示 (サマリチップ + 行の2箇所)
    expect(screen.getAllByText(/📝 メモ/).length).toBeGreaterThanOrEqual(2);
    // 未登録職員の可視化 (全角空白は Testing Library が正規化するため部分一致)
    expect(screen.getByText(/らく助未登録のため対象外/)).toBeInTheDocument();
    expect(screen.getByText(/頼子（2件）/)).toBeInTheDocument();
  });

  it('② イベント差分のみでも ❸ dry-run が押せる', () => {
    render(<InboundControls vm={makeVm({ eventsPlan: PLAN, hasEventChanges: true })} />);
    const btn = screen.getByRole('button', { name: /dry-run で確認/ });
    expect(btn).toBeEnabled();
  });

  it('②b 差分が何もなければ ❸ は無効', () => {
    render(
      <InboundControls
        vm={makeVm({ eventsPlan: { ...PLAN, changes: [], adds: 0, updates: 0 } })}
      />,
    );
    const btn = screen.getByRole('button', { name: /dry-run で確認/ });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/イベントの差分はありません/)).toBeInTheDocument();
  });

  it('③ イベント dry-run 結果テーブルが表示される', () => {
    render(
      <InboundControls
        vm={makeVm({
          eventsPlan: PLAN,
          hasEventChanges: true,
          eventsDryRunResult: APPLY_RESULT,
        })}
      />,
    );
    expect(screen.getByTestId('events-dryrun-table')).toBeInTheDocument();
    expect(screen.getByText(/イベント — 追加: 2 \/ 更新: 1/)).toBeInTheDocument();
  });

  it('⑤ 大量キャンセル警告: キャンセル候補が閾値以上なら赤警告が出る', () => {
    render(
      <InboundControls
        vm={makeVm({
          sheetId: 'a4dd44a1-0000-4000-8000-000000000000',
          summary: { delete: 42, edit: 0, add: 0 },
          massCancelWarning: true,
        })}
      />,
    );
    expect(screen.getByTestId('mass-cancel-warning')).toBeInTheDocument();
    expect(screen.getByText(/キャンセル候補が異常に多いです（42件）/)).toBeInTheDocument();
    expect(screen.getByText(/自動選択を止めています/)).toBeInTheDocument();
  });

  it('④ イベント取得失敗は Alert で明示される（訪問は取得済みの文言つき）', () => {
    render(
      <InboundControls
        vm={makeVm({ sheetId: 'a4dd44a1-0000-4000-8000-000000000000', eventsError: 'RPA 502' })}
      />,
    );
    expect(screen.getByText(/イベント（個別業務）の取得に失敗しました/)).toBeInTheDocument();
    expect(screen.getByText(/訪問だけ取り込めます/)).toBeInTheDocument();
  });
});
