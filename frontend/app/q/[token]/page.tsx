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
 *   - ログイン済み → `GET /visits/resolve-qr/{token}` で本日の visit 候補を解決。
 *     - 自分の担当 (`is_mine`) があれば `/m/today/{visitId}?qr={token}` へ replace。
 *       訪問詳細側は `?qr=` を受けてスキャン工程を省略する (プレビュー確認は必ず挟む)。
 *     - 担当候補ゼロ = **担当外** → 代行 / 予定外の選択画面を出す
 *       (QR 所持 = 現地に居る証明を認可の鍵とする・設計 §1 決定#1)。
 *   - 404 / 410 / 403 は案内のみ表示 (患者情報は出さない)。
 *   - 圏外 (通信断) は候補を出せないため、担当外はすべて**予定外扱いで退避**する
 *     (設計 §5)。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { CheckCircle2, Clock, Loader2, MapPin, QrCode, RefreshCw, UserPlus } from 'lucide-react';

import { ApiError } from '@/lib/api-client';
import { enqueuePending, type PendingPayload } from '@/lib/checkin-queue';
import { isServerUnreachable } from '@/lib/checkin-flush';
import { useCheckinFlush } from '@/lib/queries/checkinFlush';
import { coordsOf, geoErrorHint, getGeolocation, type GeoFix } from '@/lib/geo';
import { extractQrToken } from '@/lib/qr-token';
import { useAdhocCheckin } from '@/lib/queries/me';
import { pickQrCandidate, useResolveQr, type QrResolveCandidate } from '@/lib/queries/qrResolve';
import { useCheckinSettingsPublic } from '@/lib/queries/checkinSettings';
import { CHECKIN_PUBLIC_FALLBACK } from '@/lib/schemas/checkinSettings';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { toast } from '@/components/ui/sonner';

/** 予定外記録の確認フロー (自動送信はしない — 「記録する」は必ず本人が押す)。 */
type AdhocFlow =
  | { step: 'none' }
  | { step: 'locating' }
  | { step: 'preview'; geo: GeoFix }
  | { step: 'submitting' };

function shortTime(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}

function statusMeta(status: string): {
  label: string;
  variant: 'secondary' | 'success' | 'warning' | 'info';
} {
  switch (status) {
    case 'in_progress':
    case 'checked_in':
      return { label: '訪問中', variant: 'info' };
    case 'done':
    case 'completed':
    case 'checked_out':
      return { label: '完了', variant: 'success' };
    case 'cancelled':
      return { label: '取消', variant: 'secondary' };
    default:
      return { label: '未訪問', variant: 'warning' };
  }
}

function isOngoing(c: QrResolveCandidate): boolean {
  return c.status === 'in_progress' || c.status === 'checked_in';
}

function isFinished(c: QrResolveCandidate): boolean {
  // cancelled も「この枠には打刻させない」= 記録済みと同じ扱い (取消済みの予定に
  // 代行で乗せると、取消のはずの訪問が実績付きで復活してしまう)。
  return (
    c.status === 'done' ||
    c.status === 'completed' ||
    c.status === 'checked_out' ||
    c.status === 'cancelled'
  );
}

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
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  // パス断片は生トークンだが、コピペ等で URL 全体が入っても拾えるよう
  // extractQrToken で寛容に解析する (不正文字は null = 無効表示)。
  const token = useMemo(() => extractQrToken(params?.token ?? ''), [params?.token]);

  const { data, error, isPending, refetch } = useResolveQr(token);
  const adhocCheckin = useAdhocCheckin();

  // 予定外プレビューの距離しきい値 (管理者設定に追随・取得失敗は既定へ)。
  const { data: publicThresholds } = useCheckinSettingsPublic();
  const matchM = publicThresholds?.match_m ?? CHECKIN_PUBLIC_FALLBACK.match_m;

  const [flow, setFlow] = useState<AdhocFlow>({ step: 'none' });

  // 未送信の打刻 (前回圏外で退避した分) を、QR を読んだこの機会に再送する。
  // 破棄分の通知も共通フックが行う (「黙って捨てない」契約)。
  const { refreshPending } = useCheckinFlush();

  // 自分の担当候補が解決できたら訪問詳細へ replace (戻るでここに戻らない)。
  // StrictMode の二重実行や再レンダで多重 replace しないよう ref でガード。
  const navigatedRef = useRef(false);
  const picked = data ? pickQrCandidate(data.candidates) : null;
  useEffect(() => {
    if (!picked || !token || navigatedRef.current) return;
    navigatedRef.current = true;
    router.replace(`/m/today/${picked.visit_id}?qr=${encodeURIComponent(token)}`);
  }, [picked, token, router]);

  /** 予定外記録: GPS を取ってプレビューへ (POST はまだしない)。 */
  const beginAdhoc = useCallback(async () => {
    setFlow({ step: 'locating' });
    const geo = await getGeolocation();
    setFlow({ step: 'preview', geo });
  }, []);

  /** プレビューの「記録する」 — 単一 POST。圏外は予定外として退避する。 */
  async function recordAdhoc(geo: GeoFix) {
    if (!token || !staffId) {
      toast.error('ユーザー情報を取得できませんでした');
      return;
    }
    setFlow({ step: 'submitting' });
    const at = new Date().toISOString();
    const coords = coordsOf(geo);
    try {
      const visit = await adhocCheckin.mutateAsync({ qr_token: token, at, ...coords });
      setFlow({ step: 'none' });
      toast.success('予定外の訪問として到着を記録しました');
      // 生成された visit は自分の担当 — 以後は通常フロー (退出は再スキャン)。
      navigatedRef.current = true;
      router.replace(`/m/today/${visit.id}`);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : null;
      if (status === 410) {
        setFlow({ step: 'none' });
        toast.error('QRが更新されています', { description: '新しいQRをご利用ください' });
        return;
      }
      if (status === 404) {
        setFlow({ step: 'none' });
        toast.error('このQRは無効です', { description: '管理者にQRの再発行を依頼してください' });
        return;
      }
      if (isServerUnreachable(err)) {
        // 圏外 / 5xx — 記録を失わないよう予定外の到着として退避する。
        const payload: PendingPayload = { qr_token: token, at, ...coords };
        enqueuePending(staffId, { visit_id: '', kind: 'adhoc_arrival', payload });
        refreshPending();
        setFlow({ step: 'none' });
        toast.warning('未送信として保存しました', {
          description: '電波が戻り次第、自動で送信します',
        });
        navigatedRef.current = true;
        router.replace('/m/today');
        return;
      }
      setFlow({ step: 'none' });
      toast.error('記録に失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  // ---- 予定外の確認オーバーレイ (locating / preview / submitting) ----------
  if (flow.step !== 'none') {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-muted/30 p-6">
        {flow.step === 'preview' ? (
          <AdhocPreviewCard
            patientName={data?.patient_name ?? ''}
            geo={flow.geo}
            matchM={matchM}
            submitting={adhocCheckin.isPending}
            onRecord={() => void recordAdhoc(flow.geo)}
            onRelocate={() => void beginAdhoc()}
            onCancel={() => setFlow({ step: 'none' })}
          />
        ) : (
          <div className="flex flex-col items-center gap-3 text-muted-foreground" aria-busy>
            <Loader2 className="h-8 w-8 animate-spin" aria-hidden />
            <p className="text-sm">
              {flow.step === 'locating' ? '位置情報を取得しています…' : '記録しています…'}
            </p>
          </div>
        )}
      </main>
    );
  }

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
      // 通信断 (圏外) — 候補を出せないので、記録したい場合は予定外として退避する。
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
          <Button variant="outline" className="w-full" onClick={() => void beginAdhoc()}>
            <UserPlus className="h-4 w-4" />
            予定外の訪問として記録
          </Button>
          <p className="text-xs text-muted-foreground">
            電波が戻り次第、自動で送信します。予定の訪問だった場合も記録は残ります。
          </p>
          <Button asChild variant="ghost" className="w-full">
            <Link href="/m/today" replace>
              本日の訪問へ
            </Link>
          </Button>
        </Card>
      );
    }
  } else if (data && !picked && !data.patient_name) {
    // `patient_name` の有無が resolve v2 のマーカー。無い = 旧 BE (第1弾) なので、
    // 担当外の候補も adhoc-checkin もまだ存在しない。FE 先行デプロイで「代行/予定外」
    // を出すと、押した先が 404 になり現場を混乱させるため第1弾の案内へ退避する。
    body = (
      <GuideCard
        title="本日の担当訪問が見つかりません"
        description="このQRの利用者は、本日のあなたの担当訪問にありません。「本日の訪問」からご確認ください。"
      />
    );
  } else if (data && !picked) {
    // 担当外 — 代行 / 予定外を本人に選ばせる (自動マッチはしない・決定#2)。
    body = (
      <SubstituteChoice
        patientName={data.patient_name}
        candidates={data.candidates}
        onPick={(visitId) => {
          navigatedRef.current = true;
          router.replace(`/m/today/${visitId}?qr=${encodeURIComponent(token)}`);
        }}
        onAdhoc={() => void beginAdhoc()}
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

// ---------------------------------------------------------------------------
// 担当外の選択画面 — 当日予定を並べて「代行」か「予定外」かを本人が選ぶ。
// ---------------------------------------------------------------------------
interface SubstituteChoiceProps {
  patientName: string;
  candidates: QrResolveCandidate[];
  onPick: (visitId: string) => void;
  onAdhoc: () => void;
}

function SubstituteChoice({ patientName, candidates, onPick, onAdhoc }: SubstituteChoiceProps) {
  return (
    <Card className="w-full max-w-sm space-y-4 p-5" data-testid="qr-substitute-choice">
      <div className="space-y-1 text-center">
        <p className="text-xs text-muted-foreground">担当外の利用者です</p>
        <p className="font-serif text-lg font-bold">{patientName || '(利用者名未取得)'}</p>
        <p className="text-sm text-muted-foreground">
          この訪問をどう記録するか選んでください。予定の担当者は変わりません。
        </p>
      </div>

      {candidates.length === 0 ? (
        <p className="rounded-md bg-bg-muted px-3 py-2 text-xs text-muted-foreground">
          本日の予定はありません。下のボタンで予定外の訪問として記録できます。
        </p>
      ) : (
        <ul className="space-y-2">
          {candidates.map((c) => {
            const meta = statusMeta(c.status);
            const ongoing = isOngoing(c);
            const finished = isFinished(c);
            return (
              <li
                key={c.visit_id}
                className="space-y-2 rounded-lg border border-border-default p-3"
                data-testid="qr-candidate"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold tnum">
                    <Clock className="h-4 w-4 shrink-0 text-text-muted" />
                    {shortTime(c.start_time)} - {shortTime(c.end_time)}
                  </span>
                  <Badge variant={meta.variant}>{meta.label}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {c.is_unplanned
                    ? `予定外の訪問（記録者: ${c.planned_staff_name ?? '不明'}）`
                    : `予定の担当: ${c.planned_staff_name ?? '未割当'}`}
                </p>
                {finished ? (
                  <p className="text-xs text-muted-foreground">
                    {c.status === 'cancelled'
                      ? 'この訪問は取り消されています。'
                      : 'この訪問は記録済みです。'}
                  </p>
                ) : (
                  <Button className="w-full" onClick={() => onPick(c.visit_id)}>
                    {ongoing ? '退出の記録へ' : 'この予定の代行として記録'}
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Button variant="outline" className="w-full" onClick={onAdhoc}>
        <UserPlus className="h-4 w-4" />
        予定外の訪問として記録
      </Button>
      <Button asChild variant="ghost" className="w-full">
        <Link href="/m/today" replace>
          本日の訪問へ
        </Link>
      </Button>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 予定外の確認カード — 記録する前に位置を確認させる (単一 POST の起点)。
//
// resolve API は患者の座標を返さない (氏名のみ開示・設計 §4-1) ため、ここでは
// 距離そのものは出せない。取得した測位の状態と「一致とみなす距離」を示し、
// 距離判定はサーバの判定に委ねる (打刻後は訪問詳細に反映される)。
// ---------------------------------------------------------------------------
interface AdhocPreviewCardProps {
  patientName: string;
  geo: GeoFix;
  matchM: number;
  /** POST 実行中 — 二度押しで visit を 2 件作らせない。 */
  submitting: boolean;
  onRecord: () => void;
  onRelocate: () => void;
  onCancel: () => void;
}

function AdhocPreviewCard({
  patientName,
  geo,
  matchM,
  submitting,
  onRecord,
  onRelocate,
  onCancel,
}: AdhocPreviewCardProps) {
  const located = geo.lat !== undefined && geo.lng !== undefined;
  return (
    <Card className="w-full max-w-sm space-y-3 p-5" data-testid="qr-adhoc-preview">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="h-5 w-5 text-brand-primary" />
        <p className="font-semibold text-text-primary">予定外の訪問として記録</p>
      </div>
      <p className="text-sm text-text-secondary">
        {patientName || '(利用者名未取得)'} さんの訪問を、予定外の記録として残します。
      </p>

      <div
        className={
          'flex items-center justify-between rounded-lg px-3 py-2 text-sm font-semibold ' +
          (located ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning')
        }
      >
        <span className="inline-flex items-center gap-1.5">
          <MapPin className="h-4 w-4" />
          {located ? '位置情報を取得しました' : '測位不良'}
        </span>
        <span className="tnum">
          {located && geo.accuracy !== undefined ? `±${Math.round(geo.accuracy)}m` : '—'}
        </span>
      </div>

      <p className="text-xs text-text-muted">
        {located
          ? `登録住所から${matchM}m以内なら「一致」として記録されます。判定は記録後に確認できます。`
          : '位置情報を取得できませんでした。このまま記録できます。'}
      </p>
      {!located && geoErrorHint(geo.errorCode) && (
        <p className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
          {geoErrorHint(geo.errorCode)}
        </p>
      )}

      <Button className="w-full" disabled={submitting} onClick={onRecord}>
        {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
        予定外の訪問として記録する
      </Button>
      <Button variant="ghost" className="w-full" disabled={submitting} onClick={onRelocate}>
        <RefreshCw className="h-4 w-4" />
        位置を再取得
      </Button>
      <Button variant="ghost" className="w-full" onClick={onCancel}>
        戻る
      </Button>
    </Card>
  );
}
