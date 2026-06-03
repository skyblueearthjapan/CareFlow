'use client';

/**
 * CareFlow Mobile — 現場ボード (Warm & Human aligned)
 *
 * `mocks/design_bundle/carelink/project/careflow-mobile.jsx` をピクセル忠実に
 * 移植したフィールドボード本体。電話/タブレット向け: 曜日ステッパー + コース一覧
 * (agenda) 固定、カルテシート、ランキング付き提案シート、承認モード。
 *
 * ⚠️ Phase 1 はフロント完結・ダミーデータ (`./mockData`)。
 */

import { useEffect, useState, type CSSProperties } from 'react';
import {
  Heart,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Plus,
  Check,
  ClipboardCheck,
  MapPin,
} from 'lucide-react';

import {
  CF_WEEK,
  CF_DOWS,
  CF_DATES,
  CF_PATIENTS,
  CF_PENDING,
  CF_THEME,
  cc,
  getPatient,
  type Course,
  type Dow,
  type OfficeKey,
  type PendingPlacement,
} from './mockData';
import { KarteSheet, SuggestSheet, Toast } from './FieldSheets';

const {
  TEAL,
  TEAL_DEEP,
  TERRA,
  TERRA_DEEP,
  PLUM,
  MINT,
  INK,
  INK2,
  INK3,
  CREAM,
  LINE,
  PANEL,
  GOLD,
} = CF_THEME;

type ApproveVerdict = 'ok' | 'no';
type ApproveFn = (key: string, verdict: ApproveVerdict) => void;

interface BoardCommonProps {
  courses: Course[];
  pend: PendingPlacement | null;
  pendingState: Record<string, ApproveVerdict>;
  onKarte: (pk: string) => void;
  onEmpty: () => void;
  onApprove: ApproveFn;
}

// ============================ App ============================

export function FieldBoard({ topPad = 16 }: { topPad?: number }) {
  const [office, setOffice] = useState<OfficeKey>('INAGE');
  const [day, setDay] = useState<Dow>('月');
  const [approve, setApprove] = useState(false);
  const [weekOff, setWeekOff] = useState(0);
  const [karte, setKarte] = useState<string | null>(null);
  const [sheet, setSheet] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [pendingState, setPendingState] = useState<Record<string, ApproveVerdict>>({});

  const courses = CF_WEEK[office][day] || [];
  const week = 23 + weekOff;

  const showToast = (msg: string) => {
    setToast(msg);
    window.clearTimeout((window as unknown as { __cfT?: number }).__cfT);
    (window as unknown as { __cfT?: number }).__cfT = window.setTimeout(() => setToast(null), 2200);
  };

  useEffect(() => {
    // 当日にコースが無い拠点なら、最初に開いている曜日へジャンプ。
    const open = CF_DOWS.filter((d) => (CF_WEEK[office][d] || []).length > 0);
    if (!open.includes(day)) setDay(open[0] || '月');
    // office 切替時のみ評価する (day を依存に含めない — 移植元と同挙動)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [office]);

  const onApprove: ApproveFn = (key, verdict) => {
    setPendingState((s) => ({ ...s, [key]: verdict }));
    showToast(verdict === 'ok' ? '✓ 承認しました' : '却下しました');
  };

  const pend = (approve && CF_PENDING[office][day]) || null;

  const boardProps: BoardCommonProps = {
    courses,
    pend,
    pendingState,
    onKarte: setKarte,
    onEmpty: () => setSheet(true),
    onApprove,
  };

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: CREAM,
        position: 'relative',
        overflow: 'hidden',
        fontFamily: 'var(--font-sans)',
      }}
    >
      <Header
        office={office}
        setOffice={setOffice}
        week={week}
        setWeekOff={setWeekOff}
        approve={approve}
        setApprove={setApprove}
        onNew={() => setSheet(true)}
        onToast={showToast}
        topPad={topPad}
      />

      <DayStepper office={office} day={day} setDay={setDay} />

      <div
        className="cf-scroll"
        style={{ flex: 1, overflow: 'auto', WebkitOverflowScrolling: 'touch' }}
      >
        {courses.length === 0 ? <Empty day={day} /> : <AgendaBoard {...boardProps} />}
        <div style={{ height: 28 }} />
      </div>

      {/* scrim */}
      {(karte || sheet) && (
        <div
          onClick={() => {
            setKarte(null);
            setSheet(false);
          }}
          style={{
            position: 'absolute',
            inset: 0,
            background: 'rgba(28,25,23,0.42)',
            zIndex: 40,
            backdropFilter: 'blur(2px)',
          }}
        />
      )}

      {karte && (
        <KarteSheet pk={karte} office={office} onClose={() => setKarte(null)} onOpen={setKarte} />
      )}
      {sheet && <SuggestSheet onClose={() => setSheet(false)} onToast={showToast} />}

      {toast && <Toast msg={toast} />}
    </div>
  );
}

// ============================ Header ============================

interface HeaderProps {
  office: OfficeKey;
  setOffice: (o: OfficeKey) => void;
  week: number;
  setWeekOff: (fn: (w: number) => number) => void;
  approve: boolean;
  setApprove: (fn: (a: boolean) => boolean) => void;
  onNew: () => void;
  onToast: (msg: string) => void;
  topPad?: number;
}

function Header({
  office,
  setOffice,
  week,
  setWeekOff,
  approve,
  setApprove,
  onNew,
  onToast,
  topPad = 16,
}: HeaderProps) {
  const bg = approve
    ? 'linear-gradient(135deg, #E0A21A 0%, #B5790A 100%)'
    : `linear-gradient(135deg, ${TEAL} 0%, ${TEAL_DEEP} 100%)`;
  const accentInk = approve ? '#9A6700' : TEAL_DEEP;
  return (
    <div
      style={{
        flex: '0 0 auto',
        background: bg,
        color: '#fff',
        padding: `${topPad}px 16px 16px`,
        borderRadius: '0 0 24px 24px',
        boxShadow: '0 8px 22px rgba(13,148,136,0.22)',
        position: 'relative',
        zIndex: 20,
      }}
    >
      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 11,
              background: '#fff',
              color: accentInk,
              display: 'grid',
              placeItems: 'center',
              boxShadow: '0 3px 8px rgba(0,0,0,0.14)',
            }}
          >
            <Heart size={18} strokeWidth={2.4} />
          </div>
          <div>
            <div
              style={{
                fontFamily: 'var(--font-serif)',
                fontSize: 18,
                fontWeight: 700,
                lineHeight: 1,
                letterSpacing: '-0.01em',
                display: 'flex',
                alignItems: 'center',
                gap: 7,
              }}
            >
              CareFlow
              <span
                style={{
                  fontFamily: 'var(--font-sans)',
                  fontSize: 9.5,
                  fontWeight: 700,
                  letterSpacing: '0.04em',
                  padding: '2px 7px',
                  borderRadius: 999,
                  background: 'rgba(255,255,255,0.22)',
                  color: '#fff',
                  border: '1px solid rgba(255,255,255,0.35)',
                }}
              >
                DEMO
              </span>
            </div>
            <div style={{ fontSize: 10, opacity: 0.85, marginTop: 2 }}>現場ボード</div>
          </div>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            background: 'rgba(255,255,255,0.18)',
            borderRadius: 999,
            padding: 3,
          }}
        >
          <button
            onClick={() => {
              setWeekOff((w) => w - 1);
              onToast('◀ 前の週');
            }}
            style={hdrCircle}
            aria-label="前の週"
          >
            <ChevronLeft size={16} />
          </button>
          <div
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 14,
              fontWeight: 700,
              minWidth: 74,
              textAlign: 'center',
              lineHeight: 1.1,
            }}
          >
            第{week}週
            <div
              style={{
                fontSize: 9,
                opacity: 0.85,
                fontFamily: 'var(--font-sans)',
                fontWeight: 500,
              }}
            >
              6/1 - 6/7
            </div>
          </div>
          <button
            onClick={() => {
              setWeekOff((w) => w + 1);
              onToast('▶ 次の週');
            }}
            style={hdrCircle}
            aria-label="次の週"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginTop: 13,
        }}
      >
        <div
          style={{
            display: 'flex',
            gap: 5,
            background: 'rgba(255,255,255,0.16)',
            padding: 3,
            borderRadius: 999,
          }}
        >
          {(
            [
              { v: 'INAGE', l: '稲毛' },
              { v: 'TSUGA', l: '都賀' },
            ] as const
          ).map((o) => {
            const on = office === o.v;
            return (
              <button
                key={o.v}
                onClick={() => setOffice(o.v)}
                style={{
                  padding: '7px 16px',
                  borderRadius: 999,
                  fontSize: 13,
                  fontWeight: 600,
                  fontFamily: 'var(--font-serif)',
                  background: on ? '#fff' : 'transparent',
                  color: on ? accentInk : '#fff',
                  boxShadow: on ? '0 2px 6px rgba(0,0,0,0.12)' : 'none',
                }}
              >
                {o.l}
              </button>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 7 }}>
          <button onClick={onNew} style={{ ...hdrAct, background: '#fff', color: accentInk }}>
            <Plus size={15} /> 提案
          </button>
          <button
            onClick={() => setApprove((a) => !a)}
            style={{
              ...hdrAct,
              background: approve ? '#fff' : 'rgba(255,255,255,0.16)',
              color: approve ? accentInk : '#fff',
              position: 'relative',
            }}
          >
            <ClipboardCheck size={15} /> 承認
            <span
              style={{
                position: 'absolute',
                top: -6,
                right: -6,
                minWidth: 18,
                height: 18,
                padding: '0 5px',
                background: '#E1657F',
                color: '#fff',
                fontSize: 11,
                fontWeight: 700,
                borderRadius: 999,
                display: 'grid',
                placeItems: 'center',
                border: '2px solid #fff',
              }}
            >
              2
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}

const hdrCircle: CSSProperties = {
  width: 34,
  height: 34,
  borderRadius: '50%',
  display: 'grid',
  placeItems: 'center',
  color: '#fff',
};
const hdrAct: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 5,
  padding: '9px 13px',
  minHeight: 40,
  borderRadius: 13,
  fontSize: 13,
  fontWeight: 600,
  fontFamily: 'var(--font-serif)',
  boxShadow: '0 2px 6px rgba(0,0,0,0.10)',
};

// ============================ Legend (shared by DayStepper) ============================

const Legend = ({ col, t }: { col: string; t: string }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
    <i style={{ width: 9, height: 9, borderRadius: 3, background: col }} />
    {t}
  </span>
);

// ============================ Day stepper (←/→ 曜日送り) ============================

function DayStepper({
  office,
  day,
  setDay,
}: {
  office: OfficeKey;
  day: Dow;
  setDay: (d: Dow) => void;
}) {
  const idx = CF_DOWS.indexOf(day);
  const go = (dir: number) => setDay(CF_DOWS[(idx + dir + 7) % 7] ?? '月');
  const courses = CF_WEEK[office][day] || [];
  let total = 0;
  let room = 0;
  courses.forEach((co) =>
    co.slots.forEach((s) => {
      total++;
      if (!s) room++;
    }),
  );
  const closed = courses.length === 0;
  const sat = day === '土';
  const sun = day === '日';
  const arrow: CSSProperties = {
    width: 42,
    height: 42,
    flex: '0 0 auto',
    borderRadius: 14,
    background: '#fff',
    border: `1px solid ${LINE}`,
    boxShadow: '0 2px 6px rgba(28,25,23,0.06)',
    display: 'grid',
    placeItems: 'center',
    color: TEAL_DEEP,
  };
  return (
    <div style={{ flex: '0 0 auto', padding: '8px 14px 2px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button onClick={() => go(-1)} style={arrow} aria-label="前の曜日">
          <ChevronLeft size={20} />
        </button>
        <div style={{ flex: 1, textAlign: 'center' }}>
          <div
            style={{
              fontFamily: 'var(--font-serif)',
              fontSize: 19,
              fontWeight: 700,
              lineHeight: 1.1,
            }}
          >
            <span style={{ color: sat ? '#2F6FB0' : sun ? '#C75C77' : TEAL_DEEP }}>{day}曜</span>
            <span style={{ fontSize: 13, color: INK3, fontWeight: 600, marginLeft: 8 }}>
              {CF_DATES[idx]}
            </span>
          </div>
          <div style={{ fontSize: 11, color: INK2, marginTop: 3 }}>
            {closed ? (
              '休講日'
            ) : (
              <span>
                訪問 <b style={{ color: INK }}>{total - room}</b> 件 ・{' '}
                <span style={{ color: room > 0 ? '#0E8472' : INK3, fontWeight: 700 }}>
                  {room > 0 ? `空き ${room}` : '満員'}
                </span>
              </span>
            )}
          </div>
        </div>
        <button onClick={() => go(1)} style={arrow} aria-label="次の曜日">
          <ChevronRight size={20} />
        </button>
      </div>
      <div
        style={{
          display: 'flex',
          gap: 10,
          fontSize: 10,
          color: INK2,
          justifyContent: 'center',
          marginTop: 6,
        }}
      >
        <Legend col={TERRA} t="空き枠" />
        <Legend col={GOLD} t="承認待ち" />
        <Legend col={PLUM} t="同住所" />
      </div>
    </div>
  );
}

// ============================ Patient card (shared) ============================

function PatientCard({
  pk,
  si,
  co,
  inPair,
  onKarte,
  compact,
}: {
  pk: string;
  si: number;
  co: Course;
  inPair?: boolean;
  onKarte: (pk: string) => void;
  compact?: boolean;
}) {
  const p = getPatient(pk);
  const t = (co.times && co.times[si]) || null;
  const k = cc(co.c);
  const sameArea =
    !inPair &&
    co.slots.some((o, oi) => {
      if (!o || oi === si) return false;
      const other = CF_PATIENTS[o];
      return !!other && other.area === p.area;
    });
  return (
    <button
      onClick={() => onKarte(pk)}
      style={{
        width: '100%',
        textAlign: 'left',
        background: inPair ? '#fff' : k.soft,
        borderRadius: 12,
        borderLeft: `5px solid ${inPair ? PLUM : k.c}`,
        padding: compact ? '8px 11px' : '9px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
        position: 'relative',
        boxShadow: '0 1px 2px rgba(28,25,23,0.05)',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 7,
          right: 9,
          fontSize: 9.5,
          color: INK3,
          fontWeight: 600,
        }}
      >
        {si + 1}/6
      </span>
      {t && (
        <span
          style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            color: TEAL_DEEP,
            background: '#fff',
            border: `1.5px solid ${k.bg}`,
            borderRadius: 7,
            padding: '1px 7px',
            alignSelf: 'flex-start',
            fontWeight: 600,
          }}
        >
          {t}
        </span>
      )}
      <div
        style={{
          fontFamily: 'var(--font-serif)',
          fontSize: 14.5,
          fontWeight: 700,
          lineHeight: 1.15,
        }}
      >
        {p.name}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 10.5,
          color: INK2,
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            padding: '1px 7px',
            borderRadius: 999,
            background: p.ins === 'med' ? '#E2EDF8' : '#D7F2EE',
            color: p.ins === 'med' ? '#2F6FB0' : '#0E8472',
          }}
        >
          {p.ins === 'med' ? '医療' : '介護'}
        </span>
        <span>{p.svc}分</span>
        {inPair ? (
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              padding: '1px 7px',
              borderRadius: 999,
              background: '#F0E7F5',
              color: PLUM,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 2,
            }}
          >
            <MapPin size={9} />
            同住所
          </span>
        ) : sameArea ? (
          <span
            style={{
              color: MINT,
              fontWeight: 700,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 2,
            }}
          >
            <MapPin size={9} />
            同エリア
          </span>
        ) : null}
      </div>
    </button>
  );
}

function EmptySlot({ onEmpty }: { onEmpty: () => void }) {
  return (
    <button
      onClick={onEmpty}
      style={{
        width: '100%',
        minHeight: 50,
        borderRadius: 12,
        border: `2.5px dashed ${TERRA}`,
        background:
          'repeating-linear-gradient(45deg, #FFFBF4, #FFFBF4 9px, #FDF1DF 9px, #FDF1DF 18px)',
        color: TERRA_DEEP,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 6,
        fontFamily: 'var(--font-serif)',
        fontSize: 13,
        fontWeight: 700,
      }}
    >
      <span
        style={{
          width: 24,
          height: 24,
          borderRadius: '50%',
          background: TERRA,
          color: '#fff',
          display: 'grid',
          placeItems: 'center',
          fontSize: 16,
          lineHeight: 1,
        }}
      >
        ＋
      </span>
      <span style={{ whiteSpace: 'nowrap' }}>空き枠</span>
    </button>
  );
}

function PairWrap({
  co,
  si,
  si2,
  onKarte,
}: {
  co: Course;
  si: number;
  si2: number;
  onKarte: (pk: string) => void;
}) {
  const tShared = (co.times && co.times[si]) || '';
  return (
    <div
      style={{
        border: `2.5px solid ${PLUM}`,
        background: 'linear-gradient(180deg, #F7F0FB 0%, #F1E7F8 100%)',
        borderRadius: 15,
        padding: '6px 6px 7px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div
        style={{
          fontFamily: 'var(--font-serif)',
          fontSize: 11,
          color: '#fff',
          background: `linear-gradient(135deg, ${PLUM}, #7A4E91)`,
          borderRadius: 8,
          padding: '5px 8px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 5,
          fontWeight: 700,
          flexWrap: 'wrap',
          textAlign: 'center',
          lineHeight: 1.3,
        }}
      >
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
        >
          <MapPin size={11} /> 同住所・同時
        </span>{' '}
        <span style={{ fontSize: 9.5, opacity: 0.92, fontWeight: 600, whiteSpace: 'nowrap' }}>
          1スタッフ連続・約90分
        </span>
      </div>
      <PatientCard pk={co.slots[si] as string} si={si} co={co} inPair onKarte={onKarte} />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
          fontSize: 10,
          fontWeight: 700,
          color: '#7A4E91',
        }}
      >
        <span style={{ flex: 1, height: 2, background: '#DCC8EE', borderRadius: 2 }} />
        {tShared} 連続訪問
        <span style={{ flex: 1, height: 2, background: '#DCC8EE', borderRadius: 2 }} />
      </div>
      <PatientCard pk={co.slots[si2] as string} si={si2} co={co} inPair onKarte={onKarte} />
    </div>
  );
}

function PendingCard({
  pend,
  ci,
  si,
  state,
  onApprove,
}: {
  pend: PendingPlacement;
  ci: number;
  si: number;
  state: Record<string, ApproveVerdict>;
  onApprove: ApproveFn;
}) {
  const k = `${ci}-${si}`;
  const done = state[k];
  const p = getPatient(pend.patient);
  if (done === 'no') return null;
  if (done === 'ok') {
    return (
      <div
        style={{
          borderRadius: 12,
          border: `2px solid ${MINT}`,
          background: '#E6F7EF',
          padding: '9px 12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Check size={14} style={{ color: MINT }} />
          <span style={{ fontFamily: 'var(--font-serif)', fontWeight: 700, fontSize: 14 }}>
            {p.name}
          </span>
          <span style={{ fontSize: 10.5, color: MINT, fontWeight: 700 }}>承認済</span>
        </div>
      </div>
    );
  }
  return (
    <div
      style={{
        borderRadius: 12,
        border: `2.5px dashed ${GOLD}`,
        background:
          'repeating-linear-gradient(45deg, #FFFCEC, #FFFCEC 9px, #FFF5C9 9px, #FFF5C9 18px)',
        padding: '9px 12px',
        position: 'relative',
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: -9,
          right: 8,
          background: GOLD,
          color: '#fff',
          fontFamily: 'var(--font-serif)',
          fontSize: 10,
          fontWeight: 700,
          padding: '2px 8px',
          borderRadius: 999,
        }}
      >
        承認待ち
      </span>
      <div
        style={{ fontFamily: 'var(--font-serif)', fontSize: 14, fontWeight: 700, color: '#9A7400' }}
      >
        {p.name}
      </div>
      <div style={{ fontSize: 10.5, color: INK2, marginTop: 1 }}>
        {pend.who} ・ {p.svc}分 ・ {si + 1}枠目
      </div>
      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
        <button
          onClick={() => onApprove(k, 'ok')}
          style={{
            flex: 1,
            padding: '9px',
            borderRadius: 10,
            background: MINT,
            color: '#fff',
            fontFamily: 'var(--font-serif)',
            fontWeight: 700,
            fontSize: 13,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 5,
          }}
        >
          <Check size={14} />
          承認
        </button>
        <button
          onClick={() => onApprove(k, 'no')}
          style={{
            flex: 1,
            padding: '9px',
            borderRadius: 10,
            background: '#fff',
            color: '#C75C77',
            border: '2px solid #F4D4DC',
            fontFamily: 'var(--font-serif)',
            fontWeight: 700,
            fontSize: 13,
          }}
        >
          却下
        </button>
      </div>
    </div>
  );
}

// コース内のスロット一覧 (stack / carousel / agenda / week で共有)
function CourseSlots({
  co,
  ci,
  pend,
  pendingState,
  onKarte,
  onEmpty,
  onApprove,
}: {
  co: Course;
  ci: number;
  pend: PendingPlacement | null;
  pendingState: Record<string, ApproveVerdict>;
  onKarte: (pk: string) => void;
  onEmpty: () => void;
  onApprove: ApproveFn;
}) {
  const pairList = co.pairs || [];
  const pairSecond = new Set(pairList.map((pr) => pr[1]));
  const pairFirst: Record<number, number> = {};
  pairList.forEach((pr) => {
    pairFirst[pr[0]] = pr[1];
  });
  const out: React.ReactNode[] = [];
  co.slots.forEach((pk, si) => {
    if (pairSecond.has(si)) return;
    if (pend && pend.courseIdx === ci && pend.slotIdx === si) {
      out.push(
        <PendingCard
          key={'p' + si}
          pend={pend}
          ci={ci}
          si={si}
          state={pendingState}
          onApprove={onApprove}
        />,
      );
      return;
    }
    const mate = pairFirst[si];
    if (pk && mate !== undefined) {
      out.push(<PairWrap key={'pair' + si} co={co} si={si} si2={mate} onKarte={onKarte} />);
      return;
    }
    if (pk) out.push(<PatientCard key={si} pk={pk} si={si} co={co} onKarte={onKarte} />);
    else out.push(<EmptySlot key={si} onEmpty={onEmpty} />);
  });
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 7, padding: 8 }}>{out}</div>;
}

// ============================ Layout: AGENDA ============================

function AgendaBoard({
  courses,
  pend,
  pendingState,
  onKarte,
  onEmpty,
  onApprove,
}: BoardCommonProps) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(courses.map((c) => c.id)));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '4px 14px 0' }}>
      {courses.map((co, ci) => {
        const k = cc(co.c);
        const filled = co.slots.filter(Boolean).length;
        const room = 6 - filled;
        const isOpen = open.has(co.id);
        return (
          <section
            key={co.id}
            style={{
              background: PANEL,
              borderRadius: 16,
              boxShadow: '0 2px 8px rgba(28,25,23,0.06)',
              overflow: 'hidden',
              borderLeft: `5px solid ${k.c}`,
            }}
          >
            <button
              onClick={() =>
                setOpen((s) => {
                  const n = new Set(s);
                  if (n.has(co.id)) n.delete(co.id);
                  else n.add(co.id);
                  return n;
                })
              }
              style={{
                width: '100%',
                padding: '11px 14px',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                textAlign: 'left',
              }}
              aria-expanded={isOpen}
            >
              <span
                style={{
                  fontFamily: 'var(--font-serif)',
                  fontSize: 15,
                  fontWeight: 700,
                  color: k.c,
                }}
              >
                {co.id}
              </span>
              <span style={{ fontSize: 11, color: INK2 }}>{co.staff}</span>
              <span style={{ flex: 1 }} />
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: room > 0 ? TEAL_DEEP : INK3,
                  background: room > 0 ? '#D7F2EE' : '#F0ECE5',
                  padding: '2px 8px',
                  borderRadius: 999,
                }}
              >
                {filled}/6 {room > 0 ? `空${room}` : '満'}
              </span>
              <span
                style={{
                  color: INK3,
                  transform: isOpen ? 'rotate(180deg)' : 'none',
                  transition: 'transform .2s',
                  display: 'inline-flex',
                }}
              >
                <ChevronDown size={16} />
              </span>
            </button>
            {isOpen && (
              <CourseSlots
                co={co}
                ci={ci}
                pend={pend}
                pendingState={pendingState}
                onKarte={onKarte}
                onEmpty={onEmpty}
                onApprove={onApprove}
              />
            )}
          </section>
        );
      })}
    </div>
  );
}

function Empty({ day }: { day: Dow }) {
  return (
    <div
      style={{
        textAlign: 'center',
        color: INK3,
        padding: '60px 20px',
        fontFamily: 'var(--font-serif)',
      }}
    >
      <div style={{ fontSize: 44 }}>🌙</div>
      <div style={{ marginTop: 10, fontSize: 16, fontWeight: 700 }}>{day}曜は休講日です</div>
      <div style={{ fontSize: 12.5, marginTop: 4, fontFamily: 'var(--font-sans)' }}>
        出勤スタッフがいません
      </div>
    </div>
  );
}
