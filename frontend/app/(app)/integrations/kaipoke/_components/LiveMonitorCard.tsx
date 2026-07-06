'use client';

/**
 * LiveMonitorCard — カイポケ RPA のライブモニター（noVNC 埋め込み）。
 *
 * 実ブラウザ（Playwright）の操作画面を noVNC で **ページ内に埋め込んで** 目視する
 * (K-3 Step 2)。novnc.kaipoke-api.net は carelink と同一サイト (kaipoke-api.net) の
 * ため Cloudflare Access の Cookie が iframe 内でも送られ、CSP frame-ancestors も
 * noVNC 側には無いので埋め込み可能。初回は Cloudflare ログインが要るため、空欄時は
 * 「別ウィンドウ」で一度ログインする導線を残す。
 *
 * Cloudflare Access のセッション期限切れ検知 (2026-07-07):
 * 期限が切れると noVNC のサブリソースが Access ログインへ 302 → CORS で崩れ、
 * iframe が「壊れた表示」になる (現場で発生)。iframe 内は cross-origin で観測
 * できないため、同ホストの画像アセットを <img> で読む探針でセッションを判定する
 * (期限切れ = ログインHTMLが返り画像デコード失敗 → onerror)。切れている間は
 * 案内パネルに切り替え、別窓ログイン完了を 4 秒毎に待ち受けて自動で再接続する。
 */
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { LiveStatusDot } from './LiveStatusDot';

interface Props {
  monitorUrl: string | null | undefined;
  running: boolean;
  reachable: boolean;
  commandLabel: string | null;
}

/** Cloudflare Access セッションが有効かを noVNC の画像アセットで探針する。 */
function probeAccessSession(monitorUrl: string): Promise<boolean> {
  return new Promise((resolve) => {
    let probeUrl: string;
    try {
      // vnc.html 相対で app/images/ を解決 (キャッシュバスター付き)。
      const u = new URL('app/images/connect.svg', monitorUrl);
      u.searchParams.set('_', String(Date.now()));
      probeUrl = u.toString();
    } catch {
      resolve(true); // URL が組めない場合は判定不能 → 通常表示にフォールバック。
      return;
    }
    const img = new Image();
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = probeUrl;
  });
}

export function LiveMonitorCard({ monitorUrl, running, reachable, commandLabel }: Props) {
  const [shown, setShown] = useState(true);
  // null=判定中 (通常表示のまま) / true=有効 / false=期限切れ (案内パネル)。
  const [cfOk, setCfOk] = useState<boolean | null>(null);
  // 再ログイン完了後に iframe を作り直して再接続させるためのキー。
  const [iframeKey, setIframeKey] = useState(0);

  const tone = !reachable ? 'error' : running ? 'running' : 'idle';
  const statusLabel = !reachable
    ? '到達不可'
    : running
      ? `実行中${commandLabel ? ` — ${commandLabel}` : ''}`
      : '待機中';

  // noVNC 自動接続 + 画面スケール + 切断時の自動再接続。
  const embedUrl = monitorUrl ? `${monitorUrl}?autoconnect=true&resize=scale&reconnect=true` : null;

  const openWindow = () => {
    if (embedUrl) window.open(embedUrl, 'kaipoke-monitor', 'noopener,noreferrer');
  };

  // Access セッションの探針: 正常時は 60 秒毎、期限切れ検知中は 4 秒毎
  // (別窓ログインの完了を待ち受け、成功したら iframe を作り直して自動復帰)。
  useEffect(() => {
    if (!embedUrl || !shown) return;
    let cancelled = false;
    const run = async () => {
      const ok = await probeAccessSession(embedUrl);
      if (cancelled) return;
      if (ok && cfOk === false) setIframeKey((k) => k + 1);
      setCfOk(ok);
    };
    void run();
    const interval = window.setInterval(run, cfOk === false ? 4000 : 60000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [embedUrl, shown, cfOk]);

  return (
    <Card className="flex flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">ライブモニター</h2>
        <div className="flex items-center gap-3">
          <LiveStatusDot tone={tone} label={statusLabel} />
          <button
            type="button"
            onClick={() => setShown((v) => !v)}
            className="text-xs font-medium text-text-secondary hover:text-brand-primary"
          >
            {shown ? '隠す' : '表示'}
          </button>
        </div>
      </div>

      {shown && embedUrl ? (
        cfOk === false ? (
          <div className="flex aspect-video w-full flex-col items-center justify-center gap-3 bg-stone-950 px-6 text-center">
            <p className="text-sm font-medium text-white">
              Cloudflare ログインの有効期限が切れています
            </p>
            <p className="max-w-md text-xs leading-relaxed text-white/70">
              下のボタンから別ウィンドウでログイン（メール認証）してください。
              完了すると、この画面は自動で再接続します。
            </p>
            <Button variant="outline" onClick={openWindow}>
              別ウィンドウでログインする
            </Button>
          </div>
        ) : (
          <div className="relative bg-stone-950">
            <iframe
              key={iframeKey}
              src={embedUrl}
              title="カイポケ ライブモニター"
              className="block aspect-video w-full border-0"
              allow="clipboard-read; clipboard-write"
            />
            {running && (
              <span className="pointer-events-none absolute left-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-medium text-white/90 backdrop-blur">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-primary" />
                実行中
              </span>
            )}
          </div>
        )
      ) : (
        <div className="flex flex-1 items-center justify-center bg-stone-950 px-6 py-12 text-center">
          <div className="space-y-3">
            <p className="text-sm text-white/70">ライブ画面は非表示です</p>
            <Button variant="outline" onClick={() => setShown(true)} disabled={!embedUrl}>
              ライブ画面を表示
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 border-t border-border-subtle px-5 py-2.5">
        <p className="text-[11px] leading-relaxed text-text-muted">
          画面が空欄の場合は初回の Cloudflare ログインが必要です。
          <button
            type="button"
            onClick={openWindow}
            disabled={!embedUrl}
            className="ml-1 font-medium text-brand-primary hover:underline disabled:opacity-50"
          >
            別ウィンドウで開く
          </button>
          。監視のみ（画面から手操作しない）。
        </p>
      </div>
    </Card>
  );
}
