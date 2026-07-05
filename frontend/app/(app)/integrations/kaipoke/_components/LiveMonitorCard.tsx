'use client';

/**
 * LiveMonitorCard — カイポケ RPA のライブモニター（noVNC 埋め込み）。
 *
 * 実ブラウザ（Playwright）の操作画面を noVNC で **ページ内に埋め込んで** 目視する
 * (K-3 Step 2)。novnc.kaipoke-api.net は carelink と同一サイト (kaipoke-api.net) の
 * ため Cloudflare Access の Cookie が iframe 内でも送られ、CSP frame-ancestors も
 * noVNC 側には無いので埋め込み可能。初回は Cloudflare ログインが要るため、空欄時は
 * 「別ウィンドウ」で一度ログインする導線を残す。
 */
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

import { LiveStatusDot } from './LiveStatusDot';

interface Props {
  monitorUrl: string | null | undefined;
  running: boolean;
  reachable: boolean;
  commandLabel: string | null;
}

export function LiveMonitorCard({ monitorUrl, running, reachable, commandLabel }: Props) {
  const [shown, setShown] = useState(true);

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
        <div className="relative bg-stone-950">
          <iframe
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
