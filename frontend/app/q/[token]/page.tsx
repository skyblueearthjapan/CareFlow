'use client';

/**
 * `/q/{token}` — 患者宅 QR のランディングページ (汎用カメラ・ディープリンク)。
 *
 * 患者宅の固定 QR の中身は `https://<app>/q/{token}` (設計 Phase 5)。アプリ内
 * スキャナはトークンだけ抜くのでこの URL を開かないが、スマホの標準カメラや
 * Chrome の QR 読取はここへ実際に遷移する (従来はルート未実装で 404 だった)。
 *
 * 挙動:
 *   - 未ログイン → middleware が `/login?callbackUrl=/q/{token}` へ誘導 (既存機構)。
 *   - ログイン済み → `GET /visits/resolve-qr/{token}` で本日の担当 visit を解決し、
 *     `/m/today/{visitId}?qr={token}` へ replace。訪問詳細側は `?qr=` を受けて
 *     スキャン工程を省略する (プレビュー確認は必ず挟む)。
 *   - 候補ゼロ / 404 / 410 は案内のみ表示。**患者情報は一切出さない**。
 */

import { useEffect, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Loader2, QrCode } from 'lucide-react';

import { ApiError } from '@/lib/api-client';
import { extractQrToken } from '@/lib/qr-token';
import { pickQrCandidate, useResolveQr } from '@/lib/queries/qrResolve';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

/** 案内カード (エラー / 候補なし共通の器)。 */
function GuideCard({ title, description }: { title: string; description: string }) {
  return (
    <Card className="w-full max-w-sm space-y-4 p-6 text-center">
      <QrCode className="mx-auto h-10 w-10 text-muted-foreground" aria-hidden />
      <div className="space-y-1">
        <p className="text-base font-semibold">{title}</p>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <Button asChild className="w-full">
        <Link href="/m/today" replace>
          本日の訪問へ
        </Link>
      </Button>
    </Card>
  );
}

export default function QrLandingPage() {
  const params = useParams<{ token: string }>();
  const router = useRouter();
  // パス断片は生トークンだが、コピペ等で URL 全体が入っても拾えるよう
  // extractQrToken で寛容に解析する (不正文字は null = 無効表示)。
  const token = useMemo(() => extractQrToken(params?.token ?? ''), [params?.token]);

  const { data, error, isPending, refetch } = useResolveQr(token);

  // 候補が解決できたら訪問詳細へ replace (戻るでここに戻らない)。
  // StrictMode の二重実行や再レンダで多重 replace しないよう ref でガード。
  const navigatedRef = useRef(false);
  const picked = data ? pickQrCandidate(data.candidates) : null;
  useEffect(() => {
    if (!picked || !token || navigatedRef.current) return;
    navigatedRef.current = true;
    router.replace(`/m/today/${picked.visit_id}?qr=${encodeURIComponent(token)}`);
  }, [picked, token, router]);

  let body: React.ReactNode;
  if (!token) {
    body = (
      <GuideCard
        title="このQRは読み取れません"
        description="らく助の患者宅QRではないようです。正しいQRをご利用ください。"
      />
    );
  } else if (error) {
    const status = error instanceof ApiError ? error.status : null;
    if (status === 410) {
      body = (
        <GuideCard
          title="QRが更新されています"
          description="このQRは再発行により無効になりました。新しいQRをご利用ください。"
        />
      );
    } else if (status === 404) {
      body = (
        <GuideCard
          title="このQRは無効です"
          description="登録が見つかりませんでした。管理者にQRの再発行を依頼してください。"
        />
      );
    } else if (status === 403) {
      body = (
        <GuideCard
          title="このアカウントでは打刻できません"
          description="スタッフに紐付いたアカウントでログインし直してください。"
        />
      );
    } else {
      body = (
        <Card className="w-full max-w-sm space-y-4 p-6 text-center">
          <QrCode className="mx-auto h-10 w-10 text-muted-foreground" aria-hidden />
          <div className="space-y-1">
            <p className="text-base font-semibold">読み込みに失敗しました</p>
            <p className="text-sm text-muted-foreground">
              通信状態をご確認のうえ、もう一度お試しください。
            </p>
          </div>
          <Button className="w-full" onClick={() => void refetch()}>
            再試行
          </Button>
          <Button asChild variant="outline" className="w-full">
            <Link href="/m/today" replace>
              本日の訪問へ
            </Link>
          </Button>
        </Card>
      );
    }
  } else if (data && !picked) {
    body = (
      <GuideCard
        title="本日の担当訪問が見つかりません"
        description="このQRの利用者は、本日のあなたの担当訪問にありません。「本日の訪問」からご確認ください。"
      />
    );
  } else {
    // isPending (セッション確立待ち含む) or 遷移中。
    body = (
      <div className="flex flex-col items-center gap-3 text-muted-foreground" aria-busy={isPending}>
        <Loader2 className="h-8 w-8 animate-spin" aria-hidden />
        <p className="text-sm">訪問情報を確認しています…</p>
      </div>
    );
  }

  return <main className="flex min-h-dvh items-center justify-center bg-muted/30 p-6">{body}</main>;
}
