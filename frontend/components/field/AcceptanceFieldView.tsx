'use client';

/**
 * CareFlow Mobile — 受け入れ枠 (現場ボード /m 配下)。見るだけ。
 *
 * 正典: docs/plans/acceptance-strict-design-2026-10-20.md §5-2。
 * 既定の条件 (職員だけ・直近 4 週・休みは実際のとおり) の結果を出す。担い手・週数・
 * 休みの扱いの切り替えと取り直しは PC だけ。マスを押すと内訳を下に開く
 * (職員の権限では休みの理由は「休み」「半休」)。毎週の手での上書きは優先して出す。
 */

import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';

import { MobileSurfaceSwitcher } from '@/components/mobile/MobileSurfaceSwitcher';
import {
  DOW,
  cellDetail,
  cellKey,
  dayLabel,
  effectiveMark,
  jstLabel,
  officeNotice,
  overrideMap,
  rulesText,
  summarize,
  type Mark,
  type StrictPayload,
} from '@/lib/acceptanceStrict';
import { useStrictAcceptance } from '@/lib/queries/acceptance_strict';
import { useOffices } from '@/lib/queries/offices';

import { CF_THEME } from './theme';

const { TEAL_DEEP, INK, INK2, INK3, CREAM, LINE, PANEL } = CF_THEME;

const DEFAULT_WEEKS = 4;
const DEFAULT_MODE = 'real' as const;

const MARK_META: Record<Mark, { col: string; bg: string }> = {
  '○': { col: '#0E8472', bg: '#E7F4F0' },
  '△': { col: '#B5790A', bg: '#FBF1DD' },
  '×': { col: '#9A8E80', bg: 'transparent' },
  '－': { col: '#9A8E80', bg: '#F1ECE3' },
};

function dowColor(idx: number): string {
  if (idx === 5) return '#2F6FB0';
  if (idx === 6) return '#C75C77';
  return INK;
}

function StrictMatrix({ data }: { data: StrictPayload }) {
  const defaults = useMemo(
    () => new Set(data.staff.filter((s) => s.default_selected).map((s) => s.id)),
    [data.staff],
  );
  const summary = useMemo(
    () => summarize(data, defaults, DEFAULT_WEEKS, DEFAULT_MODE),
    [data, defaults],
  );
  const overrides = useMemo(() => overrideMap(data.overrides), [data.overrides]);
  const [sel, setSel] = useState<string | null>(null);
  const selected = sel ? summary.cells.get(sel) : undefined;
  const cols = summary.weekdays.length;

  const head: CSSProperties = {
    height: 34,
    display: 'grid',
    placeItems: 'center',
    fontSize: 14,
    fontWeight: 700,
    background: CREAM,
    borderBottom: `1px solid ${LINE}`,
  };

  return (
    <>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: `40px repeat(${cols}, minmax(0, 1fr))`,
          border: `1px solid ${LINE}`,
          borderRadius: 14,
          overflow: 'hidden',
          background: PANEL,
          maxWidth: '100%',
        }}
      >
        <div style={head} />
        {summary.weekdays.map((wd) => (
          <div key={wd} style={{ ...head, color: dowColor(wd) }}>
            {DOW[wd]}
          </div>
        ))}
        {summary.hours.map((h) => (
          <Row key={h}>
            <div
              style={{
                height: 40,
                display: 'grid',
                placeItems: 'center',
                fontSize: 12,
                color: INK2,
                background: CREAM,
                borderTop: `1px solid ${LINE}`,
              }}
            >
              {h}時
            </div>
            {summary.weekdays.map((wd) => {
              const key = cellKey(wd, h);
              const auto = summary.cells.get(key);
              if (!auto) return <div key={wd} />;
              const eff = effectiveMark(auto, overrides.get(key));
              const m = MARK_META[eff.mark];
              return (
                <button
                  key={wd}
                  type="button"
                  onClick={() => setSel(sel === key ? null : key)}
                  aria-pressed={sel === key}
                  aria-label={`${DOW[wd]}曜 ${h}時台 ${eff.mark} ${auto.counted} 週のうち ${auto.ok} 週空き`}
                  style={{
                    height: 40,
                    display: 'grid',
                    placeItems: 'center',
                    borderTop: `1px solid ${LINE}`,
                    borderLeft: `1px solid ${LINE}`,
                    background: m.bg,
                    color: m.col,
                    fontSize: 16,
                    fontWeight: eff.mark === '×' ? 400 : 700,
                    position: 'relative',
                    outline: sel === key ? `2px solid ${TEAL_DEEP}` : 'none',
                    outlineOffset: -2,
                  }}
                >
                  {eff.mark}
                  {eff.manual && (
                    <span
                      style={{
                        position: 'absolute',
                        top: 1,
                        right: 2,
                        fontSize: 12,
                        lineHeight: 1,
                        color: eff.conflict ? '#C75C77' : INK3,
                      }}
                    >
                      手
                    </span>
                  )}
                </button>
              );
            })}
          </Row>
        ))}
      </div>

      {selected && (
        <div
          aria-label="マスの内訳"
          style={{
            marginTop: 12,
            background: PANEL,
            border: `1px solid ${LINE}`,
            borderRadius: 14,
            padding: '12px 14px',
            fontSize: 14,
          }}
        >
          <div style={{ fontWeight: 700 }}>
            {DOW[selected.weekday]}曜 {selected.hour}時台 ・ {selected.counted} 週のうち{' '}
            {selected.ok} 週空き
          </div>
          {cellDetail(
            data,
            defaults,
            DEFAULT_WEEKS,
            DEFAULT_MODE,
            selected.weekday,
            selected.hour,
          ).map((w) => (
            <div
              key={w.date}
              style={{ borderTop: `1px solid ${LINE}`, marginTop: 8, paddingTop: 6 }}
            >
              <div style={{ fontWeight: 600 }}>{dayLabel(w.date)}</div>
              {w.skipReason ? (
                <div style={{ fontSize: 12, color: INK2 }}>{w.skipReason}</div>
              ) : (
                <div style={{ fontSize: 14 }}>
                  {w.free.length > 0
                    ? `入れる人: ${w.free.map((f) => f.name).join('・')}`
                    : 'この時間に入れる人なし'}
                  {w.off.length > 0 && (
                    <div style={{ fontSize: 12, color: '#A6425F' }}>
                      {w.off.map((f) => `${f.name}：${f.text}`).join('　')}
                    </div>
                  )}
                  {w.busy.length > 0 && (
                    <div style={{ fontSize: 12, color: INK2 }}>
                      {w.busy.map((f) => `${f.name}：${f.text}`).join('　')}
                    </div>
                  )}
                  {w.notes.map((n) => (
                    <div key={n} style={{ fontSize: 12, color: INK2 }}>
                      {n}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

export function AcceptanceFieldView() {
  const officesQuery = useOffices({ limit: 50 });
  const offices = officesQuery.allOffices;
  const [officeId, setOfficeId] = useState<string | null>(null);
  useEffect(() => {
    const first = offices[0];
    if (!officeId && first) setOfficeId(first.id);
  }, [officeId, offices]);
  const query = useStrictAcceptance(officeId);
  const data = query.data;

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
      <MobileSurfaceSwitcher />

      <div style={{ flex: '0 0 auto', padding: '6px 8px 0' }}>
        <div
          style={{
            background: 'linear-gradient(135deg, #FBD9E2 0%, #F8B4C6 100%)',
            color: '#A6425F',
            padding: '11px 14px',
            borderRadius: 16,
            boxShadow: '0 5px 14px rgba(225,90,127,0.16)',
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
            <div style={{ fontFamily: 'var(--font-serif)', fontSize: 16, fontWeight: 700 }}>
              受け入れ枠
            </div>
          </div>
          {offices.length > 1 && (
            <div
              style={{
                display: 'flex',
                gap: 5,
                background: 'rgba(255,255,255,0.16)',
                padding: 3,
                borderRadius: 999,
                marginTop: 10,
              }}
            >
              {offices.map((o) => {
                const on = officeId === o.id;
                return (
                  <button
                    key={o.id}
                    type="button"
                    onClick={() => setOfficeId(o.id)}
                    aria-pressed={on}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      padding: '7px 10px',
                      borderRadius: 999,
                      fontSize: 14,
                      fontWeight: 700,
                      background: on ? '#fff' : 'transparent',
                      color: on ? TEAL_DEEP : 'inherit',
                    }}
                  >
                    {o.name}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '12px 12px 24px' }}>
        {officeNotice(officesQuery) && (
          <p style={{ fontSize: 14, color: INK2, padding: '24px 0', textAlign: 'center' }}>
            {officeNotice(officesQuery)}
          </p>
        )}
        {query.isLoading && (
          <p style={{ fontSize: 14, color: INK2, padding: '24px 0', textAlign: 'center' }}>
            読み込み中…
          </p>
        )}
        {query.isError && (
          <p style={{ fontSize: 14, color: '#C75C77', padding: '24px 0', textAlign: 'center' }}>
            読み込みに失敗しました
          </p>
        )}
        {data && (
          <>
            <p style={{ fontSize: 14, color: INK, margin: '0 0 6px' }}>
              {rulesText(data.rules, DEFAULT_WEEKS).short}
            </p>
            <p style={{ fontSize: 12, color: INK2, margin: '0 0 12px' }}>
              カイポケから取った日時:{' '}
              {jstLabel(data.sources.oldest_fetched_at) || 'まだ取っていません'}
            </p>
            <StrictMatrix data={data} />
          </>
        )}
      </div>
    </div>
  );
}
