'use client';

/**
 * CareFlow Mobile — 受け入れ枠マトリックス (現場ボード /m 配下, P3)。
 *
 * 拠点ごとに、配下の全コースを統合した「曜日 × 時間帯」の受け入れ可否 (○△×) を
 * モバイル向けに「日めくり (曜日ステッパー) + 時間帯リスト」で表示する read-only
 * ビュー。デザイン (claude.ai/design `careflow-avail.jsx` の AvailListMode) を
 * 現場ボードの Warm パレット (inline style + CF_THEME) に移植したもの。
 *
 * 値は自動算出 (当週の実 Visit) に既存の手動「受入目安」を重ねた effective を表示。
 * 拠点・コースはアプリ登録データに完全追従する (ハードコードなし)。
 */

import { useMemo, useState, type CSSProperties } from 'react';
import Link from 'next/link';

import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { ArrowLeft, ChevronLeft, ChevronRight } from 'lucide-react';

import { toWeekStart, addDays } from '@/components/schedule/WeekSelector';
import { useAcceptanceMatrix } from '@/lib/queries/acceptance_matrix';
import { toIsoYearWeek } from '@/lib/queries/fieldBoard';
import { normalizeTimeSlot, type AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import type { MatrixCell, OfficeMatrix } from '@/lib/schemas/v2/acceptance_matrix';

import { CF_THEME } from './theme';

const { TEAL, TEAL_DEEP, INK, INK2, INK3, CREAM, LINE, PANEL } = CF_THEME;

const DOW = ['月', '火', '水', '木', '金', '土', '日'] as const;

interface StatusMeta {
  glyph: string;
  col: string;
  bg: string;
  ln: string;
  label: string;
}

// careflow-avail.jsx の AV パレットに準拠 (teal=○ / gold=△ / grey=×)。
const STATUS_META: Record<AcceptanceStatus, StatusMeta> = {
  available: { glyph: '○', col: '#0E8472', bg: '#E7F4F0', ln: '#CDE8E0', label: '受け入れ可能' },
  consult: { glyph: '△', col: '#B5790A', bg: '#FBF1DD', ln: '#F0DFBC', label: '相談ください' },
  unavailable: { glyph: '×', col: INK3, bg: 'transparent', ln: LINE, label: '枠なし' },
};

function dowColor(idx: number): string {
  if (idx === 5) return '#2F6FB0'; // 土
  if (idx === 6) return '#C75C77'; // 日
  return TEAL_DEEP;
}

const arrowBtn: CSSProperties = {
  width: 40,
  height: 40,
  flex: '0 0 auto',
  borderRadius: 13,
  background: '#fff',
  border: `1px solid ${LINE}`,
  boxShadow: '0 2px 6px rgba(28,25,23,0.06)',
  display: 'grid',
  placeItems: 'center',
  color: TEAL_DEEP,
  cursor: 'pointer',
};

const wkArrowBtn: CSSProperties = {
  ...arrowBtn,
  width: 30,
  height: 30,
  borderRadius: 9,
};

function Legend() {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 12,
        alignItems: 'center',
        fontSize: 11,
        color: INK2,
        padding: '12px 4px 4px',
      }}
    >
      {(['available', 'consult', 'unavailable'] as const).map((s) => {
        const m = STATUS_META[s];
        return (
          <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <span
              style={{
                width: 20,
                height: 20,
                borderRadius: 6,
                background: m.bg,
                border: `1px solid ${m.ln}`,
                display: 'grid',
                placeItems: 'center',
                color: m.col,
                fontSize: 12,
                fontWeight: s === 'unavailable' ? 400 : 700,
              }}
            >
              {m.glyph}
            </span>
            {m.label}
          </span>
        );
      })}
    </div>
  );
}

function OfficeDayCard({
  office,
  dayIdx,
  slots,
}: {
  office: OfficeMatrix;
  dayIdx: number;
  slots: string[];
}) {
  const { closed, cellByTime } = useMemo(() => {
    const day = office.days.find((d) => d.weekday === dayIdx);
    const operating = new Set(office.operating_weekdays);
    const closed = day?.office_closed ?? !operating.has(dayIdx);
    const cellByTime = new Map<string, MatrixCell>();
    for (const c of day?.cells ?? []) cellByTime.set(normalizeTimeSlot(c.time_slot), c);
    return { closed, cellByTime };
  }, [office, dayIdx]);

  return (
    <div style={{ marginBottom: 16 }}>
      {/* エリア帯 */}
      <div
        style={{
          background: 'linear-gradient(135deg, #0E8472 0%, #0B6E5E 100%)',
          borderRadius: 14,
          padding: '10px 14px',
          color: '#fff',
          marginBottom: 10,
          boxShadow: '0 5px 14px rgba(14,132,114,0.2)',
        }}
      >
        <div style={{ fontFamily: 'var(--font-serif)', fontSize: 15, fontWeight: 700 }}>
          {office.office_name}
        </div>
        {office.city_names.length > 0 && (
          <div style={{ fontSize: 10.5, opacity: 0.9, marginTop: 2 }}>
            {office.city_names.join('・')}
          </div>
        )}
      </div>

      {closed ? (
        <div
          style={{
            padding: '28px 0',
            textAlign: 'center',
            color: INK3,
            background: '#F1ECE3',
            borderRadius: 14,
            fontFamily: 'var(--font-serif)',
            fontSize: 15,
          }}
        >
          定休日
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          {slots.map((slot) => {
            const hhmm = normalizeTimeSlot(slot);
            const cell = cellByTime.get(hhmm);
            const status = cell?.effective_status ?? 'unavailable';
            const m = STATUS_META[status];
            const isManual = cell?.source === 'manual_standing';
            return (
              <div
                key={slot}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  background: PANEL,
                  border: `1px solid ${LINE}`,
                  borderLeft: `4px solid ${status === 'unavailable' ? LINE : m.col}`,
                  borderRadius: 13,
                  padding: '11px 14px',
                }}
              >
                <div
                  style={{
                    fontFamily: 'var(--font-mono)',
                    fontSize: 14,
                    fontWeight: 700,
                    color: INK,
                    width: 52,
                  }}
                >
                  {hhmm}
                </div>
                <div
                  style={{
                    flex: 1,
                    fontSize: 13,
                    color: status === 'unavailable' ? INK3 : INK2,
                    fontWeight: status === 'unavailable' ? 400 : 600,
                  }}
                >
                  {m.label}
                  {isManual && (
                    <span style={{ marginLeft: 6, fontSize: 10, color: INK3 }}>（手動）</span>
                  )}
                </div>
                <div
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: 10,
                    background: m.bg,
                    border: `1px solid ${m.ln}`,
                    display: 'grid',
                    placeItems: 'center',
                    color: m.col,
                    fontSize: 18,
                    fontWeight: status === 'unavailable' ? 400 : 700,
                  }}
                >
                  {m.glyph}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AcceptanceFieldView() {
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const [dayIdx, setDayIdx] = useState(0);
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  const query = useAcceptanceMatrix({ isoYear, isoWeek, officeId: null });

  const goDay = (d: number) => setDayIdx((p) => (p + d + 7) % 7);
  const goWeek = (d: number) => setWeekStart((w) => addDays(w, d * 7));

  const dayDate = addDays(weekStart, dayIdx);
  const weekRange = `${format(weekStart, 'M/d', { locale: ja })} - ${format(addDays(weekStart, 6), 'M/d', { locale: ja })}`;

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: CREAM,
        color: INK,
        fontFamily: 'var(--font-sans)',
        overflow: 'hidden',
      }}
    >
      {/* 戻るバー */}
      <Link
        href="/m"
        aria-label="現場ボードに戻る"
        style={{
          flex: '0 0 auto',
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          paddingTop: 'calc(env(safe-area-inset-top) + 6px)',
          paddingBottom: 6,
          paddingLeft: 14,
          paddingRight: 14,
          background: CREAM,
          borderBottom: `1px solid ${LINE}`,
          color: TEAL_DEEP,
          fontFamily: 'var(--font-serif)',
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        <ArrowLeft size={13} strokeWidth={2.4} />
        現場ボードへ
      </Link>

      {/* ヘッダ帯 */}
      <div style={{ flex: '0 0 auto', padding: '6px 8px 0' }}>
        <div
          style={{
            background: `linear-gradient(135deg, ${TEAL} 0%, ${TEAL_DEEP} 100%)`,
            color: '#fff',
            padding: '10px 14px',
            borderRadius: 16,
            boxShadow: '0 5px 14px rgba(13,148,136,0.18)',
          }}
        >
          <div style={{ fontFamily: 'var(--font-serif)', fontSize: 16, fontWeight: 700 }}>
            受け入れ枠
          </div>
          <div style={{ fontSize: 10, opacity: 0.85, marginTop: 2 }}>
            全コース統合の目安 ・ おおよその空き状況
          </div>
        </div>
      </div>

      {/* 週 + 曜日ステッパー */}
      <div style={{ flex: '0 0 auto', padding: '10px 10px 4px' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            marginBottom: 8,
          }}
        >
          <button type="button" onClick={() => goWeek(-1)} style={wkArrowBtn} aria-label="前の週">
            <ChevronLeft size={16} />
          </button>
          <div style={{ textAlign: 'center', minWidth: 96 }}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 13, fontWeight: 700 }}>
              第{isoWeek}週
            </div>
            <div style={{ fontSize: 9.5, color: INK3 }}>{weekRange}</div>
          </div>
          <button type="button" onClick={() => goWeek(1)} style={wkArrowBtn} aria-label="次の週">
            <ChevronRight size={16} />
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <button type="button" onClick={() => goDay(-1)} style={arrowBtn} aria-label="前の曜日">
            <ChevronLeft size={20} />
          </button>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 19, fontWeight: 700 }}>
              <span style={{ color: dowColor(dayIdx) }}>{DOW[dayIdx]}曜</span>
              <span style={{ fontSize: 13, color: INK3, fontWeight: 600, marginLeft: 8 }}>
                {format(dayDate, 'M/d', { locale: ja })}
              </span>
            </div>
          </div>
          <button type="button" onClick={() => goDay(1)} style={arrowBtn} aria-label="次の曜日">
            <ChevronRight size={20} />
          </button>
        </div>
      </div>

      {/* 本体 (スクロール) */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 12px 24px' }}>
        {query.isLoading && (
          <p style={{ fontSize: 13, color: INK2, padding: '24px 0', textAlign: 'center' }}>
            読み込み中…
          </p>
        )}
        {query.isError && (
          <p style={{ fontSize: 13, color: '#C75C77', padding: '24px 0', textAlign: 'center' }}>
            読み込みに失敗しました
          </p>
        )}
        {query.data && (
          <>
            {!query.data.week_generated_any && (
              <p
                style={{
                  fontSize: 11.5,
                  color: INK2,
                  background: '#F1ECE3',
                  borderRadius: 10,
                  padding: '8px 12px',
                  marginBottom: 12,
                }}
              >
                ※ この週はまだコースが生成されていないため、空き枠を算出できません。
              </p>
            )}
            {query.data.offices.length === 0 ? (
              <p style={{ fontSize: 13, color: INK2 }}>表示できる拠点がありません。</p>
            ) : (
              query.data.offices.map((office) => (
                <OfficeDayCard
                  key={office.office_id}
                  office={office}
                  dayIdx={dayIdx}
                  slots={query.data.slots}
                />
              ))
            )}
            <Legend />
          </>
        )}
      </div>
    </div>
  );
}
