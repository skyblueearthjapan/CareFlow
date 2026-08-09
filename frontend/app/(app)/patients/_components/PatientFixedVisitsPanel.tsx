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
import { apiErrorMessage } from '@/lib/api/errorMessage';
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
import {
  SERVICE_MINUTES_OPTIONS,
  DEFAULT_SERVICE_MINUTES,
  type WeeklyPattern,
} from '@/lib/schemas/patient';
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

// 所要時間の選択肢は希望訪問パターン (WeeklyPatternEditor) と**完全に同一**の
// 5 分刻み 15〜180 分にする (PO 指示 2026-08-09: 希望側に合わせる)。
// 独自の刻み (旧 15/30/45/60…) は 35 分が無く、35 分の行を開くと未選択表示に
// なり 30/45 へ化けるドリフトの真因だった。ソースを 1 つにして再発を防ぐ。

/** 希望訪問パターン未設定時の基本訪問時間 (分)。希望側の既定値と同じソース。 */
const DEFAULT_BASE_MINUTES = DEFAULT_SERVICE_MINUTES;

/**
 * その患者の「基本の訪問時間」(PO 決定 2026-08-09)。
 * 希望訪問パターンの service_minutes を 1 つのベースとし、固定訪問パターンの
 * 所要時間はこの値をデフォルトにする。変えるのはイレギュラー対応のみ。
 */
function baseServiceMinutes(pattern: WeeklyPattern | null | undefined): number {
  const raw = pattern?.service_minutes;
  if (typeof raw === 'number' && Number.isFinite(raw) && raw >= 1) {
    return Math.min(480, Math.floor(raw));
  }
  return DEFAULT_BASE_MINUTES;
}

/**
 * 所要時間セレクトの選択肢。標準の刻みに「基本時間」と「現在値」を必ず含める。
 * 現在値を含めるのは、選択肢に無い値 (取込由来の 65 分など) を開いたときに
 * 未選択表示のまま黙って別の値へ化けるのを防ぐため (可動域の旧値と同じ流儀)。
 */
function durationOptionsFor(baseMin: number, currentMin: number): number[] {
  return Array.from(new Set([...SERVICE_MINUTES_OPTIONS, baseMin, currentMin])).sort(
    (a, b) => a - b,
  );
}

/**
 * 旧 4 段階時代の値のラベル (PO 決定 2026-08-08 で 2 段階へ整理).
 *
 * 本番の利用実績は time_flexible / day_flexible とも **0 件** だったため移行は
 * 不要だったが、万一これらの値が入っている行を開いたときに、選択肢に無いせいで
 * 黙って別の値へ化けることがないよう、表示用のラベルだけ残す。
 * 一度でも編集されれば 2 段階のいずれかに収束する。
 */
const LEGACY_MOVABILITY_LABELS: Record<string, string> = {
  time_flexible: '時刻変更可（旧設定）',
  day_flexible: '曜日変更可（旧設定）',
};

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
 * 統合 (PO 決定 2026-08-09):
 *   - 完全固定 = movability='locked' の 1 概念 (旧「ピン留め」と統合).
 *     is_pinned は非推奨ミラー (送信時に movability から導出).
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
  /** 完全固定の非推奨ミラー (= movability==='locked'). 送信時に導出する. */
  is_pinned: boolean;
  /**
   * 可動域 = 完全固定の正典. 'locked' で全エンジン (自動最適化・週生成・提案 apply)
   * がこの枠を動かさない. 人手の編集は常に可 (PO 決定 2026-08-09). slot 0/1 共通で 1 値.
   */
  movability: Movability;
  /** この曜日に対応する既存 PFV 行の id (slot 0/1 で最大 2 件). 未保存の行は空配列. */
  pfv_ids: string[];
}

type DayRows = Record<number, DayRow>;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function emptyDayRow(baseMin: number = DEFAULT_BASE_MINUTES): DayRow {
  return {
    enabled: false,
    start_time: '09:00',
    // 基本の訪問時間 (希望訪問パターン) をデフォルトにする (PO 決定 2026-08-09)。
    duration_min: baseMin,
    course_template_id: null,
    course_template_id_2: null,
    sub_office_id: null,
    is_pinned: false,
    movability: 'unknown',
    pfv_ids: [],
  };
}

function emptyDayRows(baseMin: number = DEFAULT_BASE_MINUTES): DayRows {
  const rows: DayRows = {};
  for (let i = 0; i < 7; i++) {
    rows[i] = emptyDayRow(baseMin);
  }
  return rows;
}

/**
 * BE が返した PatientFixedVisitV2Read[] を曜日 × slot で 1 行にマージする。
 *
 * W37: slot_index=0 → course_template_id, slot_index=1 → course_template_id_2.
 * start_time / duration_min は slot 0 を優先し、slot 0 が無ければ slot 1 を使う。
 */
function readsToDayRows(
  reads: PatientFixedVisitV2Read[],
  baseMin: number = DEFAULT_BASE_MINUTES,
): DayRows {
  const rows = emptyDayRows(baseMin);
  for (const r of reads) {
    if (r.weekday < 0 || r.weekday > 6) continue;
    const slot = r.slot_index ?? 0;
    const current = rows[r.weekday] ?? emptyDayRow(baseMin);
    // start_time は HH:MM:SS の場合もあるので先頭 5 文字に切り詰める
    const startTime = r.start_time.slice(0, 5);
    // この曜日の既存 PFV id を slot 0/1 とも集める (参照用).
    const pfvIds = current.pfv_ids.includes(r.id) ? current.pfv_ids : [...current.pfv_ids, r.id];
    if (slot === 0) {
      rows[r.weekday] = {
        ...current,
        pfv_ids: pfvIds,
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
        pfv_ids: pfvIds,
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

    // 統合 (PO 決定 2026-08-09): 完全固定 = movability='locked' の 1 概念。
    // is_pinned は非推奨ミラー (BE も PUT 時にサーバ側で同期する) だが、
    // FE からも一貫した値を送る。
    const effectiveMovability: Movability = row.movability;
    const mirroredPinned = effectiveMovability === 'locked';

    items.push({
      weekday,
      start_time: row.start_time,
      duration_min: row.duration_min,
      course_template_id: effectiveSlot0Course,
      slot_index: 0,
      // Phase E-5 (項目 ⑥B): サブ拠点 ID (slot 0/1 共通).
      sub_office_id: row.sub_office_id,
      // 完全固定の非推奨ミラー (slot 0/1 共通).
      is_pinned: mirroredPinned,
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
        // 完全固定の非推奨ミラー (slot 0 と同値).
        is_pinned: mirroredPinned,
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
  baseMin: number = DEFAULT_BASE_MINUTES,
): DayRows {
  // base は current の shallow copy (= 引数なしなら従来通り全曜日 空 row).
  const rows: DayRows = current ? { ...current } : emptyDayRows(baseMin);
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
    const existing = rows[idx] ?? emptyDayRow(baseMin);
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
      // 既存 PFV への参照 (ピン留め PATCH 用) も維持する.
      pfv_ids: existing.pfv_ids,
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
  baseMinutes = DEFAULT_BASE_MINUTES,
}: ReadOnlyWeekGridProps & { baseMinutes?: number }) {
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
        const roLocked = row.movability === 'locked';
        const pinnedHighlightCls = row.enabled && roLocked ? 'bg-red-50' : '';
        return (
          <div
            key={wd}
            data-pinned={row.enabled && roLocked ? 'true' : undefined}
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
                {/* 基本時間 (希望) と違う分数はイレギュラー対応として明示 (PO 2026-08-09)。 */}
                {row.duration_min !== baseMinutes ? (
                  <span
                    className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800"
                    data-testid={`ro-duration-irregular-${wd}`}
                    title={`基本の訪問時間は ${baseMinutes} 分です（希望訪問パターン）`}
                  >
                    基本{baseMinutes}分と異なる
                  </span>
                ) : null}
                {/* 統合 (2026-08-09): 完全固定行は赤ピンバッジを併記. */}
                {roLocked ? (
                  <span
                    className="inline-flex items-center gap-0.5 rounded bg-red-100 px-1.5 py-0.5 text-xs font-medium text-red-700"
                    data-testid={`ro-pin-${wd}`}
                    aria-label="完全固定"
                    title="完全固定"
                  >
                    <PushPin className="h-3 w-3 text-red-600" />
                    完全固定
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
  /**
   * 基本の訪問時間 (分) = 希望訪問パターンの service_minutes (PO 決定 2026-08-09)。
   * 所要時間のデフォルト・「（基本）」ラベル・イレギュラー表示に使う。
   */
  baseMinutes: number;
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
  baseMinutes,
}: WeekGridProps) {
  const update = (weekday: number, patch: Partial<DayRow>) => {
    const current = rows[weekday] ?? emptyDayRow(baseMinutes);
    onChange({ ...rows, [weekday]: { ...current, ...patch } as DayRow });
  };

  // Phase E-5: 主担当拠点以外を「サブ拠点候補」として並べる. 0 件なら selector を出さない.
  const subOfficeCandidates = offices.filter((o) => o.id !== primaryOfficeId);

  return (
    <div className="space-y-2">
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const row = rows[wd] ?? emptyDayRow(baseMinutes);
        // Phase E-5: row.sub_office_id に応じてコース選択肢を切り替える.
        const rowCourseTemplates = resolveRowCourseTemplates(
          row,
          courseTemplates,
          row.sub_office_id ? getSubOfficeCourseTemplates(row.sub_office_id) : [],
        );
        // 統合 (PO 決定 2026-08-09): 完全固定でも人手の編集は常に可。
        // 完全固定の意味は「エンジンが動かさない」— 編集ロック (旧 422) は撤廃し、
        // 注意書きだけを出す。
        const isLocked = row.movability === 'locked';
        const editDisabled = disabled;
        // 完全固定行は薄い赤背景で強調する (赤ピン = 完全固定の表示).
        const pinnedHighlightCls = row.enabled && isLocked ? 'bg-red-50' : '';
        return (
          <div
            key={wd}
            data-pinned={row.enabled && isLocked ? 'true' : undefined}
            data-testid={`pfv-row-${wd}`}
            className={`flex flex-wrap items-center gap-3 rounded-md border border-border-default px-3 py-2 ${pinnedHighlightCls}`}
          >
            <span className="w-5 text-center text-sm font-medium text-text-secondary">
              {WEEKDAY_LABELS[wd]}
            </span>
            <Checkbox
              checked={row.enabled}
              onCheckedChange={(c) => update(wd, { enabled: c === true })}
              disabled={editDisabled}
              aria-label={`${WEEKDAY_LABELS[wd]}曜日 訪問あり`}
            />
            {row.enabled ? (
              <>
                <div className="flex items-center gap-1">
                  <select
                    value={row.start_time}
                    onChange={(e) => update(wd, { start_time: e.target.value })}
                    disabled={editDisabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-60"
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
                    disabled={editDisabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-60"
                    aria-label={`${WEEKDAY_LABELS[wd]} 所要時間`}
                  >
                    {durationOptionsFor(baseMinutes, row.duration_min).map((d) => (
                      <option key={d} value={d}>
                        {d} 分{d === baseMinutes ? '（基本）' : ''}
                      </option>
                    ))}
                  </select>
                  {/* 基本時間と違う分数はイレギュラー対応として明示 (PO 2026-08-09:
                      希望の 1 つの時間がベース。変えるのはイレギュラーな例のみ)。 */}
                  {row.duration_min !== baseMinutes ? (
                    <span
                      className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800"
                      data-testid={`pfv-duration-irregular-${wd}`}
                      title={`基本の訪問時間は ${baseMinutes} 分です（希望訪問パターンで設定）。イレギュラー対応の場合のみ変更してください`}
                    >
                      基本{baseMinutes}分と異なる
                    </span>
                  ) : null}
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
                      disabled={editDisabled}
                      className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-60"
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
                    disabled={editDisabled}
                    className="h-8 rounded border border-border-default bg-bg-base px-2 text-sm text-text-primary focus:outline-none focus:border-brand-primary disabled:opacity-60"
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
                    disabled={editDisabled || !requiresMultipleStaff}
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
                {/* 完全固定 (統合 / PO 決定 2026-08-09):
                    旧「ピン留め」(is_pinned) と「可動域: 完全固定」を 1 概念に統合。
                    - チェック ON = movability='locked' (BE が is_pinned をミラー)
                    - 意味は「エンジン (自動最適化・週生成・提案 apply) が動かさない」
                    - 人手の編集は常に可 (編集ロックはしない / 保存で 422 にもならない)
                    - 切替は通常の PUT (保存) に含める。旧 PATCH /pin フローは廃止。 */}
                <label
                  className="flex items-center gap-1 text-xs text-text-secondary"
                  data-testid={`pfv-locked-label-${wd}`}
                >
                  <Checkbox
                    checked={isLocked}
                    onCheckedChange={(c) =>
                      update(wd, { movability: c === true ? 'locked' : 'unknown' })
                    }
                    disabled={editDisabled}
                    aria-label={`${WEEKDAY_LABELS[wd]} 完全固定`}
                    data-testid={`pfv-locked-checkbox-${wd}`}
                  />
                  {isLocked ? <PushPin className="h-3.5 w-3.5 text-red-600" aria-hidden /> : null}
                  <span>完全固定</span>
                </label>
                {isLocked ? (
                  <span className="text-xs text-text-muted" data-testid={`pfv-locked-note-${wd}`}>
                    システムはこの枠を動かしません（手動での変更はできます）
                  </span>
                ) : null}
                {LEGACY_MOVABILITY_LABELS[row.movability] ? (
                  /* 旧 4 段階の値が残っている行のための表示。保存すると 2 段階へ収束する。 */
                  <span
                    className="text-xs text-text-muted"
                    data-testid={`pfv-legacy-movability-${wd}`}
                  >
                    旧設定: {LEGACY_MOVABILITY_LABELS[row.movability]}
                  </span>
                ) : null}
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

  // 基本の訪問時間 = 希望訪問パターンの service_minutes (PO 決定 2026-08-09)。
  const baseMinutes = baseServiceMinutes(weeklyPattern);
  const [rows, setRows] = React.useState<DayRows>(() => emptyDayRows(baseMinutes));
  const [fieldErrors, setFieldErrors] = React.useState<Record<number, string>>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = React.useState(false);

  // サーバー状態そのままの DayRows. 未保存編集の有無 (isDirty) 判定に使う.
  const serverRows = React.useMemo(() => readsToDayRows(reads, baseMinutes), [reads, baseMinutes]);
  // DayRow はプリミティブと文字列配列のみで構成されるため JSON 比較で十分.
  const isDirty = React.useMemo(
    () => JSON.stringify(rows) !== JSON.stringify(serverRows),
    [rows, serverRows],
  );

  // サーバーデータが変化したらフォームを同期
  React.useEffect(() => {
    if (!isLoading) {
      setRows(readsToDayRows(reads, baseMinutes));
      setFieldErrors({});
      setFormError(null);
    }
  }, [reads, isLoading, baseMinutes]);

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

  // ── 週全体の完全固定 (PO 要望 2026-08-09) ──────────────────────────────
  // 「1 週間分全体を完全固定にする」入口。訪問ありの全曜日の movability を
  // 一括で切り替える。ローカル state のみ変更し、「保存」(PUT) で確定する。
  // (旧 PATCH /pin の即時反映フローは統合により廃止。)
  const enabledDayCount = React.useMemo(
    () => Object.values(rows).filter((r) => r.enabled).length,
    [rows],
  );
  const allEnabledLocked = React.useMemo(
    () =>
      enabledDayCount > 0 &&
      Object.values(rows).every((r) => !r.enabled || r.movability === 'locked'),
    [rows, enabledDayCount],
  );
  const handleSetAllLocked = (locked: boolean) => {
    setRows((prev) => {
      const next: DayRows = { ...prev };
      for (const [wdStr, row] of Object.entries(prev)) {
        if (!row.enabled) continue;
        next[Number(wdStr)] = { ...row, movability: locked ? 'locked' : 'unknown' };
      }
      return next;
    });
    toast.info(
      locked
        ? '全曜日を完全固定にしました（まだ保存されていません。「保存」で確定します）'
        : '全曜日の完全固定を解除しました（まだ保存されていません。「保存」で確定します）',
    );
  };

  // ── 希望から自動生成 ──────────────────────────────────────────────────
  // Phase G-21 T4 reviewer C3 / M2: 既存 rows を base に merge する (= 既存の
  // is_pinned / コース選択 / サブ拠点 を保持). preferred_weekdays に無い曜日も
  // 既存の enabled/設定をそのまま残す.
  const handleAutoFill = () => {
    const newRows = weeklyPatternToDayRows(weeklyPattern, rows, baseMinutes);
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
      // 2026-08-07: ApiError.message は "API 422 (path)" という機械向け文字列で、
      // 理由 (pinned 保護 / サブ拠点不一致 など) はすべて body 側にある。
      // apiErrorMessage で detail を展開しないと現場は原因を判断できない。
      const msg = apiErrorMessage(e, '保存に失敗しました');
      setFormError(msg);
      toast.error(`保存に失敗しました: ${msg}`);
    }
  };

  // ── リセット (DELETE) ─────────────────────────────────────────────────
  const handleDelete = async () => {
    setDeleteDialogOpen(false);
    try {
      await deleteMut.mutateAsync(mode);
      setRows(emptyDayRows(baseMinutes));
      setFieldErrors({});
      setFormError(null);
      toast.success(`${mode === 'normal' ? '通常' : '特別週'}の固定枠を削除しました`);
    } catch (e) {
      const msg = apiErrorMessage(e, '削除に失敗しました');
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
          baseMinutes={baseMinutes}
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
          baseMinutes={baseMinutes}
        />
      )}

      {formError ? (
        // 422 detail は複数行になりうる (pinned 保護の violations 等) ため改行を保つ.
        <p className="whitespace-pre-line text-xs text-error" data-testid="pfv-form-error">
          {formError}
        </p>
      ) : null}

      {!readonly && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-md border border-red-200 bg-red-50/50 px-3 py-2"
          data-testid="pfv-lock-all-toolbar"
        >
          <span className="text-xs font-medium text-red-700">
            完全固定（システムは動かさない）:
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => handleSetAllLocked(true)}
            disabled={isBusy || enabledDayCount === 0 || allEnabledLocked}
            data-testid="pfv-lock-all-button"
          >
            全曜日を完全固定
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => handleSetAllLocked(false)}
            disabled={isBusy || enabledDayCount === 0}
            data-testid="pfv-unlock-all-button"
          >
            全曜日の完全固定を解除
          </Button>
          <span className="text-xs text-text-muted">
            各曜日ごとの切替は行内の「完全固定」で。「保存」で確定します
          </span>
        </div>
      )}

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
