'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams, usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
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
  enqueuePending,
  findPending,
  setPendingAdjustment,
  type PendingKind,
  type PendingPayload,
} from '@/lib/checkin-queue';
import { detailOf, isServerUnreachable } from '@/lib/checkin-flush';
import {
  coordsOf,
  geoErrorHint,
  getGeolocation,
  haversineMeters,
  type GeoFix as Geo,
} from '@/lib/geo';
import {
  actualTimeParts,
  hmToMinutes,
  jstHm,
  jstMinutes,
  minutesToHm,
} from '@/lib/format/actualTime';
import { QR_READ_AT_PARAM, extractQrToken, parseHandoffReadAt } from '@/lib/qr-token';
import { cn } from '@/lib/utils';
import { visitAccompaniments } from '@/lib/schemas/trainee_accompaniment';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { toast } from '@/components/ui/sonner';
import { ActualTimeSheet } from '@/components/mobile/ActualTimeSheet';
import { ArrivedAdjustCard } from '@/components/mobile/ArrivedAdjustCard';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { MobileSection } from '@/components/mobile/MobileSection';
import { Rakusuke } from '@/components/brand/Rakusuke';
import { QrScanner } from '@/components/mobile/QrScanner';
import { AuthedPhoto } from '@/components/mobile/AuthedPhoto';
import { VisitRecordCard } from '@/components/mobile/VisitRecordCard';
import { VoiceRecorderPanel } from '@/components/mobile/VoiceRecorderPanel';
import { displayVisitNote } from '@/lib/visit-note';
import {
  todayIso,
  useAdjustActualTime,
  useCheckIn,
  useCheckOut,
  useMyVisit,
  useNoShow,
  useResetActualTime,
  type ActualTimeKind,
  type AdjustReasonCode,
  type CheckInPayload,
  type CheckinMatchStatus,
  type MyVisit,
} from '@/lib/queries/me';
import { useUploadPhoto, useVisitPhotos } from '@/lib/queries/visit-photos';
import { useVisitRecordings } from '@/lib/queries/visit-recordings';
import { useCheckinFlush } from '@/lib/queries/checkinFlush';
import { useVoiceFlush } from '@/lib/voice/queue';
import { rescueVoiceSessions } from '@/lib/voice/session';
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
 *
 * `readAt` は **QR を読み取った瞬間**の端末時刻 (設計 2026-09-30 §3)。打刻の `at`
 * にはこれを載せる — 位置の取得や確認にかかった時間、「位置を再取得」、圏外での
 * 後送りで実績の時刻が遅れないようにするため。
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
      readAt: string;
    }
  | { step: 'submitting'; mode: ScanMode }
  // 読み取った QR が表示中 visit の患者と一致しない (409)。設計 §5 の
  // 「代行 / 予定外として記録しますか？」導線をここから出す。
  | { step: 'wrong_patient'; token: string }
  | { step: 'noshow' };

/** 手で入れた退出の「元に戻す」つきトーストを出しておく時間 (ms)。 */
const MANUAL_DEPARTURE_TOAST_MS = 12_000;
/** トーストの操作ボタンを、親指で押せる 44px にする (sonner の既定は 24px)。 */
const TOAST_ACTION_44PX = '!h-11 !px-4 !text-sm';

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

/** 救出は best-effort — 失敗しても画面の邪魔をしない。 */
function ignoreRescueError(): void {
  /* noop */
}

/**
 * 圏外で退避した到着 (まだサーバに届いていない)。到着した直後のカードは、この間は
 * 調整 API を呼ばずに退避キューの控えへ時刻を書き込む (設計 2026-09-30 §7-2)。
 */
interface QueuedArrival {
  /** QR を読み取った時刻 (ISO 8601)。 */
  readAt: string;
  /** その場で合わせた時刻 (JST "HH:MM")。読取時刻のままなら null。 */
  adjustedTime: string | null;
}

function readQueuedArrival(staffId: string, visitId: string): QueuedArrival | null {
  if (!staffId || !visitId) return null;
  const entry = findPending(staffId, visitId, 'arrival');
  if (!entry) return null;
  return { readAt: entry.payload.at, adjustedTime: entry.payload.adjusted_time ?? null };
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
  const pathname = usePathname();
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  // 写真の認証付き表示にだけ使う (再送は useCheckinFlush が担う)。
  const accessToken = session?.accessToken ?? null;

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
  // ディープリンクで開いた時点 = 標準カメラが QR を読んだ瞬間 (設計 2026-09-30 §3)。
  // 「記録する」を押すまでに時間が空いても、**到着の** `at` はこの時刻にする。
  // `/q/{token}` を経由して来たときは、`/q` を開いた時刻が引き継がれる (候補の解決や
  // 選択画面で止まっていた時間を含めない)。引き継いだ値は外から来るので検証し、
  // 採れなければここを開いた時刻にする。
  const [deepLinkReadAt] = useState(
    () =>
      (readToken ? parseHandoffReadAt(searchParams?.get(QR_READ_AT_PARAM), Date.now()) : null) ??
      new Date().toISOString(),
  );
  // 今週の予定から開いたときは、戻るリンクも今週へ (設計 §7-8)。
  const fromWeek = searchParams?.get('from') === 'week';

  const { data: queryVisit, isLoading, isError, error } = useMyVisit(visitId, readToken);
  // 打刻・時刻を合わせる API の応答 (どれも VisitRead)。['me'] の再取得が届くまでの
  // 間、**状態と実績の項目だけ**をここから映し、押した直後に古い時刻が残って
  // 見えないようにする。担当欄などは詳細 GET のものを使い続ける (代行モードの
  // 判定を応答の形に左右させない)。
  const [freshVisit, setFreshVisit] = useState<MyVisit | null>(null);
  useEffect(() => {
    setFreshVisit(null);
  }, [queryVisit]);
  const visit = useMemo<MyVisit | undefined>(() => {
    if (!queryVisit || !freshVisit || freshVisit.id !== queryVisit.id) return queryVisit;
    return {
      ...queryVisit,
      status: freshVisit.status,
      latest_checkin: freshVisit.latest_checkin,
      actual_arrival_at: freshVisit.actual_arrival_at,
      actual_departure_at: freshVisit.actual_departure_at,
      actual_arrival_read_at: freshVisit.actual_arrival_read_at,
      actual_departure_read_at: freshVisit.actual_departure_read_at,
      actual_arrival_adjusted: freshVisit.actual_arrival_adjusted,
      actual_departure_adjusted: freshVisit.actual_departure_adjusted,
      actual_departure_manual: freshVisit.actual_departure_manual,
      actual_adjust_allowed: freshVisit.actual_adjust_allowed,
    };
  }, [queryVisit, freshVisit]);

  const checkIn = useCheckIn(visitId);
  const checkOut = useCheckOut(visitId);
  const noShow = useNoShow(visitId);
  const adjustActual = useAdjustActualTime(visitId);
  const resetActual = useResetActualTime(visitId);
  const adjustMutating = adjustActual.isPending || resetActual.isPending;

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

  /** URL から 1 回きりのクエリだけを外す (他のクエリは保全)。無ければ何もしない。 */
  const dropUrlParams = useCallback(
    (names: readonly string[]) => {
      if (typeof window === 'undefined') return;
      const qs = new URLSearchParams(window.location.search);
      if (!names.some((name) => qs.has(name))) return;
      for (const name of names) qs.delete(name);
      const rest = qs.toString();
      router.replace(rest ? `${window.location.pathname}?${rest}` : window.location.pathname, {
        scroll: false,
      });
    },
    [router],
  );

  // 打刻用トークンは 1 記録で消費し (成功 / **圏外で退避** / 無効判明 404・409・410)、
  // 以後は通常のスキャンフローに戻す (退出時も現地で QR を読み直させる = 現地証明を
  // 弱めない)。退避でも消費するのは、残すと次の退出がスキャンを省略し、到着と同じ
  // 読取時刻で記録されてしまうため。
  const clearDeepLinkToken = useCallback(() => {
    setDeepLinkToken(null);
    // URL からも外し、リロード時に再度スキャン省略にならないようにする。
    // 引き継いだ読取時刻も同じ 1 回きりの値なので一緒に外す。
    dropUrlParams(['qr', QR_READ_AT_PARAM]);
  }, [dropUrlParams]);

  const [flow, setFlow] = useState<Flow>({ step: 'none' });
  // Reason drafts for the mismatch / no-show forms.
  const [mismatchReason, setMismatchReason] = useState('');
  const [noshowReason, setNoshowReason] = useState('');
  // Guards a no-show submit across the (awaited) GPS fetch (二重送信防止).
  const [noShowSubmitting, setNoShowSubmitting] = useState(false);
  // 未送信の再送 (マウント時 / online 時) + 残件数。通知は共通フック側で行う。
  const { pendingCount: checkinPending, refreshPending, flushNow } = useCheckinFlush();
  // 未送信の音声も同じ場所で再送し、バナーの件数に合算する (設計 §10-5)。
  const { pendingCount: voicePending, refreshPending: refreshVoicePending } = useVoiceFlush();
  const pendingCount = checkinPending + voicePending;

  // ---- 実績の時刻を合わせる (設計 2026-09-30 §7) ---------------------------
  // 到着した直後のカード。予定外訪問は `/q` で到着を記録してからここへ来るので、
  // `?arrived=1` でも出す。
  const [showArrivedCard, setShowArrivedCard] = useState(
    () => searchParams?.get('arrived') === '1',
  );
  // `arrived=1` は「いま到着した」という 1 回きりの合図。URL に残すと、再読み込みの
  // たびにカードが出直すので、受け取ったら外す。
  useEffect(() => {
    dropUrlParams(['arrived']);
  }, [dropUrlParams]);
  // 退避した到着の再送が終わるのを待っている (その間は二度押しさせない)。
  const [waitingForResend, setWaitingForResend] = useState(false);
  const adjustBusy = adjustMutating || waitingForResend;
  // 圏外で退避した到着。未送信の件数が変わるたび (退避した / 送信できた) に読み直す。
  const [queuedArrival, setQueuedArrival] = useState<QueuedArrival | null>(null);
  useEffect(() => {
    setQueuedArrival(readQueuedArrival(staffId, visitId));
  }, [staffId, visitId, checkinPending]);
  // 「実績の時刻を合わせる」シート。null = 閉じている。
  const [sheetKind, setSheetKind] = useState<ActualTimeKind | null>(null);

  /**
   * 録音セッションの救出 (レビュー H-C)。
   *
   * 録音は `VoiceRecorderPanel` ではなく `lib/voice/session.ts` が持つ。パネルは
   * QR スキャナを出しただけで unmount されるので、そこで止めると「録音中に QR を
   * 読んだら録音が切れる」ことになる。止めて未送信キューへ積むのは**ページを
   * 離れるとき**だけ: この effect の cleanup (ページ unmount / パス変更) と、
   * `pagehide` / `visibilitychange`(hidden)。
   *
   * **`beforeunload` では保存しない**(レビュー N-1)。iOS Safari / PWA では発火しない
   * ことがあり、発火しても非同期の保存を最後まで走らせる保証が無い。ここで頼ると
   * 「保存したつもり」を作る。`beforeunload` は録音中の離脱確認プロンプト専用で、
   * それは `VoiceRecorderPanel` が出す。同時に飛ぶ `pagehide` と
   * `visibilitychange` の二重実行は `rescueVoiceSessions` 側のガードが畳む。
   */
  const staffIdRef = useRef(staffId);
  staffIdRef.current = staffId;
  useEffect(() => {
    const rescue = () => {
      void rescueVoiceSessions(staffIdRef.current).then((saved) => {
        if (saved > 0) {
          toast.success(`録音 ${saved} 件を保存しました（画面を離れたため自動保存）`);
          void refreshVoicePending();
        }
      }, ignoreRescueError);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') rescue();
    };
    window.addEventListener('pagehide', rescue);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('pagehide', rescue);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      rescue();
    };
    // `pathname` が変わる = 別の画面へ移った (同じコンポーネントが使い回される
    // 訪問間の遷移でも cleanup が走る)。
  }, [pathname, refreshVoicePending]);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { data: photos } = useVisitPhotos(visitId);
  const uploadPhoto = useUploadPhoto(visitId);
  // この訪問の音声記録 (要約カード)。録音を積んだ直後は onSaved で数え直す。
  const { data: recordingList } = useVisitRecordings({ visitId });
  const recordings = recordingList?.items ?? [];
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
   * **前提に `readToken` を置く**のが要点。QR 経由 (`?qr=`) でない通常導線まで
   * この判定に晒すと、担当欄の取りこぼしがそのまま「no-show ボタンと手動
   * フォールバックが消える」回帰になる。代行モードは `/q` から来たときだけに
   * 限定し、構造的に塞ぐ。
   *
   * 担当欄は primary/secondary/mentor/同行 に加えて `staff_assignments`
   * (visit_staff_assignments) も見る (最終レビュー M-4)。この一覧だけで担当して
   * いるスタッフが `/q` 経由で開くと誤って代行扱いになっていた。担当外の QR
   * capability GET では BE がこの一覧を空配列に落とすため、真の担当外の判定には
   * 影響しない。
   */
  const substituteMode =
    !!readToken &&
    !!visit &&
    !!staffId &&
    visit.primary_staff_id !== staffId &&
    visit.secondary_staff_id !== staffId &&
    visit.mentor_staff_id !== staffId &&
    // 同行者は複数名ありうる (確定#5)。1 人でも自分なら代行扱いにしない。
    !visitAccompaniments(visit).some((a) => a.staff_id === staffId) &&
    !visit.staff_assignments?.some((a) => a.staff_id === staffId);

  useEffect(() => {
    if (!effectiveCheckedIn || effectiveCompleted) return;
    const t = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(t);
  }, [effectiveCheckedIn, effectiveCompleted]);

  // 経過時間の起点 (epoch ms)。**実績の到着** (`actual_arrival_at` = 合わせた後の
  // 時刻。無ければ読取時刻) を優先する (設計 2026-09-30 §7-6)。旧 BE は最新の到着
  // 打刻、圏外で退避中は端末の控え (その場で合わせていればその時刻) を使う。
  const arrivalMs = useMemo<number | null>(() => {
    const valid = (ms: number): number | null => (Number.isNaN(ms) ? null : ms);
    if (visit?.actual_arrival_at) return valid(new Date(visit.actual_arrival_at).getTime());
    const lc = visit?.latest_checkin;
    if (lc && lc.kind === 'arrival') return valid(new Date(lc.scanned_at).getTime());
    const stored = staffId && visitId ? loadCheckin(staffId, visitId) : null;
    if (stored?.status !== 'checked_in') return null;
    const readMs = valid(new Date(stored.at).getTime());
    const readMin = jstMinutes(queuedArrival?.readAt);
    const adjustedMin = hmToMinutes(queuedArrival?.adjustedTime);
    if (readMs == null || readMin == null || adjustedMin == null) return readMs;
    // 合わせた時刻は分単位 (秒 0)。読取時刻を分に切り捨ててから差を引く。
    return Math.floor(readMs / 60000) * 60000 - (readMin - adjustedMin) * 60000;
    // localStatus included so a fresh check-in re-derives the arrival time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    visit?.actual_arrival_at,
    visit?.latest_checkin,
    staffId,
    visitId,
    localStatus,
    queuedArrival,
  ]);

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
      //
      // 読み取った瞬間: 到着は、このページをディープリンクで開いた時点。
      // **退出には使わない** — ディープリンクの読取時刻は 1 回の読み取りの時刻で、
      // 到着に使った (使おうとした) 後に退出へ回すと、退出が到着と同じ時刻になり滞在が
      // 0 分になる。退出は、退出のボタンを押した時点にする。
      const readAt = mode === 'arrival' ? deepLinkReadAt : new Date().toISOString();
      void beginPreview(mode, deepLinkToken, readAt);
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
   *
   * `readAt` = QR を読み取った瞬間。ここでは作らず、呼び出し元が「読んだ時点」で
   * 控えた値をそのまま運ぶ (GPS の取得を待った後の時刻にしない)。
   */
  async function beginPreview(mode: ScanMode, token: string | undefined, readAt: string) {
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
      readAt,
    });
  }

  async function relocate() {
    if (flow.step !== 'preview') return;
    // 位置を取り直しても、読み取った瞬間は変えない。
    const { mode, token, readAt } = flow;
    await beginPreview(mode, token, readAt);
  }

  function handleScanned(token: string, mode: ScanMode) {
    // カメラが QR を読んだ時点。
    void beginPreview(mode, token, new Date().toISOString());
  }

  function handleManual(mode: ScanMode) {
    // 「QRなしで記録」を押した時点。
    void beginPreview(mode, undefined, new Date().toISOString());
  }

  /** Validate the preview form, then issue the single POST. */
  function recordPreview() {
    if (flow.step !== 'preview') return;
    const { mode, token, geo, status, readAt } = flow;
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
    void doRecord(mode, token, geo, readAt, {
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
    /** QR を読み取った瞬間 (ISO 8601)。打刻の `at` にそのまま載せる。 */
    readAt: string,
    extra: { reason?: string; is_override?: boolean },
  ) {
    setFlow({ step: 'submitting', mode });
    const at = readAt;
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
      setFreshVisit(updated);
      // 到着した直後は、その場で時刻を合わせるカードを出す (設計 §7-2)。
      setShowArrivedCard(mode === 'arrival');
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
        // 読んだ QR は「別の利用者」のもの。現地に居ることは確かなので、
        // 行き止まりにせず代行 / 予定外の記録へ渡す (設計 §5)。
        if (qrToken) {
          setFlow({ step: 'wrong_patient', token: qrToken });
          return;
        }
        toast.error('このQRは別の利用者のものです', {
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
        // 退避でもディープリンクのトークンは消費する (この 1 記録に使った)。残すと
        // 次の退出がスキャンを省略し、現地で読み直さないまま記録されてしまう。
        if (qrToken && qrToken === deepLinkToken) clearDeepLinkToken();
        setLocalStatus(mode === 'arrival' ? 'checked_in' : 'checked_out');
        refreshPending();
        // 退避した到着にもカードを出す。合わせた時刻は送信前の控えに書き込む。
        if (mode === 'arrival') setQueuedArrival({ readAt: at, adjustedTime: null });
        setShowArrivedCard(mode === 'arrival');
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

  /**
   * 実績の時刻を合わせる。`time` は JST の "HH:MM"、**null は読取時刻に戻す**。
   * 成功したら true。予定 (`start_time` / `end_time`) は動かない。
   *
   * 圏外で退避した到着はまだサーバに無いので、API を呼ばずに退避キューの控えへ
   * `adjusted_time` を書き込む (再送のとき打刻と一緒に届く・設計 §6-2)。控えが
   * もう無い (= たった今送信できた) ときは、そのまま API へ進む。
   *
   * **再送の POST がちょうど飛んでいる間**は控えに書いても body に載らない。書けた
   * ことにせず、再送が終わるのを待ってから決める: 送れていれば API、まだ控えに
   * 残っていれば (送信に失敗した) 控えへ書く。
   */
  async function applyActualTime(
    kind: ActualTimeKind,
    time: string | null,
    reasonCode: AdjustReasonCode,
  ): Promise<boolean> {
    if (kind === 'arrival' && queuedArrival) {
      const adjustment = time ? { adjusted_time: time, adjust_reason_code: reasonCode } : null;
      let result = setPendingAdjustment(staffId, visitId, 'arrival', adjustment);
      if (result === 'sending') {
        setWaitingForResend(true);
        try {
          // 同じスタッフの再送は直列に走る。これが返る時点で、いま飛んでいる POST は
          // 終わっている。
          await flushNow();
        } finally {
          setWaitingForResend(false);
        }
        result = setPendingAdjustment(staffId, visitId, 'arrival', adjustment);
      }
      if (result === 'written') {
        setQueuedArrival({ ...queuedArrival, adjustedTime: time });
        return true;
      }
      if (result === 'sending') {
        // 待っている間に次の再送が始まった。保存できていないことを必ず伝える。
        toast.error('時刻を合わせられませんでした', {
          description: '記録を送信しています。少し待ってから、もう一度お試しください',
        });
        return false;
      }
      setQueuedArrival(null);
    }
    try {
      const updated = time
        ? await adjustActual.mutateAsync({
            kind,
            time,
            reason_code: reasonCode,
            reason_text: null,
          })
        : await resetActual.mutateAsync(kind);
      setFreshVisit(updated);
      return true;
    } catch (err) {
      // 422 などの `detail` は、そのまま画面に出せる日本語 (設計 §6-1)。
      toast.error('時刻を合わせられませんでした', {
        description:
          detailOf(err) ??
          (isServerUnreachable(err)
            ? '電波の良い場所で、もう一度お試しください'
            : err instanceof Error
              ? err.message
              : String(err)),
      });
      return false;
    }
  }

  /** シートの「HH:MM に合わせる」。 */
  async function saveFromSheet(
    kind: ActualTimeKind,
    time: string | null,
    reasonCode: AdjustReasonCode,
  ): Promise<boolean> {
    // 退出がまだ無い訪問に退出を入れる = 退出を新しく記録する (訪問が完了になる)。
    const recordsDeparture = kind === 'departure' && sheetModel?.departure.at == null;
    const ok = await applyActualTime(kind, time, reasonCode);
    if (!ok) return false;
    setShowArrivedCard(false);
    if (recordsDeparture && time) {
      // 完了になると QR の退出・音声記録・写真の入口が消える。押し間違いをその場で
      // 取り消せるようにする (シートの退出側からも取り消せる)。
      toast.success(`退出を ${time} で記録しました`, {
        description: '訪問は完了になりました',
        duration: MANUAL_DEPARTURE_TOAST_MS,
        classNames: { actionButton: TOAST_ACTION_44PX },
        action: { label: '元に戻す', onClick: () => void cancelManualDeparture() },
      });
      return true;
    }
    // `time = null` (読取時刻に戻す) のときは、読取時刻を見せる。
    const readMin = kind === 'arrival' ? sheetModel?.arrival.readAt : sheetModel?.departure.readAt;
    const shown = time ?? (readMin != null ? minutesToHm(readMin) : '読取時刻');
    toast.success(`${kind === 'arrival' ? '到着' : '退出'}を ${shown} に合わせました`);
    return true;
  }

  /** 手で入れた退出時刻を取り消す (退出が無くなり、訪問中に戻る)。 */
  async function cancelManualDeparture(): Promise<boolean> {
    const ok = await applyActualTime('departure', null, 'no_read');
    if (ok) toast.success('入れた退出時刻を取り消しました');
    return ok;
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

  // 打刻の実時刻 (お客様要望 2026-09-18)。サーバに届いた打刻だけを出す — 退避中の
  // ローカル打刻は「経過時間」カードが受け持つので、ここで混ぜると完了後に
  // 「到着のみ」と出て誤読を生む。
  // 未訪問 (no_show) は抑止する: 「未訪問」と「到着 12:56」を並べない (BE レビュー
  // 申し送り 2026-09-18)。打刻自体は BE に残る。
  const actualParts =
    visit?.status === 'no_show'
      ? null
      : actualTimeParts(visit?.actual_arrival_at, visit?.actual_departure_at);

  // 打刻はサーバが当日以外を拒否する。過去 / 先の日の訪問では QR の打刻ボタンと
  // 「訪問できなかった」を出さない (設計 2026-09-30 §7-8)。
  const today = todayIso();
  const isToday = visit?.visit_date === today;
  const isFutureDay = !!visit && visit.visit_date > today;

  /**
   * 時刻を合わせる対象 (0 時からの分)。null = 合わせられない。
   *
   *   - 圏外で退避した到着があれば、その控え (退出はまだ合わせられない)。
   *   - それ以外は、サーバが `actual_adjust_allowed` を立てた訪問だけ
   *     (権限はサーバが判定・設計 §6-3)。到着の読み取りが必要。
   */
  const actualModel = (() => {
    if (queuedArrival) {
      const readAt = jstMinutes(queuedArrival.readAt);
      if (readAt == null) return null;
      return {
        queued: true,
        arrival: { at: hmToMinutes(queuedArrival.adjustedTime) ?? readAt, readAt },
        departure: { at: null, readAt: null },
      };
    }
    if (!visit?.actual_adjust_allowed || visit.status === 'no_show') return null;
    const at = jstMinutes(visit.actual_arrival_at);
    if (at == null) return null;
    return {
      queued: false,
      arrival: { at, readAt: jstMinutes(visit.actual_arrival_read_at) ?? at },
      departure: {
        at: jstMinutes(visit.actual_departure_at),
        readAt: jstMinutes(visit.actual_departure_read_at),
      },
    };
  })();
  /** サーバに届いた実績を合わせられる (= ボタンを出す)。 */
  const canAdjustOnServer = !!actualModel && !actualModel.queued;

  // シートに渡す対象。開いている間は、直前の値を持ち続ける。退避した到着の再送が
  // 成功すると、控えが消えてから詳細の再取得が届くまで `actualModel` が一瞬 null に
  // なる。そこでシートを外すと、選びかけの時刻ごと作り直しになってしまう。
  // (保存の行き先は保存の時点で決め直すので、古い控えへ書き込むことは無い。)
  const heldModelRef = useRef(actualModel);
  if (actualModel) heldModelRef.current = actualModel;
  const sheetModel = actualModel ?? (sheetKind != null ? heldModelRef.current : null);

  // 実績の行の下の補足: 「滞在 35 分 ・ 到着を 10 分 調整（読取 13:06）」。
  const actualNote = (() => {
    if (!visit || !actualParts) return null;
    const arr = jstMinutes(visit.actual_arrival_at);
    const dep = jstMinutes(visit.actual_departure_at);
    const parts: string[] = [];
    if (arr != null && dep != null && dep >= arr) parts.push(`滞在 ${dep - arr} 分`);
    const arrRead = jstHm(visit.actual_arrival_read_at);
    if (visit.actual_arrival_adjusted && arrRead) {
      const back = arr != null ? (hmToMinutes(arrRead) ?? arr) - arr : 0;
      parts.push(
        back > 0 ? `到着を ${back} 分 調整（読取 ${arrRead}）` : `到着を調整（読取 ${arrRead}）`,
      );
    }
    const depRead = jstHm(visit.actual_departure_read_at);
    if (visit.actual_departure_manual) parts.push('退出は手入力');
    else if (visit.actual_departure_adjusted && depRead)
      parts.push(`退出を調整（読取 ${depRead}）`);
    return parts.length > 0 ? parts.join(' ・ ') : null;
  })();

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
          href={fromWeek ? '/m/this-week' : '/m/today'}
          className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full border border-brand-primary-light bg-brand-primary-50 px-3 text-xs font-medium text-brand-primary hover:bg-brand-primary-light"
          aria-label={fromWeek ? '今週の予定に戻る' : '今日の訪問に戻る'}
        >
          <ArrowLeft className="h-4 w-4" />
          {fromWeek ? '今週の予定に戻る' : '今日の訪問に戻る'}
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
                  予定 {shortTime(visit.start_time)} - {shortTime(visit.end_time)}
                </span>
              </div>
              {/* 打刻の実績 (お客様要望 2026-09-18)。予定は書き換えず直下に並べる。
                  完了 (checked_out) でも見えるので、後から実時間を確認できる。 */}
              {actualParts && (
                <div
                  className={cn(
                    'flex items-center gap-2 font-medium',
                    actualParts.done ? 'text-success' : 'text-brand-primary',
                  )}
                  data-testid="mobile-detail-actual"
                >
                  {actualParts.done ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
                  ) : (
                    <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                  )}
                  <span className="tnum">
                    {actualParts.done ? `実績 ${actualParts.range}` : `到着 ${actualParts.range}`}
                  </span>
                  {/* 訪問中も完了後も開ける。出すかどうかはサーバの判定に従う。 */}
                  {canAdjustOnServer && (
                    <button
                      type="button"
                      onClick={() => setSheetKind('arrival')}
                      className="ml-auto inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full border border-brand-primary bg-bg-base px-3.5 text-[13px] font-bold text-brand-primary-hover"
                      data-testid="mobile-detail-adjust"
                    >
                      <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                      時刻を合わせる
                    </button>
                  )}
                </div>
              )}
              {actualNote && (
                <p
                  className="tnum ml-6 text-xs text-text-secondary"
                  data-testid="mobile-detail-actual-note"
                >
                  {actualNote}
                </p>
              )}
              {displayVisitNote(visit.note) && (
                <div className="flex items-start gap-2 text-text-secondary">
                  <StickyNote className="h-4 w-4 shrink-0 mt-0.5" />
                  <span className="whitespace-pre-wrap">{visit.note}</span>
                </div>
              )}
              {/* 同行 (§7.4): 訪問詳細でも「同行: ◯◯・◯◯」を表示 (複数名対応)。 */}
              {visitAccompaniments(visit).length > 0 && (
                <div
                  className="flex items-center gap-2 font-medium text-info"
                  data-testid="mobile-detail-accompaniment"
                >
                  <span>
                    同行:{' '}
                    {visitAccompaniments(visit)
                      .map((a) => a.staff_name ?? '同行スタッフ')
                      .join('・')}
                  </span>
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
              readAt={flow.readAt}
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

          {/* ---- 別の利用者の QR を読んだ (409) — 代行/予定外へ渡す ------- */}
          {flow.step === 'wrong_patient' && (
            <Card className="space-y-3 p-4" data-testid="wrong-patient-panel">
              <div className="flex items-center gap-2 text-warning">
                <AlertTriangle className="h-5 w-5" />
                <p className="font-semibold">このQRは別の利用者のものです</p>
              </div>
              <p className="text-sm text-text-secondary">
                読み取ったQRは「{patientName}」さんではありません。
                この利用者宅にいる場合は、代行または予定外の訪問として記録できます。
              </p>
              <Button
                type="button"
                className="w-full"
                onClick={() => router.push(`/q/${encodeURIComponent(flow.token)}`)}
              >
                代行／予定外として記録する
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="w-full"
                onClick={() => setFlow({ step: 'none' })}
              >
                戻る（この訪問の画面に留まる）
              </Button>
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
              {visit.status !== 'cancelled' &&
                !effectiveCheckedIn &&
                !effectiveCompleted &&
                isFutureDay && (
                  <Card className="p-4 text-[13px] text-text-secondary">
                    当日になると到着を記録できます。
                  </Card>
                )}
              {/* 過去の日で打刻の無い訪問。打刻ボタンを出せない (サーバが当日以外を
                  拒否する) ので、何も出ない理由を一言添える。 */}
              {visit.status !== 'cancelled' &&
                visit.status !== 'no_show' &&
                !effectiveCheckedIn &&
                !effectiveCompleted &&
                !isToday &&
                !isFutureDay && (
                  <Card
                    className="p-4 text-[13px] text-text-secondary"
                    data-testid="mobile-detail-past-no-record"
                  >
                    この日の記録はありません。必要な場合は管理者にお伝えください。
                  </Card>
                )}
              {visit.status !== 'cancelled' &&
                !effectiveCheckedIn &&
                !effectiveCompleted &&
                isToday && (
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

              {/* ---- 到着した直後: その場で時刻を合わせる (設計 §7-2) -------- */}
              {visit.status !== 'cancelled' &&
                effectiveCheckedIn &&
                !effectiveCompleted &&
                showArrivedCard &&
                actualModel && (
                  <ArrivedAdjustCard
                    readMin={actualModel.arrival.readAt}
                    adjustedMin={
                      actualModel.arrival.at !== actualModel.arrival.readAt
                        ? actualModel.arrival.at
                        : null
                    }
                    busy={adjustBusy}
                    onQuick={(n) =>
                      void applyActualTime(
                        'arrival',
                        minutesToHm(actualModel.arrival.readAt - n),
                        'intercom_wait',
                      )
                    }
                    onFine={() => setSheetKind('arrival')}
                    onUndo={() => void applyActualTime('arrival', null, 'intercom_wait')}
                    onDismiss={() => setShowArrivedCard(false)}
                  />
                )}

              {visit.status !== 'cancelled' && effectiveCheckedIn && !effectiveCompleted && (
                <Card className="space-y-3 p-4">
                  <div className="text-center">
                    {/* 過去の日の訪問 (退出の読み取りが無いまま) では時計を回さない。 */}
                    <p className="font-serif text-3xl font-bold tnum text-brand-primary-hover">
                      {arrivalMs != null && isToday ? fmtElapsed(nowTs - arrivalMs) : '--:--'}
                    </p>
                    <p className="text-xs text-text-muted" data-testid="mobile-detail-elapsed">
                      経過時間
                      {arrivalMs != null
                        ? `（到着 ${jstHm(new Date(arrivalMs).toISOString())}〜）`
                        : ''}
                    </p>
                    {/* 圏外で退避中の到着は、まだ実績の行 (「時刻を合わせる」) が出ない。
                        到着直後のカードを閉じた後も、ここから合わせられるようにする
                        (合わせた時刻は送信前の控えに書き込む)。 */}
                    {actualModel?.queued && !showArrivedCard && (
                      <button
                        type="button"
                        onClick={() => setSheetKind('arrival')}
                        className="mt-2 inline-flex h-11 items-center gap-1.5 rounded-full border border-brand-primary bg-bg-base px-3.5 text-[13px] font-bold text-brand-primary-hover"
                        data-testid="mobile-detail-adjust-queued"
                      >
                        <Clock className="h-4 w-4 shrink-0" aria-hidden="true" />
                        到着の時刻を合わせる
                      </button>
                    )}
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
              )}

              {/* ---- 音声記録 (設計 §2-1 導線 A / §10-5) ------------------
                  到着前でも訪問中でも**同じ 1 つ**を置く。分岐して別々に置くと
                  到着打刻の瞬間に片方が unmount され、停止したまま保存前だった
                  録音が巻き添えで消える (レビュー H-C)。見出しだけ出し分ける。 */}
              {visit.status !== 'cancelled' && !effectiveCompleted && (
                <VoiceRecorderPanel
                  visitId={visitId}
                  patientId={visit.patient_id}
                  patientName={patientName}
                  heading={effectiveCheckedIn ? '音声記録' : '音声記録（到着前でも録音できます）'}
                  onSaved={() => void refreshVoicePending()}
                />
              )}

              {visit.status !== 'cancelled' &&
                effectiveCheckedIn &&
                !effectiveCompleted &&
                isToday && (
                  <CheckInButton onClick={() => startScan('departure')}>
                    <QrCode className="h-5 w-5" />
                    QRで退出を記録
                  </CheckInButton>
                )}

              {/* 退出の読み取りが無いとき: シートの退出側で時刻を入れる (設計 §7-5)。 */}
              {visit.status !== 'cancelled' &&
                effectiveCheckedIn &&
                !effectiveCompleted &&
                canAdjustOnServer && (
                  <button
                    type="button"
                    onClick={() => setSheetKind('departure')}
                    className="flex min-h-11 w-full items-center justify-center rounded-md px-3 text-sm text-text-secondary underline"
                    data-testid="mobile-detail-manual-departure"
                  >
                    退出の QR を読んでいないときは、退出時刻を入れる
                  </button>
                )}

              {visit.status !== 'cancelled' && effectiveCompleted && (
                <Alert className="flex items-center gap-3">
                  <Rakusuke pose="cheer" className="h-12 shrink-0" />
                  <div>
                    <AlertTitle>訪問完了</AlertTitle>
                    <AlertDescription>
                      {canAdjustOnServer
                        ? 'おつかれさまでした！時刻は上の「時刻を合わせる」から調整できます。'
                        : 'この訪問はチェックアウト済みです。おつかれさまでした！'}
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

          {/* ---- 訪問記録 (要約が主役・設計 §2-3) ------------------------ */}
          {recordings.length > 0 && (
            <div className="space-y-2" data-testid="visit-records">
              <h2 className="text-sm font-bold text-text-secondary">訪問記録</h2>
              {recordings.map((r) => (
                <VisitRecordCard key={r.id} recording={r} />
              ))}
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

      {/* ---- 実績の時刻を合わせるシート (設計 §7-4) --------------------- */}
      {visit && sheetModel && (
        <ActualTimeSheet
          open={sheetKind != null}
          onOpenChange={(next) => {
            if (!next) setSheetKind(null);
          }}
          initialKind={sheetKind ?? 'arrival'}
          patientName={patientName}
          planStart={hmToMinutes(visit.start_time) ?? 0}
          planEnd={hmToMinutes(visit.end_time) ?? 0}
          arrival={sheetModel.arrival}
          departure={sheetModel.departure}
          isToday={isToday}
          departureDisabled={sheetModel.queued}
          departureManual={!sheetModel.queued && !!visit.actual_departure_manual}
          onCancelManualDeparture={cancelManualDeparture}
          saving={adjustBusy}
          onSave={saveFromSheet}
        />
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
  /** QR を読み取った瞬間 (ISO 8601)。この時刻で記録する。 */
  readAt: string;
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
  readAt,
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

      {/* 記録されるのは「記録する」を押した時刻ではなく、読み取った時刻。 */}
      <p
        className="tnum rounded-lg bg-bg-muted px-3 py-2 text-center text-sm text-text-secondary"
        data-testid="preview-read-time"
      >
        読み取った時刻 <b className="text-base text-text-primary">{jstHm(readAt)}</b> で記録します
      </p>

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
