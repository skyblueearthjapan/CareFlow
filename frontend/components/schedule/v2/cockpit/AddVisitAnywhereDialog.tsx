'use client';

/**
 * AddVisitAnywhereDialog — 「＋訪問（任意日付の訪問追加）」のモーダル。
 *
 * 正典 = `docs/plans/add-visit-anywhere-design.md`（§0 PO 決定 1〜12・§3・§5・§8・§9）。
 *
 * 構成 (§3-2):
 *   ① 患者（全 active・プール優先） ② 日付（カレンダー複数選択）
 *   ③ 時刻・所要・希望担当           ④ 提案（`propose-slots` を週ごとに 1 回）
 *   ⑤ 反映先（型 / その週 / 新規）    ⑥ ボタン
 *
 * このコンポーネントは **API を直接叩かない**。`proposeSlots` /
 * `loadPatientWeekVisits` / `loadPatientSubOfficeIds` / `onExecute` は親が注入する
 * （テスト可能性と実行系の分離のため）。組み立てた `AddVisitPlan` を
 * `onExecute` に渡し、実行 (`place-and-fix` / `visit-move-week-only` /
 * `fixed-visits`) と結果表示は親の責務。
 */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { mondayOfIsoWeek } from '@/lib/format/isoWeek';
import {
  assignSourceVisits,
  buildProposeRequest,
  canChoosePatternScope,
  durationOptions,
  formatDateLabel,
  groupDatesByIsoWeek,
  isMCourseCode,
  isMovableSourceVisit,
  isoWeekOfDate,
  mapSlotsToDates,
  pickExcludedReason,
  type AddVisitPlan,
  type AddVisitPlanItem,
  type AddVisitScope,
  type VisitLite,
} from '@/lib/scheduling/addVisitPlan';
import { matchCourseTemplate } from '@/lib/scheduling/courseTemplateMatch';
import { isPlaceablePatientStatus } from '@/lib/schemas/patient';
import type {
  ProposeSlotItem,
  ProposeSlotsRequest,
  ProposeSlotsResponse,
} from '@/lib/schemas/v2/propose_slots';
import {
  DateProposalRow,
  WeekSourceRow,
  M_FALLBACK_LABEL,
  M_KEY,
  selectCls,
  type CandidateEntry,
  type DateProposal,
} from './AddVisitAnywhereRows';
import { TIME_OPTIONS } from './VisitActionMenu';

export type { VisitLite };

// ───────────────────────────────────────────────────────────────────────────
// Props 契約
// ───────────────────────────────────────────────────────────────────────────

export interface AddVisitPatientOption {
  id: string;
  name: string;
  status: string;
  primary_office_id: string | null;
  /**
   * サブ担当拠点 (PO 決定 11 の 2 段目)。
   * `loadPatientSubOfficeIds` が渡されないときの **フォールバック**。
   */
  allowed_office_ids?: string[];
  lat: number | null;
  lng: number | null;
  /** 基本の訪問時間 (`weekly_pattern.service_minutes`)。所要の初期値。 */
  service_minutes: number | null;
  /** 「希望 月/木 午前・稲毛」などの補足。 */
  hint: string | null;
  sex_restriction: string | null;
  requires_multiple_staff: boolean;
}

export interface AddVisitAnywhereInitial {
  patientId?: string;
  dates?: string[];
  staffId?: string | null;
  /**
   * 反映先を固定して開く。
   *   'week' = 「📅 曜日移動」から開いたとき (§3-4 C)。
   *   'new'  = 特別訪問週間の「配置先を決める…」から開いたとき
   *            (追加枠は必ず「新しく 1 件追加」・型は変えない)。
   */
  lockedScope?: 'week' | 'new';
  sourceVisit?: VisitLite;
  /**
   * 日付を固定して開く (呼出元が日を決めている導線)。
   * カレンダー ② は出さず、`dates` をそのまま使う。
   *
   * 当日以前の除外もしない = **過去日を弾くのは呼出元の責任**
   * (BE の登録系は過去日をエラーにしない)。
   */
  lockedDates?: boolean;
}

export interface AddVisitAnywhereDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 盤面が表示している週。カレンダーの初期表示月 = その週の月曜。 */
  isoYear: number;
  isoWeek: number;
  patients: AddVisitPatientOption[];
  /** 保留プール (不足あり) の患者 id。先頭に出しバッジを付ける。 */
  poolPatientIds: ReadonlySet<string>;
  staffOptions: { id: string; name: string }[];
  offices: { id: string; name: string }[];
  /** 拠点ごとのコーステンプレート。M テンプレート = `label === 'M'`。 */
  courseTemplates: { id: string; label: string; office_id: string }[];
  initial?: AddVisitAnywhereInitial;
  /** YYYY-MM-DD。これ以前 (当日含む) は選べない。 */
  todayIso: string;
  proposeSlots: (req: ProposeSlotsRequest) => Promise<ProposeSlotsResponse>;
  /**
   * その患者のその週の訪問を返す。
   *
   * 契約: **絞り込みはしなくてよい**（`status`・`week_pinned`・日付での
   * 「動かせるか」判定はこのモーダルが `isMovableSourceVisit` で行う）。
   * `status` は BE の値をそのまま入れること (`planned` 以外は候補から外れる)。
   */
  loadPatientWeekVisits: (
    patientId: string,
    isoYear: number,
    isoWeek: number,
  ) => Promise<VisitLite[]>;
  /**
   * 段階的緩和 2 段目の対象拠点 (PO 決定 11)。
   * 患者の通常の固定訪問行 (`GET /patients/{id}/fixed-visits`) の
   * `sub_office_id` のうち主担当拠点以外。未指定なら
   * `patient.allowed_office_ids` にフォールバックする。
   */
  loadPatientSubOfficeIds?: (patientId: string) => Promise<string[]>;
  /** 親が実行する。失敗時は reject（モーダルは開いたまま）。 */
  onExecute: (plan: AddVisitPlan) => Promise<void>;
  submitting?: boolean;
}

// ───────────────────────────────────────────────────────────────────────────
// 内部ヘルパー
// ───────────────────────────────────────────────────────────────────────────

/** `buildProposeRequest` が送る `limit`。返却数がこれに達したら打ち切り (M2)。 */
const PROPOSE_LIMIT = 50;

/**
 * 2 名体制 × M（担当なし）は作れない (H2)。BE `place-and-fix` は
 * `requires_multiple_staff` の患者に staff_count=2 を要求し、staff_count=2 では
 * **異なる 2 テンプレート**を要求する = 同じ M を 2 つ渡せない。
 * BE 緩和は設計書 §10 の追跡事項。
 */
const MULTI_STAFF_ON_M_ERROR =
  '2名体制の患者は担当なし(M)へ入れられません。候補コースを選ぶか、プールから配置してください';

/** 他拠点候補 × (c) 新規追加は BE が拒む (H3)。 */
const OTHER_OFFICE_ON_NEW_NOTE =
  '新規追加では他拠点へ入れられません（「その週を変える」または「型も変える」なら可）';

/** 📅 曜日移動から開いたのに、その訪問が動かせないとき (H1)。 */
const PINNED_SOURCE_IMMOVABLE_ERROR = 'この予定は動かせません（当日以前/固定/予定外）';

/** 移動 (b) はコースを必ず付け替える。M テンプレートすら無い拠点では送れない (M4)。 */
const MOVE_COURSE_UNRESOLVED_ERROR = '移動先のコースを特定できません';

/** ⑤ 反映先のラジオ 1 行。ラベル全体がクリック領域 (設計 §3-5)。 */
const scopeRowCls =
  'flex cursor-pointer items-start gap-2 rounded border border-border-default px-3 py-2 hover:bg-bg-muted has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60';

function toIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fromIso(dateIso: string): Date {
  const [y, m, d] = dateIso.split('-');
  return new Date(Number(y), Number(m) - 1, Number(d));
}

/** ISO 週の月曜 (ローカル日付)。カレンダーの初期表示月に使う。 */
function localMondayOfIsoWeek(isoYear: number, isoWeek: number): Date {
  const utc = mondayOfIsoWeek(isoYear, isoWeek);
  return new Date(utc.getUTCFullYear(), utc.getUTCMonth(), utc.getUTCDate());
}

/** 選べる日付か (日曜でない・当日以前でない)。 */
function isSelectableDate(dateIso: string, todayIso: string): boolean {
  return dateIso > todayIso && isoWeekOfDate(dateIso).weekday !== 6;
}

/** 既定の選択 = 定員超でない先頭の候補。無ければ M (H4: 超過は既定にしない)。 */
function defaultKeyFor(proposal: DateProposal): string {
  return proposal.primary.find((e) => !e.slot.overcapacity)?.key ?? M_KEY;
}

/** 選んだ日付だけを残す (日付を変えたときの取り残し掃除)。 */
function pruneByDates<T>(state: Record<string, T>, dates: string[]): Record<string, T> {
  const keep = new Set(dates);
  const out: Record<string, T> = {};
  for (const [k, v] of Object.entries(state)) if (keep.has(k)) out[k] = v;
  return out;
}

// ───────────────────────────────────────────────────────────────────────────
// 本体
// ───────────────────────────────────────────────────────────────────────────

export function AddVisitAnywhereDialog({
  open,
  onOpenChange,
  isoYear,
  isoWeek,
  patients,
  poolPatientIds,
  staffOptions,
  offices,
  courseTemplates,
  initial,
  todayIso,
  proposeSlots,
  loadPatientWeekVisits,
  loadPatientSubOfficeIds,
  onExecute,
  submitting = false,
}: AddVisitAnywhereDialogProps) {
  // ── 注入された関数は ref 経由で使う (親のインライン関数で effect が回らないように)
  const proposeRef = React.useRef(proposeSlots);
  const loadWeekRef = React.useRef(loadPatientWeekVisits);
  const loadSubRef = React.useRef(loadPatientSubOfficeIds);
  React.useEffect(() => {
    proposeRef.current = proposeSlots;
    loadWeekRef.current = loadPatientWeekVisits;
    loadSubRef.current = loadPatientSubOfficeIds;
  });

  // ── ① 患者
  const [keyword, setKeyword] = React.useState('');
  const [patientId, setPatientId] = React.useState('');
  // ── ② 日付
  const [dates, setDates] = React.useState<string[]>([]);
  // ── ③ 時刻・所要・希望担当
  const [startHM, setStartHM] = React.useState('12:00');
  const [minutes, setMinutes] = React.useState(45);
  const [staffId, setStaffId] = React.useState('');
  // ── ④ 提案
  const [proposals, setProposals] = React.useState<Record<string, DateProposal> | null>(null);
  /** 提案を計算したときの入力キー。現在のキーと違えば失効 (§3-3 ④)。 */
  const [proposalKey, setProposalKey] = React.useState('');
  const [selection, setSelection] = React.useState<Record<string, string>>({});
  const [otherOk, setOtherOk] = React.useState<Record<string, boolean>>({});
  const [reasons, setReasons] = React.useState<Record<string, string>>({});
  const [searching, setSearching] = React.useState(false);
  const [progress, setProgress] = React.useState({ done: 0, total: 0 });
  const [error, setError] = React.useState<string | null>(null);
  // ── ⑤ 反映先
  const [scope, setScope] = React.useState<AddVisitScope>('new');
  const [weekVisits, setWeekVisits] = React.useState<Record<string, VisitLite[]>>({});
  const [sourceSel, setSourceSel] = React.useState<Record<string, string>>({});
  // ── ⑥ 実行中 (二重送信ガード)
  const [busy, setBusy] = React.useState(false);

  const subOfficeCache = React.useRef(new Map<string, Promise<string[]>>());
  /**
   * 📅「曜日を移動…」で開いたときの **動かす元** (H1)。
   * 日付を後から選ぶ導線なので、開いた瞬間の訪問を ref に留め、その訪問の
   * ISO 週で選んだ日付の既定にする（`loadPatientWeekVisits` の結果に含まれて
   * いなくても選べるよう、候補にも必ず混ぜる）。
   */
  const pinnedSourceRef = React.useRef<VisitLite | null>(null);

  const patient = React.useMemo(
    () => patients.find((p) => p.id === patientId) ?? null,
    [patients, patientId],
  );

  // 開き直しで初期値へ戻す。
  React.useEffect(() => {
    if (!open) return;
    subOfficeCache.current.clear();
    const initPatient = initial?.patientId ?? '';
    const found = patients.find((p) => p.id === initPatient) ?? null;
    // 過去日・日曜が initial に混ざっていても盤面の規則で落とす。
    // ただし日付固定 (lockedDates) の導線は呼出元の日付が正なので落とさない
    // (過去日を出さないのは呼出元の責任 — BE は過去日を弾かない)。
    const datesFixed = initial?.lockedDates === true;
    const initDates = Array.from(new Set(initial?.dates ?? []))
      .filter((d) => datesFixed || isSelectableDate(d, todayIso))
      .sort();
    const initStaff = initial?.staffId ?? '';
    setKeyword('');
    setPatientId(initPatient);
    setDates(initDates);
    setStartHM(initial?.sourceVisit?.start_time ?? '12:00');
    setMinutes(found?.service_minutes ?? 45);
    // 「（担当なし）」行の擬似 id 等、選択肢に無い値は「指定なし」に倒す。
    setStaffId(staffOptions.some((s) => s.id === initStaff) ? initStaff : '');
    setProposals(null);
    setProposalKey('');
    setSelection({});
    setOtherOk({});
    setReasons({});
    setSearching(false);
    setProgress({ done: 0, total: 0 });
    setError(null);
    setBusy(false);
    setScope(initial?.lockedScope === 'week' ? 'week' : 'new');
    setWeekVisits({});
    pinnedSourceRef.current = initial?.sourceVisit ?? null;
    setSourceSel({});
    // initial はダイアログを開く瞬間の値だけを見る (開いている間の再生成で入力を壊さない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const datesKey = dates.join(',');
  const currentKey = `${patientId}|${datesKey}|${startHM}|${minutes}`;
  const proposalsValid = proposals !== null && proposalKey === currentKey;
  const hasCoords = patient != null && patient.lat != null && patient.lng != null;
  const hasOffice = patient != null && patient.primary_office_id != null;

  // 患者を選んだらサブ担当拠点を先読みする (2 段目の即応性・キャッシュは患者ごと)。
  React.useEffect(() => {
    if (!open || !patient) return;
    void resolveSubOffices(patient);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, patient?.id]);

  function resolveSubOffices(p: AddVisitPatientOption): Promise<string[]> {
    const loader = loadSubRef.current;
    if (!loader) return Promise.resolve([...(p.allowed_office_ids ?? [])]);
    const hit = subOfficeCache.current.get(p.id);
    if (hit) return hit;
    // 失敗は「サブ拠点なし」に倒す (2 段目を諦めて M へ)。
    const promise = loader(p.id).catch(() => [] as string[]);
    subOfficeCache.current.set(p.id, promise);
    return promise;
  }

  // ── 患者リスト (プール優先・キーワード絞り込み)
  const patientOptions = React.useMemo(() => {
    const kw = keyword.trim();
    // 非稼働 (入院中・一時休止・解約済み・開始前) は予定に入れられないので候補に出さない
    // (判定は共通ヘルパ。コンポーネントで値を列挙しない)。
    const actives = patients.filter(
      (p) =>
        isPlaceablePatientStatus(p.status) &&
        (!kw || p.name.includes(kw) || (p.hint ?? '').includes(kw)),
    );
    const pool = actives.filter((p) => poolPatientIds.has(p.id));
    const rest = actives.filter((p) => !poolPatientIds.has(p.id));
    return [...pool, ...rest];
  }, [patients, poolPatientIds, keyword]);

  const minutesOptions = React.useMemo(
    () => durationOptions(patient?.service_minutes ?? minutes),
    [patient?.service_minutes, minutes],
  );

  const mTemplate = React.useMemo(
    () =>
      courseTemplates.find((t) => t.office_id === patient?.primary_office_id && t.label === 'M') ??
      null,
    [courseTemplates, patient?.primary_office_id],
  );
  const mLabel = mTemplate ? 'M（担当なし）' : M_FALLBACK_LABEL;

  const preferredStaffName = React.useMemo(
    () => staffOptions.find((s) => s.id === staffId)?.name ?? null,
    [staffOptions, staffId],
  );

  function handlePatientChange(next: string) {
    setPatientId(next);
    const p = patients.find((x) => x.id === next) ?? null;
    if (p?.service_minutes != null) setMinutes(p.service_minutes);
  }

  function handleDatesChange(days: Date[] | undefined) {
    const next = Array.from(new Set((days ?? []).map(toIso))).sort();
    setDates(next);
    // 選択から外れた日付の決定を残さない。
    setSelection((s) => pruneByDates(s, next));
    setOtherOk((s) => pruneByDates(s, next));
    setReasons((s) => pruneByDates(s, next));
    setSourceSel((s) => pruneByDates(s, next));
  }

  // ── ④ 提案を探す
  async function handlePropose() {
    if (!patient || dates.length === 0 || !patient.primary_office_id) return;
    const primaryOfficeIds = [patient.primary_office_id];
    const groups = groupDatesByIsoWeek(dates);
    setSearching(true);
    setError(null);
    setProgress({ done: 0, total: groups.length });
    try {
      const subOfficeIds = (await resolveSubOffices(patient)).filter(
        (id, i, arr) => id && id !== patient.primary_office_id && arr.indexOf(id) === i,
      );

      const perGroup = await Promise.all(
        groups.map(async (group) => {
          const res = await proposeRef.current(
            buildProposeRequest({
              patient,
              isoYear: group.isoYear,
              isoWeek: group.isoWeek,
              weekdays: group.weekdays,
              startHM,
              minutes,
              officeIds: primaryOfficeIds,
            }),
          );
          const all = [...res.slots, ...res.overcapacity_slots];
          // 上限に達した週は「空き無し」を断定できない (M2)。
          const truncated = all.length >= PROPOSE_LIMIT;
          const byDate = mapSlotsToDates(all, group);
          const zeroDates = truncated
            ? []
            : group.dates.filter((d) => (byDate.get(d) ?? []).length === 0);

          // 0 件の日の理由 (M1)。`excluded_summary` は週全体が 0 件のときしか
          // 埋まらないため、候補がある週では日ごとに 1 回だけ聞き直す。
          const reasonByDate = new Map<string, string | null>();
          if (zeroDates.length > 0) {
            if (all.length === 0) {
              for (const d of zeroDates) {
                reasonByDate.set(
                  d,
                  pickExcludedReason(res.excluded_summary, isoWeekOfDate(d).weekday),
                );
              }
            } else {
              const perDate = await Promise.all(
                zeroDates.map(async (d) => {
                  const wd = isoWeekOfDate(d).weekday;
                  const one = await proposeRef.current(
                    buildProposeRequest({
                      patient,
                      isoYear: group.isoYear,
                      isoWeek: group.isoWeek,
                      weekdays: [wd],
                      startHM,
                      minutes,
                      officeIds: primaryOfficeIds,
                    }),
                  );
                  return [d, pickExcludedReason(one.excluded_summary, wd)] as const;
                }),
              );
              for (const [d, code] of perDate) reasonByDate.set(d, code);
            }
          }

          // 主担当拠点で 0 件の日だけ、サブ担当拠点でもう 1 回聞く (PO 決定 11)。
          let otherByDate = new Map<string, ProposeSlotItem[]>();
          if (zeroDates.length > 0 && subOfficeIds.length > 0) {
            const res2 = await proposeRef.current(
              buildProposeRequest({
                patient,
                isoYear: group.isoYear,
                isoWeek: group.isoWeek,
                weekdays: zeroDates.map((d) => isoWeekOfDate(d).weekday),
                startHM,
                minutes,
                officeIds: subOfficeIds,
              }),
            );
            otherByDate = mapSlotsToDates(
              // 他拠点の M は出さない (M は常に自拠点・§5)。
              [...res2.slots, ...res2.overcapacity_slots].filter(
                (s) => !isMCourseCode(s.course_code),
              ),
              { dates: zeroDates },
            );
          }

          setProgress((p) => ({ ...p, done: p.done + 1 }));
          return { group, byDate, otherByDate, reasonByDate, truncated, zeroDates };
        }),
      );

      const next: Record<string, DateProposal> = {};
      const nextSelection: Record<string, string> = {};
      for (const g of perGroup) {
        for (const date of g.group.dates) {
          const isZero = g.zeroDates.includes(date);
          const proposal: DateProposal = {
            primary: (g.byDate.get(date) ?? []).map((slot, i) => ({ key: `p${i}`, slot })),
            other: (g.otherByDate.get(date) ?? []).map((slot, i) => ({ key: `o${i}`, slot })),
            excludedReason: g.reasonByDate.get(date) ?? null,
            reasonUnavailable: isZero && !g.reasonByDate.get(date),
            truncated: g.truncated,
          };
          next[date] = proposal;
          nextSelection[date] = defaultKeyFor(proposal);
        }
      }
      setProposals(next);
      setSelection(nextSelection);
      setOtherOk({});
      setProposalKey(currentKey);
    } catch (e) {
      setProposals(null);
      setError(e instanceof Error ? e.message : '提案の取得に失敗しました');
    } finally {
      setSearching(false);
    }
  }

  // ── ⑤ その週を変える: 患者の当該週の訪問を読む
  React.useEffect(() => {
    if (!open || scope !== 'week' || !patient || dates.length === 0) return;
    let cancelled = false;
    const groups = groupDatesByIsoWeek(dates);
    Promise.all(
      groups.map(async (g) => {
        const visits = await loadWeekRef.current(patient.id, g.isoYear, g.isoWeek);
        return [`${g.isoYear}-${g.isoWeek}`, visits] as const;
      }),
    )
      .then((entries) => {
        if (!cancelled) setWeekVisits(Object.fromEntries(entries));
      })
      .catch(() => {
        if (!cancelled) setWeekVisits({});
      });
    return () => {
      cancelled = true;
    };
    // dates は毎回新しい配列になるため、内容の等価な `datesKey` を依存に使う。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scope, patient, datesKey]);

  /** 📅 曜日移動で指定された「動かす元」が、その日付と同じ ISO 週にあるか (H1)。 */
  function pinnedSourceFor(date: string): VisitLite | null {
    const pinned = pinnedSourceRef.current;
    if (!pinned) return null;
    const a = isoWeekOfDate(date);
    const b = isoWeekOfDate(pinned.visit_date);
    return a.isoYear === b.isoYear && a.isoWeek === b.isoWeek ? pinned : null;
  }

  /**
   * その日付の週で「動かす元」に選べる訪問 (planned・青ピン以外・未来日)。
   *
   * 📅 曜日移動で指定された訪問は、週の読み込みが済んでいなくても・絞り込みで
   * 落ちても **必ず候補に入れる**（押した予定を動かすのがこの導線の目的・H1）。
   * 動かせない訪問なら `rowError` が登録を止める。
   */
  function movableVisits(date: string): VisitLite[] {
    const { isoYear: y, isoWeek: w } = isoWeekOfDate(date);
    const list = (weekVisits[`${y}-${w}`] ?? []).filter((v) => isMovableSourceVisit(v, todayIso));
    const pinned = pinnedSourceFor(date);
    if (!pinned || list.some((v) => v.id === pinned.id)) return list;
    return [pinned, ...list];
  }

  // (a) は日付 1 つのときだけ。日付が増えたら黙って (c) 扱いにする (取り残し防止)。
  const patternAllowed = canChoosePatternScope(dates);
  const scopeLocked = initial?.lockedScope != null;
  /** 日付固定で開いたか (カレンダー ② を出さない)。 */
  const datesLocked = initial?.lockedDates === true;
  const effectiveScope: AddVisitScope = scope === 'pattern' && !patternAllowed ? 'new' : scope;

  /**
   * 日付 → 動かす元。**同じ訪問を 2 つの日付に割り当てない**（1 パスで解決）。
   * 足りない日付は null = その項目だけ scope 'new' に落ちる。
   */
  const sourceAssignment = React.useMemo(() => {
    if (effectiveScope !== 'week') return new Map<string, VisitLite | null>();
    // 📅 曜日移動で開いたときは、その訪問の週に入る日付の **既定** を pinned にする。
    // 手で選び直した日 (`sourceSel`) が優先。1 訪問 = 1 日付は assignSourceVisits が守る。
    const pinnedDefaults: Record<string, string> = {};
    for (const d of dates) {
      const pinned = pinnedSourceFor(d);
      if (pinned) pinnedDefaults[d] = pinned.id;
    }
    return assignSourceVisits({
      dates,
      candidatesByDate: movableVisits,
      overrides: { ...pinnedDefaults, ...sourceSel },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveScope, datesKey, weekVisits, sourceSel, todayIso]);

  const usedSourceIds = React.useMemo(() => {
    const map = new Map<string, string>();
    for (const [date, visit] of sourceAssignment) if (visit) map.set(date, visit.id);
    return map;
  }, [sourceAssignment]);

  /**
   * その日付が **実際に**どの API を叩くか。反映先が (b) でもその週に動かす元が
   * 無い日は (c) 新規追加に落ちる (§3-3 ⑤)。他拠点の可否はこちらで判定する。
   */
  function itemScopeOf(date: string): AddVisitScope {
    if (effectiveScope !== 'week') return effectiveScope;
    return sourceAssignment.get(date) == null ? 'new' : 'week';
  }

  /**
   * H3: 実行が (c) 新規追加になる日は、選んでいた他拠点候補を既定へ戻す。
   * `place-and-fix` は拠点跨ぎのテンプレートを 422 で拒むため、選べない選択を
   * 画面に残さない（登録ボタンだけ死んでいる状態を作らない）。
   */
  const staleOtherDates = dates.filter(
    (d) => (selection[d] ?? '').startsWith('o') && proposals?.[d] && itemScopeOf(d) === 'new',
  );
  const staleOtherKey = staleOtherDates.join(',');
  React.useEffect(() => {
    if (staleOtherDates.length === 0) return;
    setSelection((cur) => {
      const next = { ...cur };
      for (const date of staleOtherDates) {
        const p = proposals?.[date];
        if (p) next[date] = defaultKeyFor(p);
      }
      return next;
    });
    setOtherOk({});
    // 日付の集合が変わったときだけ走らせる (setSelection の結果で再入しない)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [staleOtherKey]);

  // ── 決定の解決
  function entryOf(date: string, key: string): CandidateEntry | null {
    const p = proposals?.[date];
    if (!p) return null;
    return [...p.primary, ...p.other].find((e) => e.key === key) ?? null;
  }

  function selectedKey(date: string): string {
    return selection[date] ?? M_KEY;
  }

  /** slot → コーステンプレート id (拠点 × コード・`findCourseForTemplate` と同規則)。 */
  function templateIdOf(slot: ProposeSlotItem): string | null {
    return matchCourseTemplate(courseTemplates, slot.office_id, slot.course_code)?.id ?? null;
  }

  /** その日の選択が M（担当なし）か (テンプレートが無い「臨」も含む)。 */
  function isMSelectedOn(date: string): boolean {
    const key = selectedKey(date);
    if (key === M_KEY) return true;
    const slot = entryOf(date, key)?.slot ?? null;
    return slot == null || isMCourseCode(slot.course_code);
  }

  /** この日の選択が登録できない理由 (あれば登録ボタンを止める)。 */
  function rowError(date: string): string | null {
    // H1: 押した予定が動かせない (当日以前 / 青ピン / planned でない)。
    const pinned = pinnedSourceFor(date);
    if (pinned && !isMovableSourceVisit(pinned, todayIso)) {
      return PINNED_SOURCE_IMMOVABLE_ERROR;
    }
    const key = selectedKey(date);
    const isM = isMSelectedOn(date);
    // H2: 2 名体制 × M は BE が実行できない組み合わせ。入口で塞ぐ。
    if (isM && patient?.requires_multiple_staff) return MULTI_STAFF_ON_M_ERROR;
    // M4: 移動 (b) はコースを必ず付け替える。M テンプレートすら無い拠点では送れない。
    if (itemScopeOf(date) === 'week' && isM && mTemplate == null) {
      return MOVE_COURSE_UNRESOLVED_ERROR;
    }
    if (key === M_KEY) return null;
    const slot = entryOf(date, key)?.slot ?? null;
    if (!slot) return null;
    // H3: 他拠点候補 × (c) 新規追加は place-and-fix が拒む。
    if (key.startsWith('o') && itemScopeOf(date) === 'new') return OTHER_OFFICE_ON_NEW_NOTE;
    if (!templateIdOf(slot)) return `コースを特定できません（${slot.course_label}）`;
    if (patient?.requires_multiple_staff && !slot.partner_course_template_id) {
      return '2名体制の相方コースが特定できません（別の候補を選んでください）';
    }
    return null;
  }

  function buildItem(date: string): AddVisitPlanItem {
    const { isoYear: y, isoWeek: w, weekday } = isoWeekOfDate(date);
    const key = selectedKey(date);
    const entry = key === M_KEY ? null : entryOf(date, key);
    const slot = entry?.slot ?? null;
    const isM = slot == null || isMCourseCode(slot.course_code);
    const isOtherOffice = entry != null && entry.key.startsWith('o');
    const source = effectiveScope === 'week' ? (sourceAssignment.get(date) ?? null) : null;
    const itemScope: AddVisitScope =
      effectiveScope === 'week' && source == null ? 'new' : effectiveScope;
    const reason = reasons[date]?.trim() ?? '';
    // 2 名体制は候補コース（相方つき）でのみ登録できる。M は `rowError` が塞ぐ (H2)。
    const twoStaff = patient?.requires_multiple_staff === true;
    return {
      date,
      isoYear: y,
      isoWeek: w,
      weekday,
      startHM,
      minutes,
      officeId: slot ? slot.office_id : (patient?.primary_office_id ?? null),
      courseTemplateId: slot ? templateIdOf(slot) : (mTemplate?.id ?? null),
      courseLabel: slot ? slot.course_label : mLabel,
      isM,
      isOtherOffice,
      staffCount: twoStaff && !isM ? 2 : 1,
      partnerCourseTemplateId: isM ? null : (slot?.partner_course_template_id ?? null),
      reason: isM && reason !== '' ? reason : null,
      scope: itemScope,
      sourceVisit: source,
      noCandidateReason: proposals?.[date]?.excludedReason ?? null,
    };
  }

  /** 提案が要らない (座標なし) か、提案が有効か。 */
  const decisionsReady =
    patient != null && hasOffice && dates.length > 0 && (!hasCoords || proposalsValid);

  /** 「他拠点」を選んでいるのに確認チェックが無い日があるか。 */
  const otherUnconfirmed = dates.some(
    (d) => selectedKey(d).startsWith('o') && !(otherOk[d] ?? false),
  );
  const blockedRows = dates.filter((d) => rowError(d) !== null);

  const canSubmit =
    decisionsReady &&
    !otherUnconfirmed &&
    blockedRows.length === 0 &&
    !searching &&
    !submitting &&
    !busy;

  async function handleSubmit() {
    if (!patient || !canSubmit) return;
    setError(null);
    setBusy(true);
    const plan: AddVisitPlan = { patientId: patient.id, items: dates.map(buildItem) };
    try {
      await onExecute(plan);
      onOpenChange(false);
    } catch (e) {
      // 失敗時は開いたまま (結果表示は親)。
      setError(e instanceof Error ? e.message : '登録に失敗しました');
    } finally {
      setBusy(false);
    }
  }

  const selectedDays = React.useMemo(() => dates.map(fromIso), [dates]);
  const defaultMonth = React.useMemo(
    () => localMondayOfIsoWeek(isoYear, isoWeek),
    [isoYear, isoWeek],
  );
  const todayDate = React.useMemo(() => fromIso(todayIso), [todayIso]);

  /** 希望担当のコースを先頭へ (安定ソート・判定は変えない)。 */
  function orderCandidates(list: CandidateEntry[]): CandidateEntry[] {
    if (!preferredStaffName) return list;
    const hit = list.filter((e) => e.slot.staff_name === preferredStaffName);
    if (hit.length === 0) return list;
    return [...hit, ...list.filter((e) => e.slot.staff_name !== preferredStaffName)];
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-w-5xl flex-col overflow-hidden" data-testid="ava-dialog">
        <DialogHeader>
          <DialogTitle>＋ 訪問を追加</DialogTitle>
          <DialogDescription>
            患者・日付（複数可）・時刻を決めると、その週のスケジュールを見て入れられるコースを提案します。
          </DialogDescription>
        </DialogHeader>

        {/*
          設計 §3-5: 2 列 (左 = ①②③ / 右 = ④⑤)。狭い画面では 1 列に落ちる。
          DOM 順 = 左列 → 右列 なので、フォーカス順も ①→②→③→④→⑤→フッタ。
        */}
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 overflow-y-auto pr-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
          <div className="space-y-5">
            {/* ① 患者 */}
            <section className="space-y-2">
              <Label className="text-base font-semibold">① 患者</Label>
              <Input
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="名前で絞り込む"
                className="h-9"
                data-testid="ava-patient-search"
                aria-label="患者を検索"
              />
              <select
                className={selectCls}
                value={patientId}
                onChange={(e) => handlePatientChange(e.target.value)}
                disabled={submitting || busy}
                data-testid="ava-patient"
                aria-label="患者"
              >
                <option value="">選ぶ…</option>
                {patientOptions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {poolPatientIds.has(p.id) ? '（不足あり）' : ''}
                  </option>
                ))}
              </select>
              {patient ? (
                <p className="text-xs text-text-muted" data-testid="ava-patient-hint">
                  基本 {patient.service_minutes ?? '—'} 分{patient.hint ? `・${patient.hint}` : ''}
                  {patient.primary_office_id
                    ? `・${offices.find((o) => o.id === patient.primary_office_id)?.name ?? ''}`
                    : ''}
                  {patient.requires_multiple_staff ? '・2名体制' : ''}
                </p>
              ) : null}
              {patient && !hasCoords ? (
                <p className="text-sm text-warning-strong" data-testid="ava-no-coords">
                  住所の座標が無いため提案できません（M へは入れられます）
                </p>
              ) : null}
              {patient && !hasOffice ? (
                <p className="text-sm text-warning-strong" data-testid="ava-no-office">
                  主担当拠点が未設定のため提案できません
                </p>
              ) : null}
            </section>

            {/* ② 日付 */}
            <section className="space-y-2">
              <Label className="text-base font-semibold">② 日付</Label>
              {datesLocked ? (
                <p className="text-sm text-text-secondary" data-testid="ava-dates-locked">
                  日付は決まっています（この日に入れます）
                </p>
              ) : (
                <div className="rounded border border-border-default" data-testid="ava-calendar">
                  <Calendar
                    mode="multiple"
                    selected={selectedDays}
                    onSelect={handleDatesChange}
                    defaultMonth={defaultMonth}
                    disabled={[{ before: todayDate }, { dayOfWeek: [0] }, todayDate]}
                    className="mx-auto w-fit"
                  />
                </div>
              )}
              <div className="flex items-start gap-2">
                <div
                  className="flex flex-1 flex-wrap items-center gap-1.5 text-sm"
                  data-testid="ava-selected-dates"
                >
                  {dates.length === 0 ? (
                    <span className="text-text-muted">
                      日付を選んでください（日曜と当日以前は選べません）
                    </span>
                  ) : (
                    <>
                      <span className="text-text-muted">{'選択中: '}</span>
                      {dates.map((d) => (
                        <span key={d} className="rounded-full bg-bg-muted px-2 py-0.5 text-sm">
                          {formatDateLabel(d)}
                        </span>
                      ))}
                    </>
                  )}
                </div>
                {dates.length > 0 && !datesLocked ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => handleDatesChange([])}
                    data-testid="ava-clear-dates"
                  >
                    クリア
                  </Button>
                ) : null}
              </div>
            </section>

            {/* ③ 時刻・所要・希望担当 */}
            <section className="space-y-2">
              <Label className="text-base font-semibold">③ 時刻・所要・希望担当</Label>
              <div className="flex gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <Label className="text-xs text-text-muted">開始</Label>
                  <select
                    className={selectCls}
                    value={startHM}
                    onChange={(e) => setStartHM(e.target.value)}
                    disabled={submitting || busy}
                    data-testid="ava-start"
                    aria-label="開始"
                  >
                    {(TIME_OPTIONS.includes(startHM)
                      ? TIME_OPTIONS
                      : [...TIME_OPTIONS, startHM].sort()
                    ).map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="min-w-0 flex-1 space-y-1">
                  <Label className="text-xs text-text-muted">所要</Label>
                  <select
                    className={selectCls}
                    value={String(minutes)}
                    onChange={(e) => setMinutes(Number.parseInt(e.target.value, 10))}
                    disabled={submitting || busy}
                    data-testid="ava-minutes"
                    aria-label="所要"
                  >
                    {minutesOptions.map((m) => (
                      <option key={m} value={m}>
                        {m}分
                      </option>
                    ))}
                  </select>
                </div>
                <div className="min-w-0 flex-1 space-y-1">
                  <Label className="text-xs text-text-muted">希望担当</Label>
                  <select
                    className={selectCls}
                    value={staffId}
                    onChange={(e) => setStaffId(e.target.value)}
                    disabled={submitting || busy}
                    data-testid="ava-staff"
                    aria-label="希望担当"
                  >
                    <option value="">（指定なし）</option>
                    {staffOptions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </section>
          </div>

          {/* 右列は候補が多いと長くなるので独立してスクロールさせる (設計 §3-5)。 */}
          <div className="space-y-5 lg:pr-2">
            {/* ④ 提案 */}
            <section className="space-y-2">
              <div className="flex flex-wrap items-center gap-3">
                <Label className="text-base font-semibold">④ 提案</Label>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    !patient || dates.length === 0 || searching || busy || !hasCoords || !hasOffice
                  }
                  onClick={() => void handlePropose()}
                  data-testid="ava-propose"
                >
                  🔍 入れる場所を探す
                </Button>
                {searching ? (
                  <span className="text-sm text-text-muted" data-testid="ava-progress">
                    探しています…（{progress.done}/{progress.total} 週）
                  </span>
                ) : null}
                {proposals && !proposalsValid && !searching ? (
                  <span className="text-sm text-warning-strong" data-testid="ava-stale">
                    条件が変わりました。もう一度探してください
                  </span>
                ) : null}
              </div>

              {error ? (
                <p className="text-sm text-error" data-testid="ava-error">
                  {error}
                </p>
              ) : null}

              {dates.map((date) => {
                const p = proposalsValid ? (proposals?.[date] ?? null) : null;
                if (!p && hasCoords) return null;
                const key = selectedKey(date);
                return (
                  <DateProposalRow
                    key={date}
                    date={date}
                    startHM={startHM}
                    proposal={p}
                    selectedKey={key}
                    mLabel={mLabel}
                    isMSelected={isMSelectedOn(date)}
                    otherDisabledNote={
                      itemScopeOf(date) === 'new' ? OTHER_OFFICE_ON_NEW_NOTE : null
                    }
                    otherOk={otherOk[date] ?? false}
                    reason={reasons[date] ?? ''}
                    error={rowError(date)}
                    disabled={submitting || busy}
                    orderCandidates={orderCandidates}
                    onSelect={(next) => setSelection((s) => ({ ...s, [date]: next }))}
                    onToggleOther={(next) => {
                      setOtherOk((s) => ({ ...s, [date]: next }));
                      // 承知チェックを外したら他拠点の選択も戻す (宙ぶらりんにしない)。
                      if (!next && p) {
                        setSelection((s) =>
                          s[date]?.startsWith('o') ? { ...s, [date]: defaultKeyFor(p) } : s,
                        );
                      }
                    }}
                    onReasonChange={(next) => setReasons((s) => ({ ...s, [date]: next }))}
                  />
                );
              })}
            </section>

            {/* ⑤ 反映先 */}
            <section className="space-y-2">
              <Label className="text-base font-semibold">⑤ 反映先</Label>
              {initial?.lockedScope === 'new' ? (
                <p className="text-sm text-text-secondary" data-testid="ava-scope-locked-note">
                  特別訪問週間の追加枠として登録します（型は変えません）
                </p>
              ) : null}
              <div className="space-y-1.5">
                <label className={scopeRowCls}>
                  <input
                    type="radio"
                    className="mt-0.5 h-4 w-4"
                    name="ava-scope"
                    checked={effectiveScope === 'pattern'}
                    disabled={!patternAllowed || scopeLocked || submitting || busy}
                    onChange={() => setScope('pattern')}
                    data-testid="ava-scope-pattern"
                  />
                  <span className="text-sm">
                    固定訪問スケジュール（型）も変える
                    {!patternAllowed ? (
                      <span className="block text-xs text-text-muted">
                        ※ 日付が 1 つのときだけ選べます
                      </span>
                    ) : null}
                  </span>
                </label>
                <label className={scopeRowCls}>
                  <input
                    type="radio"
                    className="mt-0.5 h-4 w-4"
                    name="ava-scope"
                    checked={effectiveScope === 'week'}
                    disabled={scopeLocked || submitting || busy}
                    onChange={() => setScope('week')}
                    data-testid="ava-scope-week"
                  />
                  <span className="text-sm">
                    その週のスケジュールを変える
                    <span className="block text-xs text-text-muted">
                      既存の予定を動かす・型は変えない
                    </span>
                  </span>
                </label>
                <label className={scopeRowCls}>
                  <input
                    type="radio"
                    className="mt-0.5 h-4 w-4"
                    name="ava-scope"
                    checked={effectiveScope === 'new'}
                    disabled={scopeLocked || submitting || busy}
                    onChange={() => setScope('new')}
                    data-testid="ava-scope-new"
                  />
                  <span className="text-sm">
                    新しく 1 件追加する
                    <span className="block text-xs text-text-muted">
                      既存の予定はそのまま・型は変えない
                    </span>
                  </span>
                </label>
              </div>

              {effectiveScope === 'week'
                ? dates.map((date) => {
                    const mine = usedSourceIds.get(date) ?? '';
                    const used = new Set(
                      [...usedSourceIds.entries()].filter(([d]) => d !== date).map(([, id]) => id),
                    );
                    return (
                      <WeekSourceRow
                        key={date}
                        date={date}
                        candidates={movableVisits(date)}
                        selectedId={mine}
                        usedByOtherDates={used}
                        disabled={submitting || busy}
                        onChange={(visitId) => setSourceSel((s) => ({ ...s, [date]: visitId }))}
                      />
                    );
                  })
                : null}
            </section>
          </div>
        </div>

        <DialogFooter>
          {otherUnconfirmed ? (
            <span className="mr-auto text-sm text-warning-strong" data-testid="ava-blocked">
              他拠点の候補を選ぶには「拠点跨ぎを承知で入れる」のチェックが要ります
            </span>
          ) : null}
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => onOpenChange(false)}
          >
            キャンセル
          </Button>
          <Button
            type="button"
            disabled={!canSubmit}
            onClick={() => void handleSubmit()}
            data-testid="ava-submit"
          >
            {dates.length === 0 ? '登録する' : `${dates.length} 件を登録する`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
