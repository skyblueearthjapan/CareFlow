/**
 * PatientFixedVisitsPanel (W9-FE1 Phase 3 / W22 拡張 / W37 Phase 3-A).
 *
 * 患者編集画面に「週間訪問パターン (固定枠)」セクションを提供するコンポーネント。
 *
 * 仕様:
 * - タブで normal / special を切替
 * - 各曜日: チェックボックス + start_time picker + duration_min select + course select (W22)
 * - course select: 患者の primary_office_id に紐付く course_templates を取得
 *   - options: 「{label}」 (例: 「A」) + "未指定" option (NULL = Layer 1 フォールバック)
 *   - primary_office_id が null の患者は "未指定" のみ表示
 * - 「希望から自動生成」: patient.weekly_pattern を読みフォーム初期値に反映
 * - 「現スケから取込」: POST /from-week (Phase 2 連携)
 * - 「リセット」: DELETE (確認ダイアログあり)
 * - 「保存」: PUT (zod 検証 → 422 detail 表示)
 * - staff role は読み取り専用 (フィールド disable)
 * - admin/manager は編集可
 *
 * W37 Phase 3-A:
 * - `requiresMultipleStaff=true` の患者では「コース 1 (slot 0)」と「コース 2 (slot 1)」を
 *   並列表示し、bulk PUT に slot_index 0/1 のペアを送る。
 * - フラグ OFF 患者は従来どおり 1 セレクタのみ (slot_index=0)。
 * - コース 2 が空のままでも保存は通す (Layer 1 が寛容モードで処理する; 警告のみ表示)。
 * - コース 1 と コース 2 が同一の場合は保存ブロック (FE バリデーションで弾く)。
 */
'use client';

import * as React from 'react';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { toast } from '@/components/ui/sonner';

import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
} from '@/lib/queries/patient_fixed_visits';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import {
  patientFixedVisitsBulkPutSchema,
  PATIENT_FIXED_VISIT_MODES,
  type PatientFixedVisitMode,
  type PatientFixedVisitV2Base,
  type PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { WeeklyPattern } from '@/lib/schemas/patient';

// ─── Constants ───────────────────────────────────────────────────────────────

const WEEKDAY_LABELS: Record<number, string> = {
  0: '月',
  1: '火',
  2: '水',
  3: '木',
  4: '金',
  5: '土',
  6: '日',
};

/** 15 分ステップの時刻選択肢 (00:00 〜 23:45) */
const TIME_OPTIONS: string[] = (() => {
  const opts: string[] = [];
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m += 15) {
      opts.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return opts;
})();

const DURATION_OPTIONS = [15, 30, 45, 60, 90, 120, 150, 180, 240, 300, 360, 480] as const;

// ─── Types ───────────────────────────────────────────────────────────────────

/**
 * フォーム内部で使う 1 曜日行の状態。
 *
 * W37 Phase 3-A:
 *   - course_template_id   : コース 1 (slot_index=0) 用
 *   - course_template_id_2 : コース 2 (slot_index=1) 用 (requires_multiple_staff=true のみ使用)
 *
 * 開始時刻 / 所要時間は slot 0/1 で共通 (BE 仕様: 同曜日・同時刻・同 duration の 2 行).
 */
interface DayRow {
  enabled: boolean;
  start_time: string;
  duration_min: number;
  /** W22: コーステンプレート ID (null = 未指定). slot_index=0 用. */
  course_template_id: string | null;
  /** W37 Phase 3-A: コース 2 (slot_index=1) 用. requires_multiple_staff=true でのみ有効. */
  course_template_id_2: string | null;
}

type DayRows = Record<number, DayRow>;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function emptyDayRow(): DayRow {
  return {
    enabled: false,
    start_time: '09:00',
    duration_min: 30,
    course_template_id: null,
    course_template_id_2: null,
  };
}

function emptyDayRows(): DayRows {
  const rows: DayRows = {};
  for (let i = 0; i < 7; i++) {
    rows[i] = emptyDayRow();
  }
  return rows;
}

/**
 * BE が返した PatientFixedVisitV2Read[] を曜日 × slot で 1 行にマージする。
 *
 * W37: slot_index=0 → course_template_id, slot_index=1 → course_template_id_2.
 * start_time / duration_min は slot 0 を優先し、slot 0 が無ければ slot 1 を使う。
 */
function readsToDayRows(reads: PatientFixedVisitV2Read[]): DayRows {
  const rows = emptyDayRows();
  for (const r of reads) {
    if (r.weekday < 0 || r.weekday > 6) continue;
    const slot = r.slot_index ?? 0;
    const current = rows[r.weekday] ?? emptyDayRow();
    // start_time は HH:MM:SS の場合もあるので先頭 5 文字に切り詰める
    const startTime = r.start_time.slice(0, 5);
    if (slot === 0) {
      rows[r.weekday] = {
        ...current,
        enabled: true,
        start_time: startTime,
        duration_min: r.duration_min,
        course_template_id: r.course_template_id ?? null,
      };
    } else {
      // slot 1: enabled は slot 0 のフラグを尊重 (slot 0 が無い場合は slot 1 で起こす)
      rows[r.weekday] = {
        ...current,
        enabled: true,
        // slot 0 が後から上書きしてくれるが、slot 1 だけのケース対応
        start_time: current.enabled ? current.start_time : startTime,
        duration_min: current.enabled ? current.duration_min : r.duration_min,
        course_template_id_2: r.course_template_id ?? null,
      };
    }
  }
  return rows;
}

/**
 * DayRows を bulk PUT items に変換する。
 *
 * W37 Phase 3-A:
 *   - requires_multiple_staff=false: 各曜日 1 行 (slot_index=0)
 *   - requires_multiple_staff=true : 各曜日 1 行 (slot_index=0) + course_template_id_2 がある
 *     場合のみ slot_index=1 の行を追加 (寛容モード: 片方未設定でも保存は通す)
 */
function dayRowsToItems(rows: DayRows, requiresMultipleStaff: boolean): PatientFixedVisitV2Base[] {
  const items: PatientFixedVisitV2Base[] = [];
  for (const [weekdayStr, row] of Object.entries(rows)) {
    if (!row.enabled) continue;
    const weekday = Number(weekdayStr);
    // slot 0 は常に送る
    items.push({
      weekday,
      start_time: row.start_time,
      duration_min: row.duration_min,
      course_template_id: row.course_template_id ?? null,
      slot_index: 0,
    });
    // slot 1 は requires_multiple_staff=true かつ course_template_id_2 が設定済みの場合のみ送る
    if (requiresMultipleStaff && row.course_template_id_2) {
      items.push({
        weekday,
        start_time: row.start_time,
        duration_min: row.duration_min,
        course_template_id: row.course_template_id_2,
        slot_index: 1,
      });
    }
  }
  return items;
}

/** 患者の weekly_pattern (希望パターン) から DayRows を生成する */
function weeklyPatternToDayRows(pattern: WeeklyPattern | null | undefined): DayRows {
  const rows = emptyDayRows();
  if (!pattern) return rows;

  const WEEKDAY_KEY_MAP: Record<string, number> = {
    Mon: 0,
    Tue: 1,
    Wed: 2,
    Thu: 3,
    Fri: 4,
    Sat: 5,
    Sun: 6,
  };

  const preferred = pattern.preferred_weekdays ?? [];
  const duration = pattern.service_minutes ?? 30;

  // 開始時刻は preferred_start を最優先。未指定なら time_type からデフォルト時刻を導出。
  // - 午前: 09:00 / 午後: 13:00 / 終日: 09:00 / 時間帯・固定: preferred_start (フォールバック 09:00)
  const deriveStart = (): string => {
    const ps = (pattern.preferred_start ?? '').slice(0, 5);
    if (ps) return ps;
    switch (pattern.time_type) {
      case '午後':
        return '13:00';
      case '午前':
      case '終日':
      default:
        return '09:00';
    }
  };
  const startTime = deriveStart();

  for (const wd of preferred) {
    const idx = WEEKDAY_KEY_MAP[wd];
    if (idx !== undefined) {
      rows[idx] = {
        enabled: true,
        start_time: startTime,
        duration_min: Math.max(1, Math.min(480, duration)),
        course_template_id: null,
        course_template_id_2: null,
      };
    }
  }
  return rows;
}

// ─── Sub-component: ReadOnlyWeekGrid ─────────────────────────────────────────

/**
 * W37 Phase 3-D: 読み取り専用の週間訪問パターン表示。
 *
 * - フォーム要素 (select / checkbox) の代わりにテキストラベルで表示。
 * - requires_multiple_staff=true の場合は「コース 1: A」「コース 2: B」を併記。
 *   slot_index=1 の行が存在しない (course_template_id_2 が null) 場合は
 *   「コース 2: 未設定」を警告色で表示。
 * - requires_multiple_staff=false の場合は「コース: A」のみ表示 (従来通り)。
 */
interface ReadOnlyWeekGridProps {
  rows: DayRows;
  /** W22: 当該患者の拠点に紐付く course_templates (ラベル解決に使用) */
  courseTemplates: CourseTemplateRead[];
  /** W37 Phase 3-D: 複数スタッフ対応患者かどうか。true でコース 2 列を表示 */
  requiresMultipleStaff: boolean;
}

function ReadOnlyWeekGrid({ rows, courseTemplates, requiresMultipleStaff }: ReadOnlyWeekGridProps) {
  /** course_template_id → label の逆引きマップ */
  const labelMap = React.useMemo(() => {
    const m: Record<string, string> = {};
    for (const tpl of courseTemplates) {
      m[tpl.id] = tpl.label;
    }
    return m;
  }, [courseTemplates]);

  const courseLabel = (id: string | null): string => (id ? (labelMap[id] ?? id) : '--');

  return (
    <div className="space-y-1">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? emptyDayRow();
        return (
          <div
            key={wd}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border-default px-3 py-2 text-sm"
            data-testid={`ro-row-${wd}`}
          >
            <span className="w-5 shrink-0 text-center font-medium text-text-secondary">
              {WEEKDAY_LABELS[wd]}
            </span>
            {row.enabled ? (
              <>
                <span className="text-text-primary tnum">{row.start_time}</span>
                <span className="text-text-muted">{row.duration_min} 分</span>
                {requiresMultipleStaff ? (
                  <>
                    <span className="text-text-primary" data-testid={`ro-course1-${wd}`}>
                      コース 1: {courseLabel(row.course_template_id)}
                    </span>
                    {row.course_template_id_2 ? (
                      <span className="text-text-primary" data-testid={`ro-course2-${wd}`}>
                        コース 2: {courseLabel(row.course_template_id_2)}
                      </span>
                    ) : (
                      <span className="text-warning" data-testid={`ro-course2-missing-${wd}`}>
                        コース 2: 未設定
                      </span>
                    )}
                  </>
                ) : (
                  <span className="text-text-primary" data-testid={`ro-course-${wd}`}>
                    コース: {courseLabel(row.course_template_id)}
                  </span>
                )}
              </>
            ) : (
              <span className="text-xs text-text-muted" data-testid={`ro-no-visit-${wd}`}>
                訪問なし
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Sub-component: WeekGrid ─────────────────────────────────────────────────

interface WeekGridProps {
  rows: DayRows;
  onChange: (rows: DayRows) => void;
  disabled?: boolean;
  errors: Record<number, string>;
  warnings: Record<number, string>;
  /** W22: 当該患者の拠点に紐付く course_templates (空配列 = office 未設定) */
  courseTemplates: CourseTemplateRead[];
  /** W37 Phase 3-A: 複数スタッフ対応患者かどうか. true で コース 2 セレクタを enable */
  requiresMultipleStaff: boolean;
}

function WeekGrid({
  rows,
  onChange,
  disabled,
  errors,
  warnings,
  courseTemplates,
  requiresMultipleStaff,
}: WeekGridProps) {
  const update = (weekday: number, patch: Partial<DayRow>) => {
    const current = rows[weekday] ?? emptyDayRow();
    onChange({ ...rows, [weekday]: { ...current, ...patch } as DayRow });
  };

  return (
    <div className="space-y-2">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? emptyDayRow();
        return (
          <div
            key={wd}
            className="flex flex-wrap items-center gap-3 rounded-md border border-border-default px-3 py-2"
          >
            <span className="w-5 text-center text-sm font-medium text-text-secondary">
              {WEEKDAY_LABELS[wd]}
            </span>
            <Checkbox
              checked={row.enabled}
              onCheckedChange={(c) => update(wd, { enabled: c === true })}
              disabled={disabled}
              aria-label={`${WEEKDAY_LABELS[wd]}曜日 訪問あり`}
            />
            {row.enabled ? (
              <>
                <div className="flex items-center gap-1">
                  <select
                    value={row.start_time}
                    onChange={(e) => update(wd, { start_time: e.target.value })}
                    disabled={disabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                    aria-label={`${WEEKDAY_LABELS[wd]} 開始時刻`}
                  >
                    {TIME_OPTIONS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <span className="text-xs text-text-muted">開始</span>
                </div>
                <div className="flex items-center gap-1">
                  <select
                    value={row.duration_min}
                    onChange={(e) => update(wd, { duration_min: Number(e.target.value) })}
                    disabled={disabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                    aria-label={`${WEEKDAY_LABELS[wd]} 所要時間`}
                  >
                    {DURATION_OPTIONS.map((d) => (
                      <option key={d} value={d}>
                        {d} 分
                      </option>
                    ))}
                  </select>
                </div>
                {/* W37 Phase 3-A: コース 1 (slot_index=0) */}
                <div className="flex items-center gap-1">
                  <select
                    value={row.course_template_id ?? ''}
                    onChange={(e) => update(wd, { course_template_id: e.target.value || null })}
                    disabled={disabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                    aria-label={
                      requiresMultipleStaff
                        ? `${WEEKDAY_LABELS[wd]} コース 1`
                        : `${WEEKDAY_LABELS[wd]} コース`
                    }
                  >
                    <option value="">未指定</option>
                    {courseTemplates.map((tpl) => (
                      <option key={tpl.id} value={tpl.id}>
                        {tpl.label}
                      </option>
                    ))}
                  </select>
                  {requiresMultipleStaff ? (
                    <span className="text-xs text-text-muted">コース 1</span>
                  ) : null}
                </div>
                {/* W37 Phase 3-A: コース 2 (slot_index=1) — フラグ ON でのみ active */}
                <div className="flex items-center gap-1">
                  <select
                    value={row.course_template_id_2 ?? ''}
                    onChange={(e) => update(wd, { course_template_id_2: e.target.value || null })}
                    disabled={disabled || !requiresMultipleStaff}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-50"
                    aria-label={`${WEEKDAY_LABELS[wd]} コース 2`}
                    title={requiresMultipleStaff ? undefined : '複数対応 OFF のため不要'}
                  >
                    <option value="">
                      {requiresMultipleStaff ? '未指定' : '複数対応 OFF のため不要'}
                    </option>
                    {requiresMultipleStaff
                      ? courseTemplates.map((tpl) => (
                          <option key={tpl.id} value={tpl.id}>
                            {tpl.label}
                          </option>
                        ))
                      : null}
                  </select>
                  {requiresMultipleStaff ? (
                    <span className="text-xs text-text-muted">コース 2</span>
                  ) : null}
                </div>
                {errors[wd] ? <span className="text-xs text-error">{errors[wd]}</span> : null}
                {!errors[wd] && warnings[wd] ? (
                  <span className="text-xs text-warning" data-testid={`row-warning-${wd}`}>
                    {warnings[wd]}
                  </span>
                ) : null}
              </>
            ) : (
              <span className="text-xs text-text-muted">訪問なし</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Sub-component: ModePanel ─────────────────────────────────────────────────

interface ModePanelProps {
  patientId: string;
  mode: PatientFixedVisitMode;
  weeklyPattern?: WeeklyPattern | null;
  readonly?: boolean;
  /** W22: 当該患者の拠点に紐付く course_templates */
  courseTemplates: CourseTemplateRead[];
  /** W37 Phase 3-A: 複数スタッフ対応患者かどうか */
  requiresMultipleStaff: boolean;
}

function ModePanel({
  patientId,
  mode,
  weeklyPattern,
  readonly,
  courseTemplates,
  requiresMultipleStaff,
}: ModePanelProps) {
  const { data: reads = [], isLoading } = useFixedVisits(patientId, mode);
  const updateMut = useUpdateFixedVisits(patientId);
  const deleteMut = useDeleteFixedVisits(patientId);
  const fromWeekMut = useApplyFromWeek(patientId);

  const [rows, setRows] = React.useState<DayRows>(emptyDayRows);
  const [fieldErrors, setFieldErrors] = React.useState<Record<number, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);

  // サーバーデータが変化したらフォームを同期
  React.useEffect(() => {
    if (!isLoading) {
      setRows(readsToDayRows(reads));
      setFieldErrors({});
      setFormError(null);
    }
  }, [reads, isLoading]);

  // ── W37 Phase 3-A: クライアント側バリデーション ─────────────────────────
  // コース 1 と コース 2 が同一 → エラー (保存ブロック)
  // コース 2 が空のまま → 警告 (保存は通す: Layer 1 寛容モード)
  const { rowErrors, rowWarnings } = React.useMemo(() => {
    const errs: Record<number, string> = {};
    const warns: Record<number, string> = {};
    if (!requiresMultipleStaff) return { rowErrors: errs, rowWarnings: warns };
    for (const [wdStr, row] of Object.entries(rows)) {
      const wd = Number(wdStr);
      if (!row.enabled) continue;
      // 同一コース選択エラー (両方が同一の UUID 文字列)
      if (
        row.course_template_id &&
        row.course_template_id_2 &&
        row.course_template_id === row.course_template_id_2
      ) {
        errs[wd] = '異なるコースを選択してください';
        continue;
      }
      // 片方未設定の警告
      if (!row.course_template_id_2) {
        warns[wd] = '2 名対応の片方未設定';
      }
    }
    return { rowErrors: errs, rowWarnings: warns };
  }, [rows, requiresMultipleStaff]);

  // ── 希望から自動生成 ──────────────────────────────────────────────────
  const handleAutoFill = () => {
    const newRows = weeklyPatternToDayRows(weeklyPattern);
    setRows(newRows);
    setFieldErrors({});
    setFormError(null);
    toast.success('希望パターンをフォームに反映しました (まだ保存されていません)');
  };

  // ── 現スケから取込 (Phase 2) ──────────────────────────────────────────
  const handleFromWeek = async () => {
    // 直近 ISO 週を計算
    const now = new Date();
    const jan4 = new Date(now.getFullYear(), 0, 4);
    const dayOfWeek = jan4.getDay() || 7;
    const startOfWeek1 = new Date(jan4);
    startOfWeek1.setDate(jan4.getDate() - dayOfWeek + 1);
    const diff = now.getTime() - startOfWeek1.getTime();
    const isoWeek = Math.floor(diff / (7 * 24 * 60 * 60 * 1000)) + 1;
    const isoYear = now.getFullYear();

    try {
      await fromWeekMut.mutateAsync({ iso_year: isoYear, iso_week: isoWeek, mode });
      toast.success('現在のスケジュールから固定枠を取り込みました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '取込に失敗しました';
      toast.error(msg);
    }
  };

  // ── 保存 ─────────────────────────────────────────────────────────────
  const handleSave = async () => {
    setFieldErrors({});
    setFormError(null);

    // W37 Phase 3-A: 同一コースエラーがあれば保存ブロック
    if (Object.keys(rowErrors).length > 0) {
      setFieldErrors(rowErrors);
      setFormError('入力エラーがあります。コース 1 と コース 2 は異なるコースを選択してください。');
      return;
    }

    const items = dayRowsToItems(rows, requiresMultipleStaff);
    const result = patientFixedVisitsBulkPutSchema.safeParse({ mode, items });

    if (!result.success) {
      const errs: Record<number, string> = {};
      let generalError = '';
      for (const issue of result.error.issues) {
        const path = issue.path;
        if (typeof path[0] === 'number' && path[1] === 'weekday') {
          const item = items[path[0]];
          if (item !== undefined) {
            errs[item.weekday] = issue.message;
          }
        } else {
          generalError = issue.message;
        }
      }
      setFieldErrors(errs);
      if (generalError) setFormError(generalError);
      return;
    }

    try {
      await updateMut.mutateAsync(result.data);
      toast.success('固定枠を保存しました');
    } catch (e) {
      const msg = e instanceof Error ? e.message : '保存に失敗しました';
      setFormError(msg);
      toast.error(`保存に失敗しました: ${msg}`);
    }
  };

  // ── リセット (DELETE) ─────────────────────────────────────────────────
  const handleDelete = async () => {
    setDeleteDialogOpen(false);
    try {
      await deleteMut.mutateAsync(mode);
      setRows(emptyDayRows());
      setFieldErrors({});
      setFormError(null);
      toast.success(`${mode === 'normal' ? '通常' : '特別週'}の固定枠を削除しました`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : '削除に失敗しました';
      toast.error(`削除に失敗しました: ${msg}`);
    }
  };

  const isBusy = updateMut.isPending || deleteMut.isPending || fromWeekMut.isPending;

  return (
    <div className="space-y-4">
      {/* W37 Phase 3-A: フラグ ON 時のみヘルプ表示 */}
      {requiresMultipleStaff && !readonly ? (
        <Alert>
          <AlertTitle>2 名体制 (複数スタッフ対応) 患者です</AlertTitle>
          <AlertDescription>
            同時刻に異なるコースを 2 つ設定する必要があります。 「コース 1」と「コース
            2」を別々に選択してください。 片方のみの場合は割当ロジック (Layer 1)
            が片方のみで補完しますが、 運用上は両方設定することを推奨します。
          </AlertDescription>
        </Alert>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-text-muted">読み込み中...</p>
      ) : readonly ? (
        // W37 Phase 3-D: 詳細画面など読み取り専用時はテキスト表示コンポーネントを使用
        <ReadOnlyWeekGrid
          rows={rows}
          courseTemplates={courseTemplates}
          requiresMultipleStaff={requiresMultipleStaff}
        />
      ) : (
        <WeekGrid
          rows={rows}
          onChange={setRows}
          disabled={isBusy}
          // W37 Phase 3-A: ライブのコース重複エラー (rowErrors) と
          // 保存時の zod エラー (fieldErrors) をマージしてユーザに即時表示する.
          // fieldErrors を後置きすることで保存時の重複エラーが優先される.
          errors={{ ...rowErrors, ...fieldErrors }}
          warnings={rowWarnings}
          courseTemplates={courseTemplates}
          requiresMultipleStaff={requiresMultipleStaff}
        />
      )}

      {formError ? <p className="text-xs text-error">{formError}</p> : null}

      {!readonly && (
        <div className="flex flex-wrap gap-2 pt-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleAutoFill}
            disabled={isBusy}
          >
            希望から自動生成
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void handleFromWeek()}
            disabled={isBusy || fromWeekMut.isPending}
          >
            {fromWeekMut.isPending ? '取込中...' : '現在のスケジュールから取込'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="text-error hover:bg-error/10"
            onClick={() => setDeleteDialogOpen(true)}
            disabled={isBusy}
          >
            リセット (全削除)
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => void handleSave()}
            disabled={isBusy || isLoading}
          >
            {updateMut.isPending ? '保存中...' : '保存'}
          </Button>
        </div>
      )}

      {/* 削除確認ダイアログ */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent aria-describedby="delete-dialog-desc">
          <DialogHeader>
            <DialogTitle>固定枠を削除しますか？</DialogTitle>
          </DialogHeader>
          <p id="delete-dialog-desc" className="text-sm text-text-secondary">
            {mode === 'normal' ? '通常週' : '特別週'}の固定枠をすべて削除します。
            この操作は元に戻せません。
          </p>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDeleteDialogOpen(false)}>
              キャンセル
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleDelete()}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? '削除中...' : '削除する'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Main export ──────────────────────────────────────────────────────────────

export interface PatientFixedVisitsPanelProps {
  /** 対象患者の ID */
  patientId: string;
  /** 患者の週間訪問希望パターン (希望から自動生成 ボタンで使用) */
  weeklyPattern?: WeeklyPattern | null;
  /**
   * W22: 患者の primary_office_id。
   * 拠点に紐付く course_templates を取得するために使用。
   * null の場合はコース選択肢が空 (未指定のみ)。
   */
  primaryOfficeId?: string | null;
  /**
   * W26: true のとき強制的に読み取り専用モードにする。
   * 患者詳細ページからの埋め込みで使用。
   * セッションロールによる readonly 判定を上書きする。
   */
  readOnly?: boolean;
  /**
   * W37 Phase 3-A: 患者の `requires_multiple_staff` フラグ.
   * true でコース 2 (slot_index=1) セレクタが enable になり、
   * 保存時に slot 0/1 のペアを送る (片方のみでも寛容モードで保存可能).
   * false の場合は従来どおり 1 セレクタ (slot_index=0) のみ.
   */
  requiresMultipleStaff?: boolean;
}

export function PatientFixedVisitsPanel({
  patientId,
  weeklyPattern,
  primaryOfficeId,
  readOnly,
  requiresMultipleStaff = false,
}: PatientFixedVisitsPanelProps) {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const readonly = readOnly === true || (role !== 'admin' && role !== 'manager');

  // W22: 拠点の course_templates を取得
  const { data: courseTemplates = [] } = useCourseTemplates({
    office_id: primaryOfficeId ?? null,
  });

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-bold text-text-primary">
          週間訪問パターン (固定枠)
        </h2>
        {readonly && (
          <span className="text-xs text-text-muted bg-bg-muted rounded px-2 py-0.5">閲覧のみ</span>
        )}
      </div>

      <Tabs defaultValue="normal">
        <TabsList>
          {PATIENT_FIXED_VISIT_MODES.map((m) => (
            <TabsTrigger key={m} value={m}>
              {m === 'normal' ? '通常' : '特別週'}
            </TabsTrigger>
          ))}
        </TabsList>

        {PATIENT_FIXED_VISIT_MODES.map((m) => (
          <TabsContent key={m} value={m}>
            <ModePanel
              patientId={patientId}
              mode={m}
              weeklyPattern={weeklyPattern}
              readonly={readonly}
              courseTemplates={courseTemplates}
              requiresMultipleStaff={requiresMultipleStaff}
            />
          </TabsContent>
        ))}
      </Tabs>
    </Card>
  );
}
