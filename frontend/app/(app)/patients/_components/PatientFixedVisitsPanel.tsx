/**
 * PatientFixedVisitsPanel (W9-FE1 Phase 3 / W22 拡張 / W37 Phase 3-A).
 *
 * 患者編集画面に「固定訪問パターン」セクションを提供するコンポーネント。
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
import { useQueries } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

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
import { PushPin } from '@/components/ui/push-pin';

import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
  toastFixedVisitWarnings,
} from '@/lib/queries/patient_fixed_visits';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import { useOffices } from '@/lib/queries/offices';
import {
  patientFixedVisitsBulkPutSchema,
  PATIENT_FIXED_VISIT_MODES,
  type Movability,
  type PatientFixedVisitMode,
  type PatientFixedVisitV2Base,
  type PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { Office } from '@/lib/schemas/office';
import type { WeeklyPattern } from '@/lib/schemas/patient';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';

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

/**
 * P2-C: 可動域 (movability) セレクタの選択肢. 提案の可否を表す (§1.1).
 *   unknown(未設定・保守的) / time_flexible(時刻変更可) / day_flexible(曜日変更可) / locked(完全固定).
 * is_pinned=true の行は locked 固定表示で変更不可 (is_pinned ⇒ locked; BE V6 が矯正).
 */
const MOVABILITY_OPTIONS: ReadonlyArray<{ value: Movability; label: string }> = [
  { value: 'unknown', label: '未設定' },
  { value: 'time_flexible', label: '時刻変更可' },
  { value: 'day_flexible', label: '曜日変更可' },
  { value: 'locked', label: '完全固定' },
];

// ─── Types ───────────────────────────────────────────────────────────────────

/**
 * フォーム内部で使う 1 曜日行の状態。
 *
 * W37 Phase 3-A:
 *   - course_template_id   : コース 1 (slot_index=0) 用
 *   - course_template_id_2 : コース 2 (slot_index=1) 用 (requires_multiple_staff=true のみ使用)
 *
 * 開始時刻 / 所要時間は slot 0/1 で共通 (BE 仕様: 同曜日・同時刻・同 duration の 2 行).
 *
 * Phase G-21:
 *   - is_pinned: 「完全固定」フラグ. true で Layer 2 が visit を動かさない.
 *     slot 0/1 で共通で 1 値 (= 同曜日 2 行は同時に pin/unpin される).
 */
interface DayRow {
  enabled: boolean;
  start_time: string;
  duration_min: number;
  /** W22: コーステンプレート ID (null = 未指定). slot_index=0 用. */
  course_template_id: string | null;
  /** W37 Phase 3-A: コース 2 (slot_index=1) 用. requires_multiple_staff=true でのみ有効. */
  course_template_id_2: string | null;
  /**
   * Phase E-5 (項目 ⑥B): サブ拠点 ID (null = 主担当拠点のみ).
   * slot 0/1 共通で 1 値 (主担当 + サブ拠点の対は 1 row 単位で表現).
   */
  sub_office_id: string | null;
  /** Phase G-21: 完全固定フラグ (true = visit 移動禁止). */
  is_pinned: boolean;
  /** P2-C: 可動域 (提案の可否). is_pinned=true ⇒ locked 扱い. slot 0/1 共通で 1 値. */
  movability: Movability;
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
    sub_office_id: null,
    is_pinned: false,
    movability: 'unknown',
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
        // Phase E-5: sub_office_id は slot 0 を優先 (slot 0/1 で原則一致するが、
        // 不整合があれば slot 0 を採用).
        sub_office_id: r.sub_office_id ?? current.sub_office_id,
        // Phase G-21: is_pinned は slot 0 を優先. どちらかが true なら行全体を pin 扱い.
        is_pinned: r.is_pinned === true || current.is_pinned,
        // P2-C: movability は slot 0 を優先 (旧 BE で未返却なら unknown フォールバック).
        movability: r.movability ?? current.movability ?? 'unknown',
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
        // Phase E-5: slot 0 が未設定の場合のみ slot 1 の sub_office_id を採用.
        sub_office_id: current.sub_office_id ?? r.sub_office_id ?? null,
        // Phase G-21: slot 1 でも pin が立っていれば反映.
        is_pinned: r.is_pinned === true || current.is_pinned,
        // P2-C: slot 0 が未設定 (unknown) の場合のみ slot 1 の movability を採用.
        movability:
          current.movability && current.movability !== 'unknown'
            ? current.movability
            : (r.movability ?? current.movability ?? 'unknown'),
      };
    }
  }
  return rows;
}

/**
 * DayRows を bulk PUT items に変換する。
 *
 * W37 Phase 3-A / W37 hotfix M-4:
 *   - requires_multiple_staff=false: 各曜日 1 行 (slot_index=0)
 *   - requires_multiple_staff=true :
 *       (a) コース 1 / コース 2 両方設定 → slot 0/1 の 2 行
 *       (b) コース 1 のみ設定           → slot 0 のみ 1 行 (寛容モード)
 *       (c) コース 1 空 + コース 2 のみ → コース 2 を slot 0 に "格上げ" して 1 行
 *           (= 旧 BE で slot 0=NULL のまま保存され、Layer 1 が office フォールバック
 *           template で意図しない visit を生成する事故を防ぐ)
 *       (d) 両方空                       → slot 0 のみ (course_template_id=null) 1 行
 *           (= Layer 1 寛容モード経路; C-2 修正で「片側のみ → 保留扱い」になる)
 */
function dayRowsToItems(rows: DayRows, requiresMultipleStaff: boolean): PatientFixedVisitV2Base[] {
  const items: PatientFixedVisitV2Base[] = [];
  for (const [weekdayStr, row] of Object.entries(rows)) {
    if (!row.enabled) continue;
    const weekday = Number(weekdayStr);

    // W37 hotfix M-4: コース 1 空 + コース 2 のみ設定の場合、コース 2 を slot 0 に格上げ
    const slot0Course = row.course_template_id ?? null;
    const slot1Course = requiresMultipleStaff ? (row.course_template_id_2 ?? null) : null;
    const promote = requiresMultipleStaff && !slot0Course && !!slot1Course;

    const effectiveSlot0Course = promote ? slot1Course : slot0Course;

    // P2-C: is_pinned=true ⇒ movability='locked' (含意整合; BE V6 も矯正するが FE でも一致させる).
    const effectiveMovability: Movability = row.is_pinned ? 'locked' : row.movability;

    items.push({
      weekday,
      start_time: row.start_time,
      duration_min: row.duration_min,
      course_template_id: effectiveSlot0Course,
      slot_index: 0,
      // Phase E-5 (項目 ⑥B): サブ拠点 ID (slot 0/1 共通).
      sub_office_id: row.sub_office_id,
      // Phase G-21: 完全固定フラグ (slot 0/1 共通).
      is_pinned: row.is_pinned,
      // P2-C: 可動域 (slot 0/1 共通). 漏らすと保存のたび unknown に戻る (§1.3 運搬).
      movability: effectiveMovability,
    });

    // slot 1 は requires_multiple_staff=true かつ
    //   - promote=false (= コース 1 が設定済) かつ
    //   - course_template_id_2 が設定済み
    // のときのみ送る. promote 経路では slot 1 を送らない (1 行のみ).
    if (requiresMultipleStaff && !promote && slot1Course) {
      items.push({
        weekday,
        start_time: row.start_time,
        duration_min: row.duration_min,
        course_template_id: slot1Course,
        slot_index: 1,
        // Phase E-5: slot 0 と同じ sub_office を継承.
        sub_office_id: row.sub_office_id,
        // Phase G-21: slot 0 と同じ pin 状態を継承.
        is_pinned: row.is_pinned,
        // P2-C: slot 0 と同じ可動域を継承.
        movability: effectiveMovability,
      });
    }
  }
  return items;
}

/**
 * 患者の weekly_pattern (希望パターン) から DayRows を生成する.
 *
 * Phase G-21 T4 reviewer C3 / M2:
 *   - 既存の DayRows (= current) を受け取り、 `preferred_weekdays` に含まれる
 *     曜日のみ希望時刻 + duration で上書きする. 含まれない曜日は既存値を維持する
 *     (旧実装は強制的に空 DayRows で塗り潰していたため既存設定が消失していた).
 *   - 既存 row の `is_pinned`/`course_template_id`/`course_template_id_2`/
 *     `sub_office_id` は merge 時にそのまま保持する (= 完全固定設定の消失防止).
 *   - `preferred_weekdays` 外の曜日は **何もしない**.
 */
export function weeklyPatternToDayRows(
  pattern: WeeklyPattern | null | undefined,
  current?: DayRows,
): DayRows {
  // base は current の shallow copy (= 引数なしなら従来通り全曜日 空 row).
  const rows: DayRows = current ? { ...current } : emptyDayRows();
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
    if (idx === undefined) continue;
    const existing = rows[idx] ?? emptyDayRow();
    rows[idx] = {
      // enabled は確実に true (= 希望曜日であることを反映)
      enabled: true,
      // start_time / duration_min は希望から上書き (= 「希望から自動生成」 の主目的)
      start_time: startTime,
      duration_min: Math.max(1, Math.min(480, duration)),
      // 既存設定 (コース選択 / サブ拠点 / 完全固定) は維持
      course_template_id: existing.course_template_id,
      course_template_id_2: existing.course_template_id_2,
      sub_office_id: existing.sub_office_id,
      is_pinned: existing.is_pinned,
      // P2-C: 可動域も既存設定を保持 (希望反映で消さない).
      movability: existing.movability,
    };
  }
  return rows;
}

/**
 * Phase E-5 (項目 ⑥B): row.sub_office_id に応じた course_templates を返す.
 * sub_office_id が未指定なら patient.primary_office_id ベースの templates をそのまま返す.
 */
function resolveRowCourseTemplates(
  row: DayRow,
  primaryCourseTemplates: CourseTemplateRead[],
  subOfficeCourseTemplates: CourseTemplateRead[],
): CourseTemplateRead[] {
  if (row.sub_office_id) return subOfficeCourseTemplates;
  return primaryCourseTemplates;
}

// ─── Sub-component: ReadOnlyWeekGrid ─────────────────────────────────────────

/**
 * W37 Phase 3-D: 読み取り専用の固定訪問パターン表示。
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
  /** Phase E-5: 全 office (サブ拠点ラベル解決用). */
  offices?: Office[];
}

function ReadOnlyWeekGrid({
  rows,
  courseTemplates,
  requiresMultipleStaff,
  offices,
}: ReadOnlyWeekGridProps) {
  /** course_template_id → label の逆引きマップ */
  const labelMap = React.useMemo(() => {
    const m: Record<string, string> = {};
    for (const tpl of courseTemplates) {
      m[tpl.id] = tpl.label;
    }
    return m;
  }, [courseTemplates]);

  // Phase E-5: office_id → name の逆引きマップ.
  const officeLabelMap = React.useMemo(() => {
    const m: Record<string, string> = {};
    for (const o of offices ?? []) {
      m[o.id] = o.name;
    }
    return m;
  }, [offices]);

  const courseLabel = (id: string | null): string => (id ? (labelMap[id] ?? id) : '--');

  return (
    <div className="space-y-1">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? emptyDayRow();
        const pinnedHighlightCls = row.enabled && row.is_pinned ? 'bg-yellow-50' : '';
        return (
          <div
            key={wd}
            data-pinned={row.enabled && row.is_pinned ? 'true' : undefined}
            className={`flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border-default px-3 py-2 text-sm ${pinnedHighlightCls}`}
            data-testid={`ro-row-${wd}`}
          >
            <span className="w-5 shrink-0 text-center font-medium text-text-secondary">
              {WEEKDAY_LABELS[wd]}
            </span>
            {row.enabled ? (
              <>
                <span className="text-text-primary tnum">{row.start_time}</span>
                <span className="text-text-muted">{row.duration_min} 分</span>
                {/* Phase G-21 / #P4-B: ピン留め行はピン留めバッジを併記. */}
                {row.is_pinned ? (
                  <span
                    className="inline-flex items-center gap-0.5 rounded bg-yellow-200/70 px-1.5 py-0.5 text-xs font-medium text-yellow-800"
                    data-testid={`ro-pin-${wd}`}
                    aria-label="ピン留め"
                    title="ピン留め"
                  >
                    <PushPin className="h-3 w-3 text-yellow-700" />
                    ピン留め
                  </span>
                ) : null}
                {/* Phase E-5: サブ拠点が設定されていればバッジで明示. */}
                {row.sub_office_id ? (
                  <span
                    className="rounded bg-brand-primary/10 px-1.5 py-0.5 text-xs font-medium text-brand-primary"
                    data-testid={`ro-sub-office-${wd}`}
                  >
                    サブ拠点: {officeLabelMap[row.sub_office_id] ?? row.sub_office_id}
                  </span>
                ) : null}
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
  /** Phase E-5: 全 office 一覧 (サブ拠点 selector 用). 主担当拠点は除外して表示. */
  offices: Office[];
  /** Phase E-5: 患者の主担当拠点 ID (=サブ拠点 selector で除外する office). */
  primaryOfficeId: string | null | undefined;
  /** Phase E-5: 各 row の sub_office_id に対応する course_templates を取得する関数. */
  getSubOfficeCourseTemplates: (subOfficeId: string | null) => CourseTemplateRead[];
}

function WeekGrid({
  rows,
  onChange,
  disabled,
  errors,
  warnings,
  courseTemplates,
  requiresMultipleStaff,
  offices,
  primaryOfficeId,
  getSubOfficeCourseTemplates,
}: WeekGridProps) {
  const update = (weekday: number, patch: Partial<DayRow>) => {
    const current = rows[weekday] ?? emptyDayRow();
    onChange({ ...rows, [weekday]: { ...current, ...patch } as DayRow });
  };

  // Phase E-5: 主担当拠点以外を「サブ拠点候補」として並べる. 0 件なら selector を出さない.
  const subOfficeCandidates = offices.filter((o) => o.id !== primaryOfficeId);

  return (
    <div className="space-y-2">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? emptyDayRow();
        // Phase E-5: row.sub_office_id に応じてコース選択肢を切り替える.
        const rowCourseTemplates = resolveRowCourseTemplates(
          row,
          courseTemplates,
          row.sub_office_id ? getSubOfficeCourseTemplates(row.sub_office_id) : [],
        );
        // Phase G-21: 完全固定行は黄色背景で強調する (= リスト/テーブルと統一).
        const pinnedHighlightCls = row.enabled && row.is_pinned ? 'bg-yellow-50' : '';
        return (
          <div
            key={wd}
            data-pinned={row.enabled && row.is_pinned ? 'true' : undefined}
            data-testid={`pfv-row-${wd}`}
            className={`flex flex-wrap items-center gap-3 rounded-md border border-border-default px-3 py-2 ${pinnedHighlightCls}`}
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
                {/* Phase E-5 (項目 ⑥B): サブ拠点 selector. 主担当拠点以外の候補がある場合のみ表示. */}
                {subOfficeCandidates.length > 0 ? (
                  <div className="flex items-center gap-1">
                    <select
                      value={row.sub_office_id ?? ''}
                      onChange={(e) => {
                        const newSub = e.target.value || null;
                        // サブ拠点を切り替えたら course 選択は一旦リセット
                        // (前 office の course を別 office に流用すると 422 になるため).
                        update(wd, {
                          sub_office_id: newSub,
                          course_template_id: null,
                          course_template_id_2: null,
                        });
                      }}
                      disabled={disabled}
                      className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                      aria-label={`${WEEKDAY_LABELS[wd]} サブ拠点`}
                      data-testid={`sub-office-select-${wd}`}
                    >
                      <option value="">主担当拠点</option>
                      {subOfficeCandidates.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.name}
                        </option>
                      ))}
                    </select>
                    <span className="text-xs text-text-muted">拠点</span>
                  </div>
                ) : null}
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
                    {rowCourseTemplates.map((tpl) => (
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
                      ? rowCourseTemplates.map((tpl) => (
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
                {/* Phase G-21 / #P4-B: ピン留め checkbox (= 該当行の is_pinned). */}
                <label
                  className="flex items-center gap-1 text-xs text-text-secondary"
                  data-testid={`pfv-pin-label-${wd}`}
                >
                  <Checkbox
                    checked={row.is_pinned}
                    onCheckedChange={(c) => {
                      const nextPinned = c === true;
                      // P4-C: ピン留めを OFF にしたとき、ピン由来ロック (movability='locked')
                      // なら 'unknown' に解放する. ON 時は従来通り (保存時に V6 が locked 化).
                      const patch: Partial<DayRow> = { is_pinned: nextPinned };
                      if (!nextPinned && row.movability === 'locked') {
                        patch.movability = 'unknown';
                      }
                      update(wd, patch);
                    }}
                    disabled={disabled}
                    aria-label={`${WEEKDAY_LABELS[wd]} ピン留め`}
                    data-testid={`pfv-pin-checkbox-${wd}`}
                  />
                  {row.is_pinned ? (
                    <PushPin className="h-3.5 w-3.5 text-yellow-700" aria-hidden />
                  ) : null}
                  <span>ピン留め</span>
                </label>
                {/* P2-C / #P4-B: 可動域 (提案の可否) セレクタ.
                    - ピン留め行 (is_pinned=true) は locked 固定 → 「ピン留め（変更不可）」静的表示.
                    - それ以外は既定で畳まれた「詳細設定」disclosure に格下げ (通常運用では非表示). */}
                <div className="flex items-center gap-1" data-testid={`pfv-movability-wrap-${wd}`}>
                  {row.is_pinned ? (
                    <span
                      className="inline-flex items-center gap-0.5 rounded border border-border-default bg-bg-muted px-2 py-1 text-xs text-text-muted"
                      data-testid={`pfv-movability-locked-${wd}`}
                    >
                      <PushPin className="h-3 w-3 text-yellow-700" aria-hidden />
                      ピン留め（変更不可）
                    </span>
                  ) : (
                    <details
                      className="text-xs text-text-muted"
                      data-testid={`pfv-movability-details-${wd}`}
                    >
                      <summary className="cursor-pointer select-none text-text-secondary">
                        詳細設定
                      </summary>
                      <div className="mt-1 flex items-center gap-1">
                        <select
                          value={row.movability}
                          onChange={(e) => update(wd, { movability: e.target.value as Movability })}
                          disabled={disabled}
                          className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary"
                          aria-label={`${WEEKDAY_LABELS[wd]} 可動域`}
                          data-testid={`pfv-movability-select-${wd}`}
                        >
                          {MOVABILITY_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                        <span className="text-xs text-text-muted">可動域</span>
                      </div>
                    </details>
                  )}
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
  /**
   * Wave U-2: 保存時に今週へ即反映する対象 ISO 週 (change_scope='pattern_and_week').
   * 週文脈が無い呼び出し (患者マスタ直) では現在の ISO 週を算出して使う。
   */
  isoYear?: number;
  isoWeek?: number;
  /** W22: 当該患者の拠点に紐付く course_templates */
  courseTemplates: CourseTemplateRead[];
  /** W37 Phase 3-A: 複数スタッフ対応患者かどうか */
  requiresMultipleStaff: boolean;
  /** Phase E-5 (項目 ⑥B): 全拠点 (サブ拠点 selector 用) */
  offices: Office[];
  /** Phase E-5: 主担当拠点 ID */
  primaryOfficeId: string | null | undefined;
  /** Phase E-5: sub_office_id → course_templates の lookup */
  getSubOfficeCourseTemplates: (subOfficeId: string | null) => CourseTemplateRead[];
}

function ModePanel({
  patientId,
  mode,
  weeklyPattern,
  readonly,
  isoYear,
  isoWeek,
  courseTemplates,
  requiresMultipleStaff,
  offices,
  primaryOfficeId,
  getSubOfficeCourseTemplates,
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

  // ── W37 Phase 3-A / hotfix M-4: クライアント側バリデーション ─────────────
  // コース 1 と コース 2 が同一 → エラー (保存ブロック)
  // コース 1 / コース 2 のどちらかが空 → 警告 (保存は通す: Layer 1 寛容モード)
  //   - コース 1 空 + コース 2 のみの場合は M-4 修正で「コース 2 を slot 0 に格上げ」
  //     して 1 行送信されるが、ユーザーには「2 名運用には片方が未設定」と気付かせるため
  //     警告は維持する (C-2 修正により Layer 1 で保留扱いになるため運用上も無害).
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
      // 片方未設定の警告 (どちらが欠けていても 1 行送信になり 2 名運用が成立しない)
      if (!row.course_template_id || !row.course_template_id_2) {
        warns[wd] = '2 名対応の片方未設定';
      }
    }
    return { rowErrors: errs, rowWarnings: warns };
  }, [rows, requiresMultipleStaff]);

  // ── 希望から自動生成 ──────────────────────────────────────────────────
  // Phase G-21 T4 reviewer C3 / M2: 既存 rows を base に merge する (= 既存の
  // is_pinned / コース選択 / サブ拠点 を保持). preferred_weekdays に無い曜日も
  // 既存の enabled/設定をそのまま残す.
  const handleAutoFill = () => {
    const newRows = weeklyPatternToDayRows(weeklyPattern, rows);
    setRows(newRows);
    setFieldErrors({});
    setFormError(null);
    toast.success(
      '希望パターンをフォームに反映しました (まだ保存されていません). 既存の完全固定・コース選択は保持されています',
    );
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
    // Wave U-2 (設計 §2.1): 固定枠編集の既定は A (型 + 今週即反映)。choice UI は出さない
    //   (編集画面の文脈上、型を編集する意図が明確なため)。
    //   週文脈が無い呼び出し (患者マスタ直) では現在の ISO 週を算出して使う。
    //   'special' モードは今週が特別週とは限らないため今週反映は付けない (normal のみ)。
    const applyToWeek = mode === 'normal';
    const cur = isoWeekFromLocalDate(new Date());
    const effIsoYear = isoYear ?? cur.isoYear;
    const effIsoWeek = isoWeek ?? cur.isoWeek;
    const result = patientFixedVisitsBulkPutSchema.safeParse(
      applyToWeek
        ? {
            mode,
            items,
            change_scope: 'pattern_and_week',
            iso_year: effIsoYear,
            iso_week: effIsoWeek,
          }
        : { mode, items },
    );

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
      const res = await updateMut.mutateAsync(result.data);
      // Wave U-2: A 経路 (normal) は今週にも反映。week_sync 欠落時は U-1 と同じ警告。
      if (applyToWeek) {
        if (res?.week_sync == null) {
          toast.warning(
            '固定枠を保存しましたが、今週のスケジュールへの反映は行われませんでした。' +
              '「週を生成」を再実行すると反映されます',
          );
        } else {
          toast.success('固定枠を保存し、今週のスケジュールにも反映しました');
        }
      } else {
        toast.success('固定枠を保存しました');
      }
      // P0-2 Commit 3: 再検証 warnings (時間衝突 / 昼休み / 容量) があれば警告表示。
      // 空なら従来どおり success のみ (挙動不変)。
      toastFixedVisitWarnings(res?.warnings);
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
          offices={offices}
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
          offices={offices}
          primaryOfficeId={primaryOfficeId}
          getSubOfficeCourseTemplates={getSubOfficeCourseTemplates}
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

/**
 * Phase E-5 (項目 ⑥B): 複数の office_id について course_templates を並列 fetch して
 * Map<office_id, CourseTemplateRead[]> を返す. useCourseTemplates が単一 office 用
 * のため、サブ拠点 selector の選択肢切替で N+1 fetch を避けるために事前一括化する.
 */
function useSubOfficeCourseTemplatesMap(officeIds: string[]): Map<string, CourseTemplateRead[]> {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const queries = useQueries({
    queries: officeIds.map((oid) => ({
      queryKey: ['course-templates', 'list', oid],
      enabled: status === 'authenticated',
      queryFn: () =>
        fetcher<CourseTemplateRead[]>(
          `/api/v1/course-templates?office_id=${encodeURIComponent(oid)}`,
          { accessToken, refreshToken },
        ),
    })),
  });
  return React.useMemo(() => {
    const m = new Map<string, CourseTemplateRead[]>();
    officeIds.forEach((oid, i) => {
      const q = queries[i];
      m.set(oid, q?.data ?? []);
    });
    return m;
    // queries は object 配列なので reference 比較. data の更新時のみ再計算したい:
    // queries.map((q) => q.data) を deps にする.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [officeIds.join(','), queries.map((q) => q.data).join('|')]);
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
  /**
   * Wave U-2: 保存時に今週へ即反映する対象 ISO 週 (change_scope='pattern_and_week').
   * 週を表示中のダイアログ (PatientScheduleDetailDialog) 経由では表示中の週を配線する。
   * 患者マスタ直の呼び出しでは省略 (ModePanel が現在の ISO 週を算出する)。
   */
  isoYear?: number;
  isoWeek?: number;
}

export function PatientFixedVisitsPanel({
  patientId,
  weeklyPattern,
  primaryOfficeId,
  readOnly,
  requiresMultipleStaff = false,
  isoYear,
  isoWeek,
}: PatientFixedVisitsPanelProps) {
  const { data: session } = useSession();
  const role = session?.user?.role;
  const readonly = readOnly === true || (role !== 'admin' && role !== 'manager');

  // W22: 拠点の course_templates を取得
  const { data: courseTemplates = [] } = useCourseTemplates({
    office_id: primaryOfficeId ?? null,
  });

  // Phase E-5 (項目 ⑥B): 全拠点を一括取得 (サブ拠点 selector 用).
  const { offices } = useOffices();

  // Phase E-5: サブ拠点ごとの course_templates を on-demand fetch する.
  // 各拠点の templates は useCourseTemplates({office_id}) で個別 cache.
  // 実運用では拠点 2 つしかない (稲毛 / 都賀) ため、両方を unconditionally fetch しても
  // 1 拠点分のオーバーヘッドに過ぎない. 主担当拠点とは別の各 office を一括 fetch する.
  const subOfficeIds = React.useMemo(
    () => offices.map((o) => o.id).filter((id) => id !== primaryOfficeId),
    [offices, primaryOfficeId],
  );
  const subOfficeTemplatesById = useSubOfficeCourseTemplatesMap(subOfficeIds);
  const getSubOfficeCourseTemplates = React.useCallback(
    (subOfficeId: string | null): CourseTemplateRead[] => {
      if (!subOfficeId) return [];
      return subOfficeTemplatesById.get(subOfficeId) ?? [];
    },
    [subOfficeTemplatesById],
  );

  return (
    <Card className="p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-lg font-bold text-text-primary">固定訪問パターン</h2>
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
              isoYear={isoYear}
              isoWeek={isoWeek}
              courseTemplates={courseTemplates}
              requiresMultipleStaff={requiresMultipleStaff}
              offices={offices}
              primaryOfficeId={primaryOfficeId ?? null}
              getSubOfficeCourseTemplates={getSubOfficeCourseTemplates}
            />
          </TabsContent>
        ))}
      </Tabs>
    </Card>
  );
}
