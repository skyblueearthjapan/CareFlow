'use client';

/**
 * CareFlow Mobile — Karte ボトムシート + 提案シート (Warm & Human, Phase2-3c 実データ)
 *
 * - KarteSheet  : board の visit (patient_id) から `GET /api/v1/patients/{id}` を
 *                 取得し、基本情報 / 保険・サービス / 希望曜日 / 備考 を表示する。
 *                 同住所相手は board の same_address_group から解決する。
 * - SuggestSheet: フォーム値 + 現在の週で `POST .../propose-slots` を実呼び出し
 *                 し、返ってきた slots をランキング + ミニスケジュール (実時刻) で
 *                 レンダする。office_ids は省略 (= 全拠点検索) で拠点跨ぎ提示。
 *                 0 件 / warnings も表示する。
 *
 * Phase 1 のモック (RANK_PAIR / RANK_NORMAL / CF_PATIENTS) は撤去済み。
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import {
  X,
  MapPin,
  User,
  Heart,
  Calendar,
  Bell,
  Sparkles,
  AlertTriangle,
  Search,
  Link2,
  Check,
  CheckCircle2,
  ClipboardCheck,
  Pencil,
  Save,
  Clock,
} from 'lucide-react';

import {
  SERVICE_MINUTES_OPTIONS,
  DEFAULT_SERVICE_MINUTES,
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  SEX_RESTRICTION_OPTIONS,
  SEX_RESTRICTION_LABEL,
  SEX_OPTIONS,
  SEX_LABEL,
  STATUS_OPTIONS,
  STATUS_LABEL,
  INSURANCE_OPTIONS,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  INSURANCE_LABEL,
  coerceWeeklyPattern,
  normalizePatientSex,
  normalizePatientInsurance,
  normalizePatientSexRestriction,
  normalizePatientStatus,
  patientReadToFormValues,
  type WeekdayKey,
  type PatientFormValues,
  type WeeklyPattern,
} from '@/lib/schemas/patient';
import { usePatient, usePatients, useUpdatePatient } from '@/lib/queries/patients';
import { useCreatePendingRequest } from '@/lib/queries/pending_requests';
import { useGeocode } from '@/lib/queries/geocoding';
import { useResolveOffice } from '@/lib/queries/offices';
import type { PatientRead } from '@/lib/schemas/patient';
import type { BoardOffice } from '@/lib/schemas/v2/board';
import {
  useProposeSlots,
  useTravelEstimate,
  WEEKDAY_CODE_TO_INT,
  proposeWarningLabel,
} from '@/lib/queries/fieldBoard';
import {
  buildProposedVisits,
  buildPatientCreatePayload,
  buildPlacementMeta,
  buildPatientCreatePlacementPayload,
  buildPatientVisitAddPayload,
  computePlacementWarnings,
  type KarteInput,
  type DesiredSchedule,
  type ProposedVisit,
  type SlotPlacementContext,
  type PlacementWarning,
  type PlacementExtras,
} from '@/lib/field/patientCreate';
import { fmtHM, parseHM } from '@/lib/scheduling/freeGaps';
import type { BoardVisit, WeekdayCode } from '@/lib/schemas/v2/board';
import type {
  ProposeSlotItem,
  ProposeMiniScheduleEntry,
  ProposeTimeType,
  ProposeCoverage,
} from '@/lib/schemas/v2/propose_slots';

import type { SameAddressGroups } from './FieldBoard';

const _TERRA = '#D97706',
  _TERRAD = '#B45309',
  _PLUM = '#8B5C9E',
  _MINT = '#0E9F6E',
  _MINTD = '#0E8472',
  _ROSE = '#C75C77',
  _INK = '#1C1917',
  _INK2 = '#57534E',
  _INK3 = '#A8A29E',
  _LINE = '#EAE3D8';
const PANEL_W = '#FFFFFF';

const DOW_JA: Record<WeekdayKey, string> = WEEKDAY_LABELS_JA;

// ============================ Karte bottom sheet ============================

export function KarteSheet({
  visit,
  officeName,
  offices = [],
  canEdit = false,
  sameAddressGroups,
  onClose,
  onOpenVisit,
  onToast,
}: {
  visit: BoardVisit;
  officeName: string;
  /** 全拠点 (board.offices)。主担当拠点 (primary_office_id) の名前解決に使う。 */
  offices?: BoardOffice[];
  /** manager / admin のときのみ「編集」ボタンを出す。staff は閲覧専用。 */
  canEdit?: boolean;
  sameAddressGroups: SameAddressGroups;
  onClose: () => void;
  onOpenVisit: (v: BoardVisit) => void;
  /** 保存成功/失敗のトースト表示用 (編集機能)。 */
  onToast?: (msg: string) => void;
}) {
  // 患者詳細を実 API から取得 (基本情報 / 希望曜日 / 備考 等)。
  const patientQuery = usePatient(visit.patient_id);
  const p = patientQuery.data;

  // 閲覧 ⇄ 編集モードのトグル。患者が切り替わったら閲覧へ戻す。
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    setEditing(false);
  }, [visit.patient_id]);

  // 同住所相手: board の same_address_group から、自分以外の visit を引く。
  const gid = visit.same_address_group_id;
  const mates = gid
    ? (sameAddressGroups.byGroup.get(gid) ?? []).filter((m) => m.visit_id !== visit.visit_id)
    : [];

  // 表示値: 患者詳細が来るまでは board visit の値でフォールバック。
  const wp = p ? coerceWeeklyPattern(p.weekly_pattern) : null;
  const sex = p ? normalizePatientSex(p.sex as string | null | undefined) : null;
  const status = p ? normalizePatientStatus(p.status as string | null | undefined) : null;
  const sexRestriction = p
    ? normalizePatientSexRestriction(p.sex_restriction as string | null | undefined)
    : null;
  const requiresMultiple =
    (p as { requires_multiple_staff?: boolean | null } | undefined)?.requires_multiple_staff ===
    true;
  const insurance =
    (p ? normalizePatientInsurance(p.insurance as string | null | undefined) : null) ??
    (visit.insurance === 'med' ? 'medical' : visit.insurance === 'care' ? 'care' : null);
  const address = p?.address ?? visit.address ?? null;
  const serviceMin = wp?.service_minutes ?? visit.service_minutes;
  const note = p?.note ?? null;

  // 主担当拠点 (primary_office_id) の名前解決 (board.offices より)。不明なら表示省略。
  const primaryOfficeName = useMemo(() => {
    const id = p?.primary_office_id;
    if (!id) return null;
    return offices.find((o) => o.office_id === id)?.office_name ?? null;
  }, [p?.primary_office_id, offices]);

  // 編集モード: 患者詳細取得後のみ。フォームへは patientReadToFormValues で渡す。
  if (editing && p) {
    return (
      <KarteEditSheet
        patient={p}
        onClose={onClose}
        onCancel={() => setEditing(false)}
        onSaved={() => setEditing(false)}
        onToast={onToast}
      />
    );
  }

  return (
    <SlideSheet>
      <Grip />
      <div
        style={{
          padding: '4px 20px 16px',
          color: '#fff',
          background: `linear-gradient(135deg, ${_PLUM}, #7A4E91)`,
          margin: 0,
          borderRadius: '20px 20px 0 0',
          position: 'relative',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 13, paddingTop: 8 }}>
          <div
            style={{
              width: 50,
              height: 50,
              borderRadius: 16,
              background: '#fff',
              color: _PLUM,
              display: 'grid',
              placeItems: 'center',
              fontFamily: 'var(--font-serif)',
              fontSize: 22,
              fontWeight: 700,
              flex: '0 0 auto',
            }}
          >
            {(p?.name ?? visit.patient_name).charAt(0)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 19, fontWeight: 700 }}>
              {p?.name ?? visit.patient_name}
            </div>
            <div style={{ fontSize: 11, opacity: 0.9 }}>{p?.kana ?? visit.patient_kana ?? ''}</div>
            {p?.code && (
              <div style={{ fontSize: 10.5, opacity: 0.8, marginTop: 2 }}>患者コード: {p.code}</div>
            )}
          </div>
          {canEdit && p && (
            <button
              onClick={() => setEditing(true)}
              aria-label="カルテを編集"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                height: 36,
                padding: '0 12px',
                borderRadius: 999,
                background: 'rgba(255,255,255,0.22)',
                color: '#fff',
                fontFamily: 'var(--font-serif)',
                fontSize: 13,
                fontWeight: 700,
                flex: '0 0 auto',
              }}
            >
              <Pencil size={14} />
              編集
            </button>
          )}
          <button
            onClick={onClose}
            aria-label="閉じる"
            style={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              background: 'rgba(255,255,255,0.22)',
              color: '#fff',
              display: 'grid',
              placeItems: 'center',
              flex: '0 0 auto',
            }}
          >
            <X size={17} />
          </button>
        </div>
      </div>
      <div
        className="cf-scroll"
        style={{ overflowY: 'auto', padding: '16px 20px 28px', background: PANEL_W, flex: 1 }}
      >
        {patientQuery.isError && (
          <div
            style={{
              background: '#FCEFF2',
              border: '2px solid #F4D4DC',
              borderRadius: 12,
              padding: 11,
              fontSize: 12.5,
              fontWeight: 700,
              color: '#C75C77',
              marginBottom: 14,
            }}
          >
            患者詳細の読み込みに失敗しました。ボード上の情報のみ表示します。
          </div>
        )}

        {mates.length > 0 && (
          <KSec title="同住所ペア" icon={<MapPin size={12} />}>
            {mates.map((mate) => (
              <button
                key={mate.visit_id}
                onClick={() => onOpenVisit(mate)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  background: 'linear-gradient(180deg,#F7F0FB,#F1E7F8)',
                  border: `2px solid ${_PLUM}`,
                  borderRadius: 14,
                  padding: '12px 14px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  marginBottom: 8,
                }}
              >
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 13,
                    background: _PLUM,
                    color: '#fff',
                    display: 'grid',
                    placeItems: 'center',
                    fontFamily: 'var(--font-serif)',
                    fontSize: 17,
                    fontWeight: 700,
                    flex: '0 0 auto',
                  }}
                >
                  {mate.patient_name.charAt(0)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontFamily: 'var(--font-serif)',
                      fontSize: 14.5,
                      fontWeight: 700,
                      color: '#7A4E91',
                    }}
                  >
                    相手: {mate.patient_name} 様 ›
                  </div>
                  <div style={{ fontSize: 10.5, color: _INK2, marginTop: 2 }}>
                    同住所・連続訪問（{mate.start_time}〜{mate.end_time}）
                  </div>
                </div>
              </button>
            ))}
          </KSec>
        )}

        <KSec title="基本情報" icon={<User size={12} />}>
          {sex && <KV k="性別" v={SEX_LABEL[sex]} />}
          {status && <KV k="ステータス" v={STATUS_LABEL[status]} />}
          <KV k="性別制限" v={sexRestriction ? SEX_RESTRICTION_LABEL[sexRestriction] : 'なし'} />
          <KV k="複数スタッフ必須" v={requiresMultiple ? 'はい' : 'いいえ'} />
          {address && (
            <KV
              k="ご住所"
              v={
                <span>
                  {address}
                  {mates.length > 0 && <span style={pBadge}>同住所</span>}
                </span>
              }
            />
          )}
          {primaryOfficeName && <KV k="主担当拠点" v={primaryOfficeName} />}
        </KSec>

        <KSec title="保険・サービス" icon={<Heart size={12} />}>
          {insurance && <KV k="保険種別" v={INSURANCE_LABEL[insurance]} />}
          {wp && <KV k="週訪問回数" v={`${wp.frequency_per_week}回 / 週`} />}
          {wp?.visit_frequency && (
            <KV k="訪問頻度" v={VISIT_FREQUENCY_LABELS[wp.visit_frequency]} />
          )}
          <KV k="サービス時間" v={`${serviceMin}分`} />
          {wp?.time_type && <KV k="時間タイプ" v={wp.time_type} />}
          {wp?.time_type === '時間帯' && (wp.preferred_start || wp.preferred_end) && (
            <KV k="希望時間" v={`${wp.preferred_start ?? '—'}〜${wp.preferred_end ?? '—'}`} />
          )}
          {wp?.time_type === '固定' && wp.preferred_start && (
            <KV k="固定開始" v={wp.preferred_start} />
          )}
        </KSec>

        {wp && (
          <KSec title="希望曜日" icon={<Calendar size={12} />}>
            <div style={{ display: 'flex', gap: 5 }}>
              {WEEKDAY_KEYS.map((d) => {
                const yes = wp.preferred_weekdays.includes(d);
                return (
                  <div
                    key={d}
                    style={{
                      flex: 1,
                      textAlign: 'center',
                      padding: '6px 0',
                      borderRadius: 9,
                      fontFamily: 'var(--font-serif)',
                      fontSize: 13,
                      fontWeight: 700,
                      background: yes ? '#D7F2EE' : '#F4EFE7',
                      color: yes ? '#0E8472' : '#CDC2B2',
                    }}
                  >
                    <span style={{ fontSize: 10, display: 'block', color: _INK3, fontWeight: 500 }}>
                      {DOW_JA[d]}
                    </span>
                    {yes ? '○' : '×'}
                  </div>
                );
              })}
            </div>
          </KSec>
        )}

        <KSec title="この訪問 / 拠点" icon={<MapPin size={12} />}>
          <KV k="この枠の時刻" v={`${visit.start_time}〜${visit.end_time}`} />
          {officeName && <KV k="担当拠点" v={officeName} />}
        </KSec>

        {note && (
          <KSec title="備考・申し送り" icon={<Bell size={12} />}>
            <div
              style={{
                background: '#FFF9EC',
                borderRadius: 14,
                padding: 13,
                fontWeight: 600,
                lineHeight: 1.6,
                border: '2px solid #FBEFCE',
                fontSize: 13,
                whiteSpace: 'pre-wrap',
              }}
            >
              {note}
            </div>
          </KSec>
        )}

        {patientQuery.isLoading && (
          <div
            style={{
              textAlign: 'center',
              color: _INK3,
              fontSize: 12,
              fontFamily: 'var(--font-serif)',
              padding: '8px 0',
            }}
          >
            患者詳細を読み込み中…
          </div>
        )}
      </div>
    </SlideSheet>
  );
}

const pBadge: CSSProperties = {
  fontSize: 10,
  background: '#F0E7F5',
  color: _PLUM,
  padding: '1px 6px',
  borderRadius: 999,
  fontWeight: 700,
  marginLeft: 6,
};

function KSec({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontFamily: 'var(--font-serif)',
          fontSize: 12,
          color: _PLUM,
          fontWeight: 700,
          marginBottom: 8,
        }}
      >
        {icon}
        {title}
        <span style={{ flex: 1, height: 2, background: '#F0E7F5', borderRadius: 2 }} />
      </div>
      {children}
    </div>
  );
}

const KV = ({ k, v }: { k: string; v: ReactNode }) => (
  <div
    style={{
      display: 'flex',
      padding: '7px 0',
      borderBottom: `1px dashed ${_LINE}`,
      fontSize: 13.5,
    }}
  >
    <div style={{ width: 96, flex: '0 0 96px', color: _INK2, fontSize: 12.5 }}>{k}</div>
    <div style={{ flex: 1, fontWeight: 700 }}>{v}</div>
  </div>
);

// ============================ Karte edit sheet (manager/admin) ============================

/**
 * カルテ編集シート (マスタ主要項目) — 現場ボード `/m` の manager/admin 向け。
 *
 * - 初期値は `patientReadToFormValues(patient)` で取得 (デスクトップ PatientForm と同源)。
 * - 編集対象: name/code/kana/sex/status/insurance/address/sex_restriction/
 *   requires_multiple_staff/note ＋ weekly_pattern (frequency_per_week/visit_frequency/
 *   preferred_weekdays(月〜日)/service_minutes/time_type/preferred_start/preferred_end)。
 *   special_weekly_pattern (特別週) / 固定訪問 (PFV) は対象外。
 * - 住所変更時は保存時に best-effort で再ジオコード (lat/lng) + 拠点解決
 *   (primary_office_id) を行う。失敗しても保存は継続する。
 * - 保存は `useUpdatePatient(id, initial)` で PATCH。成功で閲覧に戻り、トースト表示。
 *
 * バリデーション/選択肢はデスクトップと同一定数 (SEX/INSURANCE/STATUS/SEX_RESTRICTION/
 * TIME_TYPE/SERVICE_MINUTES_OPTIONS/WEEKDAY) を流用し、ドリフトを防ぐ。
 */
function KarteEditSheet({
  patient,
  onClose,
  onCancel,
  onSaved,
  onToast,
}: {
  patient: PatientRead;
  onClose: () => void;
  onCancel: () => void;
  onSaved: () => void;
  onToast?: (msg: string) => void;
}) {
  // 初期値 (read → form values)。再マウントせず保持するため useState 初期化子で 1 度だけ算出。
  const initial = useMemo(() => patientReadToFormValues(patient), [patient]);

  const [name, setName] = useState(initial.name);
  const [code, setCode] = useState(initial.code);
  const [kana, setKana] = useState(initial.kana);
  const [sex, setSex] = useState<PatientFormValues['sex']>(initial.sex);
  const [status, setStatus] = useState<PatientFormValues['status']>(initial.status);
  const [insurance, setInsurance] = useState<PatientFormValues['insurance']>(initial.insurance);
  const [address, setAddress] = useState(initial.address);
  const [sexRestriction, setSexRestriction] = useState<PatientFormValues['sex_restriction']>(
    initial.sex_restriction,
  );
  const [requiresMultipleStaff, setRequiresMultipleStaff] = useState(
    initial.requires_multiple_staff,
  );
  const [note, setNote] = useState(initial.note);

  // weekly_pattern 構造化値。
  const wp0 = initial.weekly_pattern;
  const [frequencyPerWeek, setFrequencyPerWeek] = useState(wp0.frequency_per_week);
  const [visitFrequency, setVisitFrequency] = useState<(typeof VISIT_FREQUENCY_OPTIONS)[number]>(
    wp0.visit_frequency ?? 'every',
  );
  const [weekdays, setWeekdays] = useState<Record<WeekdayKey, boolean>>(() => {
    const m = {} as Record<WeekdayKey, boolean>;
    for (const d of WEEKDAY_KEYS) m[d] = wp0.preferred_weekdays.includes(d);
    return m;
  });
  const [serviceMinutes, setServiceMinutes] = useState(wp0.service_minutes);
  const [timeType, setTimeType] = useState<(typeof TIME_TYPE_OPTIONS)[number]>(wp0.time_type);
  const [preferredStart, setPreferredStart] = useState(wp0.preferred_start ?? '09:00');
  const [preferredEnd, setPreferredEnd] = useState(wp0.preferred_end ?? '12:00');

  const updateMut = useUpdatePatient(patient.id, initial);
  const geocodeMut = useGeocode();
  const resolveMut = useResolveOffice();

  // 時間タイプが「固定」「時間帯」のときのみ希望時刻欄を表示 (PatientForm/SuggestSheet と同条件)。
  const showTimeRange = timeType === '固定' || timeType === '時間帯';
  const showEnd = timeType === '時間帯';

  const addressChanged = address.trim() !== (initial.address ?? '').trim();

  const handleSave = async () => {
    if (!name.trim()) {
      onToast?.('氏名を入力してください');
      return;
    }
    if (!code.trim()) {
      onToast?.('患者コードを入力してください');
      return;
    }

    // 住所変更時のみ best-effort で再ジオコード + 拠点解決。重く結合せず、失敗は黙殺。
    let lat = initial.lat;
    let lng = initial.lng;
    let primaryOfficeId = initial.primary_office_id;
    const trimmedAddress = address.trim();
    if (addressChanged && trimmedAddress.length >= 5) {
      try {
        const geo = await geocodeMut.mutateAsync({ address: trimmedAddress });
        lat = String(geo.lat);
        lng = String(geo.lng);
      } catch {
        // ジオコード失敗 → lat/lng は据え置き (best-effort)。
      }
      try {
        const resolved = await resolveMut.mutateAsync(trimmedAddress);
        if (resolved.confidence !== 'none' && resolved.office_id) {
          primaryOfficeId = resolved.office_id;
        }
      } catch {
        // 拠点解決失敗 → primary_office_id は据え置き。
      }
    }

    const preferred_weekdays: WeekdayKey[] = WEEKDAY_KEYS.filter((d) => weekdays[d]);
    const weekly_pattern: WeeklyPattern = {
      ...wp0,
      frequency_per_week: frequencyPerWeek,
      visit_frequency: visitFrequency,
      preferred_weekdays,
      service_minutes: serviceMinutes,
      time_type: timeType,
      preferred_start: showTimeRange ? preferredStart : null,
      preferred_end: showEnd ? preferredEnd : null,
    };

    const values: PatientFormValues = {
      ...initial,
      name: name.trim(),
      code: code.trim(),
      kana: kana.trim(),
      sex,
      status,
      insurance,
      address: trimmedAddress,
      lat,
      lng,
      primary_office_id: primaryOfficeId,
      sex_restriction: sexRestriction,
      requires_multiple_staff: requiresMultipleStaff,
      note: note.trim(),
      weekly_pattern,
      // special_week は据え置き (特別週は本シートの編集対象外)。
      special_week: initial.special_week,
    };

    updateMut.mutate(values, {
      onSuccess: () => {
        onToast?.('✓ カルテを更新しました');
        onSaved();
      },
      onError: () => onToast?.('カルテの更新に失敗しました'),
    });
  };

  return (
    <SlideSheet>
      <Grip />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '4px 20px 10px',
        }}
      >
        <h3
          style={{
            fontFamily: 'var(--font-serif)',
            fontSize: 18,
            fontWeight: 700,
            color: _PLUM,
            margin: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 7,
          }}
        >
          <Pencil size={16} />
          カルテを編集
        </h3>
        <button
          onClick={onClose}
          aria-label="閉じる"
          style={{
            width: 34,
            height: 34,
            borderRadius: '50%',
            background: '#F1ECE3',
            color: _INK2,
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <X size={16} />
        </button>
      </div>

      <div className="cf-scroll" style={{ overflowY: 'auto', padding: '2px 20px 26px', flex: 1 }}>
        <Field label="氏名（必須）">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-label="氏名"
            placeholder="例: 青柳 あい"
            style={cfInput}
          />
        </Field>

        <div style={{ display: 'flex', gap: 12 }}>
          <Field label="患者コード（必須）" flex>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              aria-label="患者コード"
              placeholder="例: P-1042"
              style={cfInput}
            />
          </Field>
          <Field label="フリガナ" flex>
            <input
              value={kana}
              onChange={(e) => setKana(e.target.value)}
              aria-label="フリガナ"
              placeholder="アオヤギ アイ"
              style={cfInput}
            />
          </Field>
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Field label="性別" flex>
            <select
              style={cfInput}
              value={sex}
              aria-label="性別"
              onChange={(e) => setSex(e.target.value as PatientFormValues['sex'])}
            >
              <option value="">未選択</option>
              {SEX_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {SEX_LABEL[v]}
                </option>
              ))}
            </select>
          </Field>
          <Field label="ステータス" flex>
            <select
              style={cfInput}
              value={status}
              aria-label="ステータス"
              onChange={(e) => setStatus(e.target.value as PatientFormValues['status'])}
            >
              {STATUS_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {STATUS_LABEL[v]}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="保険">
          <select
            style={cfInput}
            value={insurance}
            aria-label="保険"
            onChange={(e) => setInsurance(e.target.value as PatientFormValues['insurance'])}
          >
            <option value="">未選択</option>
            {INSURANCE_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {INSURANCE_LABEL[v]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="ご住所">
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            aria-label="ご住所"
            placeholder="例: 千葉市稲毛区小仲台6-2-1"
            style={cfInput}
          />
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 5, lineHeight: 1.5 }}>
            ※ 住所を変更すると、保存時に緯度経度・主担当拠点を自動で取り直します。
          </div>
        </Field>

        <Field label="性別制限">
          <select
            style={cfInput}
            value={sexRestriction}
            aria-label="性別制限"
            onChange={(e) =>
              setSexRestriction(e.target.value as PatientFormValues['sex_restriction'])
            }
          >
            <option value="">なし</option>
            {SEX_RESTRICTION_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {SEX_RESTRICTION_LABEL[v]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="複数スタッフ">
          <button
            type="button"
            onClick={() => setRequiresMultipleStaff((v) => !v)}
            aria-pressed={requiresMultipleStaff}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '11px 13px',
              minHeight: 46,
              borderRadius: 12,
              border: `2px solid ${requiresMultipleStaff ? _PLUM : _LINE}`,
              background: requiresMultipleStaff ? '#F4ECF8' : '#FFFDF9',
              textAlign: 'left',
            }}
          >
            <span
              style={{
                width: 22,
                height: 22,
                flex: '0 0 auto',
                borderRadius: 7,
                display: 'grid',
                placeItems: 'center',
                background: requiresMultipleStaff ? _PLUM : '#fff',
                border: `2px solid ${requiresMultipleStaff ? _PLUM : _INK3}`,
                color: '#fff',
                fontSize: 13,
                fontWeight: 700,
                lineHeight: 1,
              }}
            >
              {requiresMultipleStaff ? '✓' : ''}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 13.5,
                fontWeight: 700,
                color: requiresMultipleStaff ? '#7A4E91' : _INK2,
              }}
            >
              2名以上での訪問が必要
            </span>
          </button>
        </Field>

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            fontFamily: 'var(--font-serif)',
            fontSize: 12,
            color: _PLUM,
            fontWeight: 700,
            margin: '6px 0 10px',
          }}
        >
          <Calendar size={13} />
          希望スケジュール
          <span style={{ flex: 1, height: 2, background: '#F0E7F5', borderRadius: 2 }} />
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Field label="週訪問回数" flex>
            <Stepper
              value={frequencyPerWeek}
              min={1}
              max={7}
              suffix="回 / 週"
              onChange={setFrequencyPerWeek}
            />
          </Field>
          <Field label="訪問頻度" flex>
            <select
              style={cfInput}
              value={visitFrequency}
              aria-label="訪問頻度"
              onChange={(e) =>
                setVisitFrequency(e.target.value as (typeof VISIT_FREQUENCY_OPTIONS)[number])
              }
            >
              {VISIT_FREQUENCY_OPTIONS.map((k) => (
                <option key={k} value={k}>
                  {VISIT_FREQUENCY_LABELS[k]}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="希望曜日（複数可）">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {WEEKDAY_KEYS.map((d) => {
              const on = !!weekdays[d];
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => setWeekdays((s) => ({ ...s, [d]: !s[d] }))}
                  aria-pressed={on}
                  aria-label={`${DOW_JA[d]}曜`}
                  style={{ ...chip, ...(on ? chipOn : {}) }}
                >
                  {DOW_JA[d]}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="サービス時間">
          <select
            style={cfInput}
            value={serviceMinutes}
            aria-label="サービス時間"
            onChange={(e) => setServiceMinutes(Number(e.target.value))}
          >
            {SERVICE_MINUTES_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}分
              </option>
            ))}
          </select>
        </Field>

        <Field label="時間タイプ">
          <select
            style={cfInput}
            value={timeType}
            aria-label="時間タイプ"
            onChange={(e) => setTimeType(e.target.value as (typeof TIME_TYPE_OPTIONS)[number])}
          >
            {TIME_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        {showTimeRange && (
          <div style={{ display: 'flex', gap: 12 }}>
            <Field label="希望開始時刻" flex>
              <select
                style={cfInput}
                value={preferredStart}
                aria-label="希望開始時刻"
                onChange={(e) => setPreferredStart(e.target.value)}
              >
                {SUGGEST_TIME_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </Field>
            {showEnd && (
              <Field label="希望終了時刻" flex>
                <select
                  style={cfInput}
                  value={preferredEnd}
                  aria-label="希望終了時刻"
                  onChange={(e) => setPreferredEnd(e.target.value)}
                >
                  {SUGGEST_TIME_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
        )}

        <Field label="備考・申し送り">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="備考・申し送り"
            rows={3}
            placeholder="玄関の鍵は植木鉢の下、など"
            style={{ ...cfInput, minHeight: 80, resize: 'vertical', lineHeight: 1.6 }}
          />
        </Field>

        <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={updateMut.isPending}
            style={{
              flex: '0 0 auto',
              padding: '14px 18px',
              minHeight: 50,
              borderRadius: 14,
              background: '#F1ECE3',
              color: _INK2,
              fontFamily: 'var(--font-serif)',
              fontSize: 14.5,
              fontWeight: 700,
            }}
          >
            キャンセル
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={updateMut.isPending}
            style={{
              flex: 1,
              padding: 14,
              minHeight: 50,
              borderRadius: 14,
              fontFamily: 'var(--font-serif)',
              fontSize: 15,
              fontWeight: 700,
              color: '#fff',
              background: `linear-gradient(135deg, ${_PLUM}, #7A4E91)`,
              boxShadow: '0 6px 16px rgba(139,92,158,0.30)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              opacity: updateMut.isPending ? 0.7 : 1,
            }}
          >
            <Save size={16} />
            {updateMut.isPending ? '保存中…' : 'カルテを保存'}
          </button>
        </div>
        <div style={{ height: 12 }} />
      </div>
    </SlideSheet>
  );
}

// ============================ Suggest sheet ============================

/**
 * 希望開始/終了時刻のプルダウン候補 (30 分刻み 06:00〜20:00)。
 * propose-slots は HH:MM を受け取る。
 */
const SUGGEST_TIME_OPTIONS: string[] = (() => {
  const out: string[] = [];
  for (let m = 6 * 60; m <= 20 * 60; m += 30) {
    const h = Math.floor(m / 60);
    const mm = m % 60;
    out.push(`${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
  }
  return out;
})();

export function SuggestSheet({
  isoYear,
  isoWeek,
  onClose,
  onToast,
}: {
  isoYear: number;
  isoWeek: number;
  onClose: () => void;
  onToast: (msg: string) => void;
}) {
  const [seg, setSeg] = useState(0);
  const [addr, setAddr] = useState('');

  // 既存患者の検索・紐付け (StageA)。
  // - existing モード (seg===1) のときだけ患者検索ボックスを出す。
  // - 選択した患者の id を `existing_patient_id` として propose に付与する。
  // - lat/lng は選択患者からコピーしてそのまま送る (再ジオコード不要)。
  const [patientQuery, setPatientQuery] = useState('');
  const [linkedPatient, setLinkedPatient] = useState<PatientRead | null>(null);
  const [linkedLat, setLinkedLat] = useState<number | null>(null);
  const [linkedLng, setLinkedLng] = useState<number | null>(null);

  // マスタ準拠の訪問条件 state。
  const [frequencyPerWeek, setFrequencyPerWeek] = useState(1); // 1〜7
  const [visitFrequency, setVisitFrequency] =
    useState<(typeof VISIT_FREQUENCY_OPTIONS)[number]>('every');
  const [weekdays, setWeekdays] = useState<Record<WeekdayKey, boolean>>({
    Mon: false,
    Tue: false,
    Wed: false,
    Thu: false,
    Fri: false,
    Sat: false,
    Sun: false,
  });
  const [serviceMinutes, setServiceMinutes] = useState(DEFAULT_SERVICE_MINUTES);
  const [timeType, setTimeType] = useState<(typeof TIME_TYPE_OPTIONS)[number]>('終日');
  const [preferredStart, setPreferredStart] = useState('09:00');
  const [preferredEnd, setPreferredEnd] = useState('12:00');
  const [requiresMultipleStaff, setRequiresMultipleStaff] = useState(false);
  const [sexRestriction, setSexRestriction] = useState<
    '' | (typeof SEX_RESTRICTION_OPTIONS)[number]
  >('');

  // StageB: 新規モードのカルテ項目 (氏名 / コード / フリガナ / 性別 / 保険)。
  // 住所・性別制限・複数スタッフは既存 state を流用 (addr / sexRestriction /
  // requiresMultipleStaff)。提案作成 (pending patient_create) 時にまとめて送る。
  const [karteName, setKarteName] = useState('');
  const [karteCode, setKarteCode] = useState('');
  const [karteKana, setKarteKana] = useState('');
  const [karteSex, setKarteSex] = useState<'' | (typeof SEX_OPTIONS)[number]>('');
  const [karteInsurance, setKarteInsurance] = useState<'' | (typeof INSURANCE_OPTIONS)[number]>('');

  // StageB: 採用した枠 (曜日 → 提案枠)。週N日なら各曜日 1 枠を選んで積む。
  // 同じ曜日を再タップすると採用解除 (toggle)。
  const [adopted, setAdopted] = useState<Map<WeekdayKey, ProposeSlotItem>>(() => new Map());

  const proposeMut = useProposeSlots();
  const createPendingMut = useCreatePendingRequest();

  // existing モードでの患者検索 (氏名 / カナ / コードで部分一致・client-side)。
  // 検索語が 1 文字以上のときのみ候補を出す (空のときは候補リストを隠す)。
  const trimmedQuery = patientQuery.trim();
  const isExisting = seg === 1;
  // StageA MEDIUM 対処: 患者一覧 (最大 500 件) は既存モードのときだけ取得する。
  // 新規モードでは enabled:false で fetch を抑止し、無駄な取得を防ぐ。
  const patientsQuery = usePatients({ search: trimmedQuery, limit: 8, enabled: isExisting });
  const candidates = isExisting && trimmedQuery ? (patientsQuery.data?.items ?? []) : [];

  // 選択した患者から提案フォームをプリフィルする。プリフィル後もユーザーが調整可。
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

    // 候補リストを畳む (検索語クリア)。
    setPatientQuery('');
  };

  // 紐付け解除 (新規入力に戻す)。住所/希望はユーザーが触れている可能性があるので保持する。
  const unlinkPatient = () => {
    setLinkedPatient(null);
    setLinkedLat(null);
    setLinkedLng(null);
  };

  // 時間タイプが「固定」「時間帯」のときのみ時刻欄を表示。
  const showTimeRange = timeType === '固定' || timeType === '時間帯';
  const showEnd = timeType === '時間帯';

  const runSuggest = () => {
    if (!addr.trim()) {
      onToast('住所を入力してください');
      return;
    }
    // 再提案のたびに採用枠をクリア (枠の顔ぶれが変わるため)。
    setAdopted(new Map());
    const preferred_weekdays: WeekdayCode[] = WEEKDAY_KEYS.filter(
      (d) => weekdays[d],
    ) as WeekdayCode[];
    // 既存患者を紐付け中なら existing_patient_id と (あれば) lat/lng を付与する。
    // 新規モード (紐付けなし) では existing_patient_id は送らない。
    const linked = isExisting ? linkedPatient : null;
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
        // 拠点プルダウン廃止に伴い office_ids は省略 (= 全拠点検索)。拠点跨ぎで提示する。
        office_ids: [],
        existing_patient_id: linked ? linked.id : null,
        limit: 10,
      },
      {
        onError: () => onToast('提案の取得に失敗しました'),
      },
    );
  };

  const result = proposeMut.data;
  const slots = result?.slots ?? [];
  const coverage = result?.coverage ?? null;

  // ── StageB: 枠の採用 (曜日 → 枠)。週N日なら各曜日 1 枠。同じ枠の再タップで解除。
  const weekdayKeyOf = (code: WeekdayCode): WeekdayKey => code as unknown as WeekdayKey;
  const slotKey = (s: ProposeSlotItem) =>
    `${s.office_id}-${s.weekday}-${s.course_code}-${s.start_time}`;
  const isAdopted = (s: ProposeSlotItem) => {
    const cur = adopted.get(weekdayKeyOf(s.weekday_code));
    return !!cur && slotKey(cur) === slotKey(s);
  };
  const toggleAdopt = (s: ProposeSlotItem) => {
    setAdopted((prev) => {
      const next = new Map(prev);
      const wk = weekdayKeyOf(s.weekday_code);
      const cur = next.get(wk);
      if (cur && slotKey(cur) === slotKey(s)) {
        next.delete(wk); // 同じ枠の再タップ → 解除
      } else {
        next.set(wk, s); // その曜日の採用枠を差し替え
      }
      return next;
    });
  };

  // 希望週N日 (required_days)。coverage 優先、無ければ frequencyPerWeek。
  const requiredDays = coverage?.required_days ?? frequencyPerWeek;
  const adoptedCount = adopted.size;
  // 提案作成可否: 新規モードで、氏名/コードあり + 採用枠が 1 件以上。
  const canCreate =
    !isExisting &&
    karteName.trim().length > 0 &&
    karteCode.trim().length > 0 &&
    adoptedCount > 0 &&
    !createPendingMut.isPending;

  const submitKarte = () => {
    if (isExisting) return; // 既存患者は本フロー対象外 (StageA のまま)。
    if (!karteName.trim()) {
      onToast('氏名を入力してください');
      return;
    }
    if (!karteCode.trim()) {
      onToast('患者コードを入力してください');
      return;
    }
    if (adoptedCount === 0) {
      onToast('採用する枠を選んでください');
      return;
    }
    const preferred_weekdays: WeekdayKey[] = WEEKDAY_KEYS.filter((d) => weekdays[d]);
    const karte: KarteInput = {
      name: karteName.trim(),
      code: karteCode.trim(),
      kana: karteKana.trim(),
      sex: karteSex,
      insurance: karteInsurance,
      address: addr.trim(),
      lat: null,
      lng: null,
      sex_restriction: sexRestriction,
      requires_multiple_staff: requiresMultipleStaff,
      // 直近 propose 結果が住所から解決した担当拠点。propose 未実行 / None なら
      // 載せず (患者の primary_office_id は applier 側で NULL のまま)。
      primary_office_id: result?.resolved_office_id ?? null,
    };
    const schedule: DesiredSchedule = {
      frequency_per_week: frequencyPerWeek,
      visit_frequency: visitFrequency,
      preferred_weekdays,
      service_minutes: serviceMinutes,
      time_type: timeType,
      preferred_start: showTimeRange ? preferredStart : null,
      preferred_end: showEnd ? preferredEnd : null,
    };
    const proposedVisits = buildProposedVisits(adopted, serviceMinutes);
    const payload = buildPatientCreatePayload(karte, schedule, proposedVisits);
    createPendingMut.mutate(
      { request_type: 'patient_create', payload },
      {
        onSuccess: () => {
          onToast('✓ 提案を作成しました（承認待ち）');
          onClose();
        },
        onError: () => onToast('提案の作成に失敗しました'),
      },
    );
  };

  return (
    <SlideSheet>
      <Grip />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '4px 20px 10px',
        }}
      >
        <h3
          style={{
            fontFamily: 'var(--font-serif)',
            fontSize: 18,
            fontWeight: 700,
            color: _TERRAD,
            margin: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 7,
          }}
        >
          <Sparkles size={17} />
          新規訪問の提案
        </h3>
        <button
          onClick={onClose}
          aria-label="閉じる"
          style={{
            width: 34,
            height: 34,
            borderRadius: '50%',
            background: '#F1ECE3',
            color: _INK2,
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <X size={16} />
        </button>
      </div>
      <div className="cf-scroll" style={{ overflowY: 'auto', padding: '2px 20px 26px', flex: 1 }}>
        <div
          style={{
            display: 'flex',
            gap: 6,
            background: '#F4EFE7',
            padding: 4,
            borderRadius: 13,
            marginBottom: 14,
          }}
        >
          {['新規のお客様', '既存のお客様'].map((s, i) => (
            <button
              key={s}
              onClick={() => setSeg(i)}
              style={{
                flex: 1,
                padding: 10,
                borderRadius: 10,
                fontFamily: 'var(--font-serif)',
                fontSize: 13.5,
                fontWeight: 700,
                background: seg === i ? '#fff' : 'transparent',
                color: seg === i ? _TERRAD : _INK3,
                boxShadow: seg === i ? '0 2px 6px rgba(0,0,0,0.07)' : 'none',
              }}
            >
              {s}
            </button>
          ))}
        </div>

        {isExisting && (
          <PatientLinkPanel
            linked={linkedPatient}
            query={patientQuery}
            onQueryChange={setPatientQuery}
            candidates={candidates}
            isFetching={patientsQuery.isFetching}
            isError={patientsQuery.isError}
            onSelect={linkPatient}
            onUnlink={unlinkPatient}
          />
        )}

        {!isExisting && (
          <KarteFormSection
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
        )}

        <Field label="ご住所">
          <input
            value={addr}
            onChange={(e) => setAddr(e.target.value)}
            placeholder="例: 千葉市稲毛区小仲台6-2-1"
            style={cfInput}
          />
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 5, lineHeight: 1.5 }}>
            ※ 住所からジオコードして担当拠点・近接スコアを判定します。既存患者と<b>同住所</b>
            のときは「同住所ペア」候補を優先表示します。
          </div>
        </Field>

        <div style={{ display: 'flex', gap: 12 }}>
          <Field label="週訪問回数" flex>
            <Stepper
              value={frequencyPerWeek}
              min={1}
              max={7}
              suffix="回 / 週"
              onChange={setFrequencyPerWeek}
            />
          </Field>
          <Field label="訪問頻度" flex>
            <select
              style={cfInput}
              value={visitFrequency}
              onChange={(e) =>
                setVisitFrequency(e.target.value as (typeof VISIT_FREQUENCY_OPTIONS)[number])
              }
            >
              {VISIT_FREQUENCY_OPTIONS.map((k) => (
                <option key={k} value={k}>
                  {VISIT_FREQUENCY_LABELS[k]}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="希望曜日（複数可）">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {WEEKDAY_KEYS.map((d) => {
              const on = !!weekdays[d];
              return (
                <button
                  key={d}
                  onClick={() => setWeekdays((s) => ({ ...s, [d]: !s[d] }))}
                  style={{ ...chip, ...(on ? chipOn : {}) }}
                >
                  {DOW_JA[d]}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="サービス時間">
          <select
            style={cfInput}
            value={serviceMinutes}
            onChange={(e) => setServiceMinutes(Number(e.target.value))}
          >
            {SERVICE_MINUTES_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}分
              </option>
            ))}
          </select>
        </Field>

        <Field label="時間タイプ">
          <select
            style={cfInput}
            value={timeType}
            onChange={(e) => setTimeType(e.target.value as (typeof TIME_TYPE_OPTIONS)[number])}
          >
            {TIME_TYPE_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>

        {showTimeRange && (
          <div style={{ display: 'flex', gap: 12 }}>
            <Field label="希望開始時刻" flex>
              <select
                style={cfInput}
                value={preferredStart}
                onChange={(e) => setPreferredStart(e.target.value)}
              >
                {SUGGEST_TIME_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </Field>
            {showEnd && (
              <Field label="希望終了時刻" flex>
                <select
                  style={cfInput}
                  value={preferredEnd}
                  onChange={(e) => setPreferredEnd(e.target.value)}
                >
                  {SUGGEST_TIME_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
        )}

        <Field label="性別制限">
          <select
            style={cfInput}
            value={sexRestriction}
            onChange={(e) =>
              setSexRestriction(e.target.value as '' | (typeof SEX_RESTRICTION_OPTIONS)[number])
            }
          >
            <option value="">なし</option>
            {SEX_RESTRICTION_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {SEX_RESTRICTION_LABEL[v]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="複数スタッフ">
          <button
            onClick={() => setRequiresMultipleStaff((v) => !v)}
            aria-pressed={requiresMultipleStaff}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '11px 13px',
              minHeight: 46,
              borderRadius: 12,
              border: `2px solid ${requiresMultipleStaff ? _TERRA : _LINE}`,
              background: requiresMultipleStaff ? '#FCEBD6' : '#FFFDF9',
              textAlign: 'left',
            }}
          >
            <span
              style={{
                width: 22,
                height: 22,
                flex: '0 0 auto',
                borderRadius: 7,
                display: 'grid',
                placeItems: 'center',
                background: requiresMultipleStaff ? _TERRA : '#fff',
                border: `2px solid ${requiresMultipleStaff ? _TERRA : _INK3}`,
                color: '#fff',
                fontSize: 13,
                fontWeight: 700,
                lineHeight: 1,
              }}
            >
              {requiresMultipleStaff ? '✓' : ''}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 13.5,
                fontWeight: 700,
                color: requiresMultipleStaff ? _TERRAD : _INK2,
              }}
            >
              2名以上での訪問が必要
            </span>
          </button>
        </Field>

        <button
          onClick={runSuggest}
          disabled={proposeMut.isPending}
          style={{
            width: '100%',
            marginTop: 8,
            padding: 15,
            minHeight: 52,
            background: `linear-gradient(135deg, ${_TERRA}, ${_TERRAD})`,
            color: '#fff',
            fontFamily: 'var(--font-serif)',
            fontSize: 16,
            fontWeight: 700,
            borderRadius: 15,
            boxShadow: '0 6px 16px rgba(217,119,6,0.30)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            opacity: proposeMut.isPending ? 0.7 : 1,
          }}
        >
          <Sparkles size={17} />
          {proposeMut.isPending ? '提案を計算中…' : '自動提案する'}
        </button>

        {proposeMut.isError && (
          <div
            style={{
              marginTop: 12,
              background: '#FCEFF2',
              border: '2px solid #F4D4DC',
              borderRadius: 12,
              padding: 11,
              fontSize: 12.5,
              fontWeight: 700,
              color: '#C75C77',
            }}
          >
            提案の取得に失敗しました。住所や通信状況をご確認ください。
          </div>
        )}

        {result && !proposeMut.isPending && (
          <div style={{ marginTop: 18 }}>
            {requiresMultipleStaff && <MultiStaffNote />}
            {slots.length === 0 ? (
              <div
                style={{
                  textAlign: 'center',
                  padding: '28px 16px',
                  color: _INK2,
                  fontFamily: 'var(--font-serif)',
                  background: '#F8F4ED',
                  borderRadius: 14,
                  border: `1px solid ${_LINE}`,
                }}
              >
                <div style={{ fontSize: 32 }}>🔍</div>
                <div style={{ marginTop: 8, fontSize: 15, fontWeight: 700 }}>
                  入れられる枠がありません
                </div>
                {result.message && (
                  <div style={{ fontSize: 11.5, marginTop: 6, fontFamily: 'var(--font-sans)' }}>
                    {result.message}
                  </div>
                )}
              </div>
            ) : (
              <>
                {coverage && <CoverageSummary coverage={coverage} onPick={toggleAdopt} />}
                <h4
                  style={{
                    fontFamily: 'var(--font-serif)',
                    fontSize: 14.5,
                    fontWeight: 700,
                    margin: '0 0 10px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  おすすめ枠 {slots.length}件
                  {!isExisting && (
                    <span style={{ fontSize: 11, color: _INK3, fontWeight: 600 }}>
                      （タップで採用）
                    </span>
                  )}
                </h4>
                {slots.map((s, i) => (
                  <RankCard
                    key={`${s.office_id}-${s.weekday}-${s.course_code}-${i}`}
                    s={s}
                    rank={i + 1}
                    selectable={!isExisting}
                    adopted={isAdopted(s)}
                    onAdopt={toggleAdopt}
                  />
                ))}
                {!isExisting && (
                  <CreatePendingBar
                    adopted={adopted}
                    requiredDays={requiredDays}
                    canCreate={canCreate}
                    isPending={createPendingMut.isPending}
                    onSubmit={submitKarte}
                  />
                )}
              </>
            )}
          </div>
        )}
        <div style={{ height: 12 }} />
      </div>
    </SlideSheet>
  );
}

/**
 * 既存患者の検索・紐付けパネル (StageA)。
 *
 * - 未紐付け: 検索ボックス (氏名/カナ/コード部分一致) + 入力に応じた候補リスト。
 *   候補を選ぶと親側 (`onSelect`) で提案フォームをプリフィルする。
 * - 紐付け中: 「○○様(コード)を紐付け中 [変更]」を上部に表示し、`onUnlink` で解除。
 *
 * Warm 意匠 (cfInput / Terra アクセント) で検索 UI も提案シートに統一する。
 */
function PatientLinkPanel({
  linked,
  query,
  onQueryChange,
  candidates,
  isFetching,
  isError,
  onSelect,
  onUnlink,
}: {
  linked: PatientRead | null;
  query: string;
  onQueryChange: (next: string) => void;
  candidates: PatientRead[];
  isFetching: boolean;
  isError: boolean;
  onSelect: (p: PatientRead) => void;
  onUnlink: () => void;
}) {
  const trimmed = query.trim();
  const showEmpty = !!trimmed && !isFetching && !isError && candidates.length === 0;

  if (linked) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 11,
          background: 'linear-gradient(180deg,#FFF7EC,#FDF0DC)',
          border: `2px solid ${_TERRA}`,
          borderRadius: 14,
          padding: '11px 13px',
          marginBottom: 14,
        }}
      >
        <span
          style={{
            width: 36,
            height: 36,
            flex: '0 0 auto',
            borderRadius: 12,
            background: _TERRA,
            color: '#fff',
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <Link2 size={17} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 14,
              fontWeight: 700,
              color: _TERRAD,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {linked.name} 様 を紐付け中
          </div>
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 1 }}>
            患者コード: {linked.code}
            {linked.address ? ` ・ ${linked.address}` : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={onUnlink}
          style={{
            flex: '0 0 auto',
            padding: '7px 14px',
            minHeight: 38,
            borderRadius: 999,
            background: '#fff',
            border: `2px solid ${_TERRA}`,
            color: _TERRAD,
            fontFamily: 'var(--font-serif)',
            fontSize: 12.5,
            fontWeight: 700,
          }}
        >
          変更
        </button>
      </div>
    );
  }

  return (
    <Field label="既存のお客様を検索">
      <div style={{ position: 'relative' }}>
        <span
          style={{
            position: 'absolute',
            left: 12,
            top: '50%',
            transform: 'translateY(-50%)',
            color: _INK3,
            pointerEvents: 'none',
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <Search size={16} />
        </span>
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="氏名 / カナ / 患者コードで検索"
          aria-label="既存のお客様を検索"
          style={{ ...cfInput, paddingLeft: 36 }}
        />
      </div>

      {isError && (
        <div style={{ fontSize: 11, color: '#C75C77', fontWeight: 700, marginTop: 7 }}>
          患者の検索に失敗しました。通信状況をご確認ください。
        </div>
      )}

      {!!trimmed && isFetching && candidates.length === 0 && (
        <div style={{ fontSize: 11, color: _INK2, marginTop: 7, fontFamily: 'var(--font-serif)' }}>
          検索中…
        </div>
      )}

      {showEmpty && (
        <div style={{ fontSize: 11, color: _INK2, marginTop: 7 }}>
          該当するお客様が見つかりませんでした。
        </div>
      )}

      {candidates.length > 0 && (
        <div
          style={{
            marginTop: 8,
            border: `2px solid ${_LINE}`,
            borderRadius: 12,
            overflow: 'hidden',
            background: '#fff',
          }}
        >
          {candidates.map((p, i) => (
            <button
              key={p.id}
              type="button"
              onClick={() => onSelect(p)}
              style={{
                width: '100%',
                textAlign: 'left',
                display: 'flex',
                alignItems: 'center',
                gap: 11,
                padding: '10px 12px',
                background: '#fff',
                borderTop: i === 0 ? 'none' : `1px solid ${_LINE}`,
              }}
            >
              <span
                style={{
                  width: 34,
                  height: 34,
                  flex: '0 0 auto',
                  borderRadius: 11,
                  background: '#F4EFE7',
                  color: _TERRAD,
                  display: 'grid',
                  placeItems: 'center',
                  fontFamily: 'var(--font-serif)',
                  fontSize: 16,
                  fontWeight: 700,
                }}
              >
                {p.name.charAt(0)}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    display: 'block',
                    fontFamily: 'var(--font-serif)',
                    fontSize: 14,
                    fontWeight: 700,
                    color: _INK,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {p.name}
                  <span style={{ fontSize: 10.5, color: _INK3, fontWeight: 600, marginLeft: 7 }}>
                    {p.code}
                  </span>
                </span>
                {(p.kana || p.address) && (
                  <span
                    style={{
                      display: 'block',
                      fontSize: 10.5,
                      color: _INK2,
                      marginTop: 1,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {p.kana ? `${p.kana}` : ''}
                    {p.kana && p.address ? ' ・ ' : ''}
                    {p.address ?? ''}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </Field>
  );
}

const WEEKDAY_INT_TO_DOW: Record<number, string> = (() => {
  const out: Record<number, string> = {};
  for (const code of WEEKDAY_KEYS) {
    out[WEEKDAY_CODE_TO_INT[code as WeekdayCode]] = DOW_JA[code];
  }
  return out;
})();

function MiniSlot({ row }: { row: ProposeMiniScheduleEntry }) {
  if (row.is_here) {
    const mint = row.is_pair;
    return (
      <div style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10.5,
            color: mint ? _MINT : _TERRAD,
            width: 42,
            flex: '0 0 42px',
            paddingTop: 9,
            fontWeight: 600,
          }}
        >
          {row.time}
        </span>
        <div
          style={{
            flex: 1,
            borderRadius: 10,
            border: `2px dashed ${mint ? _MINT : _TERRA}`,
            background: mint
              ? 'repeating-linear-gradient(45deg,#E9FAF4,#E9FAF4 8px,#D6F5EC 8px,#D6F5EC 16px)'
              : 'repeating-linear-gradient(45deg,#FFFBF4,#FFFBF4 8px,#FDF1DF 8px,#FDF1DF 16px)',
            padding: '8px 11px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span
            style={{
              width: 22,
              height: 22,
              borderRadius: '50%',
              background: mint ? _MINT : _TERRA,
              color: '#fff',
              display: 'grid',
              placeItems: 'center',
              fontSize: 15,
              lineHeight: 1,
              flex: '0 0 auto',
            }}
          >
            ＋
          </span>
          <span
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 12.5,
              fontWeight: 700,
              color: mint ? '#0E8472' : _TERRAD,
            }}
          >
            {mint ? 'ここに一緒に入れられます' : 'ここに入れられます'}
          </span>
        </div>
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
      <span
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 10.5,
          color: _INK3,
          width: 42,
          flex: '0 0 42px',
          paddingTop: 8,
        }}
      >
        {row.time}
      </span>
      <div
        style={{
          flex: 1,
          borderRadius: 10,
          background: '#F8F4ED',
          borderLeft: `4px solid ${row.ins === 'med' ? '#2F6FB0' : '#0E8472'}`,
          padding: '7px 11px',
          display: 'flex',
          alignItems: 'center',
          gap: 7,
        }}
      >
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12.5, fontWeight: 700 }}>
          {row.name}
        </span>
        {(row.ins === 'med' || row.ins === 'care') && (
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 700,
              padding: '1px 6px',
              borderRadius: 999,
              background: row.ins === 'med' ? '#E2EDF8' : '#D7F2EE',
              color: row.ins === 'med' ? '#2F6FB0' : '#0E8472',
            }}
          >
            {row.ins === 'med' ? '医療' : '介護'}
          </span>
        )}
      </div>
    </div>
  );
}

function RankCard({
  s,
  rank,
  selectable = false,
  adopted = false,
  onAdopt,
}: {
  s: ProposeSlotItem;
  rank: number;
  /** StageB: 新規モードで「採用」操作を出すか。 */
  selectable?: boolean;
  /** その枠が採用済みか。 */
  adopted?: boolean;
  onAdopt?: (s: ProposeSlotItem) => void;
}) {
  const medal = ['#E5B53A', '#B6BEC8', '#CF8048'][rank - 1] || '#CDC2B2';
  const mInk = ['#7A5200', '#3F4750', '#fff', '#fff'][rank - 1] ?? '#fff';
  const dow = WEEKDAY_INT_TO_DOW[s.weekday] ?? '';
  const filled = s.mini_schedule.filter((m) => !m.is_here).length;
  const whenLabel = `${dow} ${s.start_time}〜`;
  return (
    <div
      style={{
        borderRadius: 16,
        marginBottom: 12,
        background: adopted ? '#FCFBF4' : '#fff',
        border: `2px solid ${adopted ? _MINT : rank === 1 ? '#E5B53A' : _LINE}`,
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', gap: 11, alignItems: 'center', padding: '12px 14px' }}>
        <div
          style={{
            width: 38,
            height: 38,
            flex: '0 0 auto',
            borderRadius: 12,
            background: medal,
            color: mInk,
            display: 'grid',
            placeItems: 'center',
            fontFamily: 'var(--font-serif)',
            fontSize: 16,
            fontWeight: 700,
          }}
        >
          {rank}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 14.5, fontWeight: 700 }}>
            {s.course_label} <span style={{ color: s.is_pair ? _MINT : _TERRAD }}>{whenLabel}</span>
          </div>
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 1 }}>
            {s.staff_name ? `${s.staff_name} ・ ` : ''}
            {s.start_time}〜{s.end_time}
            {s.is_pair && s.pair_partner ? ` ・ ペア相手: ${s.pair_partner}` : ''}
          </div>
        </div>
      </div>

      {(s.reasons.length > 0 || s.is_pair) && (
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', padding: '0 14px 8px' }}>
          {s.is_pair && (
            <span
              style={{
                fontSize: 10.5,
                padding: '2px 8px',
                borderRadius: 999,
                fontWeight: 700,
                background: '#F0E7F5',
                color: _PLUM,
              }}
            >
              同住所ペア
            </span>
          )}
          {s.reasons.map((r, i) => (
            <span
              key={i}
              style={{
                fontSize: 10.5,
                padding: '2px 8px',
                borderRadius: 999,
                fontWeight: 700,
                background: '#D7F2EE',
                color: '#0E8472',
              }}
            >
              {r}
            </span>
          ))}
        </div>
      )}

      {s.warnings.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '0 14px 8px' }}>
          {s.warnings.map((w, i) => (
            <span
              key={i}
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                color: _TERRAD,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <AlertTriangle size={11} />
              {proposeWarningLabel(w)}
            </span>
          ))}
        </div>
      )}

      {s.mini_schedule.length > 0 && (
        <div
          style={{
            background: '#FCFAF6',
            borderTop: `1px solid ${_LINE}`,
            padding: '11px 14px 13px',
          }}
        >
          <div
            style={{
              fontSize: 10,
              fontWeight: 700,
              color: _INK2,
              marginBottom: 8,
              letterSpacing: '0.04em',
            }}
          >
            {s.course_label}
            {s.staff_name ? `（${s.staff_name}）` : ''}の{dow}曜 ・ {filled}件 + 提案枠
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {s.mini_schedule.map((row, i) => (
              <MiniSlot key={i} row={row} />
            ))}
          </div>
        </div>
      )}

      {selectable && (
        <div style={{ padding: '0 14px 12px' }}>
          <button
            type="button"
            onClick={() => onAdopt?.(s)}
            aria-pressed={adopted}
            style={{
              width: '100%',
              minHeight: 42,
              padding: '10px 14px',
              borderRadius: 12,
              fontFamily: 'var(--font-serif)',
              fontSize: 13.5,
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 7,
              border: `2px solid ${adopted ? _MINT : _TERRA}`,
              background: adopted ? _MINT : '#FFFDF9',
              color: adopted ? '#fff' : _TERRAD,
            }}
          >
            {adopted ? (
              <>
                <Check size={15} />
                {dow}曜 この枠を採用中
              </>
            ) : (
              <>
                <Sparkles size={14} />
                {dow}曜 この枠を採用
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}

// ============================ StageB: 新規カルテ入力 ============================

/**
 * 新規モードのカルテ項目入力 (氏名 / 患者コード / フリガナ / 性別 / 保険)。
 *
 * 住所・性別制限・複数スタッフは提案フォーム本体 (既存 Field) で入力するため
 * ここには含めない。患者マスタ準拠の選択肢 (SEX_OPTIONS / INSURANCE_OPTIONS) を流用。
 */
function KarteFormSection({
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
    <div
      style={{
        background: 'linear-gradient(180deg,#FFFDF8,#FBF4E8)',
        border: `2px solid ${_LINE}`,
        borderRadius: 16,
        padding: '13px 14px 4px',
        marginBottom: 14,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontFamily: 'var(--font-serif)',
          fontSize: 12.5,
          fontWeight: 700,
          color: _TERRAD,
          marginBottom: 10,
        }}
      >
        <User size={13} />
        新規カルテ
        <span style={{ flex: 1, height: 2, background: '#F3E6D2', borderRadius: 2 }} />
      </div>

      <Field label="氏名（必須）">
        <input
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="例: 青柳 あい"
          aria-label="氏名"
          style={cfInput}
        />
      </Field>

      <div style={{ display: 'flex', gap: 12 }}>
        <Field label="患者コード（必須）" flex>
          <input
            value={code}
            onChange={(e) => onCode(e.target.value)}
            placeholder="例: P-1042"
            aria-label="患者コード"
            style={cfInput}
          />
        </Field>
        <Field label="フリガナ" flex>
          <input
            value={kana}
            onChange={(e) => onKana(e.target.value)}
            placeholder="アオヤギ アイ"
            aria-label="フリガナ"
            style={cfInput}
          />
        </Field>
      </div>

      <div style={{ display: 'flex', gap: 12 }}>
        <Field label="性別" flex>
          <select
            style={cfInput}
            value={sex}
            aria-label="性別"
            onChange={(e) => onSex(e.target.value as '' | (typeof SEX_OPTIONS)[number])}
          >
            <option value="">未選択</option>
            {SEX_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {SEX_LABEL[v]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="保険" flex>
          <select
            style={cfInput}
            value={insurance}
            aria-label="保険"
            onChange={(e) => onInsurance(e.target.value as '' | (typeof INSURANCE_OPTIONS)[number])}
          >
            <option value="">未選択</option>
            {INSURANCE_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {INSURANCE_LABEL[v]}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </div>
  );
}

// ============================ 複数スタッフ注記 ============================

/**
 * 複数スタッフ必須 (requires_multiple_staff) ON のときに結果付近へ出す控えめな注記。
 *
 * solver は現状 2 名体制の空き判定に未対応 (1 名前提の提案) のため、提案結果が
 * 2 名体制の可否を保証しない旨を Warm 意匠で正直に伝える。
 */
function MultiStaffNote() {
  return (
    <div
      role="note"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 7,
        background: '#FFFBF3',
        border: `1px solid ${_LINE}`,
        borderRadius: 12,
        padding: '9px 12px',
        marginBottom: 12,
        fontSize: 11.5,
        lineHeight: 1.5,
        color: _INK2,
      }}
    >
      <span style={{ flex: '0 0 auto', color: _TERRAD, paddingTop: 1 }}>
        <AlertTriangle size={13} />
      </span>
      <span>※ 2名体制の空き判定は未対応です（1名前提の提案）</span>
    </div>
  );
}

// ============================ StageC: 週N日カバレッジ ============================

/**
 * 自動提案結果の上部に出すカバレッジサマリ (StageC)。
 *
 * - 見出し: 「希望 週{required_days}日 → {covered_days}/{required_days}日 提案可」。
 * - per_day を曜日チップで ○(has_slot) / ×(無し, 赤系) 表示。
 * - fully_covered なら緑「全日OK」、未充足は警告色。
 * - ○ チップ (best_slot あり) をタップでその枠を採用 (onPick)。
 */
function CoverageSummary({
  coverage,
  onPick,
}: {
  coverage: ProposeCoverage;
  onPick: (s: ProposeSlotItem) => void;
}) {
  const { required_days, covered_days, fully_covered, per_day } = coverage;
  const accent = fully_covered ? _MINTD : _TERRAD;
  const bg = fully_covered
    ? 'linear-gradient(180deg,#EAF9F4,#DCF4EC)'
    : 'linear-gradient(180deg,#FFF7EC,#FDEFD9)';
  const border = fully_covered ? _MINT : _TERRA;
  return (
    <div
      style={{
        background: bg,
        border: `2px solid ${border}`,
        borderRadius: 16,
        padding: '12px 14px',
        marginBottom: 14,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            width: 30,
            height: 30,
            flex: '0 0 auto',
            borderRadius: 10,
            background: border,
            color: '#fff',
            display: 'grid',
            placeItems: 'center',
          }}
        >
          {fully_covered ? <CheckCircle2 size={17} /> : <Calendar size={16} />}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 13.5,
              fontWeight: 700,
              color: accent,
            }}
          >
            希望 週{required_days}日 → {covered_days}/{required_days}日 提案可
          </div>
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 1 }}>
            {fully_covered ? '全日OK・採用する枠を選んでください' : '未充足の曜日があります'}
          </div>
        </div>
        {fully_covered && (
          <span
            style={{
              flex: '0 0 auto',
              fontSize: 11,
              fontWeight: 700,
              padding: '3px 10px',
              borderRadius: 999,
              background: _MINT,
              color: '#fff',
              fontFamily: 'var(--font-serif)',
            }}
          >
            全日OK
          </span>
        )}
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 11 }}>
        {per_day.map((d) => {
          const ja = WEEKDAY_INT_TO_DOW[d.weekday] ?? '';
          const ok = d.has_slot;
          const tappable = ok && !!d.best_slot;
          return (
            <button
              key={d.weekday_code}
              type="button"
              disabled={!tappable}
              onClick={() => d.best_slot && onPick(d.best_slot)}
              aria-label={`${ja}曜 ${ok ? '提案あり' : '提案なし'}`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '6px 11px',
                minHeight: 36,
                borderRadius: 999,
                fontFamily: 'var(--font-serif)',
                fontSize: 13,
                fontWeight: 700,
                cursor: tappable ? 'pointer' : 'default',
                background: ok ? '#D7F2EE' : '#FCE3E8',
                color: ok ? _MINTD : _ROSE,
                border: `2px solid ${ok ? _MINT : '#F1B9C5'}`,
              }}
            >
              <span>{ja}</span>
              <span style={{ fontSize: 14, lineHeight: 1 }}>{ok ? '○' : '×'}</span>
              {tappable && d.best_slot && (
                <span style={{ fontSize: 10, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
                  {d.best_slot.start_time}〜
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ============================ StageB: 提案作成バー ============================

/**
 * 採用した枠の要約と「提案を作成（承認へ）」ボタン (StageB)。
 *
 * - 採用枠を曜日順に並べて、未充足曜日があれば警告。
 * - pending `patient_create` 作成 (onSubmit)。氏名/コードと採用枠が揃うまで無効。
 */
function CreatePendingBar({
  adopted,
  requiredDays,
  canCreate,
  isPending,
  onSubmit,
}: {
  adopted: Map<WeekdayKey, ProposeSlotItem>;
  requiredDays: number;
  canCreate: boolean;
  isPending: boolean;
  onSubmit: () => void;
}) {
  const picks = useMemo(
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
      style={{
        marginTop: 6,
        background: '#fff',
        border: `2px solid ${_LINE}`,
        borderRadius: 16,
        padding: '13px 14px',
      }}
    >
      <div
        style={{
          fontFamily: 'var(--font-serif)',
          fontSize: 12.5,
          fontWeight: 700,
          color: _INK,
          marginBottom: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <ClipboardCheck size={13} />
        採用した枠 {adoptedCount}件 / 希望 週{requiredDays}日
      </div>

      {adoptedCount === 0 ? (
        <div style={{ fontSize: 11.5, color: _INK2, marginBottom: 10 }}>
          上のおすすめ枠から、曜日ごとに 1 枠ずつ採用してください。
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 10 }}>
          {picks.map((s) => (
            <div
              key={`${s.office_id}-${s.weekday}-${s.course_code}-${s.start_time}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                fontSize: 12,
                color: _INK2,
              }}
            >
              <span
                style={{
                  width: 22,
                  height: 22,
                  flex: '0 0 auto',
                  borderRadius: 7,
                  background: '#D7F2EE',
                  color: _MINTD,
                  display: 'grid',
                  placeItems: 'center',
                  fontFamily: 'var(--font-serif)',
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                {WEEKDAY_INT_TO_DOW[s.weekday] ?? ''}
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
                {s.start_time}〜{s.end_time}
              </span>
              <span style={{ fontWeight: 700, color: _INK }}>{s.course_label}</span>
            </div>
          ))}
        </div>
      )}

      {shortfall > 0 && adoptedCount > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 11,
            fontWeight: 700,
            color: _TERRAD,
            marginBottom: 10,
          }}
        >
          <AlertTriangle size={12} />
          あと {shortfall} 日ぶん未充足です（このまま作成も可）
        </div>
      )}

      <button
        type="button"
        onClick={onSubmit}
        disabled={!canCreate}
        style={{
          width: '100%',
          padding: 14,
          minHeight: 50,
          borderRadius: 14,
          fontFamily: 'var(--font-serif)',
          fontSize: 15,
          fontWeight: 700,
          color: '#fff',
          background: canCreate ? `linear-gradient(135deg, ${_MINT}, ${_MINTD})` : '#CFC8BC',
          boxShadow: canCreate ? '0 6px 16px rgba(14,159,110,0.28)' : 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
        }}
      >
        <ClipboardCheck size={16} />
        {isPending ? '作成中…' : '提案を作成（承認へ）'}
      </button>
    </div>
  );
}

// ============================ Phase G-84: 空き枠への直接配置シート ============================
//
// 空き枠タップ → この枠に新規/既存の患者を直接配置する。開始時刻は
//   案2(移動時間提案) を初期値 + 案3(5分刻み手動調整) + 案1(枠頭フォールバック)。
// 書き込みは pending_request 経由 (即時反映しない)。赤警告は理由付きで強行可、
// 黄警告は承知チェックで許可する。

const PLACEMENT_STEP_MIN = 5; // 開始時刻の刻み (分)。

/** 分を PLACEMENT_STEP_MIN 単位に切り上げ。 */
function ceilToStep(min: number): number {
  return Math.ceil(min / PLACEMENT_STEP_MIN) * PLACEMENT_STEP_MIN;
}

/** 開始時刻を [gapStart, gapEnd - duration] にクランプ + 刻みに丸める (案3)。 */
function clampStart(min: number, gapStart: number, gapEnd: number, durationMin: number): number {
  const lo = gapStart;
  const hi = Math.max(gapStart, gapEnd - durationMin);
  const stepped = Math.round(min / PLACEMENT_STEP_MIN) * PLACEMENT_STEP_MIN;
  return Math.min(hi, Math.max(lo, stepped));
}

export function PlacementSheet({
  ctx,
  onClose,
  onToast,
}: {
  ctx: SlotPlacementContext;
  onClose: () => void;
  onToast: (msg: string) => void;
}) {
  // 新規(0) / 既存(1) トグル。
  const [seg, setSeg] = useState(0);
  const isExisting = seg === 1;

  // 所要 (分)。サービス時間と連動。
  const [durationMin, setDurationMin] = useState(DEFAULT_SERVICE_MINUTES);

  // 開始時刻 (0 時起点の分)。初期値は枠頭 (案1)、travel-estimate 成功で案2 に上書き。
  const initialStart = clampStart(ctx.gapStartMin, ctx.gapStartMin, ctx.gapEndMin, durationMin);
  const [startMin, setStartMin] = useState(initialStart);
  // ユーザーが手動でステッパーを触ったら案2 の自動上書きを止める。
  // state ではなく ref で保持する: travel-estimate の onSuccess は effect 実行時の
  // クロージャに値をキャプチャするため、in-flight 中 (デバウンス + mutation 飛行中) に
  // 手動操作が入っても ref なら解決時点の最新値を読めてステイルクロージャ競合を防げる。
  // UI 描画には使わないので state は不要。
  const startTouchedRef = useRef(false);
  const markStartTouched = () => {
    startTouchedRef.current = true;
  };

  // 新規カルテ項目。
  const [karteName, setKarteName] = useState('');
  const [karteCode, setKarteCode] = useState('');
  const [karteKana, setKarteKana] = useState('');
  const [karteSex, setKarteSex] = useState<'' | (typeof SEX_OPTIONS)[number]>('');
  const [karteInsurance, setKarteInsurance] = useState<'' | (typeof INSURANCE_OPTIONS)[number]>('');
  const [addr, setAddr] = useState('');
  const [sexRestriction, setSexRestriction] = useState<
    '' | (typeof SEX_RESTRICTION_OPTIONS)[number]
  >('');
  const [requiresMultipleStaff, setRequiresMultipleStaff] = useState(false);

  // 既存患者の検索・紐付け。
  const [patientQuery, setPatientQuery] = useState('');
  const [linkedPatient, setLinkedPatient] = useState<PatientRead | null>(null);
  const [linkedLat, setLinkedLat] = useState<number | null>(null);
  const [linkedLng, setLinkedLng] = useState<number | null>(null);

  // 強行理由 (赤警告がある場合に必須) / 黄警告の承知チェック。
  const [overrideReason, setOverrideReason] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);

  // 直前訪問 → 配置先の移動 + バッファー所要 (案2 の根拠)。
  const [travelTotalMin, setTravelTotalMin] = useState<number | null>(null);

  const trimmedQuery = patientQuery.trim();
  const patientsQuery = usePatients({ search: trimmedQuery, limit: 8, enabled: isExisting });
  const candidates = isExisting && trimmedQuery ? (patientsQuery.data?.items ?? []) : [];

  const travelMut = useTravelEstimate();
  const createPendingMut = useCreatePendingRequest();

  // ── 案2: travel-estimate を呼び、prevVisit.end + total_minutes を5分切上げ → 初期開始時刻。
  // 新規は to_address、既存は紐付け患者の lat/lng を to に渡す。from は直前 visit の patient_id。
  // ユーザーが未だ開始時刻を触っていない場合のみ自動で開始時刻を上書きする (案3 の手動優先)。
  const toAddress = isExisting ? (linkedPatient?.address ?? null) : addr.trim() || null;
  const toLat = isExisting ? linkedLat : null;
  const toLng = isExisting ? linkedLng : null;
  const fromPatientId = ctx.prevVisit?.patientId ?? null;

  useEffect(() => {
    if (!ctx.prevVisit) {
      setTravelTotalMin(null);
      return;
    }
    // to 座標 / 住所が無いと推定できない (新規で住所未入力など) → 案1 のまま。
    // 座標があれば即時、住所のみ (新規入力中) は最小文字数ゲート (>=5) で
    // 途中入力での無駄打ちを抑える (拠点解決と同じ閾値)。
    const haveCoords = toLat !== null && toLng !== null;
    const haveAddress = toAddress !== null && toAddress.trim().length >= 5;
    if (!haveCoords && !haveAddress) {
      setTravelTotalMin(null);
      return;
    }
    // 住所キー入力毎の発火 → ネットワーク連発 + 開始時刻上書き連発を防ぐため
    // 500ms デバウンス。座標確定 (既存患者紐付け) でも同経路で問題ない。
    // 手動調整ロックは onSuccess 内で startTouchedRef.current を見て判定
    // (in-flight 中の手動操作でもステイルクロージャにならない)。
    const timer = setTimeout(() => {
      travelMut.mutate(
        {
          from_patient_id: fromPatientId,
          from_lat: null,
          from_lng: null,
          to_address: toAddress,
          to_lat: toLat,
          to_lng: toLng,
        },
        {
          onSuccess: (res) => {
            setTravelTotalMin(res.total_minutes);
            if (!startTouchedRef.current && res.total_minutes !== null) {
              const prevEnd = parseHM(ctx.prevVisit!.endTime);
              if (prevEnd !== null) {
                const proposed = ceilToStep(prevEnd + res.total_minutes);
                setStartMin(clampStart(proposed, ctx.gapStartMin, ctx.gapEndMin, durationMin));
              }
            }
          },
          onError: () => setTravelTotalMin(null),
        },
      );
    }, 500);
    return () => clearTimeout(timer);
    // 住所 / 紐付け患者 / 直前 visit が変わったら再見積。startTouched/duration は依存に含めない
    // (手動調整の度に再計算しないため)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toAddress, toLat, toLng, fromPatientId]);

  // 所要が変わったら開始時刻のクランプを取り直す。
  useEffect(() => {
    setStartMin((prev) => clampStart(prev, ctx.gapStartMin, ctx.gapEndMin, durationMin));
  }, [durationMin, ctx.gapStartMin, ctx.gapEndMin]);

  const linkPatient = (p: PatientRead) => {
    setLinkedPatient(p);
    if (p.address) setAddr(p.address);
    setLinkedLat(typeof p.lat === 'number' ? p.lat : null);
    setLinkedLng(typeof p.lng === 'number' ? p.lng : null);
    setSexRestriction(
      (normalizePatientSexRestriction(p.sex_restriction as string | null | undefined) ?? '') as
        | ''
        | (typeof SEX_RESTRICTION_OPTIONS)[number],
    );
    setRequiresMultipleStaff(
      (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true,
    );
    const wp = coerceWeeklyPattern(p.weekly_pattern);
    if (wp.service_minutes) setDurationMin(wp.service_minutes);
    setPatientQuery('');
  };
  const unlinkPatient = () => {
    setLinkedPatient(null);
    setLinkedLat(null);
    setLinkedLng(null);
  };

  const startTime = fmtHM(startMin);
  const warnings = computePlacementWarnings({
    ctx,
    startTime,
    durationMin,
    travelTotalMin,
    sexRestriction,
    requiresMultipleStaff,
  });
  const reds = warnings.filter((w) => w.level === 'red');
  const yellows = warnings.filter((w) => w.level === 'yellow');
  const hasRed = reds.length > 0;
  const hasYellow = yellows.length > 0;

  // 送信可否: 必須入力 + 赤は理由必須 + 黄は承知チェック。
  const reasonOk = !hasRed || overrideReason.trim().length > 0;
  const ackOk = !hasYellow || acknowledged;
  const baseInputOk = isExisting
    ? linkedPatient !== null
    : karteName.trim().length > 0 && karteCode.trim().length > 0;
  const canSubmit = baseInputOk && reasonOk && ackOk && !createPendingMut.isPending;

  const submit = () => {
    if (!baseInputOk) {
      onToast(isExisting ? 'お客様を選んでください' : '氏名・患者コードを入力してください');
      return;
    }
    if (!reasonOk) {
      onToast('警告を承知して強行する理由を入力してください');
      return;
    }
    if (!ackOk) {
      onToast('注意事項を承知のうえチェックしてください');
      return;
    }
    const placement = buildPlacementMeta(ctx, startTime, durationMin);
    const extras: PlacementExtras = {
      placement,
      warnings,
      overrideReason: hasRed ? overrideReason.trim() : null,
    };
    const proposedVisit: ProposedVisit = {
      weekday: ctx.weekday,
      start_time: startTime,
      duration_min: durationMin,
      course_code: ctx.courseCode,
    };

    if (isExisting && linkedPatient) {
      const payload = buildPatientVisitAddPayload(
        linkedPatient.id,
        linkedPatient.name,
        proposedVisit,
        extras,
      );
      createPendingMut.mutate(
        { request_type: 'patient_visit_add', payload },
        {
          onSuccess: () => {
            onToast('✓ 訪問追加を申請しました（承認待ち）');
            onClose();
          },
          onError: () => onToast('申請に失敗しました'),
        },
      );
      return;
    }

    // 新規患者: patient_create + placement。proposed_visits は 1 枠のみ。
    const karte: KarteInput = {
      name: karteName.trim(),
      code: karteCode.trim(),
      kana: karteKana.trim(),
      sex: karteSex,
      insurance: karteInsurance,
      address: addr.trim(),
      lat: null,
      lng: null,
      sex_restriction: sexRestriction,
      requires_multiple_staff: requiresMultipleStaff,
      primary_office_id: ctx.officeId,
    };
    const schedule: DesiredSchedule = {
      frequency_per_week: 1,
      visit_frequency: 'every',
      preferred_weekdays: [WEEKDAY_INT_TO_KEY[ctx.weekday] ?? 'Mon'],
      service_minutes: durationMin,
      time_type: '固定',
      preferred_start: startTime,
      preferred_end: null,
    };
    const payload = buildPatientCreatePlacementPayload(karte, schedule, [proposedVisit], extras);
    createPendingMut.mutate(
      { request_type: 'patient_create', payload },
      {
        onSuccess: () => {
          onToast('✓ 新規配置を申請しました（承認待ち）');
          onClose();
        },
        onError: () => onToast('申請に失敗しました'),
      },
    );
  };

  const weekdayJa = WEEKDAY_INT_TO_DOW[ctx.weekday] ?? '';
  const gapLabel = `${fmtHM(ctx.gapStartMin)}〜${fmtHM(ctx.gapEndMin)}`;
  const canStepDown = startMin > ctx.gapStartMin;
  const canStepUp = startMin < ctx.gapEndMin - durationMin;

  return (
    <SlideSheet>
      <Grip />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '4px 20px 10px',
        }}
      >
        <h3
          style={{
            fontFamily: 'var(--font-serif)',
            fontSize: 18,
            fontWeight: 700,
            color: _TERRAD,
            margin: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 7,
          }}
        >
          <MapPin size={17} />
          この枠に入れる
        </h3>
        <button
          onClick={onClose}
          aria-label="閉じる"
          style={{
            width: 34,
            height: 34,
            borderRadius: '50%',
            background: '#F1ECE3',
            color: _INK2,
            display: 'grid',
            placeItems: 'center',
          }}
        >
          <X size={16} />
        </button>
      </div>

      <div className="cf-scroll" style={{ overflowY: 'auto', padding: '2px 20px 26px', flex: 1 }}>
        {/* 枠ヘッダ: 拠点 / コース / 曜日 / gap。 */}
        <div
          style={{
            background: 'linear-gradient(180deg,#FFF7EC,#FDF0DC)',
            border: `2px solid ${_TERRA}`,
            borderRadius: 14,
            padding: '11px 13px',
            marginBottom: 14,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              fontWeight: 700,
              color: _INK2,
            }}
          >
            <MapPin size={12} />
            {ctx.officeName}
            <span style={{ color: _INK3 }}>/</span>
            <span style={{ color: _TERRAD }}>{ctx.courseLabel}</span>
            <span style={{ color: _INK3 }}>/</span>
            {weekdayJa}曜
          </div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 13,
              fontWeight: 700,
              color: _INK,
              marginTop: 4,
            }}
          >
            空き {gapLabel}
          </div>
        </div>

        {/* 新規 / 既存 トグル。 */}
        <div
          style={{
            display: 'flex',
            gap: 6,
            background: '#F4EFE7',
            padding: 4,
            borderRadius: 13,
            marginBottom: 14,
          }}
        >
          {['新規のお客様', '既存のお客様'].map((s, i) => (
            <button
              key={s}
              onClick={() => setSeg(i)}
              style={{
                flex: 1,
                padding: 10,
                borderRadius: 10,
                fontFamily: 'var(--font-serif)',
                fontSize: 13.5,
                fontWeight: 700,
                background: seg === i ? '#fff' : 'transparent',
                color: seg === i ? _TERRAD : _INK3,
                boxShadow: seg === i ? '0 2px 6px rgba(0,0,0,0.07)' : 'none',
              }}
            >
              {s}
            </button>
          ))}
        </div>

        {isExisting ? (
          <PatientLinkPanel
            linked={linkedPatient}
            query={patientQuery}
            onQueryChange={setPatientQuery}
            candidates={candidates}
            isFetching={patientsQuery.isFetching}
            isError={patientsQuery.isError}
            onSelect={linkPatient}
            onUnlink={unlinkPatient}
          />
        ) : (
          <>
            <KarteFormSection
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
            <Field label="ご住所">
              <input
                value={addr}
                onChange={(e) => setAddr(e.target.value)}
                placeholder="例: 千葉市稲毛区小仲台6-2-1"
                aria-label="ご住所"
                style={cfInput}
              />
              <div style={{ fontSize: 10.5, color: _INK2, marginTop: 5, lineHeight: 1.5 }}>
                ※ 住所から直前訪問との移動時間を見積もり、開始時刻の目安にします。
              </div>
            </Field>
            <Field label="性別制限">
              <select
                style={cfInput}
                value={sexRestriction}
                aria-label="性別制限"
                onChange={(e) =>
                  setSexRestriction(e.target.value as '' | (typeof SEX_RESTRICTION_OPTIONS)[number])
                }
              >
                <option value="">なし</option>
                {SEX_RESTRICTION_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {SEX_RESTRICTION_LABEL[v]}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}

        {/* サービス時間 (所要)。 */}
        <Field label="サービス時間（所要）">
          <select
            style={cfInput}
            value={durationMin}
            aria-label="サービス時間"
            onChange={(e) => setDurationMin(Number(e.target.value))}
          >
            {SERVICE_MINUTES_OPTIONS.map((m) => (
              <option key={m} value={m}>
                {m}分
              </option>
            ))}
          </select>
        </Field>

        {/* 開始時刻 5分ステッパー + タイムライン。 */}
        <Field label="開始時刻">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              border: `2px solid ${_LINE}`,
              borderRadius: 12,
              background: '#FFFDF9',
              padding: 6,
              minHeight: 50,
            }}
          >
            <button
              type="button"
              aria-label="開始を早める"
              onClick={() => {
                markStartTouched();
                setStartMin((m) =>
                  clampStart(m - PLACEMENT_STEP_MIN, ctx.gapStartMin, ctx.gapEndMin, durationMin),
                );
              }}
              disabled={!canStepDown}
              style={{ ...stepBtn, opacity: canStepDown ? 1 : 0.4 }}
            >
              −
            </button>
            <span
              style={{
                flex: 1,
                textAlign: 'center',
                fontFamily: 'var(--font-mono)',
                fontSize: 20,
                fontWeight: 700,
                color: _INK,
                letterSpacing: '-0.02em',
              }}
            >
              <Clock size={15} style={{ verticalAlign: -2, marginRight: 6, color: _TERRAD }} />
              {startTime}
              <span style={{ fontSize: 12, color: _INK2, marginLeft: 6 }}>
                〜{fmtHM(startMin + durationMin)}
              </span>
            </span>
            <button
              type="button"
              aria-label="開始を遅らせる"
              onClick={() => {
                markStartTouched();
                setStartMin((m) =>
                  clampStart(m + PLACEMENT_STEP_MIN, ctx.gapStartMin, ctx.gapEndMin, durationMin),
                );
              }}
              disabled={!canStepUp}
              style={{ ...stepBtn, opacity: canStepUp ? 1 : 0.4 }}
            >
              ＋
            </button>
          </div>
          <PlacementTimeline
            gapStart={ctx.gapStartMin}
            gapEnd={ctx.gapEndMin}
            start={startMin}
            durationMin={durationMin}
          />
          {ctx.prevVisit && (
            <div style={{ fontSize: 10.5, color: _INK2, marginTop: 6 }}>
              直前: {ctx.prevVisit.patientName}（〜{ctx.prevVisit.endTime}）
              {travelMut.isPending
                ? ' ・ 移動時間を計算中…'
                : travelTotalMin !== null
                  ? ` ・ 移動+待機 約${travelTotalMin}分`
                  : ''}
            </div>
          )}
          {ctx.nextVisit && (
            <div style={{ fontSize: 10.5, color: _INK2, marginTop: 2 }}>
              直後: {ctx.nextVisit.patientName}（{ctx.nextVisit.startTime}〜）
            </div>
          )}
        </Field>

        {/* 複数スタッフ。 */}
        <Field label="複数スタッフ">
          <button
            onClick={() => setRequiresMultipleStaff((v) => !v)}
            aria-pressed={requiresMultipleStaff}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '11px 13px',
              minHeight: 46,
              borderRadius: 12,
              border: `2px solid ${requiresMultipleStaff ? _TERRA : _LINE}`,
              background: requiresMultipleStaff ? '#FCEBD6' : '#FFFDF9',
              textAlign: 'left',
            }}
          >
            <span
              style={{
                width: 22,
                height: 22,
                flex: '0 0 auto',
                borderRadius: 7,
                display: 'grid',
                placeItems: 'center',
                background: requiresMultipleStaff ? _TERRA : '#fff',
                border: `2px solid ${requiresMultipleStaff ? _TERRA : _INK3}`,
                color: '#fff',
                fontSize: 13,
                fontWeight: 700,
                lineHeight: 1,
              }}
            >
              {requiresMultipleStaff ? '✓' : ''}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 13.5,
                fontWeight: 700,
                color: requiresMultipleStaff ? _TERRAD : _INK2,
              }}
            >
              2名以上での訪問が必要
            </span>
          </button>
        </Field>

        {/* 警告帯。 */}
        {hasYellow && (
          <div style={{ marginBottom: 12 }}>
            {yellows.map((w) => (
              <PlacementWarnRow key={w.code} w={w} />
            ))}
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                marginTop: 8,
                padding: '10px 12px',
                borderRadius: 12,
                border: `2px solid ${acknowledged ? '#C99700' : '#F0DFA8'}`,
                background: acknowledged ? '#FFF6D6' : '#FFFCF0',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={acknowledged}
                aria-label="注意事項を承知"
                onChange={(e) => setAcknowledged(e.target.checked)}
                style={{ width: 18, height: 18, accentColor: '#C99700' }}
              />
              <span style={{ fontSize: 12.5, fontWeight: 700, color: '#8A6D00' }}>
                上記の注意事項を承知のうえ配置します
              </span>
            </label>
          </div>
        )}
        {hasRed && (
          <div
            style={{
              marginBottom: 12,
              border: '2px solid #E1657F',
              borderRadius: 12,
              padding: '11px 12px',
              background: '#FCEFF2',
            }}
          >
            {reds.map((w) => (
              <PlacementWarnRow key={w.code} w={w} />
            ))}
            <div style={{ marginTop: 8 }}>
              <textarea
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
                placeholder="強行する理由を入力（必須）"
                aria-label="強行理由"
                rows={2}
                style={{
                  width: '100%',
                  border: '2px solid #F4D4DC',
                  borderRadius: 10,
                  padding: '9px 11px',
                  fontFamily: 'var(--font-sans)',
                  fontSize: 13,
                  fontWeight: 600,
                  color: _INK,
                  background: '#fff',
                  outline: 'none',
                  resize: 'vertical',
                }}
              />
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          style={{
            width: '100%',
            marginTop: 4,
            padding: 14,
            minHeight: 52,
            borderRadius: 14,
            fontFamily: 'var(--font-serif)',
            fontSize: 15,
            fontWeight: 700,
            color: '#fff',
            background: !canSubmit
              ? '#CFC8BC'
              : hasRed
                ? 'linear-gradient(135deg,#E1657F,#C04A64)'
                : `linear-gradient(135deg, ${_MINT}, ${_MINTD})`,
            boxShadow: canSubmit ? '0 6px 16px rgba(14,159,110,0.24)' : 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
          }}
        >
          <ClipboardCheck size={16} />
          {createPendingMut.isPending
            ? '申請中…'
            : hasRed
              ? '警告を承知して配置を申請'
              : 'この枠に配置を申請（承認へ）'}
        </button>
        <div style={{ height: 12 }} />
      </div>
    </SlideSheet>
  );
}

/** 配置警告 1 行 (赤/黄 共通の行表示)。 */
function PlacementWarnRow({ w }: { w: PlacementWarning }) {
  const red = w.level === 'red';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 7,
        fontSize: 12,
        fontWeight: 700,
        color: red ? '#C04A64' : '#8A6D00',
        lineHeight: 1.45,
        marginBottom: 4,
      }}
    >
      <span style={{ flex: '0 0 auto', paddingTop: 1 }}>
        <AlertTriangle size={13} />
      </span>
      <span>{w.message}</span>
    </div>
  );
}

/** gap 帯の中に配置 visit を帯表示する簡易タイムライン。 */
function PlacementTimeline({
  gapStart,
  gapEnd,
  start,
  durationMin,
}: {
  gapStart: number;
  gapEnd: number;
  start: number;
  durationMin: number;
}) {
  const span = Math.max(1, gapEnd - gapStart);
  const left = ((Math.max(gapStart, start) - gapStart) / span) * 100;
  const width = ((Math.min(gapEnd, start + durationMin) - Math.max(gapStart, start)) / span) * 100;
  return (
    <div
      style={{
        position: 'relative',
        height: 26,
        marginTop: 8,
        borderRadius: 8,
        background:
          'repeating-linear-gradient(45deg, #FFFBF4, #FFFBF4 7px, #FDF1DF 7px, #FDF1DF 14px)',
        border: `1.5px solid ${_LINE}`,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 3,
          bottom: 3,
          left: `${left}%`,
          width: `${Math.max(2, width)}%`,
          background: `linear-gradient(135deg, ${_MINT}, ${_MINTD})`,
          borderRadius: 6,
          boxShadow: '0 1px 3px rgba(14,159,110,0.3)',
        }}
      />
    </div>
  );
}

const stepBtn: CSSProperties = {
  width: 44,
  height: 44,
  flex: '0 0 auto',
  borderRadius: 11,
  background: '#F4EFE7',
  color: _TERRAD,
  fontFamily: 'var(--font-serif)',
  fontSize: 24,
  fontWeight: 700,
  lineHeight: 1,
  display: 'grid',
  placeItems: 'center',
};

// weekday int → WeekdayKey (新規 patient_create の preferred_weekdays 用)。
const WEEKDAY_INT_TO_KEY: Record<number, WeekdayKey> = (() => {
  const out: Record<number, WeekdayKey> = {};
  for (const code of WEEKDAY_KEYS) {
    out[WEEKDAY_CODE_TO_INT[code as WeekdayCode]] = code;
  }
  return out;
})();

// ---- shared bits ----

function SlideSheet({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 50,
        maxHeight: '86%',
        background: PANEL_W,
        borderRadius: '22px 22px 0 0',
        boxShadow: '0 -12px 40px rgba(28,25,23,0.22)',
        display: 'flex',
        flexDirection: 'column',
        transform: 'translateY(0)',
        animation: 'cfSheetUp .28s cubic-bezier(0.22, 1, 0.36, 1)',
      }}
    >
      {children}
    </div>
  );
}

const Grip = () => (
  <div
    style={{
      width: 46,
      height: 5,
      background: _LINE,
      borderRadius: 999,
      margin: '10px auto 4px',
      flex: '0 0 auto',
    }}
  />
);

function Field({ label, children, flex }: { label: string; children: ReactNode; flex?: boolean }) {
  return (
    <div style={{ marginBottom: 13, flex: flex ? 1 : undefined }}>
      <label
        style={{
          display: 'block',
          fontFamily: 'var(--font-serif)',
          fontSize: 12.5,
          fontWeight: 700,
          marginBottom: 6,
        }}
      >
        {label}
      </label>
      {children}
    </div>
  );
}

const cfInput: CSSProperties = {
  width: '100%',
  padding: '12px 13px',
  minHeight: 46,
  border: `2px solid ${_LINE}`,
  borderRadius: 12,
  fontFamily: 'var(--font-sans)',
  fontSize: 14.5,
  fontWeight: 600,
  color: _INK,
  background: '#FFFDF9',
  outline: 'none',
};
const chip: CSSProperties = {
  padding: '9px 15px',
  minHeight: 42,
  borderRadius: 999,
  background: '#F4EFE7',
  color: _INK3,
  fontFamily: 'var(--font-serif)',
  fontSize: 13.5,
  fontWeight: 700,
  border: '2px solid transparent',
};
const chipOn: CSSProperties = { background: '#FCEBD6', color: _TERRAD, borderColor: _TERRA };

/**
 * 数値ステッパー — `frequency_per_week` (1〜7) をタップしやすい ± ボタンで。
 */
function Stepper({
  value,
  min,
  max,
  suffix,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  suffix?: string;
  onChange: (next: number) => void;
}) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n));
  const btn: CSSProperties = {
    width: 38,
    height: 38,
    flex: '0 0 auto',
    borderRadius: 10,
    background: '#F4EFE7',
    color: _TERRAD,
    fontFamily: 'var(--font-serif)',
    fontSize: 20,
    fontWeight: 700,
    lineHeight: 1,
    display: 'grid',
    placeItems: 'center',
  };
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        border: `2px solid ${_LINE}`,
        borderRadius: 12,
        background: '#FFFDF9',
        padding: 4,
        minHeight: 46,
      }}
    >
      <button
        type="button"
        aria-label="減らす"
        onClick={() => onChange(clamp(value - 1))}
        disabled={value <= min}
        style={{ ...btn, opacity: value <= min ? 0.4 : 1 }}
      >
        −
      </button>
      <span
        style={{
          flex: 1,
          textAlign: 'center',
          fontFamily: 'var(--font-serif)',
          fontSize: 15,
          fontWeight: 700,
          color: _INK,
        }}
      >
        {value}
        {suffix ? (
          <span style={{ fontSize: 11, color: _INK2, marginLeft: 4 }}>{suffix}</span>
        ) : null}
      </span>
      <button
        type="button"
        aria-label="増やす"
        onClick={() => onChange(clamp(value + 1))}
        disabled={value >= max}
        style={{ ...btn, opacity: value >= max ? 0.4 : 1 }}
      >
        ＋
      </button>
    </div>
  );
}

export function Toast({ msg }: { msg: string }) {
  return (
    <div
      role="status"
      style={{
        position: 'absolute',
        left: '50%',
        bottom: 28,
        transform: 'translateX(-50%)',
        zIndex: 90,
        background: _INK,
        color: '#fff',
        padding: '12px 20px',
        borderRadius: 999,
        fontFamily: 'var(--font-serif)',
        fontSize: 14,
        fontWeight: 700,
        boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
        animation: 'cfToast .3s ease',
        whiteSpace: 'nowrap',
      }}
    >
      {msg}
    </div>
  );
}
