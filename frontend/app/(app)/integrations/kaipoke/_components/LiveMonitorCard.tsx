'use client';

/**
 * LiveMonitorCard — カイポケ RPA のライブモニター（noVNC）。
 *
 * 実ブラウザ（Playwright）の操作画面を noVNC で目視するための導線。
 * 画面は Cloudflare Access（管理者 OTP）＋別ウィンドウで開く。ページ内
 * iframe 埋め込みは第三者 Cookie 制約の解消後に対応予定（K-3 Step 2）。
 */
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

import { LiveStatusDot } from './LiveStatusDot';

interface Props {
  monitorUrl: string | null | undefined;
  running: boolean;
  reachable: boolean;
  commandLabel: string | null;
}

export function LiveMonitorCard({ monitorUrl, running, reachable, commandLabel }: Props) {
  const tone = !reachable ? 'error' : running ? 'running' : 'idle';
  const statusLabel = !reachable
    ? '到達不可'
    : running
      ? `実行中${commandLabel ? ` — ${commandLabel}` : ''}`
      : '待機中';

  const open = () => {
    if (monitorUrl) window.open(monitorUrl, 'kaipoke-monitor', 'noopener,noreferrer');
  };

  return (
    <Card className="flex flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-border-subtle px-5 py-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">ライブモニター</h2>
        <LiveStatusDot tone={tone} label={statusLabel} />
      </div>

      {/* モニタープレビュー面 — 実画面は別ウィンドウで開く */}
      <div className="relative flex flex-1 items-center justify-center bg-stone-950 px-6 py-10">
        <div className="pointer-events-none absolute inset-0 opacity-[0.07]">
          <div
            className="h-full w-full"
            style={{
              backgroundImage:
                'radial-gradient(circle at 1px 1px, var(--text-inverted) 1px, transparent 0)',
              backgroundSize: '18px 18px',
            }}
          />
        </div>
        <div className="relative flex flex-col items-center gap-4 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full border border-white/15 bg-white/5">
            {/* モニターアイコン */}
            <svg
              width="26"
              height="26"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-white/80"
              aria-hidden
            >
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <path d="M8 21h8M12 17v4" />
            </svg>
          </div>
          <div className="space-y-1">
            <p className="text-sm font-medium text-white/90">
              {running ? '実ブラウザを操作中です' : 'カイポケ実ブラウザのライブ画面'}
            </p>
            <p className="max-w-xs text-xs leading-relaxed text-white/55">
              {running
                ? '別ウィンドウで操作の様子をリアルタイムに確認できます。'
                : 'ジョブ実行中は、この画面で操作の様子を目視できます。'}
            </p>
          </div>
          <Button onClick={open} disabled={!monitorUrl} className="mt-1">
            ライブモニターを開く
          </Button>
        </div>
      </div>

      <p className="border-t border-border-subtle px-5 py-2.5 text-[11px] leading-relaxed text-text-muted">
        モニターは別ウィンドウで開きます（管理者ログインが必要）。監視のみで、画面からの手操作は行わないでください。
      </p>
    </Card>
  );
}
