'use client';

/**
 * StaffSubstituteDialog — P3-① 当日欠勤の代替スタッフ提案 (FE).
 *
 * 設計書: docs/plans/p3-1-staff-substitute-design.md
 *
 * フロー (3 段):
 *   Step1: 欠勤スタッフ + 対象日 (既定=今日) を選び「検索」.
 *   Step2: 欠勤スタッフの当日 planned visit 一覧。visit ごとに候補ラジオを選ぶ
 *          (部分選択可 / 候補ゼロは理由内訳を表示).
 *   Step3: 選択済みのみ「適用」→ 確認 (□この日を休みとして登録) → POST.
 *
 * 恒久データ (Course / PFV) は触らない。当該日の VSA 差替えのみ。
 */
import * as React from 'react';
import { Loader2, Search, UserX, Users, X } from 'lucide-react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { Badge } from '@/components/ui/badge';
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
import { useStaffList } from '@/lib/queries/staff';
import {
  useApplySubstitutions,
  useSubstituteCandidates,
} from '@/lib/queries/staffSubstitute';
import {
  noCandidateReasonLabel,
  type SubstituteScoreBreakdown,
  type SubstituteVisitCandidates,
} from '@/lib/schemas/v2/staffSubstitute';

import { formatErr, toastApplyWarnings, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/** ローカルタイムの今日を YYYY-MM-DD で返す。 */
function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** スコア内訳を「継続◎ / 移動+12分 / 当日3件」の短い日本語に整形。 */
export function formatScoreBreakdown(b: SubstituteScoreBreakdown): string {
  const parts: string[] = [];
  if (b.continuity_bonus > 0) parts.push('継続◎');
  const addMin = Math.round(-b.travel_penalty);
  if (addMin > 0) parts.push(`移動+${addMin}分`);
  const load = Math.round(-b.load_penalty / 5);
  if (load > 0) parts.push(`当日${load}件`);
  return parts.length > 0 ? parts.join(' / ') : '—';
}

/** ApiError body から FastAPI detail 文言を取り出す (str / [{msg}] の両形)。 */
function apiDetail(err: unknown): string | undefined {
  if (!(err instanceof ApiError)) return undefined;
  const body = err.body;
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d
        .map((x) =>
          x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : '',
        )
        .filter(Boolean)
        .join(' / ');
    }
  }
  return undefined;
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface StaffSubstituteDialogProps {
  open: boolean;
  onClose: () => void;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function StaffSubstituteDialog({ open, onClose }: StaffSubstituteDialogProps) {
  const staffListQuery = useStaffList({ limit: 200 });
  const allStaff = React.useMemo(() => staffListQuery.data ?? [], [staffListQuery.data]);
  const activeStaff = React.useMemo(
    () =>
      allStaff
        .filter((s) => s.status !== 'retired')
        .slice()
        .sort((a, b) => (a.kana ?? a.name).localeCompare(b.kana ?? b.name, 'ja')),
    [allStaff],
  );

  // Step1 フォーム state.
  const [absentStaffId, setAbsentStaffId] = React.useState<string>('');
  const [targetDate, setTargetDate] = React.useState<string>(() => todayIso());
  // 「検索」で確定した検索条件 (これが変わると query が走る)。
  const [committed, setCommitted] = React.useState<{ staffId: string; date: string } | null>(null);
  // visit_id → 選択された substitute_staff_id.
  const [selections, setSelections] = React.useState<Record<string, string>>({});
  // Step3 確認パネル表示中か。
  const [confirming, setConfirming] = React.useState(false);
  const [registerAbsence, setRegisterAbsence] = React.useState(false);

  const candidatesQuery = useSubstituteCandidates(
    committed?.staffId,
    committed?.date,
    committed != null,
  );
  const applyMut = useApplySubstitutions();

  // open のたびにリセット。
  React.useEffect(() => {
    if (!open) return;
    setAbsentStaffId('');
    setTargetDate(todayIso());
    setCommitted(null);
    setSelections({});
    setConfirming(false);
    setRegisterAbsence(false);
  }, [open]);

  const isPastDate = targetDate < todayIso();
  const isBusy = candidatesQuery.isFetching || applyMut.isPending;

  const handleClose = () => {
    if (applyMut.isPending) return;
    onClose();
  };

  const handleSearch = () => {
    if (!absentStaffId) {
      toast.info('欠勤スタッフを選択してください');
      return;
    }
    if (!targetDate) {
      toast.info('対象日を選択してください');
      return;
    }
    setSelections({});
    setConfirming(false);
    setCommitted({ staffId: absentStaffId, date: targetDate });
  };

  const handleSelectCandidate = (visitId: string, staffId: string) => {
    setSelections((prev) => {
      // 同じ候補を再クリックしたら選択解除 (部分選択の取り消し)。
      if (prev[visitId] === staffId) {
        const next = { ...prev };
        delete next[visitId];
        return next;
      }
      return { ...prev, [visitId]: staffId };
    });
  };

  const selectedCount = Object.keys(selections).length;

  const handleRequestApply = () => {
    if (selectedCount === 0) {
      toast.info('差し替える候補を 1 件以上選択してください');
      return;
    }
    setConfirming(true);
  };

  const handleConfirmApply = async () => {
    if (!committed || selectedCount === 0) return;
    try {
      const res = await applyMut.mutateAsync({
        absent_staff_id: committed.staffId,
        target_date: committed.date,
        substitutions: Object.entries(selections).map(([visit_id, substitute_staff_id]) => ({
          visit_id,
          substitute_staff_id,
        })),
        register_absence: registerAbsence,
      });
      toast.success(`${res.applied.length} 件の訪問を差し替えました`);
      toastApplyWarnings(res.warnings);
      onClose();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : undefined;
      const detail = apiDetail(err);
      if (status === 409) {
        toast.error(detail ?? '対象の訪問が既に別の状態に変わっています。再検索してください');
      } else if (status === 422) {
        toast.error(detail ?? '制約に抵触するため適用できませんでした');
      } else {
        toast.error(`差し替えに失敗しました: ${formatErr(err)}`);
      }
      // 確認パネルを閉じて一覧に戻す (再検索を促す)。
      setConfirming(false);
    }
  };

  const data = candidatesQuery.data;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? handleClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] max-w-3xl overflow-y-auto"
        data-testid="staff-substitute-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UserX className="h-5 w-5 text-brand-primary" aria-hidden />
            欠勤対応（代替スタッフ提案）
          </DialogTitle>
          <DialogDescription>
            欠勤スタッフと対象日を選んで検索すると、当日の訪問ごとに代われる候補を表示します。
            選んだ訪問だけをまとめて差し替えます（当日のみ・固定枠は変更しません）。
          </DialogDescription>
        </DialogHeader>

        {/* Step1: 欠勤スタッフ + 対象日 + 検索 */}
        <div className="flex flex-col gap-3 rounded-md border border-border-default bg-bg-muted p-3 sm:flex-row sm:items-end">
          <label className="flex flex-1 flex-col gap-1 text-xs text-text-secondary">
            欠勤スタッフ
            <select
              className="h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm text-text-primary"
              value={absentStaffId}
              onChange={(e) => setAbsentStaffId(e.target.value)}
              data-testid="staff-substitute-absent-select"
              disabled={isBusy}
            >
              <option value="">選択してください</option>
              {activeStaff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-secondary">
            対象日
            <input
              type="date"
              className="h-9 rounded-md border border-border-default bg-bg-base px-2 text-sm tabular-nums text-text-primary"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              data-testid="staff-substitute-date-input"
              disabled={isBusy}
            />
          </label>
          <Button
            type="button"
            onClick={handleSearch}
            disabled={isBusy || !absentStaffId}
            data-testid="staff-substitute-search-button"
          >
            {candidatesQuery.isFetching ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <Search className="mr-1 h-4 w-4" aria-hidden />
            )}
            検索
          </Button>
        </div>

        {isPastDate ? (
          <div
            className="rounded-md border border-warning bg-warning-bg px-3 py-2 text-xs text-warning-strong"
            data-testid="staff-substitute-past-warning"
            role="status"
          >
            過去日が選択されています。確認のうえ検索してください。
          </div>
        ) : null}

        {/* Step2: 候補一覧 */}
        {committed ? (
          <div className="space-y-3" data-testid="staff-substitute-results">
            {candidatesQuery.isFetching ? (
              <div className="flex items-center justify-center gap-2 py-8 text-sm text-text-muted">
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
                候補を検索中…
              </div>
            ) : candidatesQuery.isError ? (
              <div
                className="rounded-md border border-error/40 bg-error/5 px-3 py-2 text-sm text-error"
                data-testid="staff-substitute-error"
              >
                候補の取得に失敗しました: {formatErr(candidatesQuery.error)}
              </div>
            ) : data && data.visits.length === 0 ? (
              <div
                className="py-8 text-center text-sm text-text-muted"
                data-testid="staff-substitute-empty"
              >
                {data.absent_staff_name} さんの当日対象訪問はありません。
              </div>
            ) : data ? (
              <>
                <div className="text-xs text-text-secondary">
                  <span className="font-semibold text-text-primary">{data.absent_staff_name}</span>{' '}
                  さんの {data.target_date} の訪問 {data.visits.length} 件
                </div>
                <ul className="space-y-2">
                  {data.visits.map((visit) => (
                    <VisitCandidateCard
                      key={visit.visit_id}
                      visit={visit}
                      selectedStaffId={selections[visit.visit_id] ?? null}
                      onSelect={(staffId) => handleSelectCandidate(visit.visit_id, staffId)}
                      disabled={applyMut.isPending}
                    />
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}

        {/* Step3: 確認パネル */}
        {confirming ? (
          <div
            className="mt-2 rounded-lg border border-brand-primary/40 bg-brand-primary/5 p-4"
            data-testid="staff-substitute-confirm-panel"
          >
            <p className="mb-2 text-sm font-semibold text-text-primary">
              {selectedCount} 件を差し替えます。よろしいですか？
            </p>
            <label className="mb-3 flex items-center gap-2 text-sm text-text-primary">
              <Checkbox
                checked={registerAbsence}
                onCheckedChange={(v) => setRegisterAbsence(v === true)}
                data-testid="staff-substitute-register-absence"
                disabled={applyMut.isPending}
              />
              この日を欠勤スタッフの休みとして登録する
            </label>
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={() => setConfirming(false)}
                disabled={applyMut.isPending}
                data-testid="staff-substitute-confirm-cancel"
              >
                <X className="mr-1 h-4 w-4" aria-hidden />
                戻る
              </Button>
              <Button
                type="button"
                onClick={() => {
                  void handleConfirmApply();
                }}
                disabled={applyMut.isPending}
                data-testid="staff-substitute-confirm-apply"
              >
                {applyMut.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Users className="mr-1 h-4 w-4" aria-hidden />
                )}
                差し替える
              </Button>
            </div>
          </div>
        ) : (
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={applyMut.isPending}
              data-testid="staff-substitute-close-button"
            >
              閉じる
            </Button>
            {committed && data && data.visits.length > 0 ? (
              <Button
                type="button"
                onClick={handleRequestApply}
                disabled={isBusy || selectedCount === 0}
                data-testid="staff-substitute-apply-button"
              >
                <Users className="mr-1 h-4 w-4" aria-hidden />
                適用 ({selectedCount} 件)
              </Button>
            ) : null}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// VisitCandidateCard — 1 visit + 候補ラジオリスト
// ─────────────────────────────────────────────────────────────────────────

function VisitCandidateCard({
  visit,
  selectedStaffId,
  onSelect,
  disabled,
}: {
  visit: SubstituteVisitCandidates;
  selectedStaffId: string | null;
  onSelect: (staffId: string) => void;
  disabled: boolean;
}) {
  const groupName = `substitute-visit-${visit.visit_id}`;
  return (
    <li
      className="rounded-md border border-border-default p-3"
      data-testid={`staff-substitute-visit-${visit.visit_id}`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold tabular-nums text-text-primary">
          {trimSeconds(visit.start_time)}–{trimSeconds(visit.end_time)}
        </span>
        <span className="text-sm text-text-primary">{visit.patient_name}</span>
        {visit.required_staff_count === 2 ? (
          <Badge variant="outline" className="text-[10px] text-text-secondary">
            2名体制
          </Badge>
        ) : null}
        {visit.is_secondary ? (
          <Badge variant="outline" className="text-[10px] text-text-secondary">
            secondary側
          </Badge>
        ) : null}
        {visit.sex_restriction ? (
          <Badge variant="outline" className="text-[10px] text-text-secondary">
            {visit.sex_restriction === 'female_only' ? '女性限定' : '男性限定'}
          </Badge>
        ) : null}
      </div>

      {visit.candidates.length > 0 ? (
        <div className="space-y-1">
          {visit.candidates.map((c) => {
            const checked = selectedStaffId === c.staff_id;
            return (
              <label
                key={c.staff_id}
                className={`flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm ${
                  checked ? 'bg-brand-primary/10' : 'hover:bg-bg-muted'
                }`}
                data-testid={`staff-substitute-candidate-${visit.visit_id}-${c.staff_id}`}
                onClick={(e) => {
                  // ラベル経由のクリックも含めて onSelect を呼ぶ (= 解除分岐含む).
                  // input の onClick と重複しないよう preventDefault してラジオの
                  // デフォルト selected-fix を抑制し、ここで一元処理する.
                  e.preventDefault();
                  if (!disabled) onSelect(c.staff_id);
                }}
              >
                <input
                  type="radio"
                  name={groupName}
                  checked={checked}
                  // onChange が無いと React の controlled 警告が出るためダミーを設定.
                  // 実際の変更処理は label onClick に集約している.
                  onChange={() => {}}
                  // onClick は label onClick に委譲しているため不要だが、
                  // キーボード (Enter/Space → click) は label に流れるため追加不要.
                  disabled={disabled}
                  className="accent-brand-primary"
                  aria-label={c.staff_name}
                />
                <span className="text-text-primary">{c.staff_name}</span>
                <span
                  className="text-[11px] tabular-nums text-text-muted"
                  title={`スコア ${c.score.toFixed(1)} (継続 ${c.score_breakdown.continuity_bonus} / 移動 ${c.score_breakdown.travel_penalty} / 負荷 ${c.score_breakdown.load_penalty})`}
                >
                  {formatScoreBreakdown(c.score_breakdown)}
                </span>
              </label>
            );
          })}
        </div>
      ) : (
        <div
          className="flex flex-wrap gap-1 text-[11px] text-text-muted"
          data-testid={`staff-substitute-no-candidates-${visit.visit_id}`}
        >
          <span className="text-text-secondary">候補なし:</span>
          {visit.no_candidate_reasons.length > 0 ? (
            visit.no_candidate_reasons.map((r) => (
              <span
                key={r.code}
                className="rounded border border-border-default px-1.5 py-0.5 tabular-nums"
              >
                {noCandidateReasonLabel(r.code)} {r.count}
              </span>
            ))
          ) : (
            <span>理由情報なし</span>
          )}
        </div>
      )}
    </li>
  );
}
