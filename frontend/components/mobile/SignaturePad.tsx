'use client';

/**
 * サインの画面 (全画面) — 退出のときに利用者さんにサインをもらう
 * (設計 `docs/plans/signature-checkin-design-2026-10-06.md` §3・§5-1、
 * モック `docs/mockups/signature-checkin-mock.html` の場面 2)。
 *
 * - スマホ・タブレットの縦横どれでも全画面。ボタンは下に固定。スマホ縦では
 *   「横向きにすると広く書けます」と案内する。
 * - 線は 0..1 の比率で持つ。回転・大きさが変わったらもう一度描く (高 DPI 対応)。
 * - 「サインして退出を記録」は何か書くまで押せない。「消してもう一度」で消す。
 * - 保存する画像は、書いた部分を切り出して 2:1 の白地の中央に置いた PNG
 *   (縮小しても読めるように・{@link exportSignaturePng})。
 * - 文字は 15px 以上、押す所は 48px 以上。署名した人は選ばない (PO 決定 Q4)。
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, RotateCw } from 'lucide-react';

import { jstHm } from '@/lib/format/actualTime';

/** 1 本の線 (0..1 の比率の点の並び)。 */
export type Stroke = Array<[number, number]>;

const INK = '#1c1917';
/** 「サインして退出を記録」を押せるようになる線の長さ (画面の px)。触れただけの点は除く。 */
export const MIN_PATH_PX = 24;

function drawStrokes(
  ctx: CanvasRenderingContext2D,
  strokes: Stroke[],
  w: number,
  h: number,
  lineWidth: number,
): void {
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = INK;
  ctx.fillStyle = INK;
  ctx.lineWidth = lineWidth;
  for (const s of strokes) {
    const first = s[0];
    if (!first) continue;
    if (s.length === 1) {
      ctx.beginPath();
      ctx.arc(first[0] * w, first[1] * h, lineWidth / 2, 0, Math.PI * 2);
      ctx.fill();
      continue;
    }
    ctx.beginPath();
    ctx.moveTo(first[0] * w, first[1] * h);
    for (let i = 1; i < s.length - 1; i++) {
      const p = s[i]!;
      const q = s[i + 1]!;
      ctx.quadraticCurveTo(p[0] * w, p[1] * h, ((p[0] + q[0]) / 2) * w, ((p[1] + q[1]) / 2) * h);
    }
    const last = s[s.length - 1]!;
    ctx.lineTo(last[0] * w, last[1] * h);
    ctx.stroke();
  }
}

/**
 * 書いた線を PNG にする。書いた部分を切り出し、800×400 の白地の中央に置く。
 * `aspect` は書いた欄の幅 / 高さ (線の比率を元の形に戻すため)。
 */
export function exportSignaturePng(strokes: Stroke[], aspect: number): Promise<Blob> {
  const W = 800;
  const H = 400;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);
    const points = strokes.flat();
    if (points.length > 0) {
      const sx = Math.max(aspect, 0.1) * 100;
      const sy = 100;
      let x0 = Infinity;
      let y0 = Infinity;
      let x1 = -Infinity;
      let y1 = -Infinity;
      for (const [a, b] of points) {
        x0 = Math.min(x0, a * sx);
        x1 = Math.max(x1, a * sx);
        y0 = Math.min(y0, b * sy);
        y1 = Math.max(y1, b * sy);
      }
      const bw = Math.max(x1 - x0, 8);
      const bh = Math.max(y1 - y0, 4);
      const k = Math.min((W * 0.86) / bw, (H * 0.8) / bh);
      ctx.save();
      ctx.translate((W - bw * k) / 2 - x0 * k, (H - bh * k) / 2 - y0 * k);
      drawStrokes(ctx, strokes, sx * k, sy * k, 7);
      ctx.restore();
    }
  }
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('サインの画像を作れませんでした'))),
      'image/png',
    );
  });
}

export interface SignaturePadProps {
  patientName: string;
  /** 例: 「2026 年 10 月 6 日（火） ・ 退出」。 */
  subtitle: string;
  /** 位置の判定の札 (取得中・一致・要確認・不一致)。 */
  statusChip?: ReactNode;
  /** 「サインして退出を記録」。PNG を渡す。 */
  onSave: (image: Blob) => void | Promise<void>;
  onCancel: () => void;
  /** 送信中 (二度押しさせない)。 */
  saving?: boolean;
  /** 保存できなかったときの案内 (画面は閉じずに、もう一度押してもらう)。 */
  notice?: string | null;
}

export function SignaturePad({
  patientName,
  subtitle,
  statusChip,
  onSave,
  onCancel,
  saving = false,
  notice = null,
}: SignaturePadProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const strokesRef = useRef<Stroke[]>([]);
  const currentRef = useRef<Stroke | null>(null);
  // 書いている指 (ペン) の pointerId。書いている間のほかの指 (手のひら) は無視する。
  const activePointerRef = useRef<number | null>(null);
  const lastClientRef = useRef<[number, number] | null>(null);
  // 書いた線の長さの合計 (画面の px)。触れただけ (点 1 つ) では記録を押せない。
  const pathLengthRef = useRef(0);
  const [inked, setInked] = useState(false);
  const [enough, setEnough] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [clock, setClock] = useState(() => jstHm(new Date().toISOString()) ?? '');

  useEffect(() => {
    const t = setInterval(() => setClock(jstHm(new Date().toISOString()) ?? ''), 10_000);
    return () => clearInterval(t);
  }, []);

  const lineWidth = useCallback((canvas: HTMLCanvasElement) => {
    return Math.max(3, Math.min(canvas.clientWidth, canvas.clientHeight * 2) / 220);
  }, []);

  /** 大きさに合わせてキャンバスを作り、線をもう一度描く (回転・リサイズ)。 */
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const dpr = typeof window !== 'undefined' ? Math.max(1, window.devicePixelRatio || 1) : 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    drawStrokes(ctx, strokesRef.current, w, h, lineWidth(canvas));
  }, [lineWidth]);

  useEffect(() => {
    redraw();
    const canvas = canvasRef.current;
    let ro: ResizeObserver | null = null;
    if (canvas && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => redraw());
      ro.observe(canvas);
    }
    window.addEventListener('resize', redraw);
    window.addEventListener('orientationchange', redraw);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', redraw);
      window.removeEventListener('orientationchange', redraw);
    };
  }, [redraw]);

  function pointOf(e: React.PointerEvent<HTMLCanvasElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect();
    const w = rect.width || 1;
    const h = rect.height || 1;
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / w));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / h));
    return [x, y];
  }

  function handlePointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (saving || exporting) return;
    // 書いている途中に触れたほかの指・手のひらは線にしない。
    if (activePointerRef.current !== null && activePointerRef.current !== e.pointerId) return;
    activePointerRef.current = e.pointerId;
    lastClientRef.current = [e.clientX, e.clientY];
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* jsdom など */
    }
    const stroke: Stroke = [pointOf(e)];
    currentRef.current = stroke;
    strokesRef.current.push(stroke);
    setInked(true);
    const canvas = e.currentTarget;
    const ctx = canvas.getContext('2d');
    if (ctx) drawStrokes(ctx, [stroke], canvas.clientWidth, canvas.clientHeight, lineWidth(canvas));
  }

  function handlePointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    const stroke = currentRef.current;
    if (!stroke || e.pointerId !== activePointerRef.current) return;
    const prev = stroke[stroke.length - 1]!;
    const next = pointOf(e);
    stroke.push(next);
    const last = lastClientRef.current;
    if (last) {
      pathLengthRef.current += Math.hypot(e.clientX - last[0], e.clientY - last[1]);
      if (pathLengthRef.current >= MIN_PATH_PX) setEnough(true);
    }
    lastClientRef.current = [e.clientX, e.clientY];
    const canvas = e.currentTarget;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    ctx.lineCap = 'round';
    ctx.strokeStyle = INK;
    ctx.lineWidth = lineWidth(canvas);
    ctx.beginPath();
    ctx.moveTo(prev[0] * w, prev[1] * h);
    ctx.lineTo(next[0] * w, next[1] * h);
    ctx.stroke();
  }

  function handlePointerEnd(e: React.PointerEvent<HTMLCanvasElement>) {
    if (e.pointerId !== activePointerRef.current) return;
    currentRef.current = null;
    activePointerRef.current = null;
    lastClientRef.current = null;
  }

  function clear() {
    strokesRef.current = [];
    currentRef.current = null;
    activePointerRef.current = null;
    lastClientRef.current = null;
    pathLengthRef.current = 0;
    setInked(false);
    setEnough(false);
    redraw();
  }

  async function save() {
    if (!enough || saving || exporting) return;
    const canvas = canvasRef.current;
    const aspect = canvas && canvas.clientHeight > 0 ? canvas.clientWidth / canvas.clientHeight : 2;
    setExporting(true);
    try {
      const blob = await exportSignaturePng(strokesRef.current, aspect);
      await onSave(blob);
    } finally {
      setExporting(false);
    }
  }

  const busy = saving || exporting;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="サインの画面"
      className="fixed inset-0 z-50 flex flex-col bg-bg-app text-text-primary"
      data-testid="signature-pad"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-default bg-bg-base px-4 pb-2.5 pt-[max(env(safe-area-inset-top),0.75rem)]">
        <div className="min-w-0 flex-1">
          <p className="truncate font-serif text-[17px] font-bold">{patientName}</p>
          <p className="text-[15px] text-text-secondary">{subtitle}</p>
        </div>
        {statusChip}
        <div className="text-right">
          <p className="tnum text-[20px] font-bold leading-tight">{clock}</p>
          <p className="text-[12px] text-text-secondary">押した時刻で記録</p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="min-h-12 min-w-[88px] rounded-full border border-border-strong bg-bg-base px-4 text-[15px] font-semibold text-text-secondary disabled:opacity-60"
        >
          やめる
        </button>
      </div>

      {/* スマホ縦だけ: 横向きにすると広く書けます。 */}
      <div
        className="hidden items-center justify-center gap-2 bg-info-bg px-4 py-2 text-[15px] text-info-strong portrait:max-sm:flex"
        data-testid="signature-rotate-hint"
      >
        <RotateCw className="h-4 w-4 shrink-0" aria-hidden="true" />
        横向きにすると広く書けます
      </div>

      <div className="relative m-3 flex-1 overflow-hidden rounded-xl border-2 border-dashed border-border-strong bg-white sm:m-4">
        {/* 基準線と × (書く位置の目安・画像には入らない)。 */}
        <div
          className="pointer-events-none absolute inset-x-[8%] bottom-[24%] border-b-2 border-border-default"
          aria-hidden="true"
        />
        <span
          className="pointer-events-none absolute bottom-[25%] left-[8%] text-[20px] text-text-muted"
          aria-hidden="true"
        >
          ×
        </span>
        {!inked && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-6 text-center text-[17px] text-text-muted">
            この枠の中にサインをお願いします
          </p>
        )}
        <canvas
          ref={canvasRef}
          aria-label="サイン欄"
          data-testid="signature-canvas"
          className="absolute inset-0 h-full w-full cursor-crosshair touch-none"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerEnd}
          onPointerCancel={handlePointerEnd}
          onPointerLeave={handlePointerEnd}
        />
      </div>

      <div className="border-t border-border-default bg-bg-base px-4 pb-[max(env(safe-area-inset-bottom),0.75rem)] pt-2.5">
        <p className="mb-2 text-[15px] text-text-secondary">
          「サインして退出を記録」を押した時刻と位置を記録します。サインの画像は 5 年保存します。
        </p>
        {notice && (
          <p
            role="alert"
            className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-[15px] font-semibold text-warning"
            data-testid="signature-notice"
          >
            {notice}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={clear}
            disabled={!inked || busy}
            className="min-h-12 flex-1 rounded-lg border border-border-strong bg-bg-base px-4 text-[15px] font-semibold text-text-primary disabled:opacity-50 sm:flex-none"
          >
            消してもう一度
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!enough || busy}
            className="inline-flex min-h-12 flex-[2] items-center justify-center gap-2 rounded-lg bg-brand-primary px-5 text-[16px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:ml-auto sm:flex-none"
          >
            <Check className="h-5 w-5" aria-hidden="true" />
            {busy ? '記録しています…' : 'サインして退出を記録'}
          </button>
        </div>
      </div>
    </div>
  );
}
