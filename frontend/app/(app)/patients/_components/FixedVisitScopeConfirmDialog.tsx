/**
 * FixedVisitScopeConfirmDialog — 固定訪問スケジュール保存の「反映先」事前確認.
 *
 * 正典 = `docs/plans/add-visit-anywhere-design.md` §6（Phase E）。
 *
 * 背景（同 §1 欠陥 6）: 固定訪問パターンの保存は無確認で `change_scope='pattern_and_week'`
 * を送り、週文脈が無いときは **今日の ISO 週** を作り直していた。9/14 以降を直したいのに
 * 9/7 週が変わる事故（松岡様の事象）の直接原因。
 *
 * 本ダイアログは保存ボタン押下時（案Z の警告確認より **前**）に必ず出て、
 *   - 何が変わるのか（曜日ごとの 追加 / 変更 / 削除）
 *   - 反映先（型だけ / 型＋対象週も作り直す）
 *   - (B) を選んだときに「消える予定 / 保護される予定 / 作られる予定」の件数
 * を提示する。
 *
 * 既定（PO 決定 8）:
 *   - 週文脈なし（患者マスタ画面）→ (A)「型だけ変える」
 *   - 週文脈あり（盤面の患者詳細）→ (B)「型と表示中の週」
 *
 * 保護の意味論（設計 §2-3）: `week_pinned` / `source='manual_week'` / `source='import'`
 * の訪問は型保存の再生成で消えない。加えて **同じ日付の型スロットの再生成をスキップ**
 * するため、作られる件数からもその日を除く。
 */
'use client';

import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useVisits } from '@/lib/queries/visits';
import { isoWeekFromLocalDate, mondayOfIsoWeek } from '@/lib/format/isoWeek';
import type { VisitRead } from '@/lib/schemas/visit';

// ─── Types ───────────────────────────────────────────────────────────────────

/** 差分表示に使う 1 スロットの要約（曜日・開始時刻・所要時間だけ）。 */
export interface PfvSlotSummary {
  weekday: number;
  /** HH:MM */
  start_time: string;
  duration_min: number;
}

export type PfvDiffKind = 'added' | 'changed' | 'removed';

export interface PfvDiffEntry {
  weekday: number;
  kind: PfvDiffKind;
  before: PfvSlotSummary | null;
  after: PfvSlotSummary | null;
}

/** ダイアログの選択結果。呼び出し元はこれを PUT のボディに落とす。 */
export interface FixedVisitScopeChoice {
  changeScope: 'pattern_only' | 'pattern_and_week';
  /** changeScope='pattern_and_week' のときのみ設定される。 */
  isoYear?: number;
  isoWeek?: number;
  /** 事後トースト用の週ラベル（例: `9/7 週（9/7〜9/13）`）。 */
  weekLabel?: string;
}

export interface FixedVisitScopeConfirmDialogProps {
  open: boolean;
  patientId: string;
  /** 保存前（サーバー状態）の固定枠。 */
  beforeSlots: PfvSlotSummary[];
  /** これから保存する固定枠（bulk PUT の items 相当）。 */
  afterSlots: PfvSlotSummary[];
  /** 盤面など週を表示中の画面から開かれた場合の対象 ISO 週。無ければ null。 */
  weekContext?: { isoYear: number; isoWeek: number } | null;
  /** 保存 (検査 → PUT) の実行中。true の間はダイアログを開いたままボタンを止める。 */
  submitting?: boolean;
  onCancel: () => void;
  onConfirm: (choice: FixedVisitScopeChoice) => void;
}

// ─── Helpers（純関数・テスト対象） ───────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/**
 * BE が reset (型 → 週の作り直し) で soft-delete してよい source の **許可リスト**。
 * 正典 = `backend/app/services/scheduling/auto_allocator_v2.py`
 * `_RESET_DELETABLE_SOURCES`。ここに無い source (`manual` / `kaipoke` など) は
 * 消えない。`visitReadSchema.source` は欠落時 `'manual'` に既定されるため、
 * 許可リスト方式なら「不明な source」は自動的に保護側へ落ちる。
 */
const RESET_DELETABLE_SOURCES = new Set([
  'auto',
  'auto_alloc',
  'auto_alloc_v2',
  'auto_alloc_v2w',
  'pfv',
  'fixed',
  'reset_v2',
]);

/** 同じく `_RESET_DELETABLE_STATUSES`。実施済み (completed) 等は消えない。 */
const RESET_DELETABLE_STATUSES = new Set(['planned', 'proposed']);

/**
 * この訪問は型の作り直しで **消える** か。
 *
 * 旧実装は「manual_week / import / 青ピン以外は全部消える」という否定形だったが、
 * BE は許可リストで消す。`source='manual'` や `status='completed'` は消えないので、
 * 否定形だと「消える予定」を過大に見せていた。
 */
export function isDeletedByReset(v: VisitRead): boolean {
  if (v.week_pinned === true) return false;
  return (
    RESET_DELETABLE_SOURCES.has(v.source ?? '') && RESET_DELETABLE_STATUSES.has(v.status ?? '')
  );
}

/**
 * この訪問は **同じ日付の型スロットの再生成を止める** か（設計 §2-3 / BE
 * `manual_week_day_keys`）。削除可否とは別軸なので predicate を分ける。
 */
export function suppressesRegen(v: VisitRead): boolean {
  return v.week_pinned === true || v.source === 'manual_week' || v.source === 'import';
}

/** 打刻済み（QR チェックイン済み）か。到着で in_progress・退出で completed になる。 */
export function hasCheckin(v: VisitRead): boolean {
  if ((v as VisitRead & { latest_checkin?: unknown }).latest_checkin != null) return true;
  return v.status === 'in_progress' || v.status === 'completed';
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** ISO 週の月曜〜日曜の yyyy-MM-dd を 7 件返す。 */
export function isoWeekDateStrings(isoYear: number, isoWeek: number): string[] {
  const monday = mondayOfIsoWeek(isoYear, isoWeek);
  const out: string[] = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(monday.getTime() + i * 86400000);
    out.push(`${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`);
  }
  return out;
}

/** 'yyyy-MM-dd' → '9/7'。 */
function mdLabel(dateStr: string): string {
  const parts = dateStr.split('-');
  return `${Number(parts[1])}/${Number(parts[2])}`;
}

/** '9/7 週（9/7〜9/13）'。事後トーストにもそのまま使う。 */
export function formatIsoWeekLabel(isoYear: number, isoWeek: number): string {
  const dates = isoWeekDateStrings(isoYear, isoWeek);
  return `${mdLabel(dates[0] ?? '')} 週（${mdLabel(dates[0] ?? '')}〜${mdLabel(dates[6] ?? '')}）`;
}

/** ローカル日付の今日 (yyyy-MM-dd)。訪問日は日付文字列なのでローカルで比較する。 */
function localTodayString(): string {
  const n = new Date();
  return `${n.getFullYear()}-${pad2(n.getMonth() + 1)}-${pad2(n.getDate())}`;
}

/** HH:MM:SS → HH:MM。 */
function hhmm(t: string): string {
  return t.slice(0, 5);
}

/**
 * 保存前後の固定枠を曜日単位で突き合わせ、追加 / 変更 / 削除を返す。
 *
 * 同じ曜日に slot 0/1 の 2 行がある（2 名体制）場合、開始時刻・所要時間は
 * slot 間で共通なので先頭 1 件だけを代表として比較する。
 */
export function diffFixedVisitSlots(
  before: PfvSlotSummary[],
  after: PfvSlotSummary[],
): PfvDiffEntry[] {
  const firstByWeekday = (list: PfvSlotSummary[]): Map<number, PfvSlotSummary> => {
    const m = new Map<number, PfvSlotSummary>();
    for (const s of list) {
      if (!m.has(s.weekday)) m.set(s.weekday, { ...s, start_time: hhmm(s.start_time) });
    }
    return m;
  };
  const b = firstByWeekday(before);
  const a = firstByWeekday(after);
  const out: PfvDiffEntry[] = [];
  for (let wd = 0; wd < 7; wd++) {
    const bs = b.get(wd) ?? null;
    const as = a.get(wd) ?? null;
    if (!bs && !as) continue;
    if (!bs && as) {
      out.push({ weekday: wd, kind: 'added', before: null, after: as });
    } else if (bs && !as) {
      out.push({ weekday: wd, kind: 'removed', before: bs, after: null });
    } else if (
      bs &&
      as &&
      (bs.start_time !== as.start_time || bs.duration_min !== as.duration_min)
    ) {
      out.push({ weekday: wd, kind: 'changed', before: bs, after: as });
    }
  }
  return out;
}

/** 差分 1 件の日本語文（例: `月 09:30(35分) → 12:00(35分) に変更`）。 */
export function formatDiffEntry(e: PfvDiffEntry): string {
  const wd = WEEKDAY_LABELS[e.weekday] ?? String(e.weekday);
  const slot = (s: PfvSlotSummary) => `${hhmm(s.start_time)}(${s.duration_min}分)`;
  if (e.kind === 'added' && e.after) return `${wd} ${slot(e.after)} を追加`;
  if (e.kind === 'removed' && e.before) return `${wd} ${slot(e.before)} を削除`;
  if (e.kind === 'changed' && e.before && e.after) {
    return `${wd} ${slot(e.before)} → ${slot(e.after)} に変更`;
  }
  return wd;
}

/** 訪問 1 件の表示行（例: `9/7(月) 09:30 高岡`）。 */
function formatVisitLine(v: VisitRead, dates: string[]): string {
  const idx = dates.indexOf(v.visit_date);
  const wd = idx >= 0 ? WEEKDAY_LABELS[idx] : null;
  const day = wd ? `${mdLabel(v.visit_date)}(${wd})` : mdLabel(v.visit_date);
  return `${day} ${hhmm(v.start_time)} ${v.staff_name ?? '（担当なし）'}`;
}

// ─── Component ───────────────────────────────────────────────────────────────

export function FixedVisitScopeConfirmDialog({
  open,
  patientId,
  beforeSlots,
  afterSlots,
  weekContext,
  submitting,
  onCancel,
  onConfirm,
}: FixedVisitScopeConfirmDialogProps) {
  // 既定 (PO 決定 8): 週文脈があれば (B)、無ければ (A)。
  const [scope, setScope] = React.useState<'pattern_only' | 'pattern_and_week'>(
    weekContext ? 'pattern_and_week' : 'pattern_only',
  );
  const [weekSel, setWeekSel] = React.useState<'this' | 'next'>('this');

  const diff = React.useMemo(
    () => diffFixedVisitSlots(beforeSlots, afterSlots),
    [beforeSlots, afterSlots],
  );

  // 週文脈が無いときの選択肢 (今日の週 / 来週)。
  const { thisWeek, nextWeek } = React.useMemo(() => {
    const now = new Date();
    const plus7 = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7);
    return { thisWeek: isoWeekFromLocalDate(now), nextWeek: isoWeekFromLocalDate(plus7) };
  }, []);

  const target = weekContext ?? (weekSel === 'this' ? thisWeek : nextWeek);
  const dates = React.useMemo(
    () => isoWeekDateStrings(target.isoYear, target.isoWeek),
    [target.isoYear, target.isoWeek],
  );
  const weekLabel = formatIsoWeekLabel(target.isoYear, target.isoWeek);

  // 対象週の当該患者の訪問 (BE 側で患者・期間を絞り込む)。
  const visitsQuery = useVisits({
    patient_id: patientId,
    week_start: dates[0],
    week_end: dates[6],
  });

  // status での事前フィルタはしない: 打刻すると planned → in_progress → completed と
  // 変わるため、planned だけ見ると打刻済みの予定が一覧から消えてしまう。
  const weekVisits = React.useMemo(
    () => (visitsQuery.data?.items ?? []).filter((v) => !v.deleted_at),
    [visitsQuery.data],
  );
  const removedVisits = React.useMemo(() => weekVisits.filter(isDeletedByReset), [weekVisits]);
  // 消えない訪問はすべて「保護」= 今週固定 (青ピン) / 今週のみ / 取込 / 実施済み・手動。
  const survivingVisits = React.useMemo(
    () => weekVisits.filter((v) => !isDeletedByReset(v)),
    [weekVisits],
  );
  // 同じ日付の型スロットの再生成を止める訪問 (BE `manual_week_day_keys`)。
  const suppressedDates = React.useMemo(
    () => new Set(survivingVisits.filter(suppressesRegen).map((v) => v.visit_date)),
    [survivingVisits],
  );
  // 生き残る訪問と (日付, 開始時刻) が衝突する枠は BE が INSERT をスキップする
  // (`protected_existing_keys`)。
  const survivingKeys = React.useMemo(
    () => new Set(survivingVisits.map((v) => `${v.visit_date}T${hhmm(v.start_time)}`)),
    [survivingVisits],
  );
  // 作られる側 = 型を曜日展開したもの。上の 2 つの規則で落ちる日を除く。
  const createdSlots = React.useMemo(
    () =>
      afterSlots.filter((s) => {
        const d = dates[s.weekday] ?? '';
        if (suppressedDates.has(d)) return false;
        return !survivingKeys.has(`${d}T${hhmm(s.start_time)}`);
      }),
    [afterSlots, suppressedDates, survivingKeys, dates],
  );

  const today = localTodayString();
  const weekIncludesToday = dates.includes(today);
  // 打刻済みは「消える側」に限らず週全体で見る (保護されていても運用上の注意喚起)。
  const hasCheckedIn = weekVisits.some(hasCheckin);

  const handleConfirm = () => {
    if (scope === 'pattern_and_week') {
      onConfirm({
        changeScope: 'pattern_and_week',
        isoYear: target.isoYear,
        isoWeek: target.isoWeek,
        weekLabel,
      });
    } else {
      onConfirm({ changeScope: 'pattern_only' });
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onCancel();
      }}
    >
      <DialogContent aria-describedby="pfv-scope-confirm-desc" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>固定訪問スケジュールを保存します</DialogTitle>
        </DialogHeader>

        <div id="pfv-scope-confirm-desc" className="space-y-4 text-sm text-text-primary">
          {/* ── 変更内容 ───────────────────────────────────────────── */}
          <section data-testid="pfv-scope-diff">
            <p className="text-xs font-medium text-text-secondary">変更内容</p>
            {diff.length === 0 ? (
              <p className="text-xs text-text-muted">
                曜日・開始時刻・所要時間の変更はありません（コース・完全固定などの変更のみ）
              </p>
            ) : (
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {diff.map((e) => (
                  <li key={`${e.kind}-${e.weekday}`} data-testid={`pfv-scope-diff-${e.weekday}`}>
                    {formatDiffEntry(e)}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* ── 反映先 ─────────────────────────────────────────────── */}
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-text-secondary">反映先</legend>

            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="pfv-scope"
                value="pattern_only"
                className="mt-1"
                checked={scope === 'pattern_only'}
                onChange={() => setScope('pattern_only')}
                data-testid="pfv-scope-pattern-only"
              />
              <span>型だけ変える（今後生成する週から反映。既にある週の予定は触らない）</span>
            </label>

            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="pfv-scope"
                value="pattern_and_week"
                className="mt-1"
                checked={scope === 'pattern_and_week'}
                onChange={() => setScope('pattern_and_week')}
                data-testid="pfv-scope-pattern-and-week"
              />
              <span>型と {weekLabel} の予定も作り直す</span>
            </label>

            {/* 週文脈が無いときだけ対象週を選ばせる (今日の週 / 来週)。 */}
            {!weekContext ? (
              <div className="flex items-center gap-2 pl-6">
                <span className="text-xs text-text-muted">作り直す週</span>
                <select
                  value={weekSel}
                  onChange={(e) => setWeekSel(e.target.value === 'next' ? 'next' : 'this')}
                  className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:border-brand-primary focus:outline-none"
                  aria-label="作り直す週"
                  data-testid="pfv-scope-week-select"
                >
                  <option value="this">
                    今日の週（{formatIsoWeekLabel(thisWeek.isoYear, thisWeek.isoWeek)}）
                  </option>
                  <option value="next">
                    来週（{formatIsoWeekLabel(nextWeek.isoYear, nextWeek.isoWeek)}）
                  </option>
                </select>
              </div>
            ) : null}

            {/* ── (B) の影響プレビュー ──────────────────────────────── */}
            {scope === 'pattern_and_week' ? (
              <div
                className="space-y-2 rounded-md border border-border-default bg-bg-muted/40 px-3 py-2 text-xs"
                data-testid="pfv-scope-impact"
              >
                {visitsQuery.isLoading ? (
                  <p className="text-text-muted">対象週の予定を確認中…</p>
                ) : (
                  <>
                    <div data-testid="pfv-scope-removed">
                      <p className="font-medium text-text-primary">
                        消える予定 {removedVisits.length} 件
                      </p>
                      {removedVisits.length > 0 ? (
                        <ul
                          className="list-disc space-y-0.5 pl-5 text-text-secondary"
                          data-testid="pfv-scope-removed-list"
                        >
                          {removedVisits.map((v) => (
                            <li key={v.id}>{formatVisitLine(v, dates)}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>

                    <div data-testid="pfv-scope-protected">
                      <p className="font-medium text-text-primary">
                        保護される予定 {survivingVisits.length}{' '}
                        件（今週固定・今週のみ・取込・実施済み/手動）
                      </p>
                      {survivingVisits.length > 0 ? (
                        <ul
                          className="list-disc space-y-0.5 pl-5 text-text-secondary"
                          data-testid="pfv-scope-protected-list"
                        >
                          {survivingVisits.map((v) => (
                            <li key={v.id}>{formatVisitLine(v, dates)}</li>
                          ))}
                        </ul>
                      ) : null}
                    </div>

                    <div data-testid="pfv-scope-created">
                      <p className="font-medium text-text-primary">
                        作られる予定 {createdSlots.length} 件
                      </p>
                      {createdSlots.length > 0 ? (
                        <ul className="list-disc space-y-0.5 pl-5 text-text-secondary">
                          {createdSlots.map((s, i) => (
                            <li key={`${s.weekday}-${s.start_time}-${i}`}>
                              {mdLabel(dates[s.weekday] ?? '')}({WEEKDAY_LABELS[s.weekday]}){' '}
                              {hhmm(s.start_time)}（{s.duration_min}分）
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <p className="text-text-muted">
                        ※ 拠点の非稼働日・在籍外の患者は作られません
                      </p>
                    </div>

                    {weekIncludesToday ? (
                      <p className="text-amber-800" data-testid="pfv-scope-warn-today">
                        ⚠ 対象週に今日が含まれます。当日以前の予定も作り直されます
                      </p>
                    ) : null}
                    {hasCheckedIn ? (
                      <p className="text-amber-800" data-testid="pfv-scope-warn-checkin">
                        ⚠ 打刻済みの予定があります
                      </p>
                    ) : null}
                  </>
                )}
              </div>
            ) : null}
          </fieldset>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={submitting === true}
            data-testid="pfv-scope-cancel"
          >
            やめる
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={submitting === true}
            data-testid="pfv-scope-submit"
          >
            保存する
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
