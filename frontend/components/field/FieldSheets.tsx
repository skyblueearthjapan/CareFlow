'use client';

/**
 * CareFlow Mobile — Karte ボトムシート + 提案シート (Warm & Human)
 *
 * `mocks/design_bundle/carelink/project/careflow-sheets.jsx` を TypeScript へ
 * ピクセル忠実に移植。KarteSheet / SuggestSheet / RankCard / MiniSlot /
 * SlideSheet / Toast。ランキングデータは `./mockData` (RANK_PAIR / RANK_NORMAL)。
 */

import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { X, MapPin, User, Heart, Calendar, Bell, Sparkles } from 'lucide-react';

import {
  TIME_TYPE_OPTIONS,
  VISIT_FREQUENCY_OPTIONS,
  VISIT_FREQUENCY_LABELS,
  SEX_RESTRICTION_OPTIONS,
  SEX_RESTRICTION_LABEL,
  WEEKDAY_KEYS,
  WEEKDAY_LABELS_JA,
  type WeekdayKey,
} from '@/lib/schemas/patient';

import {
  CF_PATIENTS,
  CF_DOWS,
  RANK_PAIR,
  RANK_NORMAL,
  getPatient,
  type OfficeKey,
  type RankCandidate,
  type RankScheduleRow,
} from './mockData';

const _TERRA = '#D97706',
  _TERRAD = '#B45309',
  _PLUM = '#8B5C9E',
  _MINT = '#0E9F6E',
  _INK = '#1C1917',
  _INK2 = '#57534E',
  _INK3 = '#A8A29E',
  _LINE = '#EAE3D8';
const PANEL_W = '#FFFFFF';

// 同住所ペアの相手を探す (新規候補 newp/newpair は除外)
function cfMate(pk: string): string | null {
  const me = getPatient(pk);
  for (const k in CF_PATIENTS) {
    if (k === pk || k === 'newp' || k === 'newpair') continue;
    const other = CF_PATIENTS[k];
    if (other && other.addr === me.addr) return k;
  }
  return null;
}

// ============================ Karte bottom sheet ============================

export function KarteSheet({
  pk,
  office,
  onClose,
  onOpen,
}: {
  pk: string;
  office: OfficeKey;
  onClose: () => void;
  onOpen: (pk: string) => void;
}) {
  const p = getPatient(pk);
  const mateKey = cfMate(pk);
  const mate = mateKey ? getPatient(mateKey) : null;
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
            {p.name.charAt(0)}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 19, fontWeight: 700 }}>
              {p.name}
            </div>
            <div style={{ fontSize: 11, opacity: 0.9 }}>{p.kana}</div>
            <div style={{ fontSize: 10.5, opacity: 0.8, marginTop: 2 }}>患者コード: {p.code}</div>
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
        {mate && mateKey && (
          <KSec title="同住所ペア" icon={<MapPin size={12} />}>
            <button
              onClick={() => onOpen(mateKey)}
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
                }}
              >
                {mate.name.charAt(0)}
              </div>
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    fontFamily: 'var(--font-serif)',
                    fontSize: 14.5,
                    fontWeight: 700,
                    color: '#7A4E91',
                  }}
                >
                  相手: {mate.name} 様 ›
                </div>
                <div style={{ fontSize: 10.5, color: _INK2, marginTop: 2 }}>
                  同住所・同時刻に <b>1スタッフが連続訪問</b>（約90分・2枠消費）
                </div>
              </div>
            </button>
          </KSec>
        )}
        <KSec title="基本情報" icon={<User size={12} />}>
          <KV k="性別 / 年齢" v={`${p.sex} / ${p.age}歳`} />
          <KV
            k="ご住所"
            v={
              <span>
                {p.addr}
                {mate && <span style={pBadge}>同住所</span>}
              </span>
            }
          />
          <KV k="エリア" v={p.area} />
        </KSec>
        <KSec title="保険・サービス" icon={<Heart size={12} />}>
          <KV k="保険種別" v={p.ins === 'med' ? '医療保険' : '介護保険'} />
          <KV k="サービス時間" v={`${p.svc}分`} />
          <KV k="時間タイプ" v={p.type} />
        </KSec>
        <KSec title="希望曜日" icon={<Calendar size={12} />}>
          <div style={{ display: 'flex', gap: 5 }}>
            {CF_DOWS.slice(0, 6).map((d) => {
              const yes = p.dow[d] === 1;
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
                    {d}
                  </span>
                  {yes ? '○' : '×'}
                </div>
              );
            })}
          </div>
        </KSec>
        <KSec title="固定訪問 / 拠点" icon={<MapPin size={12} />}>
          <KV k="固定コース" v={p.fixed} />
          <KV k="担当拠点" v={office === 'INAGE' ? '稲毛' : '都賀'} />
        </KSec>
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
            }}
          >
            {p.note}
          </div>
        </KSec>
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
 * サービス時間プリセット — 患者マスタ `WeeklyPatternEditor` の
 * `SERVICE_MINUTES_PRESETS` と一致 (15/30/45/60)。
 */
const SUGGEST_SERVICE_PRESETS = [15, 30, 45, 60] as const;

/**
 * 希望開始/終了時刻のプルダウン候補。
 * 患者マスタは `<input type="time">` (自由 HH:MM) だが、現場ボードの提案は
 * モバイル探索用途のため 30 分刻み 06:00〜20:00 の選択式で簡略化する
 * (タスク許容: 「難しければ 30 分刻み 06:00-20:00 のプルダウンで可」)。
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

/** 時間帯では開始/終了の両方、固定では開始のみ実質有効。 */
type SuggestOfficeResult = {
  office: OfficeKey;
  name: string;
  confidence: 'high' | 'low' | 'none';
} | null;

/**
 * 住所 → 主担当拠点の簡易自動判定 (Phase 1 モック)。
 *
 * 患者マスタの `/offices/resolve` (useResolveOffice) と同 UX
 * (住所入力 → デバウンス → 拠点名 + confidence 表示) を、認証 / QueryClient に
 * 結合せずに再現する。判定ロジックは現場モックの区 → 拠点対応:
 *   稲毛区 / 花見川区  → 稲毛
 *   若葉区            → 都賀
 * それ以外 / 空        → confidence='none' (手動選択を促す)
 */
function resolveOfficeFromAddress(addr: string): SuggestOfficeResult {
  const a = addr.trim();
  if (!a) return null;
  if (a.includes('若葉区') || a.includes('都賀')) {
    return { office: 'TSUGA', name: '都賀', confidence: 'high' };
  }
  if (a.includes('稲毛区') || a.includes('花見川区')) {
    return { office: 'INAGE', name: '稲毛', confidence: 'high' };
  }
  if (a.includes('千葉市')) {
    // 市内だが区が特定できない → 稲毛を低信頼で仮置き。
    return { office: 'INAGE', name: '稲毛', confidence: 'low' };
  }
  return { office: 'INAGE', name: '稲毛', confidence: 'none' };
}

export function SuggestSheet({
  onClose,
  onToast,
}: {
  onClose: () => void;
  onToast: (msg: string) => void;
}) {
  const [seg, setSeg] = useState(0);
  const [addr, setAddr] = useState('千葉市稲毛区小仲台 6-2-1');
  const [paired, setPaired] = useState(false);

  // マスタ準拠の訪問条件 state -------------------------------------------------
  const [frequencyPerWeek, setFrequencyPerWeek] = useState(1); // 1〜7
  const [visitFrequency, setVisitFrequency] =
    useState<(typeof VISIT_FREQUENCY_OPTIONS)[number]>('every'); // 毎週/隔週/月次
  const [weekdays, setWeekdays] = useState<Record<WeekdayKey, boolean>>({
    Mon: false,
    Tue: true,
    Wed: false,
    Thu: true,
    Fri: false,
    Sat: false,
    Sun: false,
  });
  const [serviceMinutes, setServiceMinutes] = useState(60); // 1〜180
  const [timeType, setTimeType] = useState<(typeof TIME_TYPE_OPTIONS)[number]>('終日'); // 既定は終日
  const [preferredStart, setPreferredStart] = useState('09:00');
  const [preferredEnd, setPreferredEnd] = useState('12:00');
  const [requiresMultipleStaff, setRequiresMultipleStaff] = useState(false);
  const [sexRestriction, setSexRestriction] = useState<
    '' | (typeof SEX_RESTRICTION_OPTIONS)[number]
  >('');

  const [results, setResults] = useState<RankCandidate[] | null>(null);
  const [added, setAdded] = useState<Record<number, boolean>>({});

  // 住所 → 拠点自動判定 (デバウンス, マスタ /offices/resolve と同 UX の簡易版)。
  const [office, setOffice] = useState<SuggestOfficeResult>(() =>
    resolveOfficeFromAddress('千葉市稲毛区小仲台 6-2-1'),
  );
  useEffect(() => {
    const timer = setTimeout(() => {
      setOffice(resolveOfficeFromAddress(addr));
    }, 500);
    return () => clearTimeout(timer);
  }, [addr]);

  // 時間タイプが「固定」「時間帯」のときのみ時刻欄を表示 (マスタの条件付き表示)。
  const showTimeRange = timeType === '固定' || timeType === '時間帯';
  // 「固定」は開始のみ実質有効、「時間帯」は開始 + 終了。
  const showEnd = timeType === '時間帯';

  const runSuggest = () => {
    setResults(paired ? RANK_PAIR : RANK_NORMAL);
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

        <Field label="ご住所">
          <input value={addr} onChange={(e) => setAddr(e.target.value)} style={cfInput} />
          <div style={{ display: 'flex', gap: 7, marginTop: 7, flexWrap: 'wrap' }}>
            <button
              onClick={() => {
                setAddr('千葉市稲毛区天台2-3-7');
                setPaired(true);
                setResults(null);
              }}
              style={{ ...miniChip, background: '#F0E7F5', color: _PLUM, borderColor: '#DCC8EE' }}
            >
              同住所の例（天台2-3-7）
            </button>
            <button
              onClick={() => {
                setAddr('千葉市稲毛区小仲台 6-2-1');
                setPaired(false);
                setResults(null);
              }}
              style={miniChip}
            >
              通常の例に戻す
            </button>
          </div>
          {/* 住所 → 拠点自動判定ヒント (マスタ /offices/resolve と同 UX) */}
          {office && office.confidence !== 'none' && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 11.5,
                fontWeight: 700,
                color: office.confidence === 'high' ? '#0E8472' : _TERRAD,
                background: office.confidence === 'high' ? '#E9FAF4' : '#FCF1DF',
                border: `1px solid ${office.confidence === 'high' ? '#C7EFE4' : '#F2DDB8'}`,
                borderRadius: 10,
                padding: '7px 10px',
                marginTop: 7,
              }}
            >
              <MapPin size={13} />
              担当拠点: {office.name}
              <span style={{ fontWeight: 600, color: _INK2 }}>
                （自動判定{office.confidence === 'low' ? '・要確認' : ''}）
              </span>
            </div>
          )}
          {office && office.confidence === 'none' && (
            <div
              style={{
                fontSize: 11.5,
                fontWeight: 700,
                color: _TERRAD,
                marginTop: 7,
              }}
            >
              ⚠ 拠点エリア外: 住所をご確認ください
            </div>
          )}
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 5, lineHeight: 1.5 }}>
            ※ 既存患者と<b>同住所（100m以内）</b>のとき、1位に「同住所ペア」候補を表示します。
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
                  {WEEKDAY_LABELS_JA[d]}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="サービス時間">
          <input
            type="number"
            min={1}
            max={180}
            value={serviceMinutes}
            onChange={(e) => {
              const n = Number(e.target.value);
              setServiceMinutes(Number.isFinite(n) ? Math.min(180, Math.max(1, n)) : 30);
            }}
            style={cfInput}
          />
          <div style={{ display: 'flex', gap: 7, marginTop: 7, flexWrap: 'wrap' }}>
            {SUGGEST_SERVICE_PRESETS.map((m) => {
              const on = serviceMinutes === m;
              return (
                <button
                  key={m}
                  onClick={() => setServiceMinutes(m)}
                  style={{ ...miniChip, ...(on ? miniChipOn : {}) }}
                >
                  {m}分
                </button>
              );
            })}
          </div>
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
          }}
        >
          <Sparkles size={17} />
          自動提案する
        </button>

        {results && (
          <div style={{ marginTop: 18 }}>
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
              おすすめ枠 {results.length}件
            </h4>
            {results.map((r, i) => (
              <RankCard
                key={i}
                r={r}
                rank={i + 1}
                added={!!added[i]}
                onAdd={() => {
                  setAdded((s) => ({ ...s, [i]: true }));
                  onToast(r.pair ? '✓ ペアで提案リストに追加' : '✓ 提案リストに追加');
                }}
              />
            ))}
          </div>
        )}
        <div style={{ height: 12 }} />
      </div>
    </SlideSheet>
  );
}

function MiniSlot({ row }: { row: RankScheduleRow }) {
  if (row.here) {
    const mint = row.pair;
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
          {row.t}
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
            {mint ? 'ここに一緒に入れましょうか？' : 'ここに入れましょうか？'}
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
        {row.t}
      </span>
      <div
        style={{
          flex: 1,
          borderRadius: 10,
          background: '#F8F4ED',
          borderLeft: `4px solid ${row.anchor ? _PLUM : row.ins === 'med' ? '#2F6FB0' : '#0E8472'}`,
          padding: '7px 11px',
          display: 'flex',
          alignItems: 'center',
          gap: 7,
        }}
      >
        <span style={{ fontFamily: 'var(--font-serif)', fontSize: 12.5, fontWeight: 700 }}>
          {row.name}
        </span>
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
        {row.anchor && (
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 700,
              padding: '1px 6px',
              borderRadius: 999,
              background: '#F0E7F5',
              color: _PLUM,
              marginLeft: 'auto',
            }}
          >
            同住所
          </span>
        )}
      </div>
    </div>
  );
}

function RankCard({
  r,
  rank,
  added,
  onAdd,
}: {
  r: RankCandidate;
  rank: number;
  added: boolean;
  onAdd: () => void;
}) {
  const medal = ['#E5B53A', '#B6BEC8', '#CF8048'][rank - 1] || '#CDC2B2';
  const mInk = ['#7A5200', '#3F4750', '#fff', '#fff'][rank - 1];
  const filled = r.schedule.filter((s) => !s.here).length;
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
            {r.course} <span style={{ color: r.pair ? _MINT : _TERRAD }}>{r.when}</span>
          </div>
          <div style={{ fontSize: 10.5, color: _INK2, marginTop: 1 }}>
            {r.staff} ・ {r.score}
          </div>
        </div>
        <button
          onClick={onAdd}
          style={{
            flex: '0 0 auto',
            padding: '9px 13px',
            minHeight: 40,
            borderRadius: 12,
            background: added ? _INK3 : _MINT,
            color: '#fff',
            fontFamily: 'var(--font-serif)',
            fontSize: 12.5,
            fontWeight: 700,
          }}
        >
          {added ? '追加済' : '＋ 追加'}
        </button>
      </div>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', padding: '0 14px 10px' }}>
        {r.badges.map((b, i) => (
          <span
            key={i}
            style={{
              fontSize: 10.5,
              padding: '2px 8px',
              borderRadius: 999,
              fontWeight: 700,
              background: b[1],
              color: b[2],
            }}
          >
            {b[0]}
          </span>
        ))}
      </div>
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
          {r.course}（{r.staff}）の{r.when.charAt(0)}曜 ・ {filled}件 + 提案枠
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {r.schedule.map((row, i) => (
            <MiniSlot key={i} row={row} />
          ))}
        </div>
      </div>
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
const miniChip: CSSProperties = {
  fontSize: 11.5,
  padding: '7px 11px',
  borderRadius: 999,
  background: '#F4EFE7',
  color: _INK2,
  fontWeight: 700,
  border: '2px solid transparent',
};
const miniChipOn: CSSProperties = {
  background: '#FCEBD6',
  color: _TERRAD,
  borderColor: _TERRA,
};

/**
 * 数値ステッパー — 患者マスタ `frequency_per_week` (1〜7) の数値入力を、
 * 現場ボードの Warm 意匠 (cfInput と同枠) でタップしやすい ± ボタンにしたもの。
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
