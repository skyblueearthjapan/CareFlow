'use client';

/**
 * CareFlow Mobile — 受け入れ枠マトリックス (現場ボード /m 配下, P3 / 設計準拠版)。
 *
 * 拠点トグルで 1 拠点ずつ、「曜日 × 時間帯」のコンパクトな ○△× マトリックスを表示する
 * read-only ビュー。デザイン (claude.ai/design `careflow-mobile.jsx` の AvailBoard =
 * `careflow-avail.jsx` の AvailMatrixCore) を現場ボードの Warm パレット (inline style +
 * CF_THEME) に移植したもの。値は effective_status (= 週別上書き ?? 常設上書き ?? 自動算出)。
 * 拠点・コースはアプリ登録データに完全追従する (ハードコードなし)。
 */

import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { toWeekStart, addDays } from '@/components/schedule/WeekSelector';
import { useAcceptanceMatrix } from '@/lib/queries/acceptance_matrix';
import { toIsoYearWeek } from '@/lib/queries/fieldBoard';
import { normalizeTimeSlot, type AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import type { MatrixCell, OfficeMatrix } from '@/lib/schemas/v2/acceptance_matrix';

import { CF_THEME } from './theme';
import { MobileSurfaceSwitcher } from '@/components/mobile/MobileSurfaceSwitcher';

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
  unavailable: { glyph: '×', col: '#9A8E80', bg: 'transparent', ln: LINE, label: '枠なし' },
};

function dowColor(idx: number): string {
  if (idx === 5) return '#2F6FB0'; // 土
  if (idx === 6) return '#C75C77'; // 日
  return INK;
}

const wkArrowBtn: CSSProperties = {
  width: 32,
  height: 32,
  flex: '0 0 auto',
  borderRadius: 9,
  background: 'rgba(255,255,255,0.45)',
  display: 'grid',
  placeItems: 'center',
  color: 'inherit',
  cursor: 'pointer',
};

function Legend() {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 14,
        alignItems: 'center',
        fontSize: 11,
        color: INK2,
        padding: '14px 4px 4px',
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
      <span style={{ color: INK3 }}>数字 = 入れる空きコース数</span>
    </div>
  );
}

/** 1 拠点ぶんの 曜日 × 時間帯 マトリックス (AvailMatrixCore 相当)。 */
function OfficeMatrix({ office, slots }: { office: OfficeMatrix; slots: string[] }) {
  const { cellByWeekday, closedByWeekday } = useMemo(() => {
    const cellByWeekday = new Map<number, Map<string, MatrixCell>>();
    for (const d of office.days) {
      const m = new Map<string, MatrixCell>();
      for (const c of d.cells) m.set(normalizeTimeSlot(c.time_slot), c);
      cellByWeekday.set(d.weekday, m);
    }
    const operating = new Set(office.operating_weekdays);
    const closedByWeekday = new Map<number, boolean>();
    for (let w = 0; w < 7; w++) {
      const day = office.days.find((d) => d.weekday === w);
      closedByWeekday.set(w, day?.office_closed ?? !operating.has(w));
    }
    return { cellByWeekday, closedByWeekday };
  }, [office]);

  const closedFor = (w: number): boolean => closedByWeekday.get(w) ?? false;

  const cellH = 38;
  const headCellStyle: CSSProperties = {
    height: 34,
    display: 'grid',
    placeItems: 'center',
    fontFamily: 'var(--font-serif)',
    fontSize: 13,
    fontWeight: 700,
    background: CREAM,
    borderBottom: `1px solid ${LINE}`,
  };

  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `46px repeat(7, minmax(40px, 1fr))`,
          minWidth: 46 + 7 * 40,
          border: `1px solid ${LINE}`,
          borderRadius: 14,
          overflow: 'hidden',
          background: PANEL,
        }}
      >
        {/* ヘッダ行: 時刻列 + 月〜日 */}
        <div style={headCellStyle} />
        {DOW.map((d, w) => (
          <div key={w} style={{ ...headCellStyle, color: dowColor(w) }}>
            {d}
          </div>
        ))}

        {/* 本体: 時間帯ごと */}
        {slots.map((slot) => {
          const hhmm = normalizeTimeSlot(slot);
          return (
            <RowFragment key={slot}>
              <div
                style={{
                  height: cellH,
                  display: 'grid',
                  placeItems: 'center',
                  fontSize: 11,
                  color: INK2,
                  background: CREAM,
                  borderTop: `1px solid ${LINE}`,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {hhmm}
              </div>
              {DOW.map((_, w) => {
                const closed = closedFor(w);
                const cell = cellByWeekday.get(w)?.get(hhmm);
                const status = cell?.effective_status ?? 'unavailable';
                const m = STATUS_META[status];
                const overridden =
                  cell?.source === 'week_override' || cell?.source === 'manual_standing';
                // 空きコース数 (自動算出セルのみ)。
                const count =
                  cell?.source === 'auto'
                    ? status === 'available'
                      ? cell.metrics.available_course_count
                      : status === 'consult'
                        ? cell.metrics.consult_course_count
                        : 0
                    : 0;
                return (
                  <div
                    key={w}
                    style={{
                      height: cellH,
                      display: 'grid',
                      placeItems: 'center',
                      borderTop: `1px solid ${LINE}`,
                      borderLeft: `1px solid ${LINE}`,
                      background: closed ? '#F1ECE3' : m.bg,
                      position: 'relative',
                    }}
                  >
                    {closed ? (
                      <span style={{ fontSize: 10, color: INK3 }}>休</span>
                    ) : (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'baseline',
                          gap: 1,
                          color: m.col,
                          fontWeight: status === 'unavailable' ? 400 : 700,
                          lineHeight: 1,
                        }}
                      >
                        <span style={{ fontSize: 16 }}>{m.glyph}</span>
                        {count > 0 && (
                          <span style={{ fontSize: 10, fontWeight: 700 }}>{count}</span>
                        )}
                      </span>
                    )}
                    {!closed && overridden && (
                      <span
                        style={{
                          position: 'absolute',
                          top: 2,
                          right: 2,
                          width: 4,
                          height: 4,
                          borderRadius: 999,
                          background: cell?.source === 'week_override' ? TEAL : INK3,
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </RowFragment>
          );
        })}
      </div>
    </div>
  );
}

/** grid に直接子を並べるためのフラグメント (DOM ノードを作らない)。 */
function RowFragment({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function AcceptanceFieldView() {
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);
  const [officeId, setOfficeId] = useState<string | null>(null);

  const query = useAcceptanceMatrix({ isoYear, isoWeek, officeId: null });

  const goWeek = (d: number) => setWeekStart((w) => addDays(w, d * 7));
  const weekRange = `${format(weekStart, 'M/d', { locale: ja })} - ${format(addDays(weekStart, 6), 'M/d', { locale: ja })}`;

  const offices = query.data?.offices ?? [];
  const selected = offices.find((o) => o.office_id === officeId) ?? offices[0] ?? null;

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
      {/* らく助モバイル共通切替バー (R-8: 旧「現場ボードへ」戻りリンクを置換) */}
      <MobileSurfaceSwitcher />

      {/* ヘッダ帯: タイトル + 週送り + 拠点トグル */}
      <div style={{ flex: '0 0 auto', padding: '6px 8px 0' }}>
        <div
          style={{
            // らく助調 (2026-07-10 PO要望): 濃ピンク塗り→淡ピンク帯×深ピンク文字の上品トーンへ
            background: 'linear-gradient(135deg, #FBD9E2 0%, #F8B4C6 100%)',
            color: '#A6425F',
            padding: '11px 14px',
            borderRadius: 16,
            boxShadow: '0 5px 14px rgba(225,90,127,0.16)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* eslint-disable-next-line @next/next/no-img-element -- らく助マスコット (装飾) */}
              <img
                src="/brand/rakusuke-pose-heart.png"
                alt=""
                aria-hidden
                style={{ height: 34, width: 'auto', display: 'block' }}
              />
              <div>
                <div style={{ fontFamily: 'var(--font-serif)', fontSize: 16, fontWeight: 700 }}>
                  受け入れ枠
                </div>
                <div style={{ fontSize: 10, opacity: 0.85, marginTop: 2 }}>全コース統合の目安</div>
              </div>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 2,
                background: 'rgba(255,255,255,0.5)',
                borderRadius: 999,
                padding: 3,
              }}
            >
              <button onClick={() => goWeek(-1)} style={wkArrowBtn} aria-label="前の週">
                <ChevronLeft size={16} />
              </button>
              <div
                style={{
                  fontFamily: 'var(--font-serif)',
                  fontSize: 13,
                  fontWeight: 700,
                  minWidth: 78,
                  textAlign: 'center',
                  lineHeight: 1.1,
                }}
              >
                第{isoWeek}週
                <div
                  style={{
                    fontSize: 9,
                    opacity: 0.85,
                    fontFamily: 'var(--font-sans)',
                    fontWeight: 500,
                  }}
                >
                  {weekRange}
                </div>
              </div>
              <button onClick={() => goWeek(1)} style={wkArrowBtn} aria-label="次の週">
                <ChevronRight size={16} />
              </button>
            </div>
          </div>

          {/* 拠点トグル (2 拠点以上のとき) */}
          {offices.length > 1 && (
            <div
              style={{
                display: 'flex',
                gap: 5,
                background: 'rgba(255,255,255,0.16)',
                padding: 3,
                borderRadius: 999,
                marginTop: 12,
              }}
            >
              {offices.map((o) => {
                const on = (selected?.office_id ?? '') === o.office_id;
                return (
                  <button
                    key={o.office_id}
                    onClick={() => setOfficeId(o.office_id)}
                    style={{
                      flex: 1,
                      padding: '7px 10px',
                      borderRadius: 999,
                      fontSize: 13,
                      fontWeight: 700,
                      fontFamily: 'var(--font-serif)',
                      background: on ? '#fff' : 'transparent',
                      color: on ? TEAL_DEEP : 'inherit',
                      boxShadow: on ? '0 2px 6px rgba(0,0,0,0.12)' : 'none',
                    }}
                  >
                    {o.office_name}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 本体 (スクロール) */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 12px 24px' }}>
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
        {query.data && !selected && (
          <p style={{ fontSize: 13, color: INK2 }}>表示できる拠点がありません。</p>
        )}
        {query.data && selected && (
          <>
            {/* エリア帯 */}
            <div
              style={{
                // らく助調: 濃緑塗り→芽グリーンの淡緑帯×深緑文字 (上のピンク帯と同じ濃度感)
                background: 'linear-gradient(135deg, #E4F4E8 0%, #CBE9D3 100%)',
                borderRadius: 16,
                padding: '12px 16px',
                color: '#2F6B47',
                marginBottom: 14,
                boxShadow: '0 6px 16px rgba(120,200,136,0.2)',
              }}
            >
              <div style={{ fontSize: 10.5, opacity: 0.85, fontWeight: 600 }}>
                受け入れ可能枠 ・ 第{isoWeek}週（{weekRange}）現在
              </div>
              <div
                style={{
                  fontFamily: 'var(--font-serif)',
                  fontSize: 16,
                  fontWeight: 700,
                  marginTop: 3,
                }}
              >
                {selected.office_name}
                {selected.city_names.length > 0 && (
                  <span style={{ fontSize: 12, fontWeight: 600, opacity: 0.9 }}>
                    {' '}
                    ・ {selected.city_names.join('・')}
                  </span>
                )}
              </div>
            </div>

            {!selected.week_generated && (
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
                {selected.setup_state === 'no_manager'
                  ? '※ この拠点に業務ロール「マネージャー」のスタッフがいないため、空き枠を算出できません。スタッフマスタで設定してください。'
                  : selected.setup_state === 'assignment_pending'
                    ? '※ 「自動スタッフ割当」が未実行のため、空き枠を算出できません。スケジュール画面で実行してください。'
                    : '※ この週はまだコースが生成されていないため、空き枠を算出できません。スケジュール画面で「週を生成」を実行してください。'}
              </p>
            )}

            <OfficeMatrix office={selected} slots={query.data.slots} />
            <Legend />
          </>
        )}
      </div>
    </div>
  );
}
