'use client';

/**
 * カイポケ連携 — CareFlow の週次スケジュールをカイポケへ反映する統合画面。
 *
 * 構成:
 *   - 操作コンソール (KaipokeConsole): ライブモニター + 稼働状況(圧縮) + 週次反映/取り込みの操作
 *     + 下段タブ切替の大きなカレンダー枠
 *   - 実行ログ / 直近結果 / ジョブ履歴 / Geocoding 導線
 */
import Link from 'next/link';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useKaipokeLive, useKaipokeCredentials, useStopJob } from '@/lib/queries/integrations';

import { KaipokeJobsList } from '../_components/KaipokeJobsList';
import { IntegrationSettingsMenu } from './_components/IntegrationSettingsMenu';
import { ExecutionLogViewer } from './_components/ExecutionLogViewer';
import { JobResultCard } from './_components/JobResultCard';
import { KaipokeConsole } from './_components/KaipokeConsole';

export default function KaipokeIntegrationPage() {
  const { data: session } = useSession();
  const isAdmin = session?.user?.role === 'admin';

  const liveQuery = useKaipokeLive();
  const live = liveQuery.data;
  const stop = useStopJob();
  const credQuery = useKaipokeCredentials();
  // 読込中 (data 未着) は true 扱いにして「未設定」バナーの誤フラッシュを防ぐ
  // (明示的に configured=false と判った時だけガードを出す。BE 側は常に安全)。
  const credentialsConfigured = credQuery.data ? credQuery.data.configured : true;

  const running = Boolean(live?.running);
  const reachable = live?.reachable ?? true;
  const latestJob = live?.latestJob ?? null;
  const finishedJob =
    latestJob && !running && ['completed', 'failed', 'cancelled'].includes(latestJob.status)
      ? latestJob
      : null;

  if (!isAdmin) {
    return (
      <section className="space-y-6">
        <Header />
        <Alert>
          <AlertTitle>管理者専用</AlertTitle>
          <AlertDescription>カイポケ連携の操作は管理者のみ利用できます。</AlertDescription>
        </Alert>
      </section>
    );
  }

  // 接続設定はダイアログの中に隠したため、未設定のときはページ側で気づけるようにする。
  const credentialsMissing = credQuery.isSuccess && !credentialsConfigured;

  return (
    <section className="space-y-6">
      {/* 右上に「設定」→「接続設定」を格納 (PO要望: 認証情報を表側に出さない)。 */}
      <div className="flex items-start justify-between gap-4">
        <Header />
        <IntegrationSettingsMenu needsAttention={credentialsMissing} />
      </div>

      {credentialsMissing && (
        <Alert variant="warning">
          <AlertTitle>接続設定が未設定です</AlertTitle>
          <AlertDescription>
            右上の「設定」→「接続設定」から、カイポケのログイン情報を登録してください。
          </AlertDescription>
        </Alert>
      )}

      {/* 操作コンソール（ライブモニター + 稼働状況 + 週次反映/取り込み操作 + タブ式カレンダー） */}
      <KaipokeConsole
        live={live}
        liveLoading={liveQuery.isLoading}
        running={running}
        reachable={reachable}
        latestJob={latestJob}
        credentialsConfigured={credentialsConfigured}
        stop={stop}
      />

      {/* 実行ログ */}
      {live && live.logs.length > 0 && <ExecutionLogViewer lines={live.logs} />}

      {/* 直近の実行結果（完了時） */}
      {finishedJob && <JobResultCard job={finishedJob} />}

      {/* ジョブ履歴 */}
      <KaipokeJobsList />

      {/* 管理ユーティリティへの導線 */}
      <p className="text-xs text-text-muted">
        Geocoding キャッシュは{' '}
        <Link className="text-brand-primary hover:underline" href="/integrations">
          連携ユーティリティ
        </Link>{' '}
        から。
      </p>
    </section>
  );
}

function Header() {
  return (
    <header>
      <h1 className="font-serif text-2xl font-bold text-text-primary">カイポケ連携</h1>
      <p className="mt-1 text-sm text-text-secondary">
        カイポケの実ブラウザをライブで見守りながら、CareFlow
        の週次スケジュールをカイポケへ反映します。
      </p>
    </header>
  );
}
