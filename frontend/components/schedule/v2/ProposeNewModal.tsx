'use client';

/**
 * ProposeNewModal — 親機 (デスクトップ) 統合提案モーダル「＋新規提案」(StageA + C + B).
 *
 * CourseDayTablePanel Row 1 の「＋新規提案」から開く。モバイル現場ボード `/m` の
 * SuggestSheet と同じ propose-slots フローを **親機意匠** (Radix Dialog / ui/* 部品 /
 * Teal `brand-primary` / Serif 見出し) で再構成したもの。モバイルの warm 配色は持ち込まない。
 *
 * フロー (3 ステップ):
 *   1. 候補ソース切替 (タブ): プールから選ぶ / 既存患者を検索 / 新規の方。
 *      - プール / 既存: 患者を選んで住所・希望をプリフィル (existing_patient_id 付与)。
 *      - 新規: 住所 + 希望 + カルテ項目 (氏名/コード/カナ/性別/保険) を入力。
 *   2. 「提案する」で POST /propose-slots (現在の iso_year/iso_week・拠点) を呼ぶ。
 *      - StageC: カバレッジ「希望 週N日 → M/N日 提案可」+ per_day ○/× バッジ。
 *      - StageB: 候補リスト (コース/曜日/時刻/警告) + 各候補に当日ミニスケジュール
 *        (既存訪問 + 提案枠を時刻順・「ここに入れますか」)。曜日ごとに採用枠を 1 つ選ぶ。
 *   3. 採用 → 確定 (manager 直接・承認不要):
 *      - プール / 既存 (登録済): 採用枠を PUT /patients/{id}/fixed-visits (normal) で確定。
 *      - 新規: 採用後にカルテ確認フォーム → POST /patients (作成) → PUT fixed-visits の 2 段階。
 *
 * diff-add (プール投入) は別ボタンで併存し、本モーダルは一切干渉しない。
 */
import * as React from 'react';
import { useQueries } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import {
  AlertTriangle,
  ArrowRight,
  CalendarRange,
  Check,
  CheckCircle2,
  ClipboardCheck,
  Link2,
  Loader2,
  Plus,
  Search,
  Sparkles,
  Users,
} from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { fetcher } from '@/lib/api/fetcher';
import {
  useProposeSlots,
  WEEKDAY_CODE_TO_INT,
  proposeWarningLabel,
} from '@/lib/queries/fieldBoard';
import { useCreatePatient, usePatients } from '@/lib/queries/patients';
import { useFixedVisits } from '@/lib/queries/patient_fixed_visits';
import { useConfirmFixedVisits } from '@/lib/queries/propose_confirm';
import {
  coerceWeeklyPattern,
  emptyPatientFormValues,
  normalizePatientSexRestriction,
  INSURANCE_LABEL,
  INSURANCE_OPTIONS,
  SERVICE_MINUTES_OPTIONS,
  DEFAULT_SERVICE_MINUTES,
  SEX_LABEL,
  SEX_OPTIONS,
  SEX_RESTRICTION_LABEL,
  SEX_RESTRICTION_OPTIONS,
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  VISIT_FREQUENCY_OPTIONS,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  type PatientFormValues,
  type PatientRead,
  type WeekdayKey,
} from '@/lib/schemas/patient';
import { type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type {
  ProposeCoverage,
  ProposeMiniScheduleEntry,
  ProposeSlotItem,
  ProposeTimeType,
  WeekdayCode,
} from '@/lib/schemas/v2/propose_slots';
import type {
  PatientFixedVisitsBulkPut,
  PatientFixedVisitV2Base,
  PatientFixedVisitV2Read,
} from '@/lib/schemas/v2/patient_fixed_visit';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

type SourceTab = 'pool' | 'existing' | 'new';

const WEEKDAY_INT_TO_JA: Record<number, string> = (() => {
  const ja = ['月', '火', '水', '木', '金', '土', '日'];
  const out: Record<number, string> = {};
  for (const code of WEEKDAY_KEYS)
    out[WEEKDAY_CODE_TO_INT[code]] = ja[WEEKDAY_CODE_TO_INT[code]] ?? '';
  return out;
})();

/** 30 分刻みの希望時刻候補 (06:00〜20:00)。 */
const TIME_OPTIONS: string[] = (() => {
  const out: string[] = [];
  for (let m = 6 * 60; m <= 20 * 60; m += 30) {
    const h = Math.floor(m / 60);
    const mm = m % 60;
    out.push(`${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
  }
  return out;
})();

const FREQ_OPTIONS = [1, 2, 3, 4, 5, 6, 7] as const;

const slotKey = (s: ProposeSlotItem) =>
  `${s.office_id}-${s.weekday}-${s.course_code}-${s.start_time}`;

/** 枠の start_time〜end_time から所要分を算出。パース不能なら null。 */
function slotDurationMin(s: ProposeSlotItem): number | null {
  const parse = (hm: string | null | undefined): number | null => {
    if (!hm) return null;
    const m = /^(\d{1,2}):(\d{2})/.exec(hm);
    if (!m) return null;
    return Number(m[1]) * 60 + Number(m[2]);
  };
  const a = parse(s.start_time);
  const b = parse(s.end_time);
  if (a === null || b === null || b <= a) return null;
  return b - a;
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface ProposeNewModalProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** 単一拠点モード時の対象拠点 ID。null = 全拠点 (office_ids 空配列で全拠点検索)。 */
  officeId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function ProposeNewModal({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
}: ProposeNewModalProps) {
  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  // ─── 候補ソース ────────────────────────────────────────────────────
  const [tab, setTab] = React.useState<SourceTab>('pool');

  // ─── 患者紐付け (プール / 既存) ─────────────────────────────────────
  const [linkedPatient, setLinkedPatient] = React.useState<PatientRead | null>(null);
  const [linkedLat, setLinkedLat] = React.useState<number | null>(null);
  const [linkedLng, setLinkedLng] = React.useState<number | null>(null);
  const [search, setSearch] = React.useState('');

  // ─── 希望条件 (共通) ───────────────────────────────────────────────
  const [addr, setAddr] = React.useState('');
  const [frequencyPerWeek, setFrequencyPerWeek] = React.useState(1);
  const [visitFrequency, setVisitFrequency] =
    React.useState<(typeof VISIT_FREQUENCY_OPTIONS)[number]>('every');
  const [weekdays, setWeekdays] = React.useState<Record<WeekdayKey, boolean>>({
    Mon: false,
    Tue: false,
    Wed: false,
    Thu: false,
    Fri: false,
    Sat: false,
    Sun: false,
  });
  const [serviceMinutes, setServiceMinutes] = React.useState(DEFAULT_SERVICE_MINUTES);
  const [timeType, setTimeType] = React.useState<(typeof TIME_TYPE_OPTIONS)[number]>('終日');
  const [preferredStart, setPreferredStart] = React.useState('09:00');
  const [preferredEnd, setPreferredEnd] = React.useState('12:00');
  const [requiresMultipleStaff, setRequiresMultipleStaff] = React.useState(false);
  const [sexRestriction, setSexRestriction] = React.useState<
    '' | (typeof SEX_RESTRICTION_OPTIONS)[number]
  >('');

  // ─── 新規カルテ項目 ────────────────────────────────────────────────
  const [karteName, setKarteName] = React.useState('');
  const [karteCode, setKarteCode] = React.useState('');
  const [karteKana, setKarteKana] = React.useState('');
  const [karteSex, setKarteSex] = React.useState<'' | (typeof SEX_OPTIONS)[number]>('');
  const [karteInsurance, setKarteInsurance] = React.useState<
    '' | (typeof INSURANCE_OPTIONS)[number]
  >('');

  // ─── 採用枠 (曜日 → 提案枠) ─────────────────────────────────────────
  const [adopted, setAdopted] = React.useState<Map<WeekdayKey, ProposeSlotItem>>(() => new Map());

  // ─── 新規 確定フォーム表示フラグ (2 段階目) ──────────────────────────
  const [showNewConfirm, setShowNewConfirm] = React.useState(false);

  const proposeMut = useProposeSlots();
  const createPatientMut = useCreatePatient();
  const confirmMut = useConfirmFixedVisits();

  // 既存/プール患者の現在の normal 固定枠を取得 (マージ確定用)。
  // backend PUT /fixed-visits は当該 (patient, normal) を全削除→INSERT する破壊的更新の
  // ため、採用しなかった曜日の既存枠を消さないよう、確定前に現状を取り込んでマージする。
  // 新規タブ / 未選択時は patientId='' で fetch を抑止する。
  const existingFixedVisitsQuery = useFixedVisits(linkedPatient?.id ?? '', 'normal');
  const existingNormalVisits = React.useMemo(
    () => existingFixedVisitsQuery.data ?? [],
    [existingFixedVisitsQuery.data],
  );

  const isExisting = tab === 'existing';
  const isPool = tab === 'pool';
  const isNew = tab === 'new';

  // ─── open / tab 変更時の state リセット ─────────────────────────────
  const resetAll = React.useCallback(() => {
    setLinkedPatient(null);
    setLinkedLat(null);
    setLinkedLng(null);
    setSearch('');
    setAddr('');
    setFrequencyPerWeek(1);
    setVisitFrequency('every');
    setWeekdays({
      Mon: false,
      Tue: false,
      Wed: false,
      Thu: false,
      Fri: false,
      Sat: false,
      Sun: false,
    });
    setServiceMinutes(DEFAULT_SERVICE_MINUTES);
    setTimeType('終日');
    setPreferredStart('09:00');
    setPreferredEnd('12:00');
    setRequiresMultipleStaff(false);
    setSexRestriction('');
    setKarteName('');
    setKarteCode('');
    setKarteKana('');
    setKarteSex('');
    setKarteInsurance('');
    setAdopted(new Map());
    setShowNewConfirm(false);
    proposeMut.reset();
  }, [proposeMut]);

  React.useEffect(() => {
    if (!open) return;
    resetAll();
    setTab('pool');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const switchTab = (next: SourceTab) => {
    if (next === tab) return;
    resetAll();
    setTab(next);
  };

  // ─── 患者一覧 (プール / 既存検索) ────────────────────────────────────
  // プールタブ: 候補となる active 患者を一覧。既存タブ: 検索語で絞る。
  // 新規タブでは患者一覧 fetch を抑止 (enabled:false)。
  const patientsQuery = usePatients({
    search: isExisting ? search.trim() : '',
    limit: isPool ? 200 : 12,
    enabled: open && !isNew,
  });
  const patientItems = React.useMemo(() => patientsQuery.data?.items ?? [], [patientsQuery.data]);

  // プールタブの候補: 希望訪問あり (frequency_per_week>0) の active 患者を優先表示。
  const poolCandidates = React.useMemo(() => {
    if (!isPool) return [] as PatientRead[];
    const term = search.trim().toLowerCase();
    return patientItems.filter((p) => {
      if (p.status !== 'active') return false;
      if (!term) return true;
      const hay = `${p.name ?? ''} ${p.kana ?? ''} ${p.code ?? ''}`.toLowerCase();
      return hay.includes(term);
    });
  }, [isPool, patientItems, search]);

  const existingCandidates = React.useMemo(() => {
    if (!isExisting || !search.trim()) return [] as PatientRead[];
    return patientItems;
  }, [isExisting, patientItems, search]);

  // ─── 採用枠 → course_template_id 解決のため、関係拠点の course-templates を取得 ──
  // propose 結果の slots に出現する office_id の course-templates を並列 fetch し、
  // (office_id, label) → course_template_id を引けるようにする (PUT fixed-visits 用)。
  const result = proposeMut.data;
  const slots = React.useMemo(() => result?.slots ?? [], [result]);
  const coverage = result?.coverage ?? null;

  const slotOfficeIds = React.useMemo(() => {
    const s = new Set<string>();
    for (const sl of slots) s.add(sl.office_id);
    return Array.from(s).sort();
  }, [slots]);

  const templatesQueries = useQueries({
    queries: slotOfficeIds.map((oid) => ({
      queryKey: ['course-templates', 'list', oid] as const,
      enabled: sessionStatus === 'authenticated' && Boolean(oid),
      staleTime: 5 * 60 * 1000,
      queryFn: () =>
        fetcher<CourseTemplateRead[]>(
          `/api/v1/course-templates?office_id=${encodeURIComponent(oid)}`,
          { accessToken, refreshToken },
        ),
    })),
  });
  const templatesDepKey = templatesQueries.map((q) => `${q.dataUpdatedAt}:${q.status}`).join(',');
  const courseTemplateId = React.useMemo(() => {
    // (office_id, label[大文字]) → course_template_id。
    const m = new Map<string, string>();
    for (const q of templatesQueries) {
      for (const t of q.data ?? []) {
        if (t.deleted_at) continue;
        m.set(`${t.office_id}:${(t.label ?? '').trim().toUpperCase()}`, t.id);
      }
    }
    return (officeId2: string, code: string): string | null =>
      m.get(`${officeId2}:${code.trim().toUpperCase()}`) ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templatesDepKey]);

  // ─── 患者プリフィル ──────────────────────────────────────────────
  const linkPatient = (p: PatientRead) => {
    setLinkedPatient(p);
    const wp = coerceWeeklyPattern(p.weekly_pattern);
    if (p.address) setAddr(p.address);
    setLinkedLat(typeof p.lat === 'number' ? p.lat : null);
    setLinkedLng(typeof p.lng === 'number' ? p.lng : null);
    setFrequencyPerWeek(wp.frequency_per_week);
    if (wp.visit_frequency) setVisitFrequency(wp.visit_frequency);
    setWeekdays({
      Mon: wp.preferred_weekdays.includes('Mon'),
      Tue: wp.preferred_weekdays.includes('Tue'),
      Wed: wp.preferred_weekdays.includes('Wed'),
      Thu: wp.preferred_weekdays.includes('Thu'),
      Fri: wp.preferred_weekdays.includes('Fri'),
      Sat: wp.preferred_weekdays.includes('Sat'),
      Sun: wp.preferred_weekdays.includes('Sun'),
    });
    setServiceMinutes(wp.service_minutes);
    setTimeType(wp.time_type);
    if (wp.preferred_start) setPreferredStart(wp.preferred_start);
    if (wp.preferred_end) setPreferredEnd(wp.preferred_end);
    setSexRestriction(
      (normalizePatientSexRestriction(p.sex_restriction as string | null | undefined) ?? '') as
        | ''
        | (typeof SEX_RESTRICTION_OPTIONS)[number],
    );
    setRequiresMultipleStaff(
      (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true,
    );
    setSearch('');
    setAdopted(new Map());
  };

  const unlinkPatient = () => {
    setLinkedPatient(null);
    setLinkedLat(null);
    setLinkedLng(null);
    setAdopted(new Map());
  };

  const showTimeRange = timeType === '固定' || timeType === '時間帯';
  const showEnd = timeType === '時間帯';

  // ─── 提案する ──────────────────────────────────────────────────────
  const runPropose = () => {
    if (!addr.trim()) {
      toast.warning('住所を入力してください');
      return;
    }
    if (!isNew && !linkedPatient) {
      toast.warning('患者を選択してください');
      return;
    }
    setAdopted(new Map());
    setShowNewConfirm(false);
    const preferred_weekdays = WEEKDAY_KEYS.filter((d) => weekdays[d]) as WeekdayCode[];
    const linked = !isNew ? linkedPatient : null;
    proposeMut.mutate(
      {
        address: addr.trim(),
        lat: linked && linkedLat !== null ? linkedLat : null,
        lng: linked && linkedLng !== null ? linkedLng : null,
        service_minutes: serviceMinutes,
        time_type: timeType as ProposeTimeType,
        preferred_start: showTimeRange ? preferredStart : null,
        preferred_end: showEnd ? preferredEnd : null,
        preferred_weekdays,
        visit_frequency: visitFrequency,
        frequency_per_week: frequencyPerWeek,
        requires_multiple_staff: requiresMultipleStaff,
        sex_restriction: sexRestriction || null,
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeId ? [officeId] : [],
        existing_patient_id: linked ? linked.id : null,
        limit: 10,
      },
      { onError: () => toast.error('提案の取得に失敗しました') },
    );
  };

  // ─── 採用 (曜日ごとに 1 枠) ──────────────────────────────────────────
  const isAdopted = (s: ProposeSlotItem) => {
    const cur = adopted.get(s.weekday_code as unknown as WeekdayKey);
    return !!cur && slotKey(cur) === slotKey(s);
  };
  const toggleAdopt = (s: ProposeSlotItem) => {
    setAdopted((prev) => {
      const next = new Map(prev);
      const wk = s.weekday_code as unknown as WeekdayKey;
      const cur = next.get(wk);
      if (cur && slotKey(cur) === slotKey(s)) next.delete(wk);
      else next.set(wk, s);
      return next;
    });
  };

  const requiredDays = coverage?.required_days ?? frequencyPerWeek;
  const adoptedCount = adopted.size;

  // 採用枠 1 件 → bulk PUT item (mode='normal')。
  const adoptedToItem = (s: ProposeSlotItem): PatientFixedVisitV2Base => {
    const duration = slotDurationMin(s) ?? serviceMinutes;
    const tplId = courseTemplateId(s.office_id, s.course_code);
    return {
      weekday: s.weekday,
      start_time: s.start_time,
      duration_min: duration,
      slot_index: 0,
      ...(tplId ? { course_template_id: tplId } : {}),
    };
  };

  // 既存 normal 固定枠 1 件 → bulk PUT item (保持用)。slot_index / course_template_id /
  // sub_office_id / is_pinned など既存値をそのまま引き継ぐ。
  const existingToItem = (v: PatientFixedVisitV2Read): PatientFixedVisitV2Base => ({
    weekday: v.weekday,
    start_time: v.start_time,
    duration_min: v.duration_min,
    slot_index: v.slot_index ?? 0,
    ...(v.course_template_id ? { course_template_id: v.course_template_id } : {}),
    ...(v.sub_office_id ? { sub_office_id: v.sub_office_id } : {}),
    ...(typeof v.is_pinned === 'boolean' ? { is_pinned: v.is_pinned } : {}),
  });

  // 新規患者用: 採用枠だけで完全 normal セットを組む (既存枠なし)。
  const buildBulkPutNew = (): PatientFixedVisitsBulkPut => {
    const items = Array.from(adopted.values())
      .map(adoptedToItem)
      .sort((a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time));
    return { mode: 'normal', items };
  };

  // 既存/プール患者用: 既存 normal 枠とマージした完全セットを組む。
  //   - 採用した曜日: 採用枠で slot_index=0 (主担当) を置換/追加。
  //     ただし同曜日の slot_index!=0 (2 名体制の相方など) は破棄せず保持する。
  //   - 採用しなかった曜日: 既存枠をそのまま保持 (slot_index 等も維持)。
  // これにより backend の全削除→INSERT で 2 名体制の相方枠や他曜日の固定枠が
  // 消えるのを防ぐ。
  const buildBulkPutMerged = (existing: PatientFixedVisitV2Read[]): PatientFixedVisitsBulkPut => {
    const adoptedWeekdays = new Set(Array.from(adopted.values()).map((s) => s.weekday));
    const preserved = existing
      .filter((v) => !adoptedWeekdays.has(v.weekday) || (v.slot_index ?? 0) !== 0)
      .map(existingToItem);
    const adoptedItems = Array.from(adopted.values()).map(adoptedToItem);
    const items = [...preserved, ...adoptedItems].sort(
      (a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time),
    );
    return { mode: 'normal', items };
  };

  // ─── 確定: 既存 / プール (登録済) ───────────────────────────────────
  const confirmExisting = () => {
    if (!linkedPatient) return;
    if (adoptedCount === 0) {
      toast.warning('採用する枠を選んでください');
      return;
    }
    // 既存 normal 枠の取得が完了していない / 失敗していると、マージ元が欠落し
    // 他曜日の固定枠を消してしまう恐れがある。安全側に倒して確定を止める。
    if (existingFixedVisitsQuery.isLoading || existingFixedVisitsQuery.isFetching) {
      toast.warning('既存の固定枠を読み込み中です。少し待ってからお試しください');
      return;
    }
    if (existingFixedVisitsQuery.isError) {
      toast.error('既存の固定枠の読み込みに失敗しました。他曜日の枠を保護できないため中止しました');
      return;
    }
    confirmMut.mutate(
      { patientId: linkedPatient.id, body: buildBulkPutMerged(existingNormalVisits) },
      {
        onSuccess: () => {
          toast.success(`${linkedPatient.name} 様の固定枠を確定しました（他曜日の既存枠は維持）`);
          onClose();
        },
        onError: () => toast.error('固定枠の確定に失敗しました'),
      },
    );
  };

  // ─── 確定: 新規 (POST patients → PUT fixed-visits の 2 段階) ─────────
  const confirmNew = async () => {
    if (!karteName.trim()) {
      toast.warning('氏名を入力してください');
      return;
    }
    if (!karteCode.trim()) {
      toast.warning('患者コードを入力してください');
      return;
    }
    if (adoptedCount === 0) {
      toast.warning('採用する枠を選んでください');
      return;
    }
    const preferred_weekdays = WEEKDAY_KEYS.filter((d) => weekdays[d]);
    // propose レスポンスの candidate_lat/lng (住所からジオコード済) を座標として渡し、
    // 座標欠落を防ぐ。propose 未実行で座標不明なら従来通り住所のみ (空文字)。
    const candLat = result?.candidate_lat;
    const candLng = result?.candidate_lng;
    // propose が住所から解決した拠点 (resolved_office_id) を所属拠点として渡す。
    // 拠点 NULL のまま登録すると後続スケジュール最適化で不利になるため、解決済みなら設定する。
    // propose 未実行 / 解決不能なら空文字 (従来通り拠点なし)。
    const formValues: PatientFormValues = {
      ...emptyPatientFormValues,
      code: karteCode.trim(),
      name: karteName.trim(),
      kana: karteKana.trim(),
      sex: karteSex,
      insurance: karteInsurance,
      address: addr.trim(),
      lat: typeof candLat === 'number' ? String(candLat) : '',
      lng: typeof candLng === 'number' ? String(candLng) : '',
      primary_office_id: result?.resolved_office_id ?? '',
      sex_restriction: sexRestriction,
      requires_multiple_staff: requiresMultipleStaff,
      weekly_pattern: {
        ...emptyPatientFormValues.weekly_pattern,
        frequency_per_week: frequencyPerWeek,
        visit_frequency: visitFrequency,
        preferred_weekdays,
        service_minutes: serviceMinutes,
        time_type: timeType,
        preferred_start: showTimeRange ? preferredStart : null,
        preferred_end: showEnd ? preferredEnd : null,
      },
    };
    // 1) 患者作成。失敗は「登録自体の失敗」として扱う。
    let created: PatientRead;
    try {
      created = await createPatientMut.mutateAsync(formValues);
    } catch {
      toast.error('患者の登録に失敗しました');
      return;
    }
    // 2) 採用枠を normal PFV へ確定。ここで失敗した場合、患者は既に作成済みなので
    //    「登録済・固定枠のみ失敗」と明示し、再確定の導線を案内する。
    try {
      await confirmMut.mutateAsync({ patientId: created.id, body: buildBulkPutNew() });
    } catch {
      toast.error(
        `${created.name} 様は登録されました。固定枠の確定のみ失敗しました。「既存患者を検索」タブから再度確定してください`,
      );
      onClose();
      return;
    }
    toast.success(`${created.name} 様を登録し、固定枠を確定しました`);
    onClose();
  };

  const isConfirming = createPatientMut.isPending || confirmMut.isPending;
  const isBusy = proposeMut.isPending || isConfirming;
  // 既存/プール患者でのみ意味を持つ。マージ元 (現在の normal 枠) を読み込み中は確定不可。
  const existingFixedVisitsLoading =
    !isNew &&
    !!linkedPatient &&
    (existingFixedVisitsQuery.isLoading || existingFixedVisitsQuery.isFetching);

  // 新規確定フォームへ進める条件。
  const canProceedNew =
    karteName.trim().length > 0 && karteCode.trim().length > 0 && adoptedCount > 0;

  const handleOpenChange = (o: boolean) => {
    if (!o && !isBusy) onClose();
  };

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-h-[90vh] max-w-3xl overflow-y-auto"
        data-testid="propose-new-modal"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-brand-primary" aria-hidden />
            新規提案 — ここに入れますか
          </DialogTitle>
          <DialogDescription>
            候補 (プール / 既存 / 新規) の希望から実現可能な空き枠を提案します。曜日ごとに枠を
            採用し、そのまま固定枠として確定できます (承認不要・親機)。
          </DialogDescription>
        </DialogHeader>

        {/* ── 候補ソース切替 ── */}
        <Tabs value={tab} onValueChange={(v) => switchTab(v as SourceTab)}>
          <TabsList className="grid w-full grid-cols-3" data-testid="propose-source-tabs">
            <TabsTrigger value="pool" data-testid="propose-tab-pool">
              <Users className="mr-1.5 h-4 w-4" aria-hidden />
              プールから選ぶ
            </TabsTrigger>
            <TabsTrigger value="existing" data-testid="propose-tab-existing">
              <Search className="mr-1.5 h-4 w-4" aria-hidden />
              既存患者を検索
            </TabsTrigger>
            <TabsTrigger value="new" data-testid="propose-tab-new">
              <Plus className="mr-1.5 h-4 w-4" aria-hidden />
              新規の方
            </TabsTrigger>
          </TabsList>
        </Tabs>

        {/* ── プール / 既存: 患者選択 ── */}
        {!isNew ? (
          <PatientPicker
            mode={isPool ? 'pool' : 'existing'}
            linked={linkedPatient}
            search={search}
            onSearch={setSearch}
            candidates={isPool ? poolCandidates : existingCandidates}
            isFetching={patientsQuery.isFetching}
            isError={patientsQuery.isError}
            onSelect={linkPatient}
            onUnlink={unlinkPatient}
          />
        ) : null}

        {/* ── 新規: カルテ項目 ── */}
        {isNew ? (
          <KarteFields
            name={karteName}
            onName={setKarteName}
            code={karteCode}
            onCode={setKarteCode}
            kana={karteKana}
            onKana={setKarteKana}
            sex={karteSex}
            onSex={setKarteSex}
            insurance={karteInsurance}
            onInsurance={setKarteInsurance}
          />
        ) : null}

        {/* ── 希望条件 ── */}
        <ConditionForm
          addr={addr}
          onAddr={setAddr}
          frequencyPerWeek={frequencyPerWeek}
          onFrequency={setFrequencyPerWeek}
          visitFrequency={visitFrequency}
          onVisitFrequency={setVisitFrequency}
          weekdays={weekdays}
          onToggleWeekday={(d) => setWeekdays((s) => ({ ...s, [d]: !s[d] }))}
          serviceMinutes={serviceMinutes}
          onServiceMinutes={setServiceMinutes}
          timeType={timeType}
          onTimeType={setTimeType}
          preferredStart={preferredStart}
          onPreferredStart={setPreferredStart}
          preferredEnd={preferredEnd}
          onPreferredEnd={setPreferredEnd}
          showTimeRange={showTimeRange}
          showEnd={showEnd}
          requiresMultipleStaff={requiresMultipleStaff}
          onRequiresMultipleStaff={setRequiresMultipleStaff}
          sexRestriction={sexRestriction}
          onSexRestriction={setSexRestriction}
        />

        <Button
          type="button"
          onClick={runPropose}
          disabled={isBusy}
          className="w-full"
          data-testid="propose-run-button"
        >
          {proposeMut.isPending ? (
            <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Sparkles className="mr-1.5 h-4 w-4" aria-hidden />
          )}
          {proposeMut.isPending ? '提案を計算中…' : '提案する'}
        </Button>

        {proposeMut.isError ? (
          <Alert variant="destructive">
            <AlertTitle>提案の取得に失敗しました</AlertTitle>
            <AlertDescription>住所や通信状況をご確認ください。</AlertDescription>
          </Alert>
        ) : null}

        {/* ── 提案結果 ── */}
        {result && !proposeMut.isPending ? (
          <div className="space-y-3" data-testid="propose-result">
            {slots.length === 0 ? (
              <div
                className="rounded-md border border-border-default bg-bg-muted/40 px-4 py-8 text-center"
                data-testid="propose-empty"
              >
                <div className="font-serif text-sm font-semibold text-text-primary">
                  入れられる枠がありません
                </div>
                {result.message ? (
                  <div className="mt-1 text-xs text-text-muted">{result.message}</div>
                ) : null}
              </div>
            ) : (
              <>
                {coverage ? <CoverageSummary coverage={coverage} onPick={toggleAdopt} /> : null}

                {requiresMultipleStaff ? (
                  <Alert variant="warning" data-testid="propose-multistaff-note">
                    <AlertTriangle className="h-4 w-4" aria-hidden />
                    <AlertTitle>2名体制の空き判定は未対応です</AlertTitle>
                    <AlertDescription>
                      以下の提案は1名前提で算出しています。2名体制が必要な場合は、確定後に相方枠を別途ご確認ください。
                    </AlertDescription>
                  </Alert>
                ) : null}

                <div className="flex items-center gap-2 border-b border-border-default pb-2">
                  <h4 className="font-serif text-sm font-semibold text-text-primary">
                    おすすめ枠 {slots.length} 件
                  </h4>
                  <span className="text-xs text-text-muted">（曜日ごとに 1 枠を採用）</span>
                </div>

                <div className="space-y-2" data-testid="propose-slot-list">
                  {slots.map((s, i) => (
                    <SlotCard
                      key={`${slotKey(s)}-${i}`}
                      slot={s}
                      rank={i + 1}
                      adopted={isAdopted(s)}
                      onAdopt={toggleAdopt}
                    />
                  ))}
                </div>

                {/* 採用サマリ + 確定 */}
                <AdoptSummary
                  adopted={adopted}
                  requiredDays={requiredDays}
                  patientName={linkedPatient?.name ?? karteName}
                  isExistingPatient={!isNew && !!linkedPatient}
                />
              </>
            )}
          </div>
        ) : null}

        {/* ── 新規 2 段階目: 登録確認 (overlay 子 Dialog) ── */}
        {isNew && showNewConfirm ? (
          <NewConfirmDialog
            name={karteName}
            code={karteCode}
            address={addr}
            adopted={adopted}
            isPending={isConfirming}
            onCancel={() => setShowNewConfirm(false)}
            onConfirm={() => void confirmNew()}
          />
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={isBusy}>
            閉じる
          </Button>
          {result && slots.length > 0 ? (
            isNew ? (
              <Button
                type="button"
                onClick={() => setShowNewConfirm(true)}
                disabled={!canProceedNew || isBusy}
                data-testid="propose-new-proceed-button"
              >
                <ArrowRight className="mr-1.5 h-4 w-4" aria-hidden />
                登録へ進む
              </Button>
            ) : (
              <Button
                type="button"
                onClick={confirmExisting}
                disabled={adoptedCount === 0 || isBusy || existingFixedVisitsLoading}
                data-testid="propose-existing-confirm-button"
              >
                {confirmMut.isPending ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <CheckCircle2 className="mr-1.5 h-4 w-4" aria-hidden />
                )}
                固定枠を確定
              </Button>
            )
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// PatientPicker — プール / 既存の患者選択
// ─────────────────────────────────────────────────────────────────────────

function PatientPicker({
  mode,
  linked,
  search,
  onSearch,
  candidates,
  isFetching,
  isError,
  onSelect,
  onUnlink,
}: {
  mode: 'pool' | 'existing';
  linked: PatientRead | null;
  search: string;
  onSearch: (v: string) => void;
  candidates: PatientRead[];
  isFetching: boolean;
  isError: boolean;
  onSelect: (p: PatientRead) => void;
  onUnlink: () => void;
}) {
  if (linked) {
    return (
      <div
        className="flex items-center gap-3 rounded-md border border-brand-primary/40 bg-brand-primary/5 px-3 py-2.5"
        data-testid="propose-linked-patient"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand-primary text-white">
          <Link2 className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-serif text-sm font-semibold text-text-primary">
            {linked.name} 様 を選択中
          </div>
          <div className="truncate text-xs text-text-muted">
            患者コード: {linked.code}
            {linked.address ? ` ・ ${linked.address}` : ''}
          </div>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={onUnlink}>
          変更
        </Button>
      </div>
    );
  }

  const trimmed = search.trim();
  const showEmpty =
    mode === 'existing'
      ? !!trimmed && !isFetching && !isError && candidates.length === 0
      : !isFetching && !isError && candidates.length === 0;

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
          aria-hidden
        />
        <Input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={
            mode === 'existing'
              ? '氏名 / カナ / 患者コードで検索'
              : 'プールを絞り込み (氏名 / カナ / コード)'
          }
          aria-label={mode === 'existing' ? '既存患者を検索' : 'プール患者を絞り込み'}
          className="pl-9"
          data-testid="propose-patient-search"
        />
      </div>

      {isError ? (
        <p className="text-xs font-medium text-error">患者の検索に失敗しました。</p>
      ) : null}
      {isFetching && candidates.length === 0 ? (
        <p className="text-xs text-text-muted">読み込み中…</p>
      ) : null}
      {showEmpty ? (
        <p className="text-xs text-text-muted">
          {mode === 'existing'
            ? '該当する患者が見つかりませんでした。'
            : 'プール候補がありません。'}
        </p>
      ) : null}

      {candidates.length > 0 ? (
        <div
          className="max-h-56 divide-y divide-border-default overflow-y-auto rounded-md border border-border-default"
          data-testid="propose-patient-candidates"
        >
          {candidates.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p)}
              className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-bg-muted focus:bg-bg-muted focus:outline-none"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-bg-muted font-serif text-sm font-semibold text-brand-primary">
                {(p.name ?? '?').charAt(0)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-text-primary">
                  {p.name}
                  <span className="ml-2 text-xs font-normal text-text-muted">{p.code}</span>
                </span>
                {p.kana || p.address ? (
                  <span className="block truncate text-xs text-text-muted">
                    {p.kana ?? ''}
                    {p.kana && p.address ? ' ・ ' : ''}
                    {p.address ?? ''}
                  </span>
                ) : null}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// KarteFields — 新規カルテ項目
// ─────────────────────────────────────────────────────────────────────────

function KarteFields({
  name,
  onName,
  code,
  onCode,
  kana,
  onKana,
  sex,
  onSex,
  insurance,
  onInsurance,
}: {
  name: string;
  onName: (v: string) => void;
  code: string;
  onCode: (v: string) => void;
  kana: string;
  onKana: (v: string) => void;
  sex: '' | (typeof SEX_OPTIONS)[number];
  onSex: (v: '' | (typeof SEX_OPTIONS)[number]) => void;
  insurance: '' | (typeof INSURANCE_OPTIONS)[number];
  onInsurance: (v: '' | (typeof INSURANCE_OPTIONS)[number]) => void;
}) {
  return (
    <Card className="space-y-3 border-border-default p-4">
      <div className="flex items-center gap-2 font-serif text-sm font-semibold text-text-primary">
        <Plus className="h-4 w-4 text-brand-primary" aria-hidden />
        新規カルテ
      </div>
      <div className="space-y-1">
        <Label htmlFor="propose-karte-name">氏名（必須）</Label>
        <Input
          id="propose-karte-name"
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="例: 山田 太郎"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="propose-karte-code">患者コード（必須）</Label>
          <Input
            id="propose-karte-code"
            value={code}
            onChange={(e) => onCode(e.target.value)}
            placeholder="例: P-1042"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="propose-karte-kana">フリガナ</Label>
          <Input
            id="propose-karte-kana"
            value={kana}
            onChange={(e) => onKana(e.target.value)}
            placeholder="ヤマダ タロウ"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="propose-karte-sex">性別</Label>
          <Select
            value={sex || undefined}
            onValueChange={(v) => onSex(v as (typeof SEX_OPTIONS)[number])}
          >
            <SelectTrigger id="propose-karte-sex" aria-label="性別">
              <SelectValue placeholder="未選択" />
            </SelectTrigger>
            <SelectContent>
              {SEX_OPTIONS.map((v) => (
                <SelectItem key={v} value={v}>
                  {SEX_LABEL[v]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="propose-karte-insurance">保険</Label>
          <Select
            value={insurance || undefined}
            onValueChange={(v) => onInsurance(v as (typeof INSURANCE_OPTIONS)[number])}
          >
            <SelectTrigger id="propose-karte-insurance" aria-label="保険">
              <SelectValue placeholder="未選択" />
            </SelectTrigger>
            <SelectContent>
              {INSURANCE_OPTIONS.map((v) => (
                <SelectItem key={v} value={v}>
                  {INSURANCE_LABEL[v]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// ConditionForm — 希望条件
// ─────────────────────────────────────────────────────────────────────────

function ConditionForm(props: {
  addr: string;
  onAddr: (v: string) => void;
  frequencyPerWeek: number;
  onFrequency: (v: number) => void;
  visitFrequency: (typeof VISIT_FREQUENCY_OPTIONS)[number];
  onVisitFrequency: (v: (typeof VISIT_FREQUENCY_OPTIONS)[number]) => void;
  weekdays: Record<WeekdayKey, boolean>;
  onToggleWeekday: (d: WeekdayKey) => void;
  serviceMinutes: number;
  onServiceMinutes: (v: number) => void;
  timeType: (typeof TIME_TYPE_OPTIONS)[number];
  onTimeType: (v: (typeof TIME_TYPE_OPTIONS)[number]) => void;
  preferredStart: string;
  onPreferredStart: (v: string) => void;
  preferredEnd: string;
  onPreferredEnd: (v: string) => void;
  showTimeRange: boolean;
  showEnd: boolean;
  requiresMultipleStaff: boolean;
  onRequiresMultipleStaff: (v: boolean) => void;
  sexRestriction: '' | (typeof SEX_RESTRICTION_OPTIONS)[number];
  onSexRestriction: (v: '' | (typeof SEX_RESTRICTION_OPTIONS)[number]) => void;
}) {
  const {
    addr,
    onAddr,
    frequencyPerWeek,
    onFrequency,
    visitFrequency,
    onVisitFrequency,
    weekdays,
    onToggleWeekday,
    serviceMinutes,
    onServiceMinutes,
    timeType,
    onTimeType,
    preferredStart,
    onPreferredStart,
    preferredEnd,
    onPreferredEnd,
    showTimeRange,
    showEnd,
    requiresMultipleStaff,
    onRequiresMultipleStaff,
    sexRestriction,
    onSexRestriction,
  } = props;

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label htmlFor="propose-addr">ご住所</Label>
        <Input
          id="propose-addr"
          value={addr}
          onChange={(e) => onAddr(e.target.value)}
          placeholder="例: 千葉市稲毛区小仲台6-2-1"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="propose-freq">週訪問回数</Label>
          <Select value={String(frequencyPerWeek)} onValueChange={(v) => onFrequency(Number(v))}>
            <SelectTrigger id="propose-freq" aria-label="週訪問回数">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FREQ_OPTIONS.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  週 {n} 回
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="propose-visit-freq">訪問頻度</Label>
          <Select
            value={visitFrequency}
            onValueChange={(v) => onVisitFrequency(v as (typeof VISIT_FREQUENCY_OPTIONS)[number])}
          >
            <SelectTrigger id="propose-visit-freq" aria-label="訪問頻度">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {VISIT_FREQUENCY_OPTIONS.map((k) => (
                <SelectItem key={k} value={k}>
                  {VISIT_FREQUENCY_LABELS[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="space-y-1">
        <Label>希望曜日（複数可）</Label>
        <div className="flex flex-wrap gap-1.5">
          {WEEKDAY_KEYS.map((d) => {
            const on = !!weekdays[d];
            return (
              <button
                key={d}
                type="button"
                onClick={() => onToggleWeekday(d)}
                aria-pressed={on}
                className={
                  on
                    ? 'rounded-md border border-brand-primary bg-brand-primary px-3 py-1.5 text-sm font-semibold text-white'
                    : 'rounded-md border border-border-default bg-bg-base px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-bg-muted'
                }
              >
                {WEEKDAY_LABELS_JA[d]}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="propose-service">サービス時間</Label>
          <Select value={String(serviceMinutes)} onValueChange={(v) => onServiceMinutes(Number(v))}>
            <SelectTrigger id="propose-service" aria-label="サービス時間">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SERVICE_MINUTES_OPTIONS.map((m) => (
                <SelectItem key={m} value={String(m)}>
                  {m} 分
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="propose-time-type">時間タイプ</Label>
          <Select
            value={timeType}
            onValueChange={(v) => onTimeType(v as (typeof TIME_TYPE_OPTIONS)[number])}
          >
            <SelectTrigger id="propose-time-type" aria-label="時間タイプ">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIME_TYPE_OPTIONS.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {showTimeRange ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="propose-pref-start">希望開始時刻</Label>
            <Select value={preferredStart} onValueChange={onPreferredStart}>
              <SelectTrigger id="propose-pref-start" aria-label="希望開始時刻">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TIME_OPTIONS.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {showEnd ? (
            <div className="space-y-1">
              <Label htmlFor="propose-pref-end">希望終了時刻</Label>
              <Select value={preferredEnd} onValueChange={onPreferredEnd}>
                <SelectTrigger id="propose-pref-end" aria-label="希望終了時刻">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TIME_OPTIONS.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label htmlFor="propose-sex-restrict">性別制限</Label>
          <Select
            value={sexRestriction || '__none__'}
            onValueChange={(v) =>
              onSexRestriction(
                v === '__none__' ? '' : (v as (typeof SEX_RESTRICTION_OPTIONS)[number]),
              )
            }
          >
            <SelectTrigger id="propose-sex-restrict" aria-label="性別制限">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">なし</SelectItem>
              {SEX_RESTRICTION_OPTIONS.map((v) => (
                <SelectItem key={v} value={v}>
                  {SEX_RESTRICTION_LABEL[v]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-end">
          <button
            type="button"
            onClick={() => onRequiresMultipleStaff(!requiresMultipleStaff)}
            aria-pressed={requiresMultipleStaff}
            className={
              requiresMultipleStaff
                ? 'flex h-10 w-full items-center gap-2 rounded-md border border-brand-primary bg-brand-primary/10 px-3 text-sm font-semibold text-brand-primary'
                : 'flex h-10 w-full items-center gap-2 rounded-md border border-border-default bg-bg-base px-3 text-sm font-medium text-text-secondary hover:bg-bg-muted'
            }
          >
            <span
              className={
                requiresMultipleStaff
                  ? 'flex h-4 w-4 items-center justify-center rounded-sm bg-brand-primary text-white'
                  : 'flex h-4 w-4 items-center justify-center rounded-sm border border-text-muted'
              }
            >
              {requiresMultipleStaff ? <Check className="h-3 w-3" aria-hidden /> : null}
            </span>
            2名体制が必要
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// CoverageSummary — StageC: 週N日カバレッジ
// ─────────────────────────────────────────────────────────────────────────

function CoverageSummary({
  coverage,
  onPick,
}: {
  coverage: ProposeCoverage;
  onPick: (s: ProposeSlotItem) => void;
}) {
  const { required_days, covered_days, fully_covered, per_day } = coverage;
  return (
    <div
      className={
        fully_covered
          ? 'rounded-md border border-success/40 bg-success/10 p-3'
          : 'rounded-md border border-warning/40 bg-warning/10 p-3'
      }
      data-testid="propose-coverage"
    >
      <div className="flex items-center gap-2">
        {fully_covered ? (
          <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />
        ) : (
          <CalendarRange className="h-5 w-5 text-warning" aria-hidden />
        )}
        <span className="font-serif text-sm font-semibold text-text-primary">
          希望 週{required_days}日 → {covered_days}/{required_days}日 提案可
        </span>
        {fully_covered ? (
          <Badge variant="success" className="ml-auto">
            全日OK
          </Badge>
        ) : (
          <Badge variant="warning" className="ml-auto">
            未充足あり
          </Badge>
        )}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {per_day.map((d) => {
          const ja = WEEKDAY_INT_TO_JA[d.weekday] ?? '';
          const ok = d.has_slot;
          const tappable = ok && !!d.best_slot;
          return (
            <button
              key={d.weekday_code}
              type="button"
              disabled={!tappable}
              onClick={() => d.best_slot && onPick(d.best_slot)}
              aria-label={`${ja}曜 ${ok ? '提案あり' : '提案なし'}`}
              className={
                ok
                  ? 'inline-flex items-center gap-1 rounded-full border border-success/50 bg-success/15 px-2.5 py-1 text-xs font-semibold text-success disabled:cursor-default'
                  : 'inline-flex items-center gap-1 rounded-full border border-error/40 bg-error/10 px-2.5 py-1 text-xs font-semibold text-error'
              }
            >
              <span>{ja}</span>
              <span>{ok ? '○' : '×'}</span>
              {tappable && d.best_slot ? (
                <span className="tnum font-normal opacity-80">{d.best_slot.start_time}〜</span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// SlotCard — StageB: 候補 1 件 + ミニスケジュール
// ─────────────────────────────────────────────────────────────────────────

function MiniRow({ row }: { row: ProposeMiniScheduleEntry }) {
  if (row.is_here) {
    return (
      <div className="flex items-stretch gap-2">
        <span className="tnum w-12 shrink-0 pt-1.5 text-xs font-medium text-brand-primary">
          {row.time}
        </span>
        <div className="flex flex-1 items-center gap-2 rounded-md border-2 border-dashed border-brand-primary bg-brand-primary/5 px-2.5 py-1.5">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-primary text-white">
            <Plus className="h-3 w-3" aria-hidden />
          </span>
          <span className="font-serif text-xs font-semibold text-brand-primary">
            {row.is_pair ? 'ここに一緒に入れますか' : 'ここに入れますか'}
          </span>
        </div>
      </div>
    );
  }
  // 既存訪問行。row.ins は保険区分 ('med' | 'care' | null)。
  // 現状 propose API は mini_schedule の ins を埋めていないため実質常に null だが、
  // BE 拡張で値が入った場合に備え 医療=info / 介護=brand-primary で色分けする。
  // 左ボーダーはインライン style 必須 (動的色) のため親機トークンを CSS 変数で参照する。
  const accentVar = row.ins === 'med' ? 'var(--info)' : 'var(--brand-primary)';
  return (
    <div className="flex items-stretch gap-2">
      <span className="tnum w-12 shrink-0 pt-1.5 text-xs text-text-muted">{row.time}</span>
      <div
        className="flex flex-1 items-center gap-2 rounded-md bg-bg-muted/50 px-2.5 py-1.5"
        style={{ borderLeft: `3px solid ${accentVar}` }}
      >
        <span className="font-serif text-xs font-semibold text-text-primary">{row.name}</span>
        {row.ins === 'med' || row.ins === 'care' ? (
          <Badge variant={row.ins === 'med' ? 'info' : 'success'} className="text-[10px]">
            {row.ins === 'med' ? '医療' : '介護'}
          </Badge>
        ) : null}
      </div>
    </div>
  );
}

function SlotCard({
  slot,
  rank,
  adopted,
  onAdopt,
}: {
  slot: ProposeSlotItem;
  rank: number;
  adopted: boolean;
  onAdopt: (s: ProposeSlotItem) => void;
}) {
  const dow = WEEKDAY_INT_TO_JA[slot.weekday] ?? '';
  const filled = slot.mini_schedule.filter((m) => !m.is_here).length;
  return (
    <Card
      className={
        adopted
          ? 'overflow-hidden border-2 border-brand-primary'
          : 'overflow-hidden border-border-default'
      }
      data-testid={`propose-slot-${slotKey(slot)}`}
    >
      <div className="flex items-center gap-3 p-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-bg-muted font-serif text-sm font-semibold text-text-secondary">
          {rank}
        </span>
        <div className="min-w-0 flex-1">
          <div className="font-serif text-sm font-semibold text-text-primary">
            {slot.course_label}{' '}
            <span className="text-brand-primary">
              {dow} {slot.start_time}〜
            </span>
          </div>
          <div className="text-xs text-text-muted">
            {slot.staff_name ? `${slot.staff_name} ・ ` : ''}
            {slot.start_time}〜{slot.end_time}
            {slot.is_pair && slot.pair_partner ? ` ・ ペア相手: ${slot.pair_partner}` : ''}
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant={adopted ? 'default' : 'outline'}
          onClick={() => onAdopt(slot)}
          aria-pressed={adopted}
          className="shrink-0"
          data-testid={`propose-adopt-${slotKey(slot)}`}
        >
          {adopted ? (
            <>
              <Check className="mr-1 h-3.5 w-3.5" aria-hidden />
              {dow}曜 採用中
            </>
          ) : (
            <>{dow}曜 この枠を採用</>
          )}
        </Button>
      </div>

      {slot.reasons.length > 0 || slot.is_pair ? (
        <div className="flex flex-wrap gap-1 px-3 pb-2">
          {slot.is_pair ? (
            <Badge variant="secondary" className="text-[10px]">
              同住所ペア
            </Badge>
          ) : null}
          {slot.reasons.map((r, i) => (
            <Badge key={i} variant="success" className="text-[10px]">
              {r}
            </Badge>
          ))}
        </div>
      ) : null}

      {slot.warnings.length > 0 ? (
        <div className="flex flex-col gap-1 px-3 pb-2">
          {slot.warnings.map((w, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 text-xs font-medium text-warning"
            >
              <AlertTriangle className="h-3 w-3" aria-hidden />
              {proposeWarningLabel(w)}
            </span>
          ))}
        </div>
      ) : null}

      {slot.mini_schedule.length > 0 ? (
        <div className="border-t border-border-default bg-bg-muted/20 px-3 py-2.5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
            {slot.course_label}
            {slot.staff_name ? `（${slot.staff_name}）` : ''} の{dow}曜 ・ {filled}件 + 提案枠
          </div>
          <div className="flex flex-col gap-1.5">
            {slot.mini_schedule.map((row, i) => (
              <MiniRow key={i} row={row} />
            ))}
          </div>
        </div>
      ) : null}
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AdoptSummary — 採用した枠の要約
// ─────────────────────────────────────────────────────────────────────────

function AdoptSummary({
  adopted,
  requiredDays,
  patientName,
  isExistingPatient,
}: {
  adopted: Map<WeekdayKey, ProposeSlotItem>;
  requiredDays: number;
  patientName: string;
  /** 既存/プール患者の確定時 true。既存固定枠とのマージである旨を表示する。 */
  isExistingPatient: boolean;
}) {
  const picks = React.useMemo(
    () =>
      Array.from(adopted.values()).sort(
        (a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time),
      ),
    [adopted],
  );
  const adoptedCount = picks.length;
  const shortfall = Math.max(0, requiredDays - adoptedCount);

  return (
    <div
      className="rounded-md border border-border-default p-3"
      data-testid="propose-adopt-summary"
    >
      <div className="flex items-center gap-1.5 font-serif text-sm font-semibold text-text-primary">
        <ClipboardCheck className="h-4 w-4 text-brand-primary" aria-hidden />
        採用した枠 {adoptedCount} 件 / 希望 週{requiredDays}日
        {patientName ? (
          <span className="text-xs font-normal text-text-muted">・ {patientName}</span>
        ) : null}
      </div>
      {adoptedCount === 0 ? (
        <p className="mt-1.5 text-xs text-text-muted">
          上のおすすめ枠から、曜日ごとに 1 枠ずつ採用してください。
        </p>
      ) : (
        <ul className="mt-2 space-y-1">
          {picks.map((s) => (
            <li key={slotKey(s)} className="flex items-center gap-2 text-xs text-text-secondary">
              <Badge variant="success" className="text-[10px]">
                {WEEKDAY_INT_TO_JA[s.weekday] ?? ''}
              </Badge>
              <span className="tnum">
                {s.start_time}〜{s.end_time}
              </span>
              <span className="font-medium text-text-primary">{s.course_label}</span>
            </li>
          ))}
        </ul>
      )}
      {shortfall > 0 && adoptedCount > 0 ? (
        <p className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-warning">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          あと {shortfall} 日ぶん未充足です（このまま確定も可）
        </p>
      ) : null}
      {isExistingPatient && adoptedCount > 0 ? (
        <p className="mt-2 text-xs text-text-muted" data-testid="propose-merge-note">
          既存の固定枠に追加 / 更新します（採用した曜日のみ置き換え、他曜日の枠は維持されます）。
        </p>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// NewConfirmDialog — 新規 2 段階目: 登録確認
// ─────────────────────────────────────────────────────────────────────────

function NewConfirmDialog({
  name,
  code,
  address,
  adopted,
  isPending,
  onCancel,
  onConfirm,
}: {
  name: string;
  code: string;
  address: string;
  adopted: Map<WeekdayKey, ProposeSlotItem>;
  isPending: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const picks = React.useMemo(
    () =>
      Array.from(adopted.values()).sort(
        (a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time),
      ),
    [adopted],
  );
  return (
    <Dialog open onOpenChange={(o) => (!o && !isPending ? onCancel() : undefined)}>
      <DialogContent className="max-w-md" data-testid="propose-new-confirm-dialog">
        <DialogHeader>
          <DialogTitle className="text-base">{name} 様 を新規登録</DialogTitle>
          <DialogDescription>
            患者マスタに登録し、採用した枠を固定枠として確定します。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2 rounded-md border border-border-default p-3 text-sm">
          <div className="flex justify-between">
            <span className="text-text-muted">患者コード</span>
            <span className="font-medium text-text-primary">{code}</span>
          </div>
          {address ? (
            <div className="flex justify-between gap-3">
              <span className="shrink-0 text-text-muted">住所</span>
              <span className="truncate text-right font-medium text-text-primary">{address}</span>
            </div>
          ) : null}
        </div>

        <div className="space-y-1">
          <div className="text-xs font-semibold text-text-muted">確定する固定枠</div>
          <ul className="space-y-1">
            {picks.map((s) => (
              <li key={slotKey(s)} className="flex items-center gap-2 text-xs text-text-secondary">
                <Badge variant="success" className="text-[10px]">
                  {WEEKDAY_INT_TO_JA[s.weekday] ?? ''}
                </Badge>
                <span className="tnum">
                  {s.start_time}〜{s.end_time}
                </span>
                <span className="font-medium text-text-primary">{s.course_label}</span>
              </li>
            ))}
          </ul>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel} disabled={isPending}>
            戻る
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={isPending}
            data-testid="propose-new-confirm-button"
          >
            {isPending ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <CheckCircle2 className="mr-1.5 h-4 w-4" aria-hidden />
            )}
            登録して確定
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
