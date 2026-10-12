'use client';

/**
 * 画面の文脈(agent/AGENTS.md §11)と、コースの作業場での「右の列に収まる」(§11-2)。
 * - 発話ごとに {screen, ...ID} を中継へ添える。名前は入れない(ID・数・短い決まった言葉だけ)。
 * - 形の規則は agent/relay/screen_context.py と同じ。中継は形が違うと 400 で断るので、ここで先に整える。
 * - 道(URL)から分かる分はここで読み、週・日付などページの中にしか無い値はページが publish する。
 */
import { useCallback, useEffect, useRef } from 'react';
import { create } from 'zustand';

export type ScreenName =
  | 'schedule'
  | 'patients'
  | 'staff'
  | 'template_ws'
  | 'records'
  | 'monitor'
  | 'integrations'
  | 'dashboard'
  | 'special_week'
  | 'leave'
  | 'settings'
  | 'other';

export interface ScreenContext {
  screen: ScreenName;
  iso_year?: number;
  iso_week?: number;
  date?: string;
  patient_id?: string | number;
  staff_id?: string | number;
  draft_id?: string | number;
  tab?: string;
  direction?: 'send' | 'receive';
  period?: string;
}

const SCREENS: readonly ScreenName[] = [
  'schedule', 'patients', 'staff', 'template_ws', 'records', 'monitor',
  'integrations', 'dashboard', 'special_week', 'leave', 'settings', 'other',
];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const SLUG = /^[a-z][a-z0-9_]{0,23}$/;
const PERIOD = /^(?:\d{4}-\d{2}(?:-\d{2})?(?:\.\.\d{4}-\d{2}(?:-\d{2})?)?|[a-z][a-z0-9_]{0,15})$/;

function intIn(v: unknown, lo: number, hi: number): number | undefined {
  return typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi ? v : undefined;
}
function idOf(v: unknown): string | number | undefined {
  if (typeof v === 'number') return Number.isSafeInteger(v) && v > 0 ? v : undefined;
  return typeof v === 'string' && UUID.test(v) ? v.toLowerCase() : undefined;
}
function validDate(v: unknown): string | undefined {
  if (typeof v !== 'string' || !DATE.test(v)) return undefined;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v ? v : undefined;
}

/** 中継の規則に合う値だけ残す(合わない値は黙って落とす。発話そのものは必ず届ける)。 */
export function sanitizeContext(raw: Partial<ScreenContext> | null | undefined): ScreenContext | null {
  if (!raw || !raw.screen || !SCREENS.includes(raw.screen)) return null;
  const out: ScreenContext = { screen: raw.screen };
  const y = intIn(raw.iso_year, 2000, 2100);
  const w = intIn(raw.iso_week, 1, 53);
  if (y != null && w != null) {
    out.iso_year = y;
    out.iso_week = w;
  }
  const date = validDate(raw.date);
  if (date) out.date = date;
  for (const k of ['patient_id', 'staff_id', 'draft_id'] as const) {
    const id = idOf(raw[k]);
    if (id != null) out[k] = id;
  }
  if (typeof raw.tab === 'string' && SLUG.test(raw.tab)) out.tab = raw.tab;
  if (raw.direction === 'send' || raw.direction === 'receive') out.direction = raw.direction;
  if (typeof raw.period === 'string' && PERIOD.test(raw.period)) out.period = raw.period;
  return out;
}

/** 道(pathname + ?検索)から分かる文脈。ページが publish した値はこの上に重ねる。 */
export function contextFromLocation(pathname: string, search = ''): ScreenContext {
  const p = pathname.replace(/\/+$/, '') || '/';
  const q = new URLSearchParams(search);
  const seg = p.split('/').filter(Boolean);
  const id = (v: string | undefined) => (v && UUID.test(v) ? v : undefined);
  if (p === '/schedule/template' || p.startsWith('/schedule/template/')) return { screen: 'template_ws' };
  if (seg[0] === 'schedule') return { screen: 'schedule' };
  if (seg[0] === 'patients') return { screen: 'patients', patient_id: id(seg[1]) };
  if (seg[0] === 'staff') return { screen: 'staff', staff_id: id(seg[1]) };
  if (seg[0] === 'records') {
    return {
      screen: 'records',
      patient_id: id(q.get('patient') ?? undefined),
      staff_id: id(q.get('staff') ?? undefined),
    };
  }
  if (seg[0] === 'monitor') return { screen: 'monitor' };
  if (seg[0] === 'integrations') return { screen: 'integrations' };
  if (seg[0] === 'dashboard') return { screen: 'dashboard' };
  if (seg[0] === 'settings' || seg[0] === 'admin' || seg[0] === 'offices') return { screen: 'settings' };
  if (seg[0] === 'm' && seg[1] === 'leave') return { screen: 'leave' };
  if (seg[0] === 'm' && seg[1] === 'this-week') return { screen: 'schedule' };
  return { screen: 'other' };
}

interface ScreenState {
  /** ページ・ダイアログが足した値(週・日付・下書きなど)。後から足したもの(開いたダイアログ)が上 */
  pages: { id: number; ctx: Partial<ScreenContext> }[];
  /** コースの作業場の右の列(あればウィジェットがそこへ収まる) */
  dock: HTMLElement | null;
  /** 見出しの「一緒に見ているもの：コースの作業場・<下書き名>」に使う(画面だけ・中継へは送らない) */
  dockLabel: string | null;
  /** ウィジェットがいま右の列に収まっているか(列の幅を取るかどうかにページが使う) */
  docked: boolean;
  pushPage: (id: number, ctx: Partial<ScreenContext>) => void;
  dropPage: (id: number) => void;
  setDock: (el: HTMLElement | null) => void;
  setDockLabel: (label: string | null) => void;
  setDocked: (v: boolean) => void;
}

export const useScreenStore = create<ScreenState>((set) => ({
  pages: [],
  dock: null,
  dockLabel: null,
  docked: false,
  pushPage: (id, ctx) =>
    set((st) => ({
      pages: st.pages.some((e) => e.id === id)
        ? st.pages.map((e) => (e.id === id ? { id, ctx } : e))
        : [...st.pages, { id, ctx }],
    })),
  dropPage: (id) => set((st) => ({ pages: st.pages.filter((e) => e.id !== id) })),
  setDock: (dock) => set({ dock }),
  setDockLabel: (dockLabel) => set({ dockLabel }),
  setDocked: (docked) => set({ docked }),
}));

/** 送る時点の文脈: 道から読んだ分 + ページが足した分 → 整えたもの。 */
export function currentScreenContext(pathname: string, search = ''): ScreenContext | null {
  const base = contextFromLocation(pathname, search);
  const pages = useScreenStore.getState().pages;
  const page = pages[pages.length - 1]?.ctx;
  const merged = { ...base, ...(page ?? {}) } as Partial<ScreenContext>;
  if (page?.screen == null) merged.screen = base.screen;
  return sanitizeContext(merged);
}

/**
 * ページが自分の文脈(週・日付・タブなど)を足す。外れたら消す。
 * 例: usePublishScreenContext({ screen: 'schedule', iso_year, iso_week })
 */
let nextPageId = 1;
export function usePublishScreenContext(ctx: Partial<ScreenContext> | null) {
  const key = ctx ? JSON.stringify(ctx) : '';
  const id = useRef(0);
  if (!id.current) id.current = nextPageId++;
  // 値が変わっても重なりの順は保つ(開いたダイアログの下へ潜らない)。外れた時だけ消す
  useEffect(() => {
    const st = useScreenStore.getState();
    if (key) st.pushPage(id.current, JSON.parse(key) as Partial<ScreenContext>);
    else st.dropPage(id.current);
  }, [key]);
  useEffect(() => {
    const me = id.current;
    return () => useScreenStore.getState().dropPage(me);
  }, []);
}

/**
 * コースの作業場の右の列。返す ref をその列の要素に付ける。ウィジェットが無効なら何も起きない。
 * docked = ウィジェットがいま収まっているか(false の間は列を隠して盤面を広く使う)。
 */
export function useAgentDock(opts: { draftId?: string | null; label?: string | null }) {
  const { draftId, label } = opts;
  const docked = useScreenStore((s) => s.docked);
  usePublishScreenContext({ screen: 'template_ws', draft_id: draftId ?? undefined });
  const name = label ?? null;
  useEffect(() => {
    useScreenStore.getState().setDockLabel(name);
    return () => useScreenStore.getState().setDockLabel(null);
  }, [name]);
  const ref = useCallback((el: HTMLElement | null) => useScreenStore.getState().setDock(el), []);
  return { ref, docked };
}
