'use client';

/**
 * PatientScheduleDetailDialog — /schedule の visit セル / 患者名クリックで開く
 * 患者の固定枠 (PFV) × 今週 visits 比較ポップアップ.
 *
 * 構成 (ユーザー要件):
 *   (1) 基本情報サマリ  — usePatient で 1 患者を fetch
 *   (2) 固定枠 vs 今週 比較
 *        - 固定枠: useFixedVisits(patient_id, 'normal')
 *        - 今週 : useVisits({ patient_id, week_start, week_end })
 *        - diff 判定 (曜日ごとに (start_time, end_time?, course_template_id) を比較).
 *          差異あり → 並列表示 / 全曜日同一 → 単一表示 + 「今週も同一」緑バッジ
 *   (3) 「今週を固定枠に反映」ボタン
 *        - クリック → dry_run preview (件数表示)
 *        - 確定 → apply (dry_run=false) → toast + invalidate
 *
 * RBAC:
 *   - admin / manager のみボタン操作可 (BE が 403 で担保). canEdit=false なら閲覧専用.
 *
 * 設計判断:
 *   - 「今週 visits」と「固定枠」の比較キーは (weekday, start_time, duration_min,
 *     course_template_id) の 4 つ. duration を比較するのは、PFV 側は end_time を
 *     持たない (start + duration) のため.
 *   - 1 曜日に複数 visits がある場合は最早 start_time の visit を「代表」とする.
 *     BE 側 sync endpoint と同じ採用ルール.
 */
import * as React from 'react';
import { Loader2, Pencil } from 'lucide-react';
import { toast } from 'sonner';

import { addDays } from '@/components/schedule/WeekSelector';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSyncWeekVisitsToFixedMutation } from '@/lib/api/patientSync';
import type { SyncWeekToFixedResponse } from '@/lib/schemas/patientSync';
import { usePatient } from '@/lib/queries/patients';
import { useFixedVisits } from '@/lib/queries/patient_fixed_visits';
import { useVisits } from '@/lib/queries/visits';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';
import type { VisitRead } from '@/lib/schemas/visit';

import { type TimelineRowMeta } from './CourseMoveTimeline';
import { ImprovementSuggestionsSection } from './ImprovementSuggestionsSection';
import { PatientEditDialog } from './PatientEditDialog';
import { PoolCandidateList, type PoolCandidateSpecialTicket } from './PoolCandidateList';
import { SpecialVisitWeekDialog } from './SpecialVisitWeekDialog';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function trimSeconds(t: string | null | undefined): string {
  if (!t) return '';
  return t.length >= 5 ? t.slice(0, 5) : t;
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

function formatDateISO(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function fromIsoYearWeek(isoYear: number, isoWeek: number): Date {
  // 月曜が week_start. Python の date.fromisocalendar(year, week, 1) と等価.
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4Day = (jan4.getUTCDay() + 6) % 7; // 0=Mon..6=Sun
  const week1Monday = new Date(jan4.getTime() - jan4Day * 86400000);
  return new Date(week1Monday.getTime() + (isoWeek - 1) * 7 * 86400000);
}

/**
 * Wave Next 1 M2: ある (iso_year, iso_week) の月曜日 (UTC) を逆算して
 * ISO week を取り直し、入力と一致するか確認する.
 *
 * 例: ``isoYear=2025, isoWeek=53`` は 2025 年に W53 が存在しないため、
 * ``fromIsoYearWeek`` は次年 (2026-W01) の月曜日 (= 2025-12-29) を返す.
 * 月曜日 → ISO 週を取り直すと W01 (2026 年) となり、入力 ``isoWeek=53`` と
 * 一致しないので「invalid」と判定する.
 */
function getIsoYearWeekFromDate(d: Date): { year: number; week: number } {
  // d は UTC 月曜日想定. UTC で日付計算する.
  const utc = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  // ISO 仕様: 木曜日を含む週がその週の番号を決める.
  const dayNum = (utc.getUTCDay() + 6) % 7; // Mon=0..Sun=6
  utc.setUTCDate(utc.getUTCDate() - dayNum + 3); // 当週の木曜日
  const isoYear = utc.getUTCFullYear();
  const jan4 = new Date(Date.UTC(isoYear, 0, 4));
  const jan4DayNum = (jan4.getUTCDay() + 6) % 7;
  const week1Thursday = new Date(jan4.getTime() + (3 - jan4DayNum) * 86400000);
  const diffDays = Math.round((utc.getTime() - week1Thursday.getTime()) / 86400000);
  const isoWeek = Math.floor(diffDays / 7) + 1;
  return { year: isoYear, week: isoWeek };
}

function isValidIsoYearWeek(isoYear: number, isoWeek: number): boolean {
  if (!Number.isInteger(isoYear) || !Number.isInteger(isoWeek)) return false;
  if (isoWeek < 1 || isoWeek > 53) return false;
  const monday = fromIsoYearWeek(isoYear, isoWeek);
  const back = getIsoYearWeekFromDate(monday);
  return back.year === isoYear && back.week === isoWeek;
}

function computeDurationMin(start: string, end: string): number {
  const m1 = start.match(/^(\d{1,2}):(\d{2})/);
  const m2 = end.match(/^(\d{1,2}):(\d{2})/);
  if (!m1 || !m2) return 30;
  const s = Number(m1[1]) * 60 + Number(m1[2]);
  const e = Number(m2[1]) * 60 + Number(m2[2]);
  const diff = e - s;
  if (diff <= 0) return 30;
  if (diff > 480) return 480;
  return diff;
}

/** ISO 日付文字列 → 月曜=0..日曜=6 の weekday. */
function visitDateToWeekday(visitDate: string): number {
  // visit_date は "YYYY-MM-DD". Date 経由で JS getDay() → Mon=0 変換.
  const m = visitDate.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return 0;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return (d.getDay() + 6) % 7;
}

// ---------------------------------------------------------------------------
// 比較用の正規化レコード
// ---------------------------------------------------------------------------

export interface WeekdayCell {
  weekday: number;
  start_time: string; // "HH:MM"
  end_time: string | null;
  duration_min: number;
  course_template_id: string | null;
}

export function pfvToCell(pfv: PatientFixedVisitV2Read): WeekdayCell {
  return {
    weekday: pfv.weekday,
    start_time: trimSeconds(pfv.start_time),
    end_time: null,
    duration_min: pfv.duration_min ?? 30,
    course_template_id: pfv.course_template_id ?? null,
  };
}

export function visitToCell(v: VisitRead): WeekdayCell {
  const start = trimSeconds(v.start_time);
  const end = trimSeconds(v.end_time);
  return {
    weekday: visitDateToWeekday(v.visit_date),
    start_time: start,
    end_time: end || null,
    duration_min: computeDurationMin(start, end),
    course_template_id: null, // FE 側では visit から course_template_id まで辿らない (UI 比較は時刻と duration を主軸)
  };
}

/**
 * 曜日ごとに 1 件 (最早 start_time) を採用したマップに変換.
 * BE 側 sync endpoint と同じ採用ルール.
 */
export function groupByWeekday(cells: WeekdayCell[]): Map<number, WeekdayCell> {
  const out = new Map<number, WeekdayCell>();
  const sorted = [...cells].sort((a, b) => a.start_time.localeCompare(b.start_time));
  for (const c of sorted) {
    if (!out.has(c.weekday)) out.set(c.weekday, c);
  }
  return out;
}

export interface DiffEntry {
  weekday: number;
  fixed: WeekdayCell | null;
  week: WeekdayCell | null;
  /** 内容が異なるか. fixed === null XOR week === null も diff 扱い. */
  differs: boolean;
}

/**
 * 2026-W20 後期 D: 重複判定ヘルパー.
 * - `differs=false` かつ `fixed` / `week` の両方が非 null = 同一エントリ.
 * - 右カラム描画時はこのフラグで「今は固定枠が使われています」表示に切替える.
 */
export function isDuplicateEntry(entry: DiffEntry): boolean {
  return !entry.differs && entry.fixed != null && entry.week != null;
}

export function buildDiff(
  fixedByWd: Map<number, WeekdayCell>,
  weekByWd: Map<number, WeekdayCell>,
): DiffEntry[] {
  const allWd = new Set<number>([...fixedByWd.keys(), ...weekByWd.keys()]);
  const out: DiffEntry[] = [];
  for (const wd of [...allWd].sort((a, b) => a - b)) {
    const fx = fixedByWd.get(wd) ?? null;
    const wk = weekByWd.get(wd) ?? null;
    let differs = false;
    if ((fx === null) !== (wk === null)) {
      differs = true;
    } else if (fx && wk) {
      differs =
        fx.start_time !== wk.start_time ||
        fx.duration_min !== wk.duration_min ||
        // course_template_id は visit 側で取れない場合があるため、両方非 null のときだけ比較.
        (fx.course_template_id !== null &&
          wk.course_template_id !== null &&
          fx.course_template_id !== wk.course_template_id);
    }
    out.push({ weekday: wd, fixed: fx, week: wk, differs });
  }
  return out;
}

function summarizeCell(cell: WeekdayCell | null): string {
  if (!cell) return '—';
  const s = cell.start_time;
  // end_time が無い (PFV) 場合は duration_min から計算する.
  const e =
    cell.end_time ??
    (() => {
      const m = s.match(/^(\d{1,2}):(\d{2})$/);
      if (!m) return '';
      const start = Number(m[1]) * 60 + Number(m[2]);
      const end = start + cell.duration_min;
      return `${pad2(Math.floor(end / 60))}:${pad2(end % 60)}`;
    })();
  return `${s}–${e}`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface PatientScheduleDetailDialogProps {
  patientId: string | null;
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** admin / manager 以外は反映ボタンを無効化する. */
  canEdit?: boolean;
  /**
   * 保留プール由来のクリックで開いたとき true. プール投入 (diff-add) の
   * 「この患者 1 人分の提案」セクションを表示する。テーブル/週ビュー由来の
   * 通常クリックでは false にして重い diff-add 算出を走らせない。
   */
  enablePoolProposal?: boolean;
  /**
   * プール投入提案を取得する拠点スコープ. 一括ダイアログ (DiffAddDialog) と
   * 同じ値を渡すこと (同一患者が両表示で同一提案 = ドリフト防止)。null = 全拠点。
   */
  officeId?: string | null;
  /**
   * W-5b: BulkPoolInsertDialog done 画面から「定員超過候補を直接開く」導線用.
   * true のとき PoolCandidateList が通常候補 0 件 + 超過候補あり を検知して
   * 自動で include_overcapacity 再リクエストを発火する。1 回だけ (ref ガード済み)。
   */
  autoRequestOvercapacity?: boolean;
  /**
   * W-14: BulkPoolInsertDialog done 画面「ずらせば入る手を探せます」からの直行専用.
   * true のとき PoolCandidateList が通常候補 0 件 + 時間起因の除外理由を検知して
   * 詰まり解消探索 (propose-unblock) を 1 回だけ自動発火する (ref ガード済み)。
   */
  autoRequestUnblock?: boolean;
  /**
   * T-4: 患者 ID → 表示メタ (性別ウォッシュ・住所)。提案系タイムラインの
   * カード視覚言語に使う。optional — 未指定でも各セクションが対象患者分を補完する。
   */
  patientMetaById?: ReadonlyMap<string, TimelineRowMeta>;
  /**
   * 特別訪問週間 (⭐) のチケットから開いたときだけ渡す。PoolCandidateList を特別モード
   * (曜日固定・この週のみ・place 確定) にして、通常プール患者と同じポップアップで
   * 「ここに入れますか」を確認できるようにする。未指定 = 従来どおり。
   */
  specialTicket?: PoolCandidateSpecialTicket;
}

type ApplyStage = 'idle' | 'preview' | 'applying';

export function PatientScheduleDetailDialog({
  patientId,
  open,
  onClose,
  isoYear,
  isoWeek,
  canEdit = true,
  enablePoolProposal = false,
  officeId = null,
  autoRequestOvercapacity = false,
  autoRequestUnblock = false,
  patientMetaById,
  specialTicket,
}: PatientScheduleDetailDialogProps) {
  // 週 範囲 (ISO Mon..Sun)
  // Wave Next 1 M2: ISO W53 が存在しない年 (e.g. 2025-W53) を検出.
  const isoWeekValid = React.useMemo(
    () => isValidIsoYearWeek(isoYear, isoWeek),
    [isoYear, isoWeek],
  );
  const weekMonday = React.useMemo(() => fromIsoYearWeek(isoYear, isoWeek), [isoYear, isoWeek]);
  const weekSunday = React.useMemo(() => addDays(weekMonday, 6), [weekMonday]);
  const weekStartStr = React.useMemo(() => formatDateISO(weekMonday), [weekMonday]);
  const weekEndStr = React.useMemo(() => formatDateISO(weekSunday), [weekSunday]);

  // データ fetch (patientId が null / dialog 閉じている間は無効).
  const patientQuery = usePatient(open ? patientId : null);
  const pfvQuery = useFixedVisits(open && patientId ? patientId : '', 'normal');
  const visitsQuery = useVisits(
    open && patientId
      ? { patient_id: patientId, week_start: weekStartStr, week_end: weekEndStr }
      : {},
  );

  const fixedRows = (pfvQuery.data ?? []).filter((p) => p.mode === 'normal');
  const weekRows = (visitsQuery.data?.items ?? []).filter((v) => !v.deleted_at);

  const fixedByWd = React.useMemo(() => groupByWeekday(fixedRows.map(pfvToCell)), [fixedRows]);
  const weekByWd = React.useMemo(() => groupByWeekday(weekRows.map(visitToCell)), [weekRows]);
  const diff = React.useMemo(() => buildDiff(fixedByWd, weekByWd), [fixedByWd, weekByWd]);
  const anyDiffers = diff.some((d) => d.differs);

  // 反映 mutation.
  const syncMut = useSyncWeekVisitsToFixedMutation(patientId);
  const [stage, setStage] = React.useState<ApplyStage>('idle');
  const [preview, setPreview] = React.useState<SyncWeekToFixedResponse | null>(null);

  // Phase G-20: 編集ダイアログ (= dialog 内 dialog) の open 状態.
  const [editOpen, setEditOpen] = React.useState(false);

  // 特別訪問週間の設定モーダル (= dialog 内 dialog). 開いたときだけマウントする。
  const [specialWeekOpen, setSpecialWeekOpen] = React.useState(false);

  // ─── プール投入の提案セクション (Pool-detail 統合) ───
  // enablePoolProposal=true (= プール由来クリック) のときだけ表示する。
  // 単体クリックは「その 1 人だけ」を実際の確定済スケジュールに対して空き探索する
  // propose-slots (PoolCandidateList primary) を主提案にする。diff-add (全員分バッチ
  // 最適) は他プール患者との架空競合で当該患者が弾かれ得るため単体では使わない
  // (一括ダイアログ DiffAddDialog は従来どおり全員バッチのまま)。
  // 採用完了フラグ (採用後はカードを隠して成功表示に切り替える).
  const [poolAdopted, setPoolAdopted] = React.useState(false);

  // open 状態のリセット.
  React.useEffect(() => {
    if (!open) {
      setStage('idle');
      setPreview(null);
      setEditOpen(false);
      setSpecialWeekOpen(false);
      setPoolAdopted(false);
    }
  }, [open]);

  // 別の患者を開き直したらプール投入セクションの一時状態をリセットする.
  React.useEffect(() => {
    setPoolAdopted(false);
  }, [patientId]);

  const isLoading = patientQuery.isLoading || pfvQuery.isLoading || visitsQuery.isLoading;
  const isError = patientQuery.isError || pfvQuery.isError || visitsQuery.isError;

  const handlePreview = React.useCallback(async () => {
    if (!patientId) return;
    setStage('preview');
    try {
      const res = await syncMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        dry_run: true,
      });
      setPreview(res);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`プレビューに失敗しました: ${msg}`);
      setStage('idle');
    }
  }, [patientId, isoYear, isoWeek, syncMut]);

  const handleApply = React.useCallback(async () => {
    if (!patientId) return;
    setStage('applying');
    try {
      const res = await syncMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        dry_run: false,
      });
      const s = res.summary;
      toast.success(
        `固定枠を更新しました (新規 ${s.pfv_inserted} 件 / 更新 ${s.pfv_updated} 件 / 変更なし ${s.pfv_unchanged} 件)`,
      );
      setPreview(null);
      setStage('idle');
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(`反映に失敗しました: ${msg}`);
      setStage('idle');
    }
  }, [patientId, isoYear, isoWeek, syncMut, onClose]);

  const handleCancelPreview = React.useCallback(() => {
    setStage('idle');
    setPreview(null);
  }, []);

  const patient = patientQuery.data ?? null;
  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent
        className="max-w-3xl"
        aria-describedby="patient-schedule-detail-description"
        data-testid="patient-schedule-detail-dialog"
      >
        <DialogHeader>
          <DialogTitle>
            患者スケジュール詳細
            {patient ? (
              <span className="ml-2 text-sm font-normal text-text-secondary">
                {patient.name}
                {patient.code ? ` (${patient.code})` : null}
              </span>
            ) : null}
            {/* 特別訪問週間の追加枠から開いたときの小表示 (何を配置しているかの明示)。 */}
            {specialTicket ? (
              <span
                className="ml-2 rounded bg-brand-primary/10 px-1.5 py-0.5 text-[11px] font-normal text-brand-primary"
                data-testid="patient-schedule-special-ticket-label"
              >
                ⭐特別訪問週間の追加枠（{WEEKDAY_LABELS[specialTicket.weekday] ?? '?'}曜）
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription id="patient-schedule-detail-description">
            {isoWeekLabel} の固定枠と今週 visits を比較します。
          </DialogDescription>
        </DialogHeader>

        {/* loading / error */}
        {isLoading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-text-secondary">
            <Loader2 className="h-4 w-4 animate-spin" />
            読み込み中…
          </div>
        ) : isError ? (
          <div className="rounded border border-error/40 bg-error/10 p-3 text-sm text-error">
            データの取得に失敗しました
          </div>
        ) : (
          <div className="space-y-4">
            {/* Wave Next 1 M2: ISO W53 が存在しない年の警告 */}
            {!isoWeekValid ? (
              <div
                className="rounded border border-warning/40 bg-warning/10 p-3 text-sm text-warning"
                data-testid="patient-schedule-iso-week-invalid"
                role="alert"
              >
                {isoYear} 年には ISO 週 {isoWeek} が存在しません。週選択をやり直してください。
              </div>
            ) : null}
            {/* (1) 基本情報サマリ */}
            <section className="rounded border border-border-default bg-bg-muted/40 p-3 text-xs">
              <h3 className="mb-2 text-sm font-semibold text-text-primary">基本情報</h3>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
                <div>
                  <dt className="text-text-muted">コード</dt>
                  <dd className="font-medium">{patient?.code ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-text-muted">氏名</dt>
                  <dd className="font-medium">{patient?.name ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-text-muted">ステータス</dt>
                  <dd className="font-medium">{patient?.status ?? '—'}</dd>
                </div>
                <div className="col-span-2 sm:col-span-1">
                  <dt className="text-text-muted">性別制限</dt>
                  <dd className="font-medium">
                    {patient?.sex_restriction === 'female_only'
                      ? '女性のみ'
                      : patient?.sex_restriction === 'male_only'
                        ? '男性のみ'
                        : 'なし'}
                  </dd>
                </div>
                <div className="col-span-2">
                  <dt className="text-text-muted">備考</dt>
                  <dd className="whitespace-pre-line font-medium">
                    {patient?.note?.trim() ? patient.note : '—'}
                  </dd>
                </div>
              </dl>
            </section>

            {/* (2) 固定枠 vs 今週 — 2026-W20 後期 D: 常に横並び (左=固定枠 / 右=今週).
                重複行 (同一の weekday+start_time+duration_min+course_template_id) は
                1 つの merged 行に集約し「今は固定枠が使われています」と注記する. */}
            <section className="rounded border border-border-default p-3">
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-text-primary">
                  固定枠 vs 今週スケジュール
                </h3>
                {!anyDiffers && diff.length > 0 ? (
                  <Badge variant="success" data-testid="patient-schedule-same-as-fixed-badge">
                    今週のスケジュールも同一
                  </Badge>
                ) : null}
              </div>
              {diff.length === 0 ? (
                <div className="py-3 text-center text-xs text-text-muted">
                  この週には固定枠も今週 visit もありません。
                </div>
              ) : (
                <div className="grid grid-cols-[2rem_1fr_1fr] text-xs">
                  <div />
                  <div
                    className="border-b border-border-default px-2 py-1 font-semibold text-text-muted"
                    data-testid="patient-schedule-col-header-fixed"
                  >
                    固定枠 (PFV)
                  </div>
                  <div
                    className="border-b border-border-default px-2 py-1 font-semibold text-brand-primary"
                    data-testid="patient-schedule-col-header-week"
                  >
                    今週 ({isoWeekLabel})
                  </div>
                  {diff.map((d) => {
                    // 重複抑制: differs=false かつ 両方非 null のとき = 同一エントリ.
                    // 横並びに同じ値を 2 個並べる代わりに、左カラムに 1 個 + 右カラムに
                    // 「今は固定枠が使われています」と表示し視覚ノイズを減らす.
                    const isDuplicate = isDuplicateEntry(d);
                    return (
                      <React.Fragment key={`row-${d.weekday}`}>
                        <div className="border-b border-border-default/60 px-2 py-1 font-semibold text-text-primary tnum">
                          {WEEKDAY_LABELS[d.weekday]}
                        </div>
                        <div
                          className={`border-b border-border-default/60 px-2 py-1 tnum ${
                            d.differs ? 'text-text-muted' : 'text-text-primary'
                          }`}
                          data-testid={`patient-schedule-fixed-cell-${d.weekday}`}
                        >
                          {summarizeCell(d.fixed)}
                        </div>
                        {isDuplicate ? (
                          <div
                            className="border-b border-border-default/60 px-2 py-1 text-[11px] text-success"
                            data-testid={`patient-schedule-week-cell-${d.weekday}`}
                            data-duplicate="true"
                          >
                            今は固定枠が使われています
                          </div>
                        ) : (
                          <div
                            className={`border-b border-border-default/60 px-2 py-1 tnum ${
                              d.differs ? 'font-semibold text-warning' : 'text-text-primary'
                            }`}
                            data-testid={`patient-schedule-week-cell-${d.weekday}`}
                          >
                            {summarizeCell(d.week)}
                          </div>
                        )}
                      </React.Fragment>
                    );
                  })}
                </div>
              )}
            </section>

            {/* (2.3) 配置改善の提案 (P2-C).
                配置済み患者クリック (= !enablePoolProposal) のときだけ表示する。
                プール由来クリックはプール投入 (下段) が主提案なので出さない。
                API 未デプロイ環境でもセクション内でエラーテキストに落ち、ダイアログは生きる。 */}
            {!enablePoolProposal && patient ? (
              <ImprovementSuggestionsSection
                patient={patient}
                isoYear={isoYear}
                isoWeek={isoWeek}
                canEdit={canEdit}
                patientMetaById={patientMetaById}
              />
            ) : null}

            {/* (2.5) プール投入の提案 (Pool-detail 統合).
                プール由来クリック (enablePoolProposal) のときのみ表示。
                単体は「その 1 人だけ」を確定済スケジュールに対し空き探索する
                propose-slots (PoolCandidateList primary) を主提案にする
                (= モバイル盤の新規患者 空き確認と同方式)。 */}
            {enablePoolProposal ? (
              <section
                className="rounded border border-border-default p-3"
                data-testid="patient-schedule-pool-proposal"
              >
                <h3 className="mb-2 text-sm font-semibold text-text-primary">
                  {specialTicket ? '追加枠の配置先' : 'プール投入の提案'}
                </h3>
                {poolAdopted ? (
                  <div
                    className="rounded border border-success/40 bg-success/5 p-2 text-xs text-success"
                    data-testid="patient-schedule-pool-adopted"
                  >
                    この患者を採用し、固定枠に反映しました。上の「固定枠」表示も更新されています。
                  </div>
                ) : patient ? (
                  <PoolCandidateList
                    patient={patient}
                    isoYear={isoYear}
                    isoWeek={isoWeek}
                    officeId={officeId}
                    canEdit={canEdit}
                    // 特別モードは「配置しました」トーストのあとダイアログを閉じる
                    // (固定枠に反映していないので「反映しました」表示に切り替えない)。
                    onAdopted={() => (specialTicket ? onClose() : setPoolAdopted(true))}
                    primary
                    autoRequestOvercapacity={autoRequestOvercapacity}
                    autoRequestUnblock={autoRequestUnblock}
                    patientMetaById={patientMetaById}
                    specialTicket={specialTicket}
                  />
                ) : null}
              </section>
            ) : null}

            {/* (3) preview */}
            {stage === 'preview' && preview ? (
              <section
                className="rounded border border-warning/40 bg-warning/5 p-3 text-xs"
                data-testid="patient-schedule-sync-preview"
              >
                <h3 className="mb-1 text-sm font-semibold text-warning">反映プレビュー</h3>
                <p className="mb-2 text-text-secondary">
                  この患者の固定枠を以下の通り更新します。よろしいですか？
                </p>
                <ul className="tnum">
                  <li>新規追加: {preview.summary.pfv_inserted} 件</li>
                  <li>上書き更新: {preview.summary.pfv_updated} 件</li>
                  <li>変更なし: {preview.summary.pfv_unchanged} 件</li>
                </ul>
                <p className="mt-2 text-[11px] text-text-muted">
                  今週に visit が無い既存の固定枠は **そのまま保持** されます (削除しません)。
                </p>
              </section>
            ) : null}
          </div>
        )}

        <DialogFooter>
          {stage === 'preview' ? (
            <>
              <Button
                type="button"
                variant="outline"
                onClick={handleCancelPreview}
                disabled={syncMut.isPending}
              >
                戻る
              </Button>
              <Button
                type="button"
                onClick={handleApply}
                disabled={!canEdit || syncMut.isPending}
                data-testid="patient-schedule-sync-confirm-button"
              >
                {syncMut.isPending ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    反映中…
                  </>
                ) : (
                  '反映する'
                )}
              </Button>
            </>
          ) : (
            <>
              <Button type="button" variant="outline" onClick={onClose}>
                閉じる
              </Button>
              {/* Phase G-20: 患者情報を編集する dialog 内 dialog を開く. admin / manager のみ. */}
              {canEdit ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setEditOpen(true)}
                  disabled={isLoading || isError || !patientId}
                  data-testid="patient-schedule-edit-patient-button"
                >
                  <Pencil className="mr-1 h-4 w-4" />
                  編集
                </Button>
              ) : null}
              {/* 特別訪問週間 (上乗せ型・期間限定). 編集系なので canEdit 準拠.
                  PO指示 2026-07-29: 枠に色を付けて少し目立たせる. */}
              {canEdit ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setSpecialWeekOpen(true)}
                  disabled={isLoading || isError || !patientId}
                  className="border-brand-primary text-brand-primary hover:bg-brand-primary/10"
                  data-testid="patient-schedule-special-visit-week-button"
                >
                  ⭐ 特別訪問週間
                </Button>
              ) : null}
              <Button
                type="button"
                onClick={handlePreview}
                disabled={!canEdit || isLoading || isError || !isoWeekValid || syncMut.isPending}
                data-testid="patient-schedule-sync-preview-button"
              >
                {syncMut.isPending ? (
                  <>
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                    プレビュー中…
                  </>
                ) : (
                  '今週を固定枠に反映'
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>

      {/* Phase G-20: 患者情報編集ダイアログ (= dialog 内 dialog).
          canEdit=false のときはレンダリング自体しない (= ボタンも出ない). */}
      {canEdit ? (
        <PatientEditDialog
          patientId={patientId}
          open={editOpen}
          onClose={() => setEditOpen(false)}
          canEdit={canEdit}
        />
      ) : null}

      {/* 特別訪問週間の設定モーダル. 開いたときだけマウントする (クエリ節約). */}
      {canEdit && patientId && specialWeekOpen ? (
        <SpecialVisitWeekDialog
          patientId={patientId}
          patientName={patient?.name ?? ''}
          open
          onOpenChange={setSpecialWeekOpen}
        />
      ) : null}
    </Dialog>
  );
}
