'use client';

/**
 * AssignWarningDialog — Phase G-91 (確認レビューフロー / コースカード型レビュー).
 *
 * 「自動スタッフ割当」 (旧称: 自動スタッフ割付) を、 問題のあるコースだけ管理者が
 * 最終判断する確認レビューフローに作り替えたダイアログ。
 * 直前の「埋めて事後警告」 (Phase G-89) を置換する。
 *
 * 表示対象 (review_items) は 2 種:
 *   🔴 性別 (gender / 重度): 適合性別の同拠点スタッフが居ないコース。
 *      候補スタッフ (= 性別無視時の候補) を提示し、 「割り当てる」 ボタン →
 *      確認モーダル 1 回 (= 計 2 ステップ) を踏んで承認する。
 *   🟡 連続 (consecutive / 軽度): 患者の直近担当者と同じになるコース。
 *      候補スタッフを提示し、 チェックボックスで承認する (追加モーダル無し)。
 *      件数が多いときのために「一斉承認」ボタンで全件を一括チェックできる
 *      (2026-07: 十数件を 1 件ずつチェックする手間の解消)。 性別 (重度) は
 *      誤操作リスクが高いため一斉承認の対象外 (従来どおり 2 ステップ個別承認)。
 *
 * apply = 承認されたカードを ``POST /api/v1/schedule/apply-staff-review`` で
 * 一括反映する (呼び出し側 onApply に委譲)。 自動スタッフ割当と同一の _persist
 * 経路で VSA / course_status / primary・secondary 同期 / 2 名体制 を全て反映する
 * (= 旧 PATCH /courses ループのリグレッションを解消)。旧 trainee companion 注入は
 * 新人同行 Phase 2 (trainee-accompaniment-design.md §3) で撤去済み。
 *
 * review_items が空なら呼び出し側でこのダイアログを出さず、 success toast のみ。
 */
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { RakusukeSays } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type {
  AutoCommittedNotice,
  CrossOfficeNotice,
  RescueSwapNotice,
  ReviewItem,
  StageAssignmentNotice,
  UnresolvedGenderWarning,
} from '@/lib/queries/assign_staff_only';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function fmtWeekday(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

/** "09:00:00" → "09:00" (秒を落とす). null/undefined はダッシュ. */
function fmtTime(t: string | null | undefined): string {
  if (!t) return '—';
  return t.slice(0, 5);
}

/** 性別制限ラベル ('female_only' → '女性のみ' 等). */
function fmtSexRestriction(sr: string | null | undefined): string | null {
  if (!sr) return null;
  if (sr === 'female_only') return '女性のみ';
  if (sr === 'male_only') return '男性のみ';
  return sr;
}

/** 承認された 1 カード (= apply 対象). */
export interface ApprovedReviewItem {
  course_id: string;
  candidate_staff_id: string;
}

export interface AssignWarningDialogProps {
  open: boolean;
  onClose: () => void;
  reviewItems: ReviewItem[];
  /** 承認カードに対し PATCH /courses/{id} を発行する (呼び出し側で実装). */
  onApply: (approved: ApprovedReviewItem[]) => Promise<void> | void;
  /** apply 実行中フラグ (= ボタン無効化). */
  applying?: boolean;
  /**
   * Wave N-2: 体制上不可避な連続 (自動確定済み) のお知らせ.
   * アクションボタンなし。折りたたみで表示。
   */
  notices?: AutoCommittedNotice[];
  /**
   * W-11: 性別制約を満たす候補ゼロで残った違反の警告.
   * 承認/確定ではなくアクション不能の警告 (= 管理者に手動調整を促す)。
   * このダイアログでは approve 対象外・常時表示。
   */
  unresolvedWarnings?: UnresolvedGenderWarning[];
  /**
   * 4段ソルバ Stage 2: マネージャー動員で埋めたコースのお知らせ (確定済み・アクション不要).
   * 折りたたみで表示。同一コースが auto_committed_notices にも載る場合は、
   * auto_committed 側の行に 👔 チップを併記して重複を視覚整理する (片方向)。
   */
  managerMobilizedNotices?: StageAssignmentNotice[];
  /**
   * v2.0 新Stage 3: 拠点をまたぐ救援割当の警告 (確定済み・アクション不要).
   * 警告系トーンで常時表示。同一コースが auto_committed_notices にも載る場合は、
   * auto_committed 側の行に 🚗 チップを併記して重複を視覚整理する (片方向)。
   */
  crossOfficeNotices?: CrossOfficeNotice[];
  /**
   * v2.0 新Stage 3: 拠点跨ぎ救援で発生した割当入れ替えの報告 (確定済み・アクション不要).
   * お知らせトーンで折りたたみ表示。
   */
  rescueSwapNotices?: RescueSwapNotice[];
}

export function AssignWarningDialog({
  open,
  onClose,
  reviewItems,
  onApply,
  applying = false,
  notices = [],
  unresolvedWarnings = [],
  managerMobilizedNotices = [],
  crossOfficeNotices = [],
  rescueSwapNotices = [],
}: AssignWarningDialogProps) {
  // 承認済み course_id 集合 (= チェック / 確認モーダル通過分).
  const [approved, setApproved] = React.useState<Set<string>>(() => new Set());
  // 性別カードの確認モーダル対象 (= 「割り当てる」 を押したカード).
  const [confirmTarget, setConfirmTarget] = React.useState<ReviewItem | null>(null);
  // Wave N-2: お知らせセクションの折りたたみ状態 (既定: 閉).
  const [noticesOpen, setNoticesOpen] = React.useState(false);
  // 4段ソルバ Stage 2 / v2.0 新Stage 3: 各セクションの折りたたみ状態 (既定: 閉).
  const [managerMobilizedOpen, setManagerMobilizedOpen] = React.useState(false);
  const [rescueSwapOpen, setRescueSwapOpen] = React.useState(false);

  // ダイアログ open 時に承認状態をリセットする.
  React.useEffect(() => {
    if (open) {
      setApproved(new Set());
      setConfirmTarget(null);
      setNoticesOpen(false);
      setManagerMobilizedOpen(false);
      setRescueSwapOpen(false);
    }
  }, [open]);

  const genderItems = reviewItems.filter((i) => i.reason === 'gender');
  const consecutiveItems = reviewItems.filter((i) => i.reason === 'consecutive');

  // §4.1 チップ併記: auto_committed_notices と新 Stage 通知の重複を視覚整理するための course_id セット.
  const managerMobilizedIds = new Set(managerMobilizedNotices.map((n) => n.course_id));
  const crossOfficeIds = new Set(crossOfficeNotices.map((n) => n.course_id));

  const toggleApproved = (courseId: string, next: boolean) => {
    setApproved((prev) => {
      const s = new Set(prev);
      if (next) s.add(courseId);
      else s.delete(courseId);
      return s;
    });
  };

  const handleConfirmGender = () => {
    if (confirmTarget) {
      toggleApproved(confirmTarget.course_id, true);
      setConfirmTarget(null);
    }
  };

  // 一斉承認 (連続のみ): 🟡 連続カードを全件まとめてチェックする。
  // 全件承認済みのときはトグルで全件解除に切り替わる。 🔴 性別は対象外。
  const allConsecutiveApproved =
    consecutiveItems.length > 0 && consecutiveItems.every((i) => approved.has(i.course_id));
  const handleToggleAllConsecutive = () => {
    setApproved((prev) => {
      const s = new Set(prev);
      for (const item of consecutiveItems) {
        if (allConsecutiveApproved) s.delete(item.course_id);
        else s.add(item.course_id);
      }
      return s;
    });
  };

  const handleApply = async () => {
    // 修正B: 承認カードの linked partner (= 2 名体制の相方 Y) も自動で apply 対象に
    // 含める (co-select)。 clean な未 commit partner (assigned_staff_id=None) は BE の
    // partner 補完では DB から拾えないため、 FE で review_item の candidate を明示的に
    // 同梱して X+Y 一括 _persist させ、 secondary 対称解決で half-assigned を防ぐ。
    const itemByCourse = new Map(reviewItems.map((i) => [i.course_id, i]));
    const selected = new Map<string, string>();
    for (const item of reviewItems) {
      if (!approved.has(item.course_id)) continue;
      // 承認カード本体.
      if (!selected.has(item.course_id)) selected.set(item.course_id, item.candidate_staff_id);
      // linked partner も candidate 付きで co-select (先勝ち)。
      for (const linkedId of item.linked_course_ids ?? []) {
        const linked = itemByCourse.get(linkedId);
        if (linked && !selected.has(linkedId)) {
          selected.set(linkedId, linked.candidate_staff_id);
        }
      }
    }
    const list: ApprovedReviewItem[] = Array.from(selected, ([course_id, candidate_staff_id]) => ({
      course_id,
      candidate_staff_id,
    }));
    await onApply(list);
  };

  const approvedCount = approved.size;

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
        <DialogContent
          className="max-h-[88vh] max-w-3xl overflow-y-auto"
          data-testid="assign-warning-dialog"
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <span aria-hidden>📋</span>
              自動スタッフ割当のレビュー
            </DialogTitle>
            {reviewItems.length === 0 ? (
              // W-11: review が 0 件でも notices / 残留違反 / Stage 通知があるときは実態を反映する
              // (= 「管理者の判断が必要なコースはありません」と誤誘導しない)。
              <DialogDescription>
                {notices.length > 0
                  ? `体制上避けられない連続が ${notices.length} 件あり、理由つきで確定済みです。`
                  : null}
                {unresolvedWarnings.length > 0
                  ? `性別制約を満たすスタッフが見つからない残留が ${unresolvedWarnings.length} 件あります。理由をご確認のうえ手動で調整してください。`
                  : null}
                {managerMobilizedNotices.length > 0
                  ? `マネージャー動員が ${managerMobilizedNotices.length} 件あり、確定済みです。`
                  : null}
                {crossOfficeNotices.length > 0
                  ? `拠点をまたぐ応援が ${crossOfficeNotices.length} 件あり、確定済みです。`
                  : null}
                {rescueSwapNotices.length > 0
                  ? `応援による入れ替えが ${rescueSwapNotices.length} 件あります。`
                  : null}
                {notices.length === 0 &&
                unresolvedWarnings.length === 0 &&
                managerMobilizedNotices.length === 0 &&
                crossOfficeNotices.length === 0 &&
                rescueSwapNotices.length === 0
                  ? 'レビュー対象はありません。'
                  : null}
              </DialogDescription>
            ) : (
              <DialogDescription>
                問題のないコースは自動で確定しました。 以下のコースは管理者の判断が必要です。
                内容を確認し、 割り当てるコースを選んで「選んだ内容で割り当て」 を押してください。
              </DialogDescription>
            )}
          </DialogHeader>

          {/* R-10: らく助アドバイザー (docs/plans/rakusuke-advisor-ux-design.md) */}
          <RakusukeSays
            pose={
              reviewItems.length === 0 &&
              unresolvedWarnings.length === 0 &&
              managerMobilizedNotices.length === 0 &&
              crossOfficeNotices.length === 0 &&
              rescueSwapNotices.length === 0
                ? 'cheer'
                : 'clap'
            }
            message={
              reviewItems.length === 0 &&
              unresolvedWarnings.length === 0 &&
              managerMobilizedNotices.length === 0 &&
              crossOfficeNotices.length === 0 &&
              rescueSwapNotices.length === 0
                ? 'スタッフの割当ができました！このまま確定して大丈夫です✨'
                : `スタッフの割当ができました。${reviewItems.length > 0 ? `${reviewItems.length}件だけ一緒に確認させてください` : '残った気になる点を確認してください'}`
            }
          />

          <div className="space-y-5 py-1">
            {/* 🔴 性別セクション */}
            {genderItems.length > 0 ? (
              <section data-testid="assign-review-gender-section">
                <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🔴</span>
                  性別 ({genderItems.length} 件) — 確認のうえ割り当て
                </h3>
                <ul className="space-y-2">
                  {genderItems.map((item) => (
                    <ReviewCard
                      key={item.course_id}
                      item={item}
                      approved={approved.has(item.course_id)}
                      onApproveGender={() => setConfirmTarget(item)}
                      onUnapprove={() => toggleApproved(item.course_id, false)}
                    />
                  ))}
                </ul>
              </section>
            ) : null}

            {/* 🟡 連続セクション */}
            {consecutiveItems.length > 0 ? (
              <section data-testid="assign-review-consecutive-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🟡</span>
                  連続 ({consecutiveItems.length} 件) — チェックで割り当て
                  {/* 一斉承認: 連続カードを全件まとめてチェック (全件承認済みならトグルで解除). */}
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="ml-auto"
                    onClick={handleToggleAllConsecutive}
                    data-testid="assign-review-consecutive-approve-all"
                  >
                    {allConsecutiveApproved ? '一斉承認を解除' : '一斉承認'}
                  </Button>
                </h3>
                <ul className="space-y-2">
                  {consecutiveItems.map((item) => (
                    <ReviewCard
                      key={item.course_id}
                      item={item}
                      approved={approved.has(item.course_id)}
                      onToggleConsecutive={(next) => toggleApproved(item.course_id, next)}
                    />
                  ))}
                </ul>
              </section>
            ) : null}

            {/* 🔵 お知らせセクション (体制上不可避な連続・確定済み) */}
            {notices.length > 0 ? (
              <section data-testid="assign-notice-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🔵</span>
                  体制上避けられない連続（{notices.length} 件・確定済み）
                  <button
                    type="button"
                    className="ml-auto text-xs font-normal text-text-secondary hover:text-text-primary"
                    onClick={() => setNoticesOpen((o) => !o)}
                  >
                    {noticesOpen ? '隠す ▲' : '理由を見る ▼'}
                  </button>
                </h3>
                {noticesOpen ? (
                  <ul className="space-y-1">
                    {notices.map((n, i) => (
                      <li
                        key={`${n.course_id}-${i}`}
                        className="flex flex-wrap items-center gap-1 rounded border border-border-default bg-bg-base px-2 py-1 text-xs text-text-secondary"
                        data-testid="assign-notice-row"
                      >
                        <span>
                          {n.office_name || '—'} / {n.course_code} / {fmtWeekday(n.weekday)}
                        </span>
                        <span className="text-text-muted">|</span>
                        <span className="font-medium text-text-primary">{n.staff_name}</span>
                        <span>→</span>
                        <span>{n.cause_patient_names.join('・')}</span>
                        <span className="text-text-muted">|</span>
                        <span>{n.reason_text}</span>
                        {/* §4.1 チップ併記: 同一コースが Stage 通知にも掲載されている場合 */}
                        {managerMobilizedIds.has(n.course_id) ? (
                          <Badge
                            variant="outline"
                            className="text-[10px]"
                            data-testid="chip-manager-mobilized"
                          >
                            👔マネージャー動員
                          </Badge>
                        ) : null}
                        {crossOfficeIds.has(n.course_id) ? (
                          <Badge
                            variant="outline"
                            className="text-[10px]"
                            data-testid="chip-cross-office"
                          >
                            🚗拠点またぎ
                          </Badge>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            {/* 👔 マネージャー動員セクション (4段ソルバ Stage 2・確定済み) */}
            {managerMobilizedNotices.length > 0 ? (
              <section data-testid="assign-manager-mobilized-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>👔</span>
                  マネージャー動員（{managerMobilizedNotices.length} 件・確定済み）
                  <button
                    type="button"
                    className="ml-auto text-xs font-normal text-text-secondary hover:text-text-primary"
                    onClick={() => setManagerMobilizedOpen((o) => !o)}
                  >
                    {managerMobilizedOpen ? '隠す ▲' : '詳細を見る ▼'}
                  </button>
                </h3>
                <p className="mb-1 text-xs text-text-secondary">
                  スタッフ不足のため、以下のコースにマネージャーを割り当てました
                </p>
                {managerMobilizedOpen ? (
                  <ul className="space-y-1">
                    {managerMobilizedNotices.map((n, i) => (
                      <li
                        key={`${n.course_id}-${i}`}
                        className="flex flex-wrap items-center gap-1 rounded border border-border-default bg-bg-base px-2 py-1 text-xs text-text-secondary"
                        data-testid="assign-manager-mobilized-row"
                      >
                        <span>
                          {fmtWeekday(n.weekday)} / {n.course_code}
                        </span>
                        <span className="text-text-muted">|</span>
                        <span className="font-medium text-text-primary">{n.staff_name}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            {/* 🚗 拠点をまたぐ応援セクション (v2.0 新Stage 3・確定済み・警告系トーン) */}
            {crossOfficeNotices.length > 0 ? (
              <section data-testid="assign-cross-office-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🚗</span>
                  拠点をまたぐ応援（{crossOfficeNotices.length} 件・確定済み）
                </h3>
                <ul className="space-y-1">
                  {crossOfficeNotices.map((n, i) => (
                    <li
                      key={`${n.course_id}-${i}`}
                      className="flex flex-wrap items-center gap-1 rounded border border-warning/40 bg-warning/5 px-2 py-1 text-xs text-text-secondary"
                      data-testid="assign-cross-office-row"
                    >
                      <span>
                        {fmtWeekday(n.weekday)} / {n.course_code}
                      </span>
                      <span className="text-text-muted">|</span>
                      <span className="font-medium text-text-primary">
                        {n.staff_name}（{n.staff_office_name} → {n.course_office_name}）
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {/* 🔄 応援による入れ替えセクション (v2.0 新Stage 3・お知らせトーン・折りたたみ) */}
            {rescueSwapNotices.length > 0 ? (
              <section data-testid="assign-rescue-swap-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🔄</span>
                  応援による入れ替え（{rescueSwapNotices.length} 件）
                  <button
                    type="button"
                    className="ml-auto text-xs font-normal text-text-secondary hover:text-text-primary"
                    onClick={() => setRescueSwapOpen((o) => !o)}
                  >
                    {rescueSwapOpen ? '隠す ▲' : '詳細を見る ▼'}
                  </button>
                </h3>
                {rescueSwapOpen ? (
                  <ul className="space-y-1">
                    {rescueSwapNotices.map((n, i) => (
                      <li
                        key={`${n.course_id}-${i}`}
                        className="flex flex-wrap items-center gap-1 rounded border border-border-default bg-bg-base px-2 py-1 text-xs text-text-secondary"
                        data-testid="assign-rescue-swap-row"
                      >
                        <span>
                          {fmtWeekday(n.weekday)} / {n.course_code}
                        </span>
                        <span className="text-text-muted">|</span>
                        <span className="font-medium text-text-primary">{n.before_staff_name}</span>
                        <span>→</span>
                        <span className="font-medium text-text-primary">{n.after_staff_name}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            {/* 🟧 残留違反セクション (W-11: 性別候補ゼロ・手動調整が必要・承認不可) */}
            {unresolvedWarnings.length > 0 ? (
              <section data-testid="assign-unresolved-section">
                <h3 className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                  <span aria-hidden>🟧</span>
                  性別制約を満たせない残留（{unresolvedWarnings.length} 件・要手動調整）
                </h3>
                <ul className="space-y-1">
                  {unresolvedWarnings.map((w, i) => (
                    <li
                      key={`${w.course_id}-${i}`}
                      className="flex flex-wrap items-center gap-1 rounded border border-warning/40 bg-warning/5 px-2 py-1 text-xs text-text-secondary"
                      data-testid="assign-unresolved-row"
                    >
                      <span>
                        {w.office_name || '—'} / {w.course_code} / {fmtWeekday(w.weekday)}
                      </span>
                      <span className="text-text-muted">|</span>
                      <span className="font-medium text-text-primary">{w.current_staff_name}</span>
                      <span className="text-text-muted">|</span>
                      <span>{w.reason_text}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {reviewItems.length === 0 &&
            notices.length === 0 &&
            unresolvedWarnings.length === 0 &&
            managerMobilizedNotices.length === 0 &&
            crossOfficeNotices.length === 0 &&
            rescueSwapNotices.length === 0 ? (
              <div className="py-4 text-center text-xs text-text-muted">
                レビュー対象はありません。
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={applying}
              data-testid="assign-review-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              onClick={handleApply}
              disabled={applying || approvedCount === 0}
              data-testid="assign-review-apply"
            >
              選んだ内容で割り当て{approvedCount > 0 ? ` (${approvedCount})` : ''}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 🔴 性別カードの確認モーダル (1 回). */}
      <Dialog
        open={confirmTarget !== null}
        onOpenChange={(o) => (!o ? setConfirmTarget(null) : undefined)}
      >
        <DialogContent className="max-w-md" data-testid="assign-review-gender-confirm">
          <DialogHeader>
            <DialogTitle>本当に割り当てますか？</DialogTitle>
            <DialogDescription>
              {confirmTarget ? (
                <>
                  性別制限のあるコース「{confirmTarget.course_code} /{' '}
                  {fmtWeekday(confirmTarget.weekday)}」 に{' '}
                  <span className="font-medium text-text-primary">
                    {confirmTarget.candidate_staff_name}
                  </span>{' '}
                  を割り当てます。 適合する性別のスタッフが居ないため、 管理者の判断で割り当てます。
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmTarget(null)}
              data-testid="assign-review-gender-confirm-cancel"
            >
              やめる
            </Button>
            <Button
              type="button"
              onClick={handleConfirmGender}
              data-testid="assign-review-gender-confirm-ok"
            >
              割り当てる
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface ReviewCardProps {
  item: ReviewItem;
  approved: boolean;
  /** 🟡 連続: チェックボックス切替. */
  onToggleConsecutive?: (next: boolean) => void;
  /** 🔴 性別: 「割り当てる」 ボタン (= 確認モーダルを開く). */
  onApproveGender?: () => void;
  /** 🔴 性別: 承認解除 (= 「割り当てる」 後に取り消す). */
  onUnapprove?: () => void;
}

function ReviewCard({
  item,
  approved,
  onToggleConsecutive,
  onApproveGender,
  onUnapprove,
}: ReviewCardProps) {
  const isGender = item.reason === 'gender';
  return (
    <li
      className={cn(
        'rounded-md border p-3 text-xs',
        isGender ? 'border-error/40 bg-error/5' : 'border-warning/40 bg-warning/5',
        approved && 'ring-2 ring-brand-primary',
      )}
      data-testid="assign-review-card"
      data-reason={item.reason}
      data-approved={approved ? 'true' : 'false'}
    >
      {/* ヘッダ行: 拠点 / コード / 曜日 + 候補スタッフ (右上). */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant={isGender ? 'destructive' : 'warning'} className="text-[10px]">
          {isGender ? '性別' : '連続'}
        </Badge>
        <span className="font-medium text-text-primary">{item.course_code}</span>
        <span className="text-text-muted">{fmtWeekday(item.weekday)}</span>
        {item.office_name ? <span className="text-text-muted">{item.office_name}</span> : null}
        <span className="ml-auto text-text-secondary">
          担当（候補）:{' '}
          <span className="font-medium text-text-primary">{item.candidate_staff_name}</span>
        </span>
      </div>

      {/* visit 一覧 (原因患者をマーク). */}
      <ul className="space-y-1">
        {item.visits.map((v) => {
          const sr = fmtSexRestriction(v.sex_restriction);
          return (
            <li
              key={v.patient_id}
              className={cn(
                'flex flex-wrap items-center gap-2 rounded border border-border-default bg-bg-base px-2 py-1',
                v.is_cause && 'border-l-4',
                v.is_cause && (isGender ? 'border-l-error' : 'border-l-warning'),
              )}
              data-testid="assign-review-visit"
              data-cause={v.is_cause ? 'true' : 'false'}
            >
              <span className="text-text-muted">{fmtTime(v.start_time)}</span>
              <span className="font-medium text-text-primary">{v.patient_name}</span>
              {sr ? (
                <Badge variant="outline" className="text-[10px]">
                  {sr}
                </Badge>
              ) : null}
              {v.is_cause ? (
                <Badge
                  variant={isGender ? 'destructive' : 'warning'}
                  className="ml-auto text-[10px]"
                >
                  {isGender ? '性別NG' : '連続'}
                </Badge>
              ) : null}
            </li>
          );
        })}
      </ul>

      {/* アクション行. */}
      <div className="mt-2 flex items-center justify-end gap-2">
        {isGender ? (
          approved ? (
            <div className="flex items-center gap-2">
              <Badge variant="success" className="text-[10px]">
                割り当て予定
              </Badge>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onUnapprove}
                data-testid="assign-review-gender-unapprove"
              >
                取り消す
              </Button>
            </div>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={onApproveGender}
              data-testid="assign-review-gender-approve"
            >
              割り当てる
            </Button>
          )
        ) : (
          <label className="flex cursor-pointer items-center gap-2 text-text-secondary">
            <Checkbox
              checked={approved}
              onCheckedChange={(c) => onToggleConsecutive?.(c === true)}
              data-testid="assign-review-consecutive-checkbox"
            />
            このコースを割り当てる
          </label>
        )}
      </div>
    </li>
  );
}
