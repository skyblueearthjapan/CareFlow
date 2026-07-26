/**
 * InboundControls — smart-inbound (日単位ハイブリッド自動判別) の描画契約 (2026-07-27)。
 *
 * ① イベント差分（追加/変更/削除・📝メモ・未登録職員）が表示される
 * ② イベント差分のみでも ❸「取り込む」が押せる（訪問プランなしでも取り込める）
 * ③ smart 統合プレビュー: 日別バッジ（🔒差分/置換）・置換サマリ・対象外・新人警告
 * ④ イベント取得失敗 (eventsError) が Alert で明示される
 * ⑤ 確認ダイアログ: PO指示の「すべて削除される可能性」文言が置換日ありのとき表示される
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { InboundControls } from '../_components/InboundControls';
import type { InboundVm } from '../_components/useInbound';
import type { EventsInboundPreview, SmartInboundPreview } from '@/lib/schemas/integration';

const idleMutation = {
  mutateAsync: vi.fn(),
  mutate: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
};
const eligQuery = { data: { eligible: true }, isLoading: false };

const MONDAY = new Date(2026, 6, 20); // 2026-07-20 (月)

const EVENTS_PLAN: EventsInboundPreview = {
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

/** 打刻あり火曜=差分・他は置換、の混在週プラン。 */
const SMART_PLAN: SmartInboundPreview = {
  weekStart: '2026-07-20',
  weekEnd: '2026-07-25',
  protectedDays: ['2026-07-20', '2026-07-21'],
  replaceDays: ['2026-07-22', '2026-07-23', '2026-07-24', '2026-07-25'],
  sheetId: 'a4dd44a1-0000-4000-8000-000000000000',
  diffSummary: { delete: 3, edit: 2, add: 1 },
  replace: {
    jobId: null,
    weekStart: '2026-07-20',
    weekEnd: '2026-07-25',
    dryRun: true,
    wiped: 78,
    inserted: 71,
    sundaySkipped: 0,
    tempCourses: 2,
    coursesReassigned: 8,
    coursesCreated: 1,
    skipped: [
      {
        reason: '患者を名寄せできません（らく助未登録の可能性）',
        userName: '高尾　幸子',
        staffName: '川名　千恵',
        date: '2026-07-24',
        start: '10:00',
      },
    ],
    traineeSolo: [{ staffName: '髙梨　桂子', count: 17 }],
  },
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
    handleWeekChange: vi.fn(),
    smartPreview: { ...idleMutation },
    applySmart: { ...idleMutation },
    smartPlan: null,
    sheetId: null,
    itemsQuery: { data: { items: [] } },
    items: [],
    eventsPreview: { ...idleMutation },
    applyEvents: { ...idleMutation },
    eventsPlan: null,
    eventsError: null,
    hasEventChanges: false,
    confirm: false,
    setConfirm: vi.fn(),
    runDiff: vi.fn(),
    runApply: vi.fn(),
    fetching: false,
    applying: false,
    canApply: false,
    ...overrides,
  } as unknown as InboundVm;
}

describe('InboundControls — smart-inbound', () => {
  it('① イベント差分（チップ・行・📝メモ・未登録職員）が表示される', () => {
    render(
      <InboundControls
        vm={makeVm({ eventsPlan: EVENTS_PLAN, hasEventChanges: true, canApply: true })}
      />,
    );
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

  it('② イベント差分のみでも ❸「取り込む」が押せる', () => {
    render(
      <InboundControls
        vm={makeVm({ eventsPlan: EVENTS_PLAN, hasEventChanges: true, canApply: true })}
      />,
    );
    expect(screen.getByTestId('smart-apply-button')).toBeEnabled();
  });

  it('②b 取り込む対象がなければ ❸ は無効', () => {
    render(
      <InboundControls
        vm={makeVm({
          eventsPlan: { ...EVENTS_PLAN, changes: [], adds: 0, updates: 0 },
          canApply: false,
        })}
      />,
    );
    expect(screen.getByTestId('smart-apply-button')).toBeDisabled();
    expect(screen.getByText(/イベントの差分はありません/)).toBeInTheDocument();
    expect(screen.getByText(/取り込む対象がありません/)).toBeInTheDocument();
  });

  it('③ smart 統合プレビュー: 日別バッジ・差分/置換サマリ・対象外・新人警告', () => {
    render(<InboundControls vm={makeVm({ smartPlan: SMART_PLAN, canApply: true })} />);
    expect(screen.getByTestId('smart-plan-panel')).toBeInTheDocument();
    // 日別バッジ: 実績日は 🔒差分・それ以外は置換 (自動判別が見える)
    expect(screen.getByText(/🔒 7\/20（月） 差分/)).toBeInTheDocument();
    expect(screen.getByText(/🔒 7\/21（火） 差分/)).toBeInTheDocument();
    expect(screen.getByText(/7\/22（水） 置換/)).toBeInTheDocument();
    // 実績日の差分サマリ
    expect(screen.getByText('実績日の差分:')).toBeInTheDocument();
    expect(screen.getByText('キャンセル候補')).toBeInTheDocument();
    // 置換サマリ (削除/挿入/コース担当変更/コース新設/臨時/対象外)
    expect(screen.getByTestId('replace-plan-panel')).toBeInTheDocument();
    expect(screen.getByText('削除（白紙化）')).toBeInTheDocument();
    expect(screen.getByText('コース担当変更')).toBeInTheDocument();
    expect(screen.getByText('コース新設')).toBeInTheDocument();
    // 対象外の一覧 (隠さない)
    expect(screen.getByText(/患者を名寄せできません/)).toBeInTheDocument();
    // ⚠新人の単独訪問 警告 (取り込むが新人フラグ見直しを促す・PO指示 2026-07-26)
    expect(screen.getByTestId('trainee-solo-warning')).toBeInTheDocument();
    expect(screen.getByText(/新人の単独訪問が含まれています（17件）/)).toBeInTheDocument();
    expect(screen.getByText(/新人フラグをOFFにすることを検討/)).toBeInTheDocument();
    // ❸ が有効
    expect(screen.getByTestId('smart-apply-button')).toBeEnabled();
  });

  it('③b プレビュー前は ❸ 実行エリアが出ない（モード選択も存在しない）', () => {
    render(<InboundControls vm={makeVm()} />);
    expect(screen.queryByTestId('smart-apply-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('inbound-mode-toggle')).not.toBeInTheDocument();
  });

  it('④ イベント取得失敗は Alert で明示される（訪問は取得済みの文言つき）', () => {
    render(
      <InboundControls vm={makeVm({ smartPlan: SMART_PLAN, eventsError: 'RPA 502' })} />,
    );
    expect(screen.getByText(/イベント（個別業務）の取得に失敗しました/)).toBeInTheDocument();
    expect(screen.getByText(/訪問だけ取り込めます/)).toBeInTheDocument();
  });

  it('⑤ 確認ダイアログ: 置換日ありのとき PO指示の削除警告文言と🔒保護日が表示される', () => {
    render(
      <InboundControls
        vm={makeVm({
          smartPlan: SMART_PLAN,
          eventsPlan: EVENTS_PLAN,
          hasEventChanges: true,
          canApply: true,
          confirm: true,
        })}
      />,
    );
    // PO指示の文言 (2026-07-25)
    expect(screen.getAllByText(/すべて削除される可能性がございます/).length).toBeGreaterThan(0);
    // 🔒実績日は行を守る旨
    expect(screen.getByText(/行を残したまま差分を反映します/)).toBeInTheDocument();
    // 置換規模とイベント件数
    expect(screen.getByText(/78 件を削除/)).toBeInTheDocument();
    expect(screen.getByText(/追加 2 \/ 変更 1 \/ 削除 0/)).toBeInTheDocument();
  });

  it('⑤b 全日置換（打刻ゼロ週）: 🔒行は出ず、置換警告のみ', () => {
    const cleanPlan: SmartInboundPreview = {
      ...SMART_PLAN,
      protectedDays: [],
      replaceDays: [
        '2026-07-20',
        '2026-07-21',
        '2026-07-22',
        '2026-07-23',
        '2026-07-24',
        '2026-07-25',
      ],
      sheetId: null,
      diffSummary: {},
    };
    render(
      <InboundControls vm={makeVm({ smartPlan: cleanPlan, canApply: true, confirm: true })} />,
    );
    expect(screen.queryByText(/行を残したまま差分を反映します/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/すべて削除される可能性がございます/).length).toBeGreaterThan(0);
  });
});
