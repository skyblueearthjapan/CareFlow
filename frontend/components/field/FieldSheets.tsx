'use client';

/**
 * CareFlow Mobile — Karte ボトムシート + 提案シート (Warm & Human, Phase2-3c 実データ)
 *
 * - KarteSheet  : board の visit (patient_id) から `GET /api/v1/patients/{id}` を
 *                 取得し、基本情報 / 保険・サービス / 希望曜日 / 備考 を表示する。
 *                 同住所相手は board の same_address_group から解決する。
 * - SuggestSheet: フォーム値 + 現在の週/拠点で `POST .../propose-slots` を実呼び出し
 *                 し、返ってきた slots をランキング + ミニスケジュール (実時刻) で
 *                 レンダする。0 件 / warnings も表示する。
 *
 * Phase 1 のモック (RANK_PAIR / RANK_NORMAL / CF_PATIENTS) は撤去済み。
 */

import { useState, type CSSProperties, type ReactNode } from 'react';
import { X, MapPin, User, Heart, Calendar, Bell, Sparkles, AlertTriangle } from 'lucide-react';

import {
  SERVICE_MINUTES_OPTIONS,
  DEFAULT_SERVICE_MINUTES,
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  SEX_RESTRICTION_OPTIONS,
  SEX_RESTRICTION_LABEL,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  INSURANCE_LABEL,
  SEX_LABEL,
  coerceWeeklyPattern,
  normalizePatientSex,
  normalizePatientInsurance,
  type WeekdayKey,
} from '@/lib/schemas/patient';
import { usePatient } from '@/lib/queries/patients';
import {
  useProposeSlots,
  WEEKDAY_CODE_TO_INT,
  proposeWarningLabel,
} from '@/lib/queries/fieldBoard';
import type { BoardVisit, WeekdayCode } from '@/lib/schemas/v2/board';
import type {
  ProposeSlotItem,
  ProposeMiniScheduleEntry,
  ProposeTimeType,
} from '@/lib/schemas/v2/propose_slots';

import type { SameAddressGroups } from './FieldBoard';

const _TERRA = '#D97706',
  _TERRAD = '#B45309',
  _PLUM = '#8B5C9E',
  _MINT = '#0E9F6E',
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
  sameAddressGroups,
  onClose,
  onOpenVisit,
}: {
  visit: BoardVisit;
  officeName: string;
  sameAddressGroups: SameAddressGroups;
  onClose: () => void;
  onOpenVisit: (v: BoardVisit) => void;
}) {
  // 患者詳細を実 API から取得 (基本情報 / 希望曜日 / 備考 等)。
  const patientQuery = usePatient(visit.patient_id);
  const p = patientQuery.data;

  // 同住所相手: board の same_address_group から、自分以外の visit を引く。
  const gid = visit.same_address_group_id;
  const mates = gid
    ? (sameAddressGroups.byGroup.get(gid) ?? []).filter((m) => m.visit_id !== visit.visit_id)
    : [];

  // 表示値: 患者詳細が来るまでは board visit の値でフォールバック。
  const wp = p ? coerceWeeklyPattern(p.weekly_pattern) : null;
  const sex = p ? normalizePatientSex(p.sex as string | null | undefined) : null;
  const insurance =
    (p ? normalizePatientInsurance(p.insurance as string | null | undefined) : null) ??
    (visit.insurance === 'med' ? 'medical' : visit.insurance === 'care' ? 'care' : null);
  const address = p?.address ?? visit.address ?? null;
  const serviceMin = wp?.service_minutes ?? visit.service_minutes;
  const note = p?.note ?? null;

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
        </KSec>

        <KSec title="保険・サービス" icon={<Heart size={12} />}>
          {insurance && <KV k="保険種別" v={INSURANCE_LABEL[insurance]} />}
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
              {WEEKDAY_KEYS.slice(0, 6).map((d) => {
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
  officeId,
  onClose,
  onToast,
}: {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  onClose: () => void;
  onToast: (msg: string) => void;
}) {
  const [seg, setSeg] = useState(0);
  const [addr, setAddr] = useState('');

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

  const proposeMut = useProposeSlots();

  // 時間タイプが「固定」「時間帯」のときのみ時刻欄を表示。
  const showTimeRange = timeType === '固定' || timeType === '時間帯';
  const showEnd = timeType === '時間帯';

  const runSuggest = () => {
    if (!addr.trim()) {
      onToast('住所を入力してください');
      return;
    }
    const preferred_weekdays: WeekdayCode[] = WEEKDAY_KEYS.filter(
      (d) => weekdays[d],
    ) as WeekdayCode[];
    proposeMut.mutate(
      {
        address: addr.trim(),
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
        limit: 10,
      },
      {
        onError: () => onToast('提案の取得に失敗しました'),
      },
    );
  };

  const result = proposeMut.data;
  const slots = result?.slots ?? [];

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
                </h4>
                {slots.map((s, i) => (
                  <RankCard
                    key={`${s.office_id}-${s.weekday}-${s.course_code}-${i}`}
                    s={s}
                    rank={i + 1}
                  />
                ))}
              </>
            )}
          </div>
        )}
        <div style={{ height: 12 }} />
      </div>
    </SlideSheet>
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

function RankCard({ s, rank }: { s: ProposeSlotItem; rank: number }) {
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
        background: '#fff',
        border: `2px solid ${rank === 1 ? '#E5B53A' : _LINE}`,
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
    </div>
  );
}

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
