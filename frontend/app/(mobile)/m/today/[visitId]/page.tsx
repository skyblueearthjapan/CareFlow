'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  Camera,
  CheckCircle2,
  Clock,
  Home,
  Loader2,
  MapPin,
  Phone,
  QrCode,
  RefreshCw,
  StickyNote,
} from 'lucide-react';

import { ApiError } from '@/lib/api-client';
import { clearCheckin, loadCheckin, saveCheckin } from '@/lib/checkin-storage';
import {
  countPending,
  enqueuePending,
  type PendingKind,
  type PendingPayload,
} from '@/lib/checkin-queue';
import { detailOf, flushCheckinQueue, isServerUnreachable } from '@/lib/checkin-flush';
import {
  coordsOf,
  geoErrorHint,
  getGeolocation,
  haversineMeters,
  type GeoFix as Geo,
} from '@/lib/geo';
import { extractQrToken } from '@/lib/qr-token';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/sonner';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { MobileSection } from '@/components/mobile/MobileSection';
import { Rakusuke } from '@/components/brand/Rakusuke';
import { QrScanner } from '@/components/mobile/QrScanner';
import { AuthedPhoto } from '@/components/mobile/AuthedPhoto';
import { displayVisitNote } from '@/lib/visit-note';
import {
  useCheckIn,
  useCheckOut,
  useMyVisit,
  useNoShow,
  type CheckInPayload,
  type CheckinMatchStatus,
  type MyVisit,
} from '@/lib/queries/me';
import { useUploadPhoto, useVisitPhotos } from '@/lib/queries/visit-photos';
import { useCheckinSettingsPublic } from '@/lib/queries/checkinSettings';
import { CHECKIN_PUBLIC_FALLBACK } from '@/lib/schemas/checkinSettings';

type ScanMode = 'arrival' | 'departure';

/**
 * Transient overlay flow layered on top of the base visit-detail view.
 *
 * クロスレビュー #5: スキャン直後に即 POST せず、`locating`(GPS取得) →
 * `preview`(クライアント距離プレビュー + 不一致なら理由入力) を経て、ユーザーが
 * 「記録する」を押したときに **1 回だけ** POST する。これで途中離脱しても記録が
 * 残らず、旧実装の override 再送 (2 回目 POST) も無くなる。
 */
type Flow =
  | { step: 'none' }
  | { step: 'scanning'; mode: ScanMode }
  | { step: 'locating'; mode: ScanMode }
  | {
      step: 'preview';
      mode: ScanMode;
      token: string | undefined;
      geo: Geo;
      distance: number | null;
      status: CheckinMatchStatus;
    }
  | { step: 'submitting'; mode: ScanMode }
  | { step: 'noshow' };

/** Quick-pick chips for the no-show reason. */
const NOSHOW_CHIPS = ['不在（応答なし）', '本人都合キャンセル', '入院／受診', '家族都合'] as const;

function shortTime(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}

function statusLabel(status: string): {
  label: string;
  variant: 'default' | 'secondary' | 'success' | 'warning' | 'info';
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

/**
 * Position status → display metadata. Used both for the client-side preview
 * (記録前) and to paint the server's authoritative verdict.
 *
 * しきい値 (matchM/reviewM) は `GET /api/v1/checkin-settings/public` から取得した
 * 値を渡す (管理者が Phase 4 で変更したしきい値にラベルも追随させる)。
 */
function matchInfo(
  s: CheckinMatchStatus,
  matchM: number,
  reviewM: number,
): {
  label: string;
  variant: 'success' | 'warning' | 'destructive';
  hint: string;
} {
  switch (s) {
    case 'match':
      return { label: '登録住所と一致', variant: 'success', hint: '位置を確認しました。' };
    case 'review':
      return {
        label: `要確認（${matchM}〜${reviewM}m）`,
        variant: 'warning',
        hint: '登録住所からやや離れています。',
      };
    case 'mismatch':
      return {
        label: `登録住所と不一致（${reviewM}m超）`,
        variant: 'destructive',
        hint: '別の場所で測位された可能性があります。',
      };
    case 'no_gps':
    default:
      return {
        label: '測位不良',
        variant: 'warning',
        hint: '位置情報を取得できませんでした。このまま記録できます。',
      };
  }
}

/** Client-side position verdict from a previewed distance (server is authoritative). */
function previewStatusOf(
  distance: number | null,
  matchM: number,
  reviewM: number,
): CheckinMatchStatus {
  if (distance === null) return 'no_gps';
  if (distance <= matchM) return 'match';
  if (distance <= reviewM) return 'review';
  return 'mismatch';
}

function distanceLabel(d: number | null): string {
  return d === null ? '—' : `${Math.round(d)}m`;
}

function fmtElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function isCheckedIn(visit: MyVisit | undefined): boolean {
  if (!visit) return false;
  return visit.status === 'in_progress' || visit.status === 'checked_in';
}

function isCompleted(visit: MyVisit | undefined): boolean {
  if (!visit) return false;
  return visit.status === 'done' || visit.status === 'completed' || visit.status === 'checked_out';
}

function memoKey(staffId: string, visitId: string): string {
  return `visit-memo:${staffId}:${visitId}`;
}

export default function MobileVisitDetailPage() {
  // useSearchParams (?qr= ディープリンク) は Suspense 境界が必須
  // (Next 15 の CSR bailout 対策 — qr-print ページと同パターン)。
  return (
    <Suspense
      fallback={
        <div className="space-y-3 p-4">
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-40 w-full" />
        </div>
      }
    >
      <MobileVisitDetailPageInner />
    </Suspense>
  );
}

function MobileVisitDetailPageInner() {
  const params = useParams<{ visitId: string }>();
  const visitId = params?.visitId ?? '';
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  // ディープリンク (/q/{token} → ?qr=) 由来の QR トークン。
  //   - `readToken`     … 詳細 GET の担当外フォールバック用 (画面を離れるまで保持・
  //                        URL には残さない)。担当外 visit は通常 GET が 404 のため、
  //                        記録後の再取得でも同じ鍵が要る。
  //   - `deepLinkToken` … **打刻用**。1 記録で消費し、退出は現地で読み直させる
  //                        (設計 §4-2 の「代行の退出は再スキャン必須」)。
  const [readToken] = useState<string | null>(() => {
    const raw = searchParams?.get('qr');
    return raw ? extractQrToken(raw) : null;
  });
  const [deepLinkToken, setDeepLinkToken] = useState<string | null>(readToken);

  const { data: visit, isLoading, isError, error } = useMyVisit(visitId, readToken);

  const checkIn = useCheckIn(visitId);
  const checkOut = useCheckOut(visitId);
  const noShow = useNoShow(visitId);

  // 到着プレビューの距離しきい値 (管理者が設定した値に追随)。取得失敗時は既定へ
  // フォールバックする (100/300/50)。距離系のみの public エンドポイントを使う。
  const { data: publicThresholds } = useCheckinSettingsPublic();
  const matchM = publicThresholds?.match_m ?? CHECKIN_PUBLIC_FALLBACK.match_m;
  const reviewM = publicThresholds?.review_m ?? CHECKIN_PUBLIC_FALLBACK.review_m;

  // Seed from localStorage so a previous check-in survives navigation. The
  // backend (QR checkin Phase 1) is the source of truth on success; the local
  // record is only an offline insurance for unreachable-server errors.
  const [localStatus, setLocalStatus] = useState<'idle' | 'checked_in' | 'checked_out'>(() =>
    staffId && visitId ? (loadCheckin(staffId, visitId)?.status ?? 'idle') : 'idle',
  );

  // When the visit id (or staff id) changes, re-seed from storage.
  const lastKeyRef = useRef(`${staffId}:${visitId}`);
  useEffect(() => {
    const next = `${staffId}:${visitId}`;
    if (lastKeyRef.current !== next) {
      lastKeyRef.current = next;
      setLocalStatus(
        staffId && visitId ? (loadCheckin(staffId, visitId)?.status ?? 'idle') : 'idle',
      );
    }
  }, [visitId, staffId]);

  // 打刻用トークンは記録成功 / 無効判明 (404/409/410) で消費し、以後は通常の
  // スキャンフローに戻す (退出時も現地で QR を読み直させる = 現地証明を弱めない)。
  const clearDeepLinkToken = useCallback(() => {
    setDeepLinkToken(null);
    // URL からも ?qr= を外し、リロード時に再度スキャン省略にならないようにする。
    if (typeof window !== 'undefined' && window.location.search.includes('qr=')) {
      router.replace(window.location.pathname, { scroll: false });
    }
  }, [router]);

  const [flow, setFlow] = useState<Flow>({ step: 'none' });
  // Reason drafts for the mismatch / no-show forms.
  const [mismatchReason, setMismatchReason] = useState('');
  const [noshowReason, setNoshowReason] = useState('');
  // Guards a no-show submit across the (awaited) GPS fetch (二重送信防止).
  const [noShowSubmitting, setNoShowSubmitting] = useState(false);
  // Count of un-sent records waiting for connectivity (control display).
  const [pendingCount, setPendingCount] = useState(0);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { data: photos } = useVisitPhotos(visitId);
  const uploadPhoto = useUploadPhoto(visitId);
  // 写真の拡大表示 (認証付き blob の objectURL)。
  const [photoViewerUrl, setPhotoViewerUrl] = useState<string | null>(null);

  // Live elapsed timer for the in-progress screen.
  const [nowTs, setNowTs] = useState(() => Date.now());

  // Memo draft (端末内の下書き). The backend has no memo field, so this is a
  // best-effort local draft persisted per (staff, visit) — it does NOT reach
  // the visit monitor. Clearly labelled as 下書き in the UI.
  const [memo, setMemo] = useState('');
  useEffect(() => {
    if (typeof window === 'undefined' || !staffId || !visitId) return;
    try {
      setMemo(window.localStorage.getItem(memoKey(staffId, visitId)) ?? '');
    } catch {
      setMemo('');
    }
  }, [staffId, visitId]);

  function persistMemo(value: string) {
    setMemo(value);
    if (typeof window === 'undefined' || !staffId || !visitId) return;
    try {
      window.localStorage.setItem(memoKey(staffId, visitId), value);
    } catch {
      /* quota / private mode — ignore */
    }
  }

  // ---- Pending re-send queue (offline best-effort) -------------------------
  const refreshPending = useCallback(() => {
    if (!staffId) return;
    setPendingCount(countPending(staffId));
  }, [staffId]);

  const flushNow = useCallback(async () => {
    if (typeof window === 'undefined' || !staffId) return;
    const { remaining, dropped } = await flushCheckinQueue(staffId, accessToken, refreshToken);
    setPendingCount(remaining);
    // 4xx で破棄された未送信分は黙って消さず、利用者へ通知する。
    if (dropped.length > 0) {
      toast.error(`未送信の${dropped.length}件は送信できませんでした`, {
        description: '無効なQR／対象外のため破棄しました',
      });
    }
    // Re-sent records change server-side status; refresh the visit view.
    void qc.invalidateQueries({ queryKey: ['me'] });
  }, [staffId, accessToken, refreshToken, qc]);

  // On mount (and whenever connectivity returns) flush the pending queue.
  useEffect(() => {
    if (typeof window === 'undefined' || !staffId) return;
    refreshPending();
    void flushNow();
    const onOnline = () => void flushNow();
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [staffId, flushNow, refreshPending]);

  const effectiveCheckedIn = isCheckedIn(visit) || localStatus === 'checked_in';
  const effectiveCompleted = isCompleted(visit) || localStatus === 'checked_out';

  /**
   * 代行モード — 自分の担当ではない visit を QR を鍵に開いている状態
   * (`/q/{token}` の「この予定の代行として記録」から来る)。
   *
   * この画面では:
   *   - 「代行」バッジを出し、予定の担当者名を並記する (予定の担当は書き換えない)。
   *   - 打刻には QR トークンを必ず添える (担当外は QR 必須・設計 決定#6)。
   *     手動フォールバック (QRなしで記録) は出さない。
   *   - 未訪問 (no-show) は担当スタッフ専用なので出さない (設計 §2)。
   *
   * 判定は visit の担当欄のみで行う (visit_staff_assignments は VisitRead に
   * 含まれないため、そこだけで担当している稀なケースは代行表示になる — 記録内容は
   * 変わらず、サーバが実績スタッフとして正しく残す)。
   */
  const substituteMode =
    !!visit &&
    !!staffId &&
    visit.primary_staff_id !== staffId &&
    visit.secondary_staff_id !== staffId &&
    visit.mentor_staff_id !== staffId &&
    visit.accompaniment?.staff_id !== staffId;

  useEffect(() => {
    if (!effectiveCheckedIn || effectiveCompleted) return;
    const t = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [effectiveCheckedIn, effectiveCompleted]);

  // Arrival timestamp for the elapsed timer: prefer the server's arrival
  // checkin, fall back to the local record.
  const arrivalIso = useMemo<string | null>(() => {
    const lc = visit?.latest_checkin;
    if (lc && lc.kind === 'arrival') return lc.scanned_at;
    const stored = staffId && visitId ? loadCheckin(staffId, visitId) : null;
    if (stored?.status === 'checked_in') return stored.at;
    return null;
    // localStatus included so a fresh check-in re-derives the arrival time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visit?.latest_checkin, staffId, visitId, localStatus]);

  function startScan(mode: ScanMode) {
    if (!staffId || !visitId) {
      toast.error('ユーザー情報を取得できませんでした');
      return;
    }
    setMismatchReason('');
    if (deepLinkToken) {
      // ディープリンク経由: トークンは URL から取得済みなのでスキャンを省略し、
      // GPS 取得 → プレビュー確認へ直行する (自動送信はしない — 「記録する」は
      // 必ず本人が押す)。トークンの真正性はサーバが検証する (別患者 = 409)。
      void beginPreview(mode, deepLinkToken);
      return;
    }
    setFlow({ step: 'scanning', mode });
  }

  /** Distance (m) between the captured GPS fix and the patient's geocode. */
  function previewDistance(geo: Geo): number | null {
    const plat = visit?.patient_lat;
    const plng = visit?.patient_lng;
    if (geo.lat == null || geo.lng == null || plat == null || plng == null) return null;
    return haversineMeters({ lat: geo.lat, lng: geo.lng }, { lat: plat, lng: plng });
  }

  /**
   * After a scan / manual pick: capture GPS, compute the client-side distance
   * preview, and show the confirm screen. NO POST happens here — recording is a
   * single POST triggered by the user pressing 「記録する」(`recordPreview`).
   */
  async function beginPreview(mode: ScanMode, token: string | undefined) {
    setMismatchReason('');
    setFlow({ step: 'locating', mode });
    const geo = await getGeolocation();
    const distance = previewDistance(geo);
    setFlow({
      step: 'preview',
      mode,
      token,
      geo,
      distance,
      status: previewStatusOf(distance, matchM, reviewM),
    });
  }

  async function relocate() {
    if (flow.step !== 'preview') return;
    const { mode, token } = flow;
    await beginPreview(mode, token);
  }

  function handleScanned(token: string, mode: ScanMode) {
    void beginPreview(mode, token);
  }

  function handleManual(mode: ScanMode) {
    void beginPreview(mode, undefined);
  }

  /** Validate the preview form, then issue the single POST. */
  function recordPreview() {
    if (flow.step !== 'preview') return;
    const { mode, token, geo, status } = flow;
    // 担当外 (代行) は QR 必須 — トークン無しの記録は受け付けない (決定#6)。
    if (substituteMode && !token) {
      toast.error('QRの読み取りが必要です', {
        description: '担当外の訪問は患者宅のQRを読み取って記録してください',
      });
      setFlow({ step: 'none' });
      return;
    }
    const isMismatch = status === 'mismatch';
    const reason = mismatchReason.trim();
    // Arrival mismatch requires a reason (departure mismatch reason is optional).
    if (mode === 'arrival' && isMismatch && !reason) {
      toast.error('理由を入力してください', { description: '不一致のため理由が必要です' });
      return;
    }
    void doRecord(mode, token, geo, {
      reason: reason || undefined,
      is_override: isMismatch,
    });
  }

  /**
   * The single check-in / check-out POST. `qrToken` undefined = manual. On a
   * true network failure / 5xx we stash a local record AND enqueue a pending
   * re-send; on 404 / 409 we surface a real error (the QR is invalid / belongs
   * to another patient) and do NOT stash so the user re-scans.
   */
  async function doRecord(
    mode: ScanMode,
    qrToken: string | undefined,
    geo: Geo,
    extra: { reason?: string; is_override?: boolean },
  ) {
    setFlow({ step: 'submitting', mode });
    const at = new Date().toISOString();
    const coords = coordsOf(geo);
    const payload: CheckInPayload = {
      ...coords,
      at,
      ...(qrToken ? { qr_token: qrToken } : {}),
      ...(extra.reason ? { reason: extra.reason } : {}),
      ...(extra.is_override ? { is_override: true } : {}),
    };
    const mutation = mode === 'arrival' ? checkIn : checkOut;
    try {
      const updated = await mutation.mutateAsync(payload);
      clearCheckin(staffId, visitId);
      // ディープリンクのトークンは 1 記録で消費する (退出時は改めて現地で読む)。
      if (qrToken && qrToken === deepLinkToken) clearDeepLinkToken();
      setLocalStatus(mode === 'arrival' ? 'checked_in' : 'checked_out');
      setFlow({ step: 'none' });
      // Reflect the server's authoritative verdict.
      const serverStatus = updated.latest_checkin?.match_status;
      const base = mode === 'arrival' ? '到着を記録しました' : '訪問を完了しました';
      if (serverStatus === 'mismatch') {
        toast.warning(base, { description: '登録住所から離れた位置で記録されました' });
      } else {
        toast.success(base);
      }
    } catch (err) {
      const apiStatus = err instanceof ApiError ? err.status : null;
      // 無効と確定したディープリンク token は破棄し、次回は通常スキャンに戻す。
      if (
        (apiStatus === 404 || apiStatus === 409 || apiStatus === 410) &&
        qrToken === deepLinkToken
      ) {
        clearDeepLinkToken();
      }
      if (apiStatus === 404) {
        toast.error('このQRは無効です', {
          description: detailOf(err) ?? '患者マスタでQRを再発行してください',
        });
        setFlow({ step: 'none' });
        return;
      }
      if (apiStatus === 409) {
        toast.error('このQRは別の患者のものです', {
          description: detailOf(err) ?? '正しい患者宅のQRを読み取ってください',
        });
        setFlow({ step: 'none' });
        return;
      }
      if (isServerUnreachable(err)) {
        const kind: PendingKind = mode === 'arrival' ? 'arrival' : 'departure';
        const pending: PendingPayload = {
          ...coords,
          at,
          ...(qrToken ? { qr_token: qrToken } : {}),
          ...(extra.reason ? { reason: extra.reason } : {}),
          ...(extra.is_override ? { is_override: true } : {}),
        };
        // Offline insurance: remember the state AND keep the record for re-send.
        saveCheckin(staffId, visitId, {
          status: mode === 'arrival' ? 'checked_in' : 'checked_out',
          at,
          ...(geo.lat !== undefined ? { lat: geo.lat } : {}),
          ...(geo.lng !== undefined ? { lng: geo.lng } : {}),
        });
        enqueuePending(staffId, { visit_id: visitId, kind, payload: pending });
        setLocalStatus(mode === 'arrival' ? 'checked_in' : 'checked_out');
        refreshPending();
        setFlow({ step: 'none' });
        toast.warning('未送信として保存しました', {
          description: '電波が戻り次第、自動で再送します',
        });
        return;
      }
      toast.error('記録に失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
      setFlow({ step: 'none' });
    }
  }

  async function handleNoShowSubmit() {
    if (!noshowReason.trim()) {
      toast.error('理由を入力してください');
      return;
    }
    if (!staffId || !visitId) {
      toast.error('ユーザー情報を取得できませんでした');
      return;
    }
    if (noShowSubmitting || noShow.isPending) return;
    // Set the guard BEFORE awaiting GPS so a double-tap can't fire two POSTs.
    setNoShowSubmitting(true);
    const at = new Date().toISOString();
    const reason = noshowReason.trim();
    // Capture GPS once up-front so the offline-queued payload reuses the same
    // fix (no second permission prompt on failure).
    const geo = await getGeolocation();
    try {
      await noShow.mutateAsync({
        reason,
        at,
        ...(geo.lat !== undefined ? { lat: geo.lat } : {}),
        ...(geo.lng !== undefined ? { lng: geo.lng } : {}),
      });
      setNoshowReason('');
      setFlow({ step: 'none' });
      toast.success('未訪問として記録しました');
    } catch (err) {
      if (isServerUnreachable(err)) {
        enqueuePending(staffId, {
          visit_id: visitId,
          kind: 'no_show',
          payload: { reason, at, ...coordsOf(geo) },
        });
        refreshPending();
        setFlow({ step: 'none' });
        toast.warning('未送信として保存しました', {
          description: '電波が戻り次第、自動で再送します',
        });
      } else {
        toast.error('記録に失敗しました', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    } finally {
      setNoShowSubmitting(false);
    }
  }

  function appendChip(chip: string) {
    setNoshowReason((prev) => (prev ? `${prev}、${chip}` : chip));
  }

  async function handlePhotoSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      await uploadPhoto.mutateAsync({ file });
      toast.success('写真をアップロードしました', { description: file.name });
    } catch (err) {
      toast.error('写真のアップロードに失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  const meta = effectiveCompleted
    ? statusLabel('done')
    : effectiveCheckedIn
      ? statusLabel('in_progress')
      : statusLabel(visit?.status ?? 'planned');

  const patientName = visit?.patient_name ?? '(患者名未設定)';

  // ---- Full-screen scanner overlay -----------------------------------------
  if (flow.step === 'scanning') {
    return (
      <QrScanner
        targetLabel={`${patientName} / ${flow.mode === 'arrival' ? '到着' : '退出'}`}
        onScan={(token) => handleScanned(token, flow.mode)}
        // 代行 (担当外) は QR 必須 — 手動フォールバックは出さない (決定#6)。
        onManual={substituteMode ? undefined : () => handleManual(flow.mode)}
        onCancel={() => setFlow({ step: 'none' })}
      />
    );
  }

  return (
    <MobileSection
      title="訪問詳細"
      action={
        // 無言の矢印だけでは戻り先が分からない (PO要望 2026-07-10) → テキスト付きボタンへ
        <Link
          href="/m/today"
          className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border border-brand-primary-light bg-brand-primary-50 px-3 text-xs font-medium text-brand-primary hover:bg-brand-primary-light"
          aria-label="今日の訪問に戻る"
        >
          <ArrowLeft className="h-4 w-4" />
          今日の訪問に戻る
        </Link>
      }
    >
      {pendingCount > 0 && (
        <div className="flex items-center gap-2 rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
          <Clock className="h-3.5 w-3.5 shrink-0" />
          未送信 {pendingCount} 件・電波が戻ると自動で再送します
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {visit && (
        <>
          <Card className="p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-serif text-lg font-bold text-text-primary">{patientName}</p>
                <p className="text-xs text-text-muted">{visit.visit_date}</p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                {substituteMode && (
                  <Badge variant="warning" data-testid="mobile-detail-substitute">
                    代行
                  </Badge>
                )}
                <Badge variant={meta.variant}>{meta.label}</Badge>
              </div>
            </div>
            {substituteMode && (
              <div className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
                担当外の訪問です。予定の担当: {visit.staff_name ?? '未割当'}。
                あなたが実際に訪問した記録として残ります（予定の担当者は変わりません）。
              </div>
            )}
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2 text-text-secondary">
                <Clock className="h-4 w-4 shrink-0" />
                <span className="tnum">
                  {shortTime(visit.start_time)} - {shortTime(visit.end_time)}
                </span>
              </div>
              {displayVisitNote(visit.note) && (
                <div className="flex items-start gap-2 text-text-secondary">
                  <StickyNote className="h-4 w-4 shrink-0 mt-0.5" />
                  <span className="whitespace-pre-wrap">{visit.note}</span>
                </div>
              )}
              {/* 新人同行 (§7.4): 訪問詳細でも「同行: ◯◯」を表示。 */}
              {visit.accompaniment && (
                <div
                  className="flex items-center gap-2 font-medium text-info"
                  data-testid="mobile-detail-accompaniment"
                >
                  <span>同行: {visit.accompaniment.staff_name ?? '同行スタッフ'}</span>
                </div>
              )}
              {/* Address / phone live on the patient record; surfaced as
                  TODO until the patient relation is included in VisitRead. */}
              <div className="flex items-center gap-2 text-text-muted">
                <MapPin className="h-4 w-4 shrink-0" />
                <span>玄関の固定QRを読み取って打刻してください</span>
              </div>
              <div className="flex items-center gap-2 text-text-muted">
                <Phone className="h-4 w-4 shrink-0" />
                <span>連絡先は患者マスタを参照</span>
              </div>
            </div>
          </Card>

          {/* ---- Preview / confirm overlay (BEFORE the single POST) ------- */}
          {flow.step === 'preview' && (
            <PreviewPanel
              mode={flow.mode}
              distance={flow.distance}
              status={flow.status}
              matchM={matchM}
              reviewM={reviewM}
              geoErrorCode={flow.geo.errorCode}
              mismatchReason={mismatchReason}
              onMismatchReasonChange={setMismatchReason}
              onRecord={recordPreview}
              onRelocate={() => void relocate()}
            />
          )}

          {/* ---- Locating / submitting spinner overlay ------------------- */}
          {(flow.step === 'locating' || flow.step === 'submitting') && (
            <Card className="flex items-center gap-3 p-4 text-sm text-text-secondary">
              <Rakusuke pose="visit" className="h-10 shrink-0" />
              <Loader2 className="h-5 w-5 animate-spin text-brand-primary" />
              {flow.step === 'locating' ? '位置情報を取得しています…' : '記録しています…'}
            </Card>
          )}

          {/* ---- Base actions (only when no overlay is active) ----------- */}
          {flow.step === 'none' && (
            <div className="space-y-2">
              {/* R-2: キャンセル済み訪問はチェックイン・写真UPを封鎖 */}
              {visit.status === 'cancelled' && (
                <Alert>
                  <AlertTitle>この訪問はキャンセルされました</AlertTitle>
                  <AlertDescription>チェックインや写真の登録はできません。</AlertDescription>
                </Alert>
              )}
              {visit.status !== 'cancelled' && !effectiveCheckedIn && !effectiveCompleted && (
                <>
                  <CheckInButton onClick={() => startScan('arrival')}>
                    <QrCode className="h-5 w-5" />
                    QRで到着を記録
                  </CheckInButton>
                  {/* 未訪問 (no-show) は担当スタッフ専用 — 代行では出さない (設計 §2)。 */}
                  {!substituteMode && (
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full text-error"
                      onClick={() => {
                        setNoshowReason('');
                        setFlow({ step: 'noshow' });
                      }}
                    >
                      訪問できなかった（理由を記録）
                    </Button>
                  )}
                </>
              )}

              {visit.status !== 'cancelled' && effectiveCheckedIn && !effectiveCompleted && (
                <>
                  <Card className="space-y-3 p-4">
                    <div className="text-center">
                      <p className="font-serif text-3xl font-bold tnum text-brand-primary-hover">
                        {arrivalIso ? fmtElapsed(nowTs - new Date(arrivalIso).getTime()) : '--:--'}
                      </p>
                      <p className="text-xs text-text-muted">
                        経過時間
                        {arrivalIso
                          ? `（到着 ${shortTime(new Date(arrivalIso).toTimeString())}〜）`
                          : ''}
                      </p>
                    </div>
                    <div>
                      <label
                        htmlFor="visit-memo"
                        className="text-xs font-semibold text-text-secondary"
                      >
                        サービスメモ（下書き・端末内）
                      </label>
                      <Textarea
                        id="visit-memo"
                        rows={3}
                        className="mt-1.5"
                        placeholder="実施内容・申し送り"
                        value={memo}
                        onChange={(e) => persistMemo(e.target.value)}
                      />
                    </div>
                  </Card>
                  <CheckInButton onClick={() => startScan('departure')}>
                    <QrCode className="h-5 w-5" />
                    QRで退出を記録
                  </CheckInButton>
                </>
              )}

              {visit.status !== 'cancelled' && effectiveCompleted && (
                <Alert className="flex items-center gap-3">
                  <Rakusuke pose="cheer" className="h-12 shrink-0" />
                  <div>
                    <AlertTitle>訪問完了</AlertTitle>
                    <AlertDescription>
                      この訪問はチェックアウト済みです。おつかれさまでした！
                    </AlertDescription>
                  </div>
                </Alert>
              )}

              {/* Photo capture (existing visit-photos backend). */}
              {visit.status !== 'cancelled' && !effectiveCompleted && (
                <>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploadPhoto.isPending}
                    className="flex w-full items-center justify-center gap-2 rounded-md border border-dashed border-border-default bg-bg-base p-3 text-sm text-text-secondary hover:bg-bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Camera className="h-4 w-4" />
                    {uploadPhoto.isPending ? 'アップロード中…' : '写真を撮影 / 選択'}
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    capture="environment"
                    className="hidden"
                    onChange={handlePhotoSelected}
                  />
                </>
              )}

              {photos && photos.length > 0 && (
                <div className="grid grid-cols-3 gap-2 pt-1">
                  {/* download API は Bearer 必須 — 素の <img src>/<a href> だと
                      {"detail":"Authentication required"} になるため認証付き fetch で表示。 */}
                  {photos.map((p) => (
                    <AuthedPhoto
                      key={p.id}
                      url={p.download_url}
                      accessToken={accessToken}
                      alt={p.caption ?? '訪問写真'}
                      className="aspect-square overflow-hidden rounded-md border border-border-default bg-bg-muted"
                      onOpen={setPhotoViewerUrl}
                    />
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ---- No-show form overlay ------------------------------------ */}
          {flow.step === 'noshow' && (
            <Card className="space-y-3 p-4">
              <div className="flex items-center gap-2 text-error">
                <AlertTriangle className="h-5 w-5" />
                <p className="font-semibold">訪問できなかった理由</p>
              </div>
              <p className="text-xs text-text-muted">理由は管理者に共有されます。</p>
              <Textarea
                aria-label="未訪問の理由"
                rows={3}
                placeholder="例: 不在。呼び鈴・電話とも応答なし。"
                value={noshowReason}
                onChange={(e) => setNoshowReason(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                {NOSHOW_CHIPS.map((chip) => (
                  <button
                    key={chip}
                    type="button"
                    onClick={() => appendChip(chip)}
                    className="rounded-full bg-bg-muted px-3 py-1.5 text-xs text-text-secondary hover:bg-bg-muted/70"
                  >
                    {chip}
                  </button>
                ))}
              </div>
              <Button
                type="button"
                variant="destructive"
                className="w-full"
                disabled={noShow.isPending || noShowSubmitting}
                onClick={handleNoShowSubmit}
              >
                {(noShow.isPending || noShowSubmitting) && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                未訪問として記録する
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => setFlow({ step: 'none' })}
              >
                戻る
              </Button>
            </Card>
          )}
        </>
      )}

      {/* ---- 写真の拡大表示 (タップで閉じる) --------------------------- */}
      {photoViewerUrl && (
        <button
          type="button"
          aria-label="拡大表示を閉じる"
          onClick={() => setPhotoViewerUrl(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-stone-950/85 p-4"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={photoViewerUrl}
            alt="訪問写真の拡大表示"
            className="max-h-full max-w-full rounded-lg object-contain"
          />
        </button>
      )}
    </MobileSection>
  );
}

// ---------------------------------------------------------------------------
// Preview panel — shown AFTER a scan but BEFORE recording. Displays the
// client-side distance / match preview; a mismatch on arrival requires a
// reason, on departure the reason is optional (item 6 警告). Pressing 記録する
// issues the single POST.
// ---------------------------------------------------------------------------
interface PreviewPanelProps {
  mode: ScanMode;
  distance: number | null;
  status: CheckinMatchStatus;
  matchM: number;
  reviewM: number;
  /** 測位失敗時の GeolocationPositionError.code (成功時 undefined)。 */
  geoErrorCode?: number;
  mismatchReason: string;
  onMismatchReasonChange: (v: string) => void;
  onRecord: () => void;
  onRelocate: () => void;
}

function PreviewPanel({
  mode,
  distance,
  status,
  matchM,
  reviewM,
  geoErrorCode,
  mismatchReason,
  onMismatchReasonChange,
  onRecord,
  onRelocate,
}: PreviewPanelProps) {
  const isArrival = mode === 'arrival';
  const isMismatch = status === 'mismatch';
  const isReview = status === 'review';
  const info = matchInfo(status, matchM, reviewM);
  // Show a reason box when位置がずれている: arrival mismatch (必須) /
  // departure mismatch・review (任意・item 6 の非対称解消)。
  const showReason = (isArrival && isMismatch) || (!isArrival && (isMismatch || isReview));
  const reasonRequired = isArrival && isMismatch;

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="h-5 w-5 text-brand-primary" />
        <p className="font-semibold text-text-primary">{isArrival ? '到着の確認' : '退出の確認'}</p>
      </div>

      {/* Simple map thumbnail (装飾) — 距離/判定は下のカードが正。 */}
      <div className="relative h-24 overflow-hidden rounded-lg border border-border-default bg-gradient-to-br from-brand-primary-light/40 to-bg-muted">
        <Home className="absolute left-1/2 top-1/2 h-6 w-6 -translate-x-1/2 -translate-y-1/2 text-brand-primary-hover" />
        <MapPin
          className={
            'absolute h-5 w-5 ' +
            (isMismatch ? 'left-[72%] top-[70%] text-error' : 'left-[54%] top-[58%] text-info')
          }
        />
      </div>

      <div
        className={
          'flex items-center justify-between rounded-lg px-3 py-2 text-sm font-semibold ' +
          (info.variant === 'success'
            ? 'bg-success/15 text-success'
            : info.variant === 'destructive'
              ? 'bg-error/15 text-error'
              : 'bg-warning/15 text-warning')
        }
      >
        <span>{info.label}</span>
        <span className="tnum">{distanceLabel(distance)}</span>
      </div>

      <p className="text-xs text-text-muted">{info.hint}</p>

      {/* 測位失敗の理由別ヒント (権限オフ/タイムアウト/測位不能)。 */}
      {status === 'no_gps' && geoErrorHint(geoErrorCode) && (
        <p className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">
          {geoErrorHint(geoErrorCode)}
        </p>
      )}

      {showReason && (
        <div className="space-y-2">
          <label htmlFor="mismatch-reason" className="text-xs font-semibold text-text-secondary">
            {reasonRequired
              ? '理由（不一致のため必須・管理者に共有）'
              : '理由（任意・管理者に共有）'}
          </label>
          <Textarea
            id="mismatch-reason"
            rows={2}
            placeholder="例: マンション裏口で測位 など"
            value={mismatchReason}
            onChange={(e) => onMismatchReasonChange(e.target.value)}
          />
        </div>
      )}

      <Button
        type="button"
        variant={isMismatch ? 'destructive' : 'default'}
        className="w-full"
        onClick={onRecord}
      >
        {isArrival ? (isMismatch ? '理由を付けて到着を記録' : '到着を記録する') : '退出を記録する'}
      </Button>
      <Button type="button" variant="ghost" className="w-full" onClick={onRelocate}>
        <RefreshCw className="h-4 w-4" />
        位置を再取得
      </Button>
    </Card>
  );
}
