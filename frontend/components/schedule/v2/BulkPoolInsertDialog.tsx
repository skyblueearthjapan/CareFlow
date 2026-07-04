'use client';

/**
 * BulkPoolInsertDialog — プール一括投入 (W-2 FE).
 *
 * 設計書: `docs/plans/pool-bulk-insert-design.md` §5 (UI) / §5.3 (「見せる」4点).
 *
 * フロー (5 段ステート): idle → simulating → previewing → applying → done
 *   1. 開くと自動で POST /v2/pool-bulk-simulate (read-only, spinner)
 *   2. プレビュー: 左=投入リスト(順序説明+delta+警告+部分/不能), 右=Tabs(全体/曜日 Before/After)
 *   3. 「見せる」4点 (D-2 の対価): 常時アンバーバナー + 必須チェックボックス +
 *      適用ボタン文言に登録内容を明記 + 適用後トースト/done サマリ
 *   4. POST /v2/pool-bulk-apply (1 TX, pattern_and_week 固定)。409 は再計算導線。
 *
 * 反映先は A 固定 (pattern_and_week)。change_scope は送らない (仕様として固定)。
 * Ctrl+Z (undo) 対象外 — バナーで明示。
 */
import * as React from 'react';
import {
  AlertTriangle,
  CalendarRange,
  CheckCircle2,
  Layers,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { ApiError } from '@/lib/api-client';
import { proposeWarningLabel } from '@/lib/queries/fieldBoard';
import {
  usePoolBulkApplyMutation,
  usePoolBulkSimulateMutation,
} from '@/lib/queries/poolBulk';
import { useStaffList } from '@/lib/queries/staff';
import type {
  V2CourseSummary,
  V2VisitForUI,
  V2WeekdayBeforeAfter,
} from '@/lib/schemas/v2/autoScheduleV2';
import {
  POOL_BULK_MAX_PATIENTS,
  type PoolBulkPlacement,
  type PoolBulkSimulateResponse,
} from '@/lib/schemas/v2/poolBulk';

import { WeekdayScheduleCard, type CourseListItem } from '../WeekdayScheduleCard';
import { EXCLUDED_REASON_LABEL } from './PoolCandidateList';
import { ProposalWeekCalendar } from './ProposalWeekCalendar';
import { formatDelta, formatErr, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Constants / Helpers
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;
const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

function fmtWd(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

/**
 * 投入不能 / 部分投入の理由コード → 日本語ラベル。
 * PoolCandidateList の EXCLUDED_REASON_LABEL を再利用し、一括投入固有の
 * no_coordinates を補う (未知コードは原文フォールバック)。
 */
const BULK_EXTRA_REASON_LABEL: Record<string, string> = {
  no_coordinates: '座標未登録 (住所のジオコーディング未完了)',
  // BE (place-and-fix / pool-bulk-simulate) の拠点フィルタ reason.
  no_primary_office: '主担当拠点が未設定',
  office_mismatch: '他拠点の患者',
  // W-12a (D-6): 一括投入は v1 で 2名体制を対象外 (個別提案から配置)。
  two_staff_pending: '2名体制（個別提案から配置してください）',
};

function bulkReasonLabel(reason: string): string {
  return EXCLUDED_REASON_LABEL[reason] ?? BULK_EXTRA_REASON_LABEL[reason] ?? reason;
}

/** overcapacity_available_count を寛容に読む (mock 等で欠落しても NaN にしない)。 */
function overcapCount(entry: { overcapacity_available_count?: number }): number {
  return entry.overcapacity_available_count ?? 0;
}

/**
 * A 案 (方式b 橋渡し): 「定員 +1 名なら入る候補あり」アンバーバッジ。
 * プレビュー段階は情報表示のみ (クリック不可)。done 画面では患者名クリックで個別フローへ導く。
 */
function OvercapacityBadge() {
  return (
    <Badge
      variant="outline"
      className="border-amber-400 bg-amber-50 text-[9px] text-amber-800 dark:border-amber-600 dark:bg-amber-950 dark:text-amber-200"
      data-testid="bulk-pool-insert-overcap-badge"
    >
      定員+1名なら入る候補あり
    </Badge>
  );
}

type DialogStage = 'idle' | 'simulating' | 'previewing' | 'applying' | 'done';

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

/** 一括投入ダイアログが受け取るプール患者の最小情報 (拠点グループ化に使う)。 */
export interface BulkPoolPatient {
  id: string;
  name: string;
  /** 主担当拠点。null / 未設定なら「拠点未設定」セクションへ分離する。 */
  primary_office_id: string | null;
}

export interface BulkPoolInsertDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** ページで選択中の拠点 (null = 全拠点表示 → 拠点タブ UI)。 */
  officeId: string | null;
  /** 保留プール患者 (id / name / primary_office_id)。親が抽出して渡す。 */
  poolPatients: BulkPoolPatient[];
  /** 拠点一覧 (タブ名・グループ表示用)。 */
  offices: { id: string; name: string }[];
  /**
   * A 案 (2026-07-04 PO 承認): done 画面で投入できなかった患者名クリック時に、
   * 一括ダイアログを閉じてから患者詳細 (→ PoolCandidateList → 方式b の定員+1 相談) を開く導線。
   * W-5b: opts.autoOvercapacity=true で呼ぶと PoolCandidateList が超過候補まで自動展開する。
   * 未指定なら名前はクリック不可 (バッジ表示のみ)。
   */
  onOpenPatientDetail?: (patientId: string, opts?: { autoOvercapacity?: boolean }) => void;
}

// ─────────────────────────────────────────────────────────────────────────
// Component (outer) — 拠点グループ化 + タブ/単一拠点の振り分け
//
// W-6: プール患者を primary_office_id でグループ化する。
//   - ページで拠点選択中 (officeId 非 null) → その拠点の患者のみを 1 パネルで扱う
//     (混入修正)。他拠点/拠点未設定は対象外として明示する。
//   - 全拠点表示 (officeId null) → 拠点タブ。各タブが独立に simulate → 適用する
//     (= OfficeBulkPanel インスタンスごとに stage/result/confirmed/appliedSummary を保持)。
//     アクティブになった初回のみ自動 simulate (全タブ一斉実行を避ける)。
//   - primary_office_id 未設定の患者は simulate に送らず「拠点未設定」セクションへ分離。
// ─────────────────────────────────────────────────────────────────────────

export function BulkPoolInsertDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
  poolPatients,
  offices,
  onOpenPatientDetail,
}: BulkPoolInsertDialogProps) {
  const officeNameById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices) m.set(o.id, o.name);
    return m;
  }, [offices]);

  // 主担当拠点でグループ化 (未設定は別枠へ分離)。
  const { byOffice, noOfficePatients } = React.useMemo(() => {
    const grouped = new Map<string, BulkPoolPatient[]>();
    const noOffice: BulkPoolPatient[] = [];
    for (const p of poolPatients) {
      if (!p.primary_office_id) {
        noOffice.push(p);
        continue;
      }
      const arr = grouped.get(p.primary_office_id) ?? [];
      arr.push(p);
      grouped.set(p.primary_office_id, arr);
    }
    return { byOffice: grouped, noOfficePatients: noOffice };
  }, [poolPatients]);

  // 全拠点モードの拠点タブ (患者のいる拠点のみ)。offices 順 + 未知拠点は末尾。
  const officeTabs = React.useMemo(() => {
    const tabs: { id: string; name: string; patients: BulkPoolPatient[] }[] = [];
    const seen = new Set<string>();
    for (const o of offices) {
      const pats = byOffice.get(o.id);
      if (pats && pats.length > 0) {
        tabs.push({ id: o.id, name: o.name, patients: pats });
        seen.add(o.id);
      }
    }
    for (const [oid, pats] of byOffice) {
      if (!seen.has(oid) && pats.length > 0) {
        tabs.push({
          id: oid,
          name: officeNameById.get(oid) ?? `拠点 ${oid.slice(0, 6)}`,
          patients: pats,
        });
      }
    }
    return tabs;
  }, [offices, byOffice, officeNameById]);

  // 全拠点モードのアクティブタブ。
  const [activeTab, setActiveTab] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!open) return;
    // 開いた時 / タブ構成が変わった時に、有効な選択が無ければ先頭タブを既定にする。
    setActiveTab((prev) =>
      prev && officeTabs.some((t) => t.id === prev) ? prev : (officeTabs[0]?.id ?? null),
    );
  }, [open, officeTabs]);

  // busy 状態を各パネルから受け取り、ESC/backdrop クローズをガードする。
  const busyOfficesRef = React.useRef<Set<string>>(new Set());
  const [anyBusy, setAnyBusy] = React.useState(false);
  const handleBusyChange = React.useCallback((panelOfficeId: string, busy: boolean) => {
    if (busy) {
      busyOfficesRef.current.add(panelOfficeId);
    } else {
      busyOfficesRef.current.delete(panelOfficeId);
    }
    setAnyBusy(busyOfficesRef.current.size > 0);
  }, []);

  const handleClose = () => {
    if (anyBusy) return;
    onClose();
  };

  // 単一拠点モード (ページで拠点選択中): その拠点の患者のみを対象 (混入修正)。
  const singleOffice = officeId != null;
  const targetPatients = singleOffice ? (byOffice.get(officeId) ?? []) : [];
  const otherOfficeCount = singleOffice
    ? poolPatients.filter((p) => p.primary_office_id && p.primary_office_id !== officeId).length
    : 0;

  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? handleClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] max-w-5xl overflow-y-auto"
        data-testid="bulk-pool-insert-dialog"
        onEscapeKeyDown={(e) => { if (anyBusy) e.preventDefault(); }}
        onInteractOutside={(e) => { if (anyBusy) e.preventDefault(); }}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-5 w-5 text-brand-primary" aria-hidden />
            プール一括投入
          </DialogTitle>
          <DialogDescription>
            保留プールの患者をまとめて固定訪問週間（毎週の型）に投入します。
            選択肢のない方から確保し、残りは効果順に投入します。
          </DialogDescription>
        </DialogHeader>

        {singleOffice ? (
          // ── 単一拠点モード ──
          <div className="space-y-3">
            {otherOfficeCount > 0 ? (
              <div
                className="text-xs text-text-muted"
                data-testid="bulk-pool-insert-other-office-note"
              >
                他拠点の患者 {otherOfficeCount}名は対象外です（各拠点を選んで投入してください）。
              </div>
            ) : null}
            {targetPatients.length > 0 ? (
              <OfficeBulkPanel
                key={officeId}
                open={open}
                active
                isoYear={isoYear}
                isoWeek={isoWeek}
                officeId={officeId}
                patientIds={targetPatients.map((p) => p.id)}
                onClose={handleClose}
                onBusyChange={handleBusyChange}
                onOpenPatientDetail={onOpenPatientDetail}
              />
            ) : (
              <div
                className="rounded border border-border-default bg-bg-muted px-3 py-6 text-center text-sm text-text-muted"
                data-testid="bulk-pool-insert-empty-office"
              >
                この拠点に投入対象の保留患者はいません。
              </div>
            )}
          </div>
        ) : officeTabs.length > 0 ? (
          // ── 全拠点モード: 拠点タブ ──
          <div className="space-y-3">
            <div
              className="flex flex-wrap gap-1 border-b border-border-default pb-2"
              data-testid="bulk-pool-insert-office-tabs"
              role="tablist"
            >
              {officeTabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === t.id}
                  onClick={() => setActiveTab(t.id)}
                  data-testid={`bulk-pool-insert-office-tab-${t.id}`}
                  data-active={activeTab === t.id ? 'true' : 'false'}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    activeTab === t.id
                      ? 'bg-brand-primary text-white'
                      : 'bg-bg-muted text-text-secondary hover:bg-bg-muted/70'
                  }`}
                >
                  {t.name}
                  <span className="tnum ml-1 text-[11px] opacity-80">{t.patients.length}名</span>
                </button>
              ))}
            </div>
            {/* 各拠点パネルは常時マウントし hidden で出し分ける (= タブを戻ってもステート保持)。 */}
            {officeTabs.map((t) => (
              <div key={t.id} hidden={activeTab !== t.id}>
                <OfficeBulkPanel
                  open={open}
                  active={activeTab === t.id}
                  isoYear={isoYear}
                  isoWeek={isoWeek}
                  officeId={t.id}
                  patientIds={t.patients.map((p) => p.id)}
                  onClose={handleClose}
                  onBusyChange={handleBusyChange}
                  onOpenPatientDetail={onOpenPatientDetail}
                />
              </div>
            ))}
          </div>
        ) : noOfficePatients.length === 0 ? (
          <div
            className="rounded border border-border-default bg-bg-muted px-3 py-8 text-center text-sm text-text-muted"
            data-testid="bulk-pool-insert-empty"
          >
            保留プールに投入対象の患者がいません。
          </div>
        ) : null}

        {/* 拠点未設定患者の分離 (simulate 対象外; 患者マスタで設定するよう案内)。 */}
        <NoOfficeSection
          patients={noOfficePatients}
          onOpenPatientDetail={onOpenPatientDetail}
          onClose={handleClose}
        />
      </DialogContent>
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// NoOfficeSection — 主担当拠点が未設定の患者リスト (simulate に送らない)。
// 患者名クリックでダイアログを閉じてから患者詳細を開く (拠点設定を促す)。
// ─────────────────────────────────────────────────────────────────────────

function NoOfficeSection({
  patients,
  onOpenPatientDetail,
  onClose,
}: {
  patients: BulkPoolPatient[];
  onOpenPatientDetail?: (patientId: string) => void;
  onClose: () => void;
}) {
  if (patients.length === 0) return null;
  const openDetail = (id: string) => {
    onClose();
    onOpenPatientDetail?.(id);
  };
  return (
    <div
      className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-600 dark:bg-amber-950"
      data-testid="bulk-pool-insert-no-office"
    >
      <div className="mb-1 text-xs font-semibold text-amber-900 dark:text-amber-200">
        拠点未設定 {patients.length}名
      </div>
      <p className="mb-2 text-[11px] text-amber-800 dark:text-amber-300">
        患者マスタで主担当拠点を設定すると一括投入の対象になります。
      </p>
      <ul className="flex flex-wrap gap-x-3 gap-y-1">
        {patients.map((p) => (
          <li key={p.id} className="text-[11px]">
            {onOpenPatientDetail ? (
              <button
                type="button"
                className="text-amber-900 underline underline-offset-2 hover:text-amber-700 dark:text-amber-200 dark:hover:text-amber-400"
                onClick={() => openDetail(p.id)}
                data-testid="bulk-pool-insert-no-office-patient"
              >
                {p.name}
              </button>
            ) : (
              <span className="text-amber-900 dark:text-amber-200">{p.name}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// OfficeBulkPanel — 単一拠点分の一括投入フロー (5 段ステート)。
// 拠点タブごとに 1 インスタンス = 独立したステート (stage/result/confirmed/appliedSummary)。
// `active` が true になった初回のみ自動 simulate する (全タブ一斉実行を避ける)。
// ─────────────────────────────────────────────────────────────────────────

function OfficeBulkPanel({
  open,
  active,
  isoYear,
  isoWeek,
  officeId,
  patientIds,
  onClose,
  onBusyChange,
  onOpenPatientDetail,
}: {
  open: boolean;
  active: boolean;
  isoYear: number;
  isoWeek: number;
  officeId: string;
  patientIds: string[];
  onClose: () => void;
  onBusyChange?: (officeId: string, busy: boolean) => void;
  onOpenPatientDetail?: (patientId: string, opts?: { autoOvercapacity?: boolean }) => void;
}) {
  const simulateMut = usePoolBulkSimulateMutation();
  const applyMut = usePoolBulkApplyMutation();
  const { mutateAsync: simulate, reset: resetSimulate } = simulateMut;
  const { mutateAsync: apply, reset: resetApply } = applyMut;

  const [stage, setStage] = React.useState<DialogStage>('idle');
  const [result, setResult] = React.useState<PoolBulkSimulateResponse | null>(null);
  const [confirmed, setConfirmed] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState<string>('all');
  const [appliedSummary, setAppliedSummary] = React.useState<{
    patients: number;
    slots: number;
  } | null>(null);

  // 対象患者 id (先頭 50 名にキャップ)。
  // patientIds はレンダーごとに新配列になりうるため join で安定化して effect deps に使う。
  const targetIdsKey = React.useMemo(
    () => patientIds.slice(0, POOL_BULK_MAX_PATIENTS).join(','),
    [patientIds],
  );
  // 総数は ref で常に最新を参照 (表示中にプールが増えても切り捨てトーストが正しく出る)。
  const totalPatientCountRef = React.useRef(patientIds.length);
  totalPatientCountRef.current = patientIds.length;

  /** simulate 実行 (アクティブ初回 + 再計算ボタン)。 */
  const runSimulate = React.useCallback(async () => {
    const targetIds = targetIdsKey ? targetIdsKey.split(',') : [];
    if (targetIds.length === 0) {
      setStage('idle');
      return;
    }
    setStage('simulating');
    setResult(null);
    setConfirmed(false);
    setAppliedSummary(null);
    try {
      const res = await simulate({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
        patient_ids: targetIds,
        ordering: 'hybrid',
      });
      setResult(res);
      setStage('previewing');
      const totalCount = totalPatientCountRef.current;
      if (totalCount > POOL_BULK_MAX_PATIENTS) {
        toast.warning(
          `プール患者が ${totalCount} 名います。先頭 ${POOL_BULK_MAX_PATIENTS} 名のみ一括投入の対象にしました。`,
        );
      }
    } catch (err) {
      toast.error(`一括投入のシミュレーションに失敗しました: ${formatErr(err)}`);
      setStage('idle');
    }
  }, [targetIdsKey, isoYear, isoWeek, officeId, simulate]);

  // アクティブになった初回に自動 simulate。simKey が同じ間は再計算しない
  // (= タブを戻ってもステートを保持する。全タブ一斉実行も避けられる)。
  const simKey = `${isoYear}|${isoWeek}|${officeId}|${targetIdsKey}`;
  const simulatedKeyRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!open) {
      simulatedKeyRef.current = null;
      return;
    }
    if (!active) return;
    if (simulatedKeyRef.current === simKey) return;
    simulatedKeyRef.current = simKey;
    resetSimulate();
    resetApply();
    void runSimulate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active, simKey]);

  const isBusy = stage === 'simulating' || stage === 'applying';

  // 親コンポーネントに busy 状態を伝える (ESC/backdrop ガード用)。
  React.useEffect(() => {
    onBusyChange?.(officeId, isBusy);
    return () => {
      onBusyChange?.(officeId, false);
    };
  }, [isBusy, officeId, onBusyChange]);

  const handleClose = () => {
    if (isBusy) return;
    onClose();
  };

  /**
   * A 案: 投入できなかった患者名クリックで一括ダイアログを閉じてから患者詳細を開く
   * (→ PoolCandidateList → 方式b の定員+1 相談)。基準がずれないよう done 画面のみで導線を出す。
   */
  const openPatientDetail = React.useCallback(
    (patientId: string, opts?: { autoOvercapacity?: boolean }) => {
      onClose();
      onOpenPatientDetail?.(patientId, opts);
    },
    [onClose, onOpenPatientDetail],
  );

  // 投入枠 (patient_id|weekday) のハイライトキー集合 (After 週ビューの強調用)。
  const insertedKeys = React.useMemo(() => {
    const s = new Set<string>();
    for (const p of result?.placements ?? []) s.add(`${p.patient_id}|${p.weekday}`);
    return s;
  }, [result]);

  const placedPatients = result?.kpi.placed_patients ?? 0;
  const placedSlots = result?.kpi.placed_slots ?? 0;

  /** 適用実行 (pattern_and_week 固定・全件)。 */
  const handleApply = async () => {
    if (!result || !confirmed) return;
    setStage('applying');
    try {
      const res = await apply({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
        placements: result.placements,
        state_token: result.state_token,
      });
      setAppliedSummary({ patients: res.applied_patients, slots: res.applied_slots });
      toast.success(
        `${res.applied_patients}名 ${res.applied_slots}枠を固定訪問週間に登録しました`,
      );
      for (const w of res.warnings.slice(0, 3)) toast.warning(w);
      if (res.warnings.length > 3) {
        toast.warning(`他 ${res.warnings.length - 3} 件の警告があります`);
      }
      setStage('done');
    } catch (err) {
      // 409 = state_token 不一致 (simulate 以降にスケジュールが変わった)。
      // fetcher の ApiError.message は detail を含まないため status で判定する (過去の教訓)。
      if (err instanceof ApiError && err.status === 409) {
        toast.error('シミュレーション後にスケジュールが変更されました。再計算してください');
        setStage('previewing');
      } else {
        toast.error(`一括投入の適用に失敗しました: ${formatErr(err)}`);
        setStage('previewing');
      }
    }
  };

  return (
    <>
      {/* ── simulating: spinner ── */}
      {stage === 'simulating' ? (
          <div
            className="flex flex-col items-center justify-center gap-2 py-16 text-sm text-text-muted"
            data-testid="bulk-pool-insert-loading"
          >
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />
            プールの投入シミュレーションを計算中…（数秒かかる場合があります）
          </div>
        ) : null}

        {/* ── done: 結果サマリ ── */}
        {stage === 'done' && appliedSummary ? (
          <div
            className="flex flex-col items-center justify-center gap-3 py-8 text-center"
            data-testid="bulk-pool-insert-done"
          >
            <CheckCircle2 className="h-10 w-10 text-emerald-600" aria-hidden />
            <div className="text-base font-semibold text-text-primary">
              {appliedSummary.patients}名 {appliedSummary.slots}枠を固定訪問週間に登録しました
            </div>
            <p className="max-w-md text-xs text-text-muted">
              固定訪問週間（毎週の型）に登録され、今週のスケジュールにも反映されました。
              ダイアログを閉じると画面に反映されます。
            </p>

            {/* A 案: 投入できなかった患者を個別フロー (方式b の定員+1 相談) へ橋渡し。 */}
            <DoneRemainingList
              result={result}
              onOpenPatientDetail={onOpenPatientDetail ? openPatientDetail : undefined}
            />
          </div>
        ) : null}

        {/* ── previewing / applying: プレビュー本体 (適用中も表示し続ける) ── */}
        {(stage === 'previewing' || stage === 'applying') && result ? (
          <div className="space-y-3 py-2" data-testid="bulk-pool-insert-preview">
            {/* 「見せる」1: 常時アンバーバナー (閉じられない). */}
            <Alert variant="warning" data-testid="bulk-pool-insert-banner">
              <AlertTriangle className="h-4 w-4" aria-hidden />
              <AlertTitle>この操作は元に戻せません（Ctrl+Z 対象外）</AlertTitle>
              <AlertDescription>
                適用すると <strong>{placedPatients}名 {placedSlots}枠が固定訪問週間（毎週の型）に登録</strong>
                され、<strong>今週のスケジュールにも即反映</strong>されます。
                この操作は元に戻す（Ctrl+Z）の対象外です。
              </AlertDescription>
            </Alert>

            {/* KPI タイル (投入 / 部分 / 投入不能 / 移動時間). */}
            <section
              className="grid grid-cols-2 gap-2 sm:grid-cols-4"
              data-testid="bulk-pool-insert-kpi"
            >
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">投入</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {placedPatients}名 / {placedSlots}枠
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">部分投入</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.partial.length}名
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">投入不能</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.unplaced.length}名
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">移動時間 (分)</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.kpi.travel_minutes_before} → {result.kpi.travel_minutes_after}
                </div>
                <div className="tnum text-[10px] text-text-muted">
                  {result.kpi.travel_km_before.toFixed(1)} → {result.kpi.travel_km_after.toFixed(1)} km
                </div>
              </div>
            </section>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[300px_1fr]">
              {/* 左カラム: 投入リスト + 部分/不能. */}
              <InsertListColumn result={result} />

              {/* 右カラム: 全体 + 曜日タブ (Before/After). */}
              <div className="min-w-0">
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                  <TabsList className="flex w-full flex-wrap gap-1 bg-bg-muted">
                    <TabsTrigger value="all" data-testid="bulk-pool-insert-tab-all">
                      全体
                    </TabsTrigger>
                    {DISPLAY_WEEKDAYS.map((wd) => (
                      <TabsTrigger
                        key={wd}
                        value={String(wd)}
                        data-testid={`bulk-pool-insert-tab-${wd}`}
                      >
                        {WEEKDAY_LABELS[wd]}
                      </TabsTrigger>
                    ))}
                  </TabsList>

                  <TabsContent value="all" data-testid="bulk-pool-insert-panel-all">
                    <AllWeekBeforeAfter proposals={result.week_before_after} />
                  </TabsContent>

                  {DISPLAY_WEEKDAYS.map((wd) => {
                    const wp = result.week_before_after.find((w) => w.weekday === wd);
                    return (
                      <TabsContent
                        key={wd}
                        value={String(wd)}
                        data-testid={`bulk-pool-insert-panel-${wd}`}
                      >
                        {wp ? (
                          <WeekdayBeforeAfter
                            weekday={wd}
                            proposal={wp}
                            insertedKeys={insertedKeys}
                          />
                        ) : (
                          <div className="py-6 text-center text-xs text-text-muted">
                            {WEEKDAY_LABELS[wd]}曜日の投入はありません
                          </div>
                        )}
                      </TabsContent>
                    );
                  })}
                </Tabs>
              </div>
            </div>
          </div>
        ) : null}

        {/* ── フッター: ステージ別 ── */}
        {(stage === 'previewing' || stage === 'applying') && result ? (
          <div
            className="mt-4 rounded-lg border border-border-default bg-bg-muted p-4"
            data-testid="bulk-pool-insert-footer"
          >
            {/* 「見せる」2: 必須チェックボックス. */}
            <label className="mb-3 flex items-start gap-2 text-sm text-text-primary">
              <Checkbox
                checked={confirmed}
                onCheckedChange={(v) => setConfirmed(v === true)}
                className="mt-0.5"
                data-testid="bulk-pool-insert-confirm-checkbox"
              />
              <span>固定訪問週間（毎週の型）が変わることを確認しました</span>
            </label>
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={() => void runSimulate()}
                disabled={isBusy}
                data-testid="bulk-pool-insert-recompute-button"
              >
                <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                再計算
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={handleClose}
                disabled={isBusy}
                data-testid="bulk-pool-insert-cancel-button"
              >
                閉じる
              </Button>
              {/* 「見せる」3: 適用ボタン文言に登録内容を明記. */}
              <Button
                type="button"
                onClick={() => void handleApply()}
                disabled={!confirmed || isBusy || placedSlots === 0}
                data-testid="bulk-pool-insert-apply-button"
              >
                {stage === 'applying' ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <CalendarRange className="mr-1 h-4 w-4" aria-hidden />
                )}
                固定訪問週間に登録する（{placedPatients}名・{placedSlots}枠）
              </Button>
            </div>
          </div>
        ) : stage === 'done' ? (
          <DialogFooter>
            <Button
              type="button"
              onClick={handleClose}
              data-testid="bulk-pool-insert-done-close-button"
            >
              閉じる
            </Button>
          </DialogFooter>
        ) : stage === 'idle' ? (
          <DialogFooter>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => void runSimulate()}
                data-testid="bulk-pool-insert-recompute-button"
              >
                <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                再計算
              </Button>
              <Button type="button" variant="outline" onClick={handleClose}>
                閉じる
              </Button>
            </div>
          </DialogFooter>
        ) : null}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// InsertListColumn — 左カラム: 投入リスト + 部分投入/投入不能
// ─────────────────────────────────────────────────────────────────────────

function InsertListColumn({ result }: { result: PoolBulkSimulateResponse }) {
  // W-5b: 部分投入/投入不能に定員超過候補がある場合にヒントを表示する。
  const hasOvercapEntry =
    result.partial.some((p) => overcapCount(p) >= 1) ||
    result.unplaced.some((u) => overcapCount(u) >= 1);

  return (
    <div className="space-y-2" data-testid="bulk-pool-insert-list">
      {/* 順序の説明文言 (D-1). */}
      <div className="rounded border border-brand-primary/40 bg-brand-primary/5 px-2 py-1.5 text-[11px] text-text-secondary">
        選択肢のない方を先に確保し、残りを効果順に投入します
      </div>

      {/* 投入リスト. */}
      <div className="rounded border border-border-default">
        <div className="border-b border-border-default bg-bg-muted px-2 py-1 text-[11px] font-semibold text-text-primary">
          投入枠（{result.placements.length} 枠）
        </div>
        {result.placements.length === 0 ? (
          <div className="px-2 py-3 text-center text-[11px] text-text-muted">
            投入できる枠がありません
          </div>
        ) : (
          <ul className="divide-y divide-border-default">
            {result.placements.map((p) => (
              <PlacementRow key={`${p.patient_id}-${p.weekday}-${p.seq}`} placement={p} />
            ))}
          </ul>
        )}
      </div>

      {/* 部分投入. */}
      {result.partial.length > 0 ? (
        <div
          className="rounded border border-amber-300 bg-amber-50 dark:border-amber-600 dark:bg-amber-950"
          data-testid="bulk-pool-insert-partial"
        >
          <div className="border-b border-amber-200 px-2 py-1 text-[11px] font-semibold text-amber-900 dark:border-amber-700 dark:text-amber-200">
            部分投入（{result.partial.length} 名）
          </div>
          <ul className="divide-y divide-amber-200 dark:divide-amber-700">
            {result.partial.map((p) => (
              <li key={p.patient_id} className="px-2 py-1.5 text-[11px] text-amber-900 dark:text-amber-200">
                <div className="flex flex-wrap items-center gap-1 font-medium">
                  <span>
                    {p.patient_name}（{p.placed_days}枠投入 / {p.missing_days}枠不足）
                  </span>
                  {overcapCount(p) >= 1 ? <OvercapacityBadge /> : null}
                </div>
                {Object.entries(p.unplaced_reasons).length > 0 ? (
                  <ul className="mt-0.5 ml-3 list-disc text-[10px] text-amber-700 dark:text-amber-400">
                    {Object.entries(p.unplaced_reasons).map(([wd, reason]) => (
                      <li key={wd}>
                        {fmtWd(Number.parseInt(wd, 10))}曜日: {bulkReasonLabel(reason)}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 投入不能. */}
      {result.unplaced.length > 0 ? (
        <div
          className="rounded border border-border-default"
          data-testid="bulk-pool-insert-unplaced"
        >
          <div className="border-b border-border-default bg-bg-muted px-2 py-1 text-[11px] font-semibold text-text-primary">
            投入不能（{result.unplaced.length} 名）
          </div>
          <ul className="divide-y divide-border-default">
            {result.unplaced.map((p) => (
              <li
                key={p.patient_id}
                className="flex flex-wrap items-center justify-between gap-1 px-2 py-1.5 text-[11px]"
              >
                <span className="flex flex-wrap items-center gap-1">
                  <span className="text-text-primary">{p.patient_name}</span>
                  {overcapCount(p) >= 1 ? <OvercapacityBadge /> : null}
                </span>
                <span className="text-text-muted">{bulkReasonLabel(p.reason)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* W-5b: 定員超過候補があるときのヒント行 (適用後の個別相談導線を案内)。 */}
      {hasOvercapEntry ? (
        <p
          className="text-[10px] text-amber-800 dark:text-amber-300"
          data-testid="bulk-pool-insert-overcap-hint"
        >
          定員+1名の個別相談は、適用後にこの画面の患者名から移動できます
        </p>
      ) : null}
    </div>
  );
}

function PlacementRow({ placement: p }: { placement: PoolBulkPlacement }) {
  return (
    <li className="px-2 py-1.5 text-[11px]" data-testid="bulk-pool-insert-placement-row">
      <div className="flex items-center justify-between gap-1">
        <span className="flex items-center gap-1">
          <Badge variant="outline" className="tnum text-[9px]">
            {p.seq}
          </Badge>
          <span className="font-medium text-text-primary">{p.patient_name}</span>
        </span>
        {p.delta_minutes != null ? (
          <Badge variant="secondary" className="tnum text-[9px]">
            コースの移動 {Math.round(p.delta_minutes) <= 0 ? '±0' : `+${Math.round(p.delta_minutes)}`}分
          </Badge>
        ) : null}
      </div>
      <div className="tnum mt-0.5 text-[10px] text-text-muted">
        {fmtWd(p.weekday)}曜日 {p.course_code}コース {trimSeconds(p.start_time)}
      </div>
      {p.warnings.length > 0 ? (
        <div className="mt-0.5 flex flex-wrap gap-1">
          {p.warnings.map((w) => (
            <Badge key={w} variant="warning" className="text-[9px]">
              {proposeWarningLabel(w)}
            </Badge>
          ))}
        </div>
      ) : null}
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// DoneRemainingList — done 画面: 投入できなかった患者を個別フロー (方式b) へ橋渡し.
// 患者名クリックで一括ダイアログを閉じ、患者詳細 → PoolCandidateList → 定員+1 相談へ。
// ─────────────────────────────────────────────────────────────────────────

interface RemainingEntry {
  patientId: string;
  patientName: string;
  overcapCount: number;
  isPartial: boolean;
}

function DoneRemainingList({
  result,
  onOpenPatientDetail,
}: {
  result: PoolBulkSimulateResponse | null;
  onOpenPatientDetail?: (patientId: string, opts?: { autoOvercapacity?: boolean }) => void;
}) {
  const entries: RemainingEntry[] = React.useMemo(() => {
    if (!result) return [];
    const rows: RemainingEntry[] = [];
    for (const u of result.unplaced) {
      rows.push({
        patientId: u.patient_id,
        patientName: u.patient_name,
        overcapCount: overcapCount(u),
        isPartial: false,
      });
    }
    for (const p of result.partial) {
      rows.push({
        patientId: p.patient_id,
        patientName: p.patient_name,
        overcapCount: overcapCount(p),
        isPartial: true,
      });
    }
    return rows;
  }, [result]);

  if (entries.length === 0) return null;

  const hasOvercapCandidate = entries.some((e) => e.overcapCount >= 1);

  return (
    <div
      className="mt-2 w-full max-w-md rounded-lg border border-amber-300 bg-amber-50 p-3 text-left dark:border-amber-600 dark:bg-amber-950"
      data-testid="bulk-pool-insert-done-remaining"
    >
      <p className="mb-2 text-xs text-amber-900 dark:text-amber-200">
        投入できなかった方は保留プールに残っています。患者名を開くと個別の候補を確認できます。
        {hasOvercapCandidate ? (
          <>
            <br />
            「定員+1名なら入る候補あり」の方は、個別画面で定員+1名の相談（理由を入れて採用）ができます。
          </>
        ) : null}
      </p>
      <ul className="divide-y divide-amber-200 dark:divide-amber-700">
        {entries.map((e) => (
          <li
            key={e.patientId}
            className="flex flex-wrap items-center justify-between gap-1 py-1.5 text-[11px]"
          >
            {onOpenPatientDetail ? (
              <button
                type="button"
                className="font-medium text-amber-900 underline underline-offset-2 hover:text-amber-700 dark:text-amber-200 dark:hover:text-amber-400"
                onClick={() => onOpenPatientDetail(e.patientId)}
                data-testid="bulk-pool-insert-done-patient-button"
              >
                {e.patientName}
                {e.isPartial ? '（部分投入）' : ''}
              </button>
            ) : (
              <span className="font-medium text-amber-900 dark:text-amber-200">
                {e.patientName}
                {e.isPartial ? '（部分投入）' : ''}
              </span>
            )}
            {/* W-5b: 定員超過バッジ — count>=1 かつ onOpenPatientDetail ありなら
                バッジ自体もクリック可能にして autoOvercapacity=true で直行する。 */}
            {e.overcapCount >= 1 ? (
              onOpenPatientDetail ? (
                <button
                  type="button"
                  onClick={() => onOpenPatientDetail(e.patientId, { autoOvercapacity: true })}
                  className="cursor-pointer rounded hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500"
                  data-testid="bulk-pool-insert-done-overcap-button"
                >
                  <OvercapacityBadge />
                </button>
              ) : (
                <OvercapacityBadge />
              )
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// WeekdayBeforeAfter — 曜日タブ (FullOptimizeDialog の BeforeAfterWeekPanel 相当).
// WeekdayScheduleCard をそのまま再利用する薄いラッパー。
// insertedKeys で投入枠 (patient_id|weekday) を After 側でハイライトする。
// ─────────────────────────────────────────────────────────────────────────

function totalsFor(courses: V2CourseSummary[]): { distance: number; visits: number } {
  let distance = 0;
  let visits = 0;
  for (const c of courses) {
    distance += c.distance_km;
    visits += c.visits_count;
  }
  return { distance, visits };
}

/** V2CourseSummary[] → CourseListItem[]. inserted な visit は名前に ⊕ を付す。 */
function toCourseListItems(
  courses: V2CourseSummary[],
  weekday: number,
  insertedKeys: Set<string>,
): CourseListItem[] {
  const sorted = [...courses].sort((a, b) => {
    const ofA = a.office_name ?? a.office_id;
    const ofB = b.office_name ?? b.office_id;
    if (ofA !== ofB) return ofA.localeCompare(ofB);
    return (a.code ?? 'Z').localeCompare(b.code ?? 'Z');
  });
  return sorted.map((c) => ({
    key: `${c.office_id}-${c.code}-${c.assigned_staff_id ?? 'none'}`,
    title: `${c.office_name ?? '不明'} ${c.code} コース`,
    summary: `${c.visits_count}件 / ${c.distance_km.toFixed(1)}km`,
    visits: c.visits.map((v: V2VisitForUI, i) => {
      const inserted = insertedKeys.has(`${v.patient_id}|${weekday}`);
      return {
        key: `${c.office_id}-${c.code}-${i}-${v.patient_id}`,
        patient_id: v.patient_id,
        start_time: v.start_time,
        // 投入枠は名前の頭に ⊕ を付けて視認可能にする (共通カードを改変せず強調)。
        patient_name: inserted ? `⊕ ${v.patient_name}` : v.patient_name,
        address: v.address,
        area_label: v.area_label,
        time_type: v.time_type,
        preferred_start: v.preferred_start,
        preferred_end: v.preferred_end,
        sex_restriction: v.sex_restriction,
        same_address_group_id: v.same_address_group_id,
        distance_to_next_km: v.distance_to_next_km,
        duration_min: v.duration_min,
      };
    }),
  }));
}

function WeekdayBeforeAfter({
  weekday,
  proposal,
  insertedKeys,
}: {
  weekday: number;
  proposal: V2WeekdayBeforeAfter;
  insertedKeys: Set<string>;
}) {
  const beforeTotal = totalsFor(proposal.before.courses);
  const afterTotal = totalsFor(proposal.after.courses);
  const beforeItems = React.useMemo(
    () => toCourseListItems(proposal.before.courses, weekday, new Set()),
    [proposal.before.courses, weekday],
  );
  const afterItems = React.useMemo(
    () => toCourseListItems(proposal.after.courses, weekday, insertedKeys),
    [proposal.after.courses, weekday, insertedKeys],
  );
  return (
    <div
      className="grid grid-cols-1 gap-2 md:grid-cols-2"
      data-testid={`bulk-pool-insert-before-after-${weekday}`}
    >
      <WeekdayScheduleCard
        title="変更前"
        totalSummary={`${beforeTotal.visits}件 / ${beforeTotal.distance.toFixed(1)}km`}
        tone="muted"
        courses={beforeItems}
        maxVisitsPerCourse={8}
      />
      <WeekdayScheduleCard
        title="変更後（⊕ = 今回の投入枠）"
        totalSummary={`${afterTotal.visits}件 / ${afterTotal.distance.toFixed(1)}km`}
        tone="primary"
        courses={afterItems}
        maxVisitsPerCourse={8}
      />
      <div className="rounded border border-border-default p-2 text-xs md:col-span-2">
        <div className="font-semibold text-text-primary">変化</div>
        <ul className="ml-4 mt-1 list-disc text-text-secondary">
          <li>距離: {formatDelta(afterTotal.distance - beforeTotal.distance)}</li>
          <li>
            訪問数: {beforeTotal.visits} → {afterTotal.visits}
          </li>
        </ul>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AllWeekBeforeAfter — 「全体」タブ: ProposalWeekCalendar を before/after 縦積み.
// ─────────────────────────────────────────────────────────────────────────

function AllWeekBeforeAfter({ proposals }: { proposals: V2WeekdayBeforeAfter[] }) {
  const staffQuery = useStaffList({ limit: 500 });
  const staffNameById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const s of staffQuery.data ?? []) m.set(s.id, s.name);
    return m;
  }, [staffQuery.data]);

  if (proposals.length === 0) {
    return <div className="py-4 text-center text-xs text-text-muted">投入枠がありません</div>;
  }
  return (
    <div className="space-y-3" data-testid="bulk-pool-insert-all-summary">
      <ProposalWeekCalendar proposals={proposals} side="before" staffNameById={staffNameById} />
      <ProposalWeekCalendar proposals={proposals} side="after" staffNameById={staffNameById} />
    </div>
  );
}
