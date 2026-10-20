'use client';

/**
 * /settings/google-drive — Google ドライブの接続 (勤務表のスプレッドシートを置く場所・管理者のみ)。
 *
 * 正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §7-3。
 *   GET    /api/v1/google-drive/status
 *   POST   /api/v1/google-drive/connect       → 許可の画面へ移る → /api/v1/google-drive/callback
 *                                               → この画面へ ?result=connected|error&reason=…
 *   POST   /api/v1/google-drive/picker-token  → Google Picker で保存先フォルダを選ぶ
 *   PUT    /api/v1/google-drive/folder
 *   DELETE /api/v1/google-drive/connection
 *
 * Google Cloud の準備 (.env の値) がまだのときは「未設定」として案内だけ出す。
 * 接続していなくても休みの確定・実績の記録はでき、勤務表だけ「未作成」で残る (§7-2 の 5)。
 */

import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { AlertTriangle, CheckCircle2, FolderOpen, HardDrive, Info } from 'lucide-react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { pickDriveFolder } from '@/lib/googlePicker';
import {
  googleDriveErrorMessage,
  useDisconnectGoogleDrive,
  useGoogleDrivePickerToken,
  useGoogleDriveStatus,
  useSaveGoogleDriveFolder,
  useStartGoogleDriveConnect,
} from '@/lib/queries/googleDrive';
import { GOOGLE_DRIVE_CALLBACK_REASON, type GoogleDriveStatus } from '@/lib/schemas/googleDrive';
import { isAdminRole } from '@/lib/rbac';

/** 許可の画面はここで始まる URL だけ (L-9)。 */
const GOOGLE_AUTH_ORIGIN = 'https://accounts.google.com/';

const STATUS_LABEL: Record<GoogleDriveStatus['status'], string> = {
  not_connected: '未接続',
  connected: '接続中',
  needs_reconnect: 'もう一度接続が要ります',
};

function formatJst(iso: string | null): string | null {
  if (!iso) return null;
  return new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(iso));
}

export default function GoogleDriveSettingsPage() {
  // useSearchParams は Suspense 境界が必須 (Next 15 の CSR bailout 対策)。
  return (
    <Suspense fallback={<Skeleton className="mx-auto h-80 w-full max-w-3xl" />}>
      <GoogleDriveSettings />
    </Suspense>
  );
}

function CallbackResult() {
  const params = useSearchParams();
  const result = params.get('result');
  if (result === 'connected') {
    return (
      <Alert data-testid="callback-connected">
        <CheckCircle2 className="h-4 w-4" />
        <AlertDescription>
          Google ドライブに接続しました。続けて、勤務表を置くフォルダを選んでください。
        </AlertDescription>
      </Alert>
    );
  }
  if (result === 'error') {
    const reason = params.get('reason') ?? '';
    return (
      <Alert variant="destructive" data-testid="callback-error">
        <AlertDescription>
          {GOOGLE_DRIVE_CALLBACK_REASON[reason] ?? 'Google ドライブに接続できませんでした。'}
        </AlertDescription>
      </Alert>
    );
  }
  return null;
}

function GoogleDriveSettings() {
  const { data: session, status: sessionStatus } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);
  const statusQuery = useGoogleDriveStatus(isAdmin);
  const connect = useStartGoogleDriveConnect();
  const pickerToken = useGoogleDrivePickerToken();
  const saveFolder = useSaveGoogleDriveFolder();
  const disconnect = useDisconnectGoogleDrive();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const [revokeFailed, setRevokeFailed] = useState(false);

  if (sessionStatus === 'loading') return null;

  const header = (
    <header className="space-y-1">
      <RakusukeTitle
        pose="think"
        title="Google ドライブ"
        subtitle="勤務表（Google スプレッドシート）を置く場所です。"
      />
    </header>
  );

  if (!isAdmin) {
    return (
      <section className="mx-auto w-full max-w-3xl space-y-6 pb-24">
        {header}
        <Alert>
          <AlertDescription>この設定は管理者だけが使えます。</AlertDescription>
        </Alert>
      </section>
    );
  }

  const handleConnect = async () => {
    try {
      const url = await connect.mutateAsync();
      // Google の許可の画面以外へは移らない (L-9)。
      if (!url.startsWith(GOOGLE_AUTH_ORIGIN)) {
        toast.error('接続を始められませんでした（許可の画面の URL が想定と違います）');
        return;
      }
      window.location.assign(url);
    } catch (e) {
      toast.error(googleDriveErrorMessage(e, '接続を始められませんでした'));
    }
  };

  const handlePickFolder = async () => {
    setPicking(true);
    try {
      const t = await pickerToken.mutateAsync();
      const picked = await pickDriveFolder({
        accessToken: t.access_token,
        apiKey: t.api_key,
        appId: t.app_id,
      });
      if (!picked) return;
      await saveFolder.mutateAsync(picked.id);
      toast.success(`保存先を「${picked.name ?? '選んだフォルダ'}」にしました`);
    } catch (e) {
      toast.error(googleDriveErrorMessage(e, 'フォルダを選べませんでした'));
    } finally {
      setPicking(false);
    }
  };

  const handleDisconnect = async () => {
    try {
      const result = await disconnect.mutateAsync();
      setRevokeFailed(result.revoke_failed);
      toast.success('Google ドライブを切断しました');
      setConfirmOpen(false);
    } catch (e) {
      toast.error(googleDriveErrorMessage(e, '切断できませんでした'));
    }
  };

  const data = statusQuery.data;

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6 pb-24">
      {header}
      <CallbackResult />

      {statusQuery.isError && (
        <Alert variant="destructive">
          <AlertDescription>状態を読み込めませんでした。再読み込みしてください。</AlertDescription>
        </Alert>
      )}

      {statusQuery.isLoading || !data ? (
        !statusQuery.isError && <Skeleton className="h-80 w-full" />
      ) : (
        <>
          {!data.configured && (
            <Alert data-testid="not-configured">
              <Info className="h-4 w-4" />
              <AlertTitle>Google ドライブの接続はまだ準備ができていません</AlertTitle>
              <AlertDescription className="space-y-1">
                <p>
                  Google Cloud での準備（プロジェクト・同意画面・リダイレクト先・API
                  キー）と、サーバーの設定（.env）が要ります。手順書
                  docs/deployment/google-drive-setup.md をご覧ください。
                </p>
                <p>
                  準備ができるまでも、休みの確定と実績の記録はできます。勤務表だけ「未作成」として残り、接続したあとに作れます。
                </p>
              </AlertDescription>
            </Alert>
          )}

          {revokeFailed && (
            <Alert variant="destructive" data-testid="revoke-failed">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Google 側の許可を取り消せませんでした</AlertTitle>
              <AlertDescription>
                らく助の接続は外しました。念のため、接続していた Google アカウントで「Google
                アカウント → セキュリティ →
                サードパーティ製のアプリとサービス」（https://myaccount.google.com/connections）を開き、らく助へのアクセスを外してください。
              </AlertDescription>
            </Alert>
          )}

          {data.status === 'needs_reconnect' && (
            <Alert variant="destructive" data-testid="needs-reconnect">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Google ドライブにもう一度接続してください</AlertTitle>
              <AlertDescription>
                許可が取り消された、または長いあいだ使わなかったため、接続が使えなくなりました。
                {data.last_error ? `（${data.last_error}）` : ''}
              </AlertDescription>
            </Alert>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <HardDrive className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                接続
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
                <dt className="text-text-muted">状態</dt>
                <dd className="font-medium text-text-primary" data-testid="drive-status">
                  {STATUS_LABEL[data.status]}
                </dd>
                {data.status !== 'not_connected' && (
                  <>
                    <dt className="text-text-muted">Google アカウント</dt>
                    <dd data-testid="drive-account">
                      {data.account_email
                        ? data.account_name
                          ? `${data.account_name}（${data.account_email}）`
                          : data.account_email
                        : '—'}
                    </dd>
                    <dt className="text-text-muted">接続した人</dt>
                    <dd>{data.connected_by_name ?? '—'}</dd>
                    <dt className="text-text-muted">最後の変更</dt>
                    <dd>{formatJst(data.updated_at) ?? '—'}</dd>
                  </>
                )}
              </dl>
              <div className="flex flex-wrap gap-3">
                {data.status !== 'connected' && (
                  <Button
                    type="button"
                    onClick={handleConnect}
                    disabled={!data.configured || connect.isPending}
                    data-testid="connect"
                  >
                    {data.status === 'needs_reconnect'
                      ? 'もう一度接続する'
                      : 'Google ドライブに接続'}
                  </Button>
                )}
                {data.status !== 'not_connected' && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setConfirmOpen(true)}
                    data-testid="disconnect"
                  >
                    切断する
                  </Button>
                )}
              </div>
              <p className="flex items-start gap-1.5 text-xs text-text-muted">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                らく助が触れるのは、らく助が作ったファイルと、ここで選んだフォルダだけです（Google
                の「drive.file」の許可）。
              </p>
            </CardContent>
          </Card>

          {data.status === 'connected' && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FolderOpen className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
                  保存先フォルダ
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm" data-testid="folder">
                  {data.folder_id
                    ? (data.folder_name ?? 'フォルダ名を読み込めませんでした')
                    : 'まだ選ばれていません'}
                  {data.shared_drive_id && (
                    <span className="ml-2 text-xs text-text-muted">（共有ドライブ）</span>
                  )}
                </p>
                {data.folder_missing && (
                  <p className="text-xs font-medium text-error" data-testid="folder-missing">
                    選んだフォルダがドライブに見つかりません。別のフォルダを選んでください。
                  </p>
                )}
                <Button
                  type="button"
                  variant="outline"
                  onClick={handlePickFolder}
                  disabled={!data.picker_configured || picking}
                  data-testid="pick-folder"
                >
                  {picking
                    ? '開いています…'
                    : data.folder_id
                      ? '別のフォルダを選ぶ'
                      : 'フォルダを選ぶ'}
                </Button>
                {!data.picker_configured && (
                  <p className="text-xs text-text-muted" data-testid="picker-not-configured">
                    フォルダを選ぶ画面（Google Picker）の API キーとアプリ ID
                    がまだ設定されていません。手順書をご覧ください。
                  </p>
                )}
                <p className="text-xs text-text-muted">
                  共有ドライブのフォルダをおすすめします（作った人が辞めても勤務表が残ります）。
                </p>
              </CardContent>
            </Card>
          )}
        </>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Google ドライブを切断しますか？</DialogTitle>
            <DialogDescription>
              Google
              側の許可も取り消します。作った勤務表のファイルはドライブに残ります。切断している間は、勤務表が「未作成」として残ります。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmOpen(false)}>
              やめる
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleDisconnect}
              disabled={disconnect.isPending}
              data-testid="disconnect-confirm"
            >
              切断する
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
