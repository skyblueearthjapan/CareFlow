'use client';

/**
 * PlacementConfirmDialog — 「配置の確認」モーダル。
 *
 * 正典: `docs/plans/dnd-all-views-design-2026-09-08.md` §2-2 (Phase 1)。
 * サイズ・文字の基準: `docs/plans/add-visit-anywhere-design.md` §3-5
 * (幅 `max-w-2xl` / 本文 14px / 入力 `h-9` / 注記のみ 12px)。
 *
 * PO 指示 (2026-09-08): ⭐/プールカードは**どこでも掴める**ようにし、代わりに
 * **置く瞬間**に案内と警告を出す。時間軸のないビュー (職員スケジュール・週リスト) は
 * 時刻が決まらないので必ずここを通り、⭐ を別曜日へ落としたときは
 * 「これは◯曜日の予定ですが…」と問い直す。曜日ゲートを外した以上、
 * **このモーダルが唯一の砦** なので、閉じたら place は絶対に飛ばさない。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

import { TIME_OPTIONS } from './cockpit/VisitActionMenu';
import { specialTicketWeekdayLabel } from './courseDnd';

/** 営業時間 (盤面の TL_DAY_START_MIN / TL_DAY_END_MIN と同じ 9:00〜18:00)。 */
const DAY_START_MIN = 9 * 60;
const DAY_END_MIN = 18 * 60;

/** 開始時刻の候補 (設計 §2-2: 9:00〜18:00)。`TIME_OPTIONS` は 8:00〜18:45。 */
const START_TIME_OPTIONS = TIME_OPTIONS.filter((t) => t >= '09:00' && t <= '18:00');

/** "HH:MM:SS" / "HH:MM" → "HH:MM"。空値は null。 */
function toHM(v: string | null | undefined): string | null {
  if (!v) return null;
  const m = /^(\d{2}):(\d{2})/.exec(v);
  return m ? `${m[1]}:${m[2]}` : null;
}

/** "HH:MM" → 通算分。壊れた値は null。 */
function toMin(hm: string): number | null {
  const m = /^(\d{2}):(\d{2})$/.exec(hm);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** 配置するカードの正体 (⭐ チケット / プール患者)。 */
export interface PlacementSubject {
  kind: 'pool' | 'special';
  patientName: string;
  patientId: string;
  /** ⭐ のみ: mark id。 */
  markId?: string;
  /** ⭐ のみ: チケットの曜日 (0=月)。target と違うときだけ警告を出す。 */
  ticketWeekday?: number | null;
  /** 枠の長さ (分)。表示のみ (実際の所要は呼び出し側が決める)。 */
  serviceMinutes: number;
  /** 2 名体制の患者か (表示 + 呼び出し側の分岐用)。 */
  requiresMultipleStaff: boolean;
}

/** 配置先のコース候補 1 件。 */
export interface PlacementCourseOption {
  templateId: string;
  /** 例: "A" / "M（担当なし枠）"。 */
  label: string;
  /** 拠点名 (跨ぎ候補は呼び出し側で除外済み。表示のみ)。 */
  officeName: string;
}

/** ドロップ先 (曜日 + 行スタッフ + コース候補)。 */
export interface PlacementTarget {
  weekday: number;
  /** 職員スケジュールの行スタッフ名。「（担当なし）」行・時間軸ビューは null。 */
  staffName: string | null;
  courseOptions: PlacementCourseOption[];
  defaultTemplateId: string | null;
  /**
   * その職員はこの曜日にコースを持つが、**別拠点**なので候補から外した
   * (設計 §2-2「拠点跨ぎは候補から除外」)。理由を隠すと「なぜ M なのか」が
   * 分からなくなるので、その旨をモーダルに出す。
   */
  crossOfficeExcluded?: boolean;
}

/**
 * 開始時刻の既定値の**候補**。優先順は設計 §2-2:
 *   ⭐ `last_placement.start_time` → 患者の希望開始 → 09:00。
 * どれが採用されたかはモーダルが小さな注記で見せる (当てずっぽうにしない)。
 */
export interface PlacementDefaultStart {
  /** ⭐ の前回配置時刻 ("HH:MM" / "HH:MM:SS")。 */
  lastPlacement?: string | null;
  /** 患者の希望開始 (`weekly_pattern.preferred_start`)。 */
  preferred?: string | null;
}

export interface PlacementConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  subject: PlacementSubject;
  target: PlacementTarget;
  defaultStart: PlacementDefaultStart;
  onConfirm: (args: { courseTemplateId: string; startHM: string }) => void;
}

/**
 * 既定の開始時刻と、その出所ラベルを解く。
 * 候補 (⭐ 前回配置 → 患者の希望開始) が 9:00〜18:00 の枠に収まらないときは
 * **候補の中の最も近い時刻へ丸める** (枠外の時刻を差し込むと、そのまま「配置する」を
 * 押されて BE 422 / 盤面ガードに弾かれるため。設計 §2-2 の 9:00〜18:00 を守る)。
 */
export function resolveDefaultStart(
  d: PlacementDefaultStart,
  options: readonly string[],
): { time: string; source: string; clamped: boolean } {
  const pick = (): { time: string; source: string } | null => {
    const last = toHM(d.lastPlacement);
    if (last) return { time: last, source: '前回の配置時刻' };
    const preferred = toHM(d.preferred);
    if (preferred) return { time: preferred, source: '患者の希望開始' };
    return null;
  };
  const fallback = options.includes('09:00') ? '09:00' : (options[0] ?? '09:00');
  const candidate = pick();
  if (!candidate) return { time: fallback, source: '既定 (9:00)', clamped: false };
  if (options.includes(candidate.time)) return { ...candidate, clamped: false };
  const want = toMin(candidate.time);
  if (want === null || options.length === 0) {
    return { time: fallback, source: candidate.source, clamped: true };
  }
  // 一番近い候補へ丸める (08:30 → 09:00 / 17:45(60分) → 17:00)。
  let best = options[0]!;
  let bestDiff = Number.POSITIVE_INFINITY;
  for (const o of options) {
    const m = toMin(o);
    if (m === null) continue;
    const diff = Math.abs(m - want);
    if (diff < bestDiff) {
      best = o;
      bestDiff = diff;
    }
  }
  return { time: best, source: candidate.source, clamped: true };
}

export function PlacementConfirmDialog({
  open,
  onOpenChange,
  subject,
  target,
  defaultStart,
  onConfirm,
}: PlacementConfirmDialogProps) {
  const options = target.courseOptions;
  // 既定の丸め先は「枠に収まる開始時刻」だけ (18:00 に 60 分は入らない)。
  // 選択肢そのものは 9:00〜18:00 のまま出し、はみ出す組み合わせは盤面のガードが
  // 「9:00〜18:00 の範囲に…」で弾く (最終判定は盤面が単一ソース)。
  const fittingOptions = React.useMemo(
    () =>
      START_TIME_OPTIONS.filter((t) => {
        const m = toMin(t);
        return m !== null && m >= DAY_START_MIN && m + subject.serviceMinutes <= DAY_END_MIN;
      }),
    [subject.serviceMinutes],
  );
  const initial = React.useMemo(
    () => resolveDefaultStart(defaultStart, fittingOptions),
    [defaultStart, fittingOptions],
  );
  const [templateId, setTemplateId] = React.useState<string>('');
  const [startHM, setStartHM] = React.useState<string>(initial.time);

  // 開き直すたびに既定へ戻す (前のドロップの選択が残らないように)。
  React.useEffect(() => {
    if (!open) return;
    setStartHM(initial.time);
    setTemplateId(target.defaultTemplateId ?? (options.length === 1 ? options[0]!.templateId : ''));
    // options/target は開いた時点の値で十分 (開いている間は親が差し替えない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const wdLabel = (wd: number) => specialTicketWeekdayLabel(wd);
  const targetLabel = wdLabel(target.weekday);
  const moved =
    subject.ticketWeekday != null && subject.ticketWeekday !== target.weekday
      ? { from: wdLabel(subject.ticketWeekday) }
      : null;

  // 候補が 1 件だけならセレクトを出さずテキストで見せる (選ぶ余地がない)。
  const singleOption = options.length === 1 ? options[0]! : null;
  const canConfirm = templateId !== '' && startHM !== '';

  // 選択肢は 9:00〜18:00 で固定 (枠からはみ出す選択は盤面のガードが警告する)。
  const timeOptions = START_TIME_OPTIONS;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-2xl"
        aria-describedby="placement-confirm-desc"
        data-testid="pcd-root"
      >
        <DialogHeader>
          <DialogTitle>配置の確認</DialogTitle>
          <DialogDescription id="placement-confirm-desc" className="text-sm">
            コースと開始時刻を確かめてから配置します。この配置は「この週のみ」で、毎週の型は
            変更しません。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-1 text-sm">
          <div data-testid="pcd-patient">
            {subject.kind === 'special' ? (
              <span className="mr-1" aria-hidden>
                ⭐
              </span>
            ) : null}
            <span className="font-semibold text-text-primary">{subject.patientName} 様</span>
            <span className="ml-2 text-xs text-text-muted">
              {subject.kind === 'special' ? '特別訪問週間の追加枠' : '保留プール'} ・
              {subject.serviceMinutes}分{subject.requiresMultipleStaff ? ' ・2名体制' : ''}
            </span>
          </div>

          <div data-testid="pcd-target">
            配置先:
            <span className="ml-1 font-semibold text-text-primary">{targetLabel}曜</span>
            {target.staffName ? (
              <span className="ml-1 font-semibold text-text-primary">／{target.staffName}</span>
            ) : (
              <span className="ml-1 text-text-muted">／（担当なし）</span>
            )}
          </div>

          {moved ? (
            <div
              className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning-strong"
              role="alert"
              data-testid="pcd-warning"
            >
              これは{moved.from}曜日の予定ですが、{targetLabel}
              曜日に配置して本当によろしいですか？（追加枠 ○ も{targetLabel}曜日へ移ります）
            </div>
          ) : null}

          {target.crossOfficeExcluded ? (
            <div
              className="rounded border border-border-default bg-bg-muted px-3 py-2 text-sm text-text-secondary"
              data-testid="pcd-cross-office"
            >
              この職員の{targetLabel}曜のコースは別拠点のため候補外です。担当なし枠(M)に入ります
            </div>
          ) : null}

          {options.length === 0 ? (
            <div
              className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning-strong"
              role="alert"
              data-testid="pcd-course-none"
            >
              受け皿になるコース（M）が見つかりません
            </div>
          ) : singleOption ? (
            <div data-testid="pcd-course-text">
              コース:
              <span className="ml-1 font-semibold text-text-primary">
                {singleOption.officeName ? `${singleOption.officeName} ` : ''}
                {singleOption.label}
              </span>
            </div>
          ) : (
            <label className="flex flex-col gap-1">
              <span className="font-semibold text-text-primary">コース</span>
              <select
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                className="h-9 rounded border border-border-default bg-bg-base px-2 text-sm"
                data-testid="pcd-course-select"
                aria-label="コース"
              >
                <option value="">— 選択してください —</option>
                {options.map((o) => (
                  <option key={o.templateId} value={o.templateId}>
                    {o.officeName ? `${o.officeName} ` : ''}
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label className="flex flex-col gap-1">
            <span className="font-semibold text-text-primary">開始時刻</span>
            <select
              value={startHM}
              onChange={(e) => setStartHM(e.target.value)}
              className="h-9 w-40 rounded border border-border-default bg-bg-base px-2 text-sm"
              data-testid="pcd-time-select"
              aria-label="開始時刻"
            >
              {timeOptions.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <span className="text-xs text-text-muted" data-testid="pcd-time-source">
              既定: {initial.source}
              {initial.clamped ? `（9:00〜18:00 に収まる ${initial.time} に寄せました）` : ''}
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="pcd-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            disabled={!canConfirm}
            onClick={() => {
              if (!canConfirm) return;
              onConfirm({ courseTemplateId: templateId, startHM });
            }}
            data-testid="pcd-confirm"
          >
            配置する
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// PlacementConflictConfirmDialog — 曜日移動が衝突したときの二段目の確認
// ---------------------------------------------------------------------------

export interface PlacementConflictConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 移動先の曜日ラベル (例: "木")。 */
  weekdayLabel: string;
  onConfirm: () => void;
}

/**
 * 「{△}曜には既に追加枠（○）があります。そちらを配置しますか？」
 * (`dnd-all-views-design-2026-09-08.md` §2-3 の 409 経路)。
 * `window.confirm` は使わない (SpecialVisitWeekDialog の ConfirmDialog と同じ作法)。
 * **開いているときだけマウントする** = テストの Dialog モックが open を見なくても揃う。
 */
export function PlacementConflictConfirmDialog({
  open,
  onOpenChange,
  weekdayLabel,
  onConfirm,
}: PlacementConflictConfirmDialogProps) {
  if (!open) return null;
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" data-testid="pcd-conflict">
        <DialogHeader>
          <DialogTitle>すでに追加枠があります</DialogTitle>
          <DialogDescription>
            {weekdayLabel}曜には既に追加枠（○）があります。そちらを配置しますか？
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="pcd-conflict-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
            data-testid="pcd-conflict-ok"
          >
            そちらを配置する
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
