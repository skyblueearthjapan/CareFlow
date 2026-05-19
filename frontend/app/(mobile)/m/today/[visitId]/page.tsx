'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { ArrowLeft, Camera, Clock, MapPin, Phone, StickyNote } from 'lucide-react';

import { ApiError } from '@/lib/api-client';
import { clearCheckin, loadCheckin, saveCheckin } from '@/lib/checkin-storage';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { toast } from '@/components/ui/sonner';
import { CheckInButton } from '@/components/mobile/CheckInButton';
import { MobileSection } from '@/components/mobile/MobileSection';
import {
  useCheckIn,
  useCheckOut,
  useMyVisit,
  type CheckInPayload,
  type MyVisit,
} from '@/lib/queries/me';
import { useUploadPhoto, useVisitPhotos } from '@/lib/queries/visit-photos';

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

/** Best-effort browser geolocation. Resolves with `undefined` lat/lng on failure. */
function getGeolocation(): Promise<{ lat?: number; lng?: number }> {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      resolve({});
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      () => resolve({}),
      { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 },
    );
  });
}

function isCheckedIn(visit: MyVisit | undefined): boolean {
  if (!visit) return false;
  return visit.status === 'in_progress' || visit.status === 'checked_in';
}

function isCompleted(visit: MyVisit | undefined): boolean {
  if (!visit) return false;
  return visit.status === 'done' || visit.status === 'completed' || visit.status === 'checked_out';
}

export default function MobileVisitDetailPage() {
  const params = useParams<{ visitId: string }>();
  const visitId = params?.visitId ?? '';
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const { data: visit, isLoading, isError, error } = useMyVisit(visitId);

  const checkIn = useCheckIn(visitId);
  const checkOut = useCheckOut(visitId);

  // The check-in/out backend endpoints are not implemented yet (Phase 5
  // backlog). Rather than dropping the user's tap on the floor when they
  // navigate away, we persist a small record under
  // `checkin:{staffId}:{visitId}` in localStorage (see lib/checkin-storage)
  // so the screen rehydrates the correct UI state on revisit. mutateAsync
  // is currently expected to reject; only specific ApiError statuses are
  // treated as "server not implemented yet". Once the server ships,
  // `onSuccess` clears the entry and `visit.status` becomes the source of
  // truth again.

  // Seed from localStorage so a previous check-in survives navigation.
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

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // W4-D: real photo upload wired to /api/v1/visits/{id}/photos.
  const { data: photos } = useVisitPhotos(visitId);
  const uploadPhoto = useUploadPhoto(visitId);

  /**
   * "Server can't accept this yet" — only the explicit ApiError statuses
   * 404 (route missing), 405 (method not allowed), 501 (not implemented).
   * Network failures are NOT swallowed here; they surface as a retry toast
   * so the user knows the tap did not reach the server.
   */
  function isUnimplementedServerError(err: unknown): boolean {
    if (!(err instanceof ApiError)) return false;
    return err.status === 404 || err.status === 405 || err.status === 501;
  }

  /** True when the failure is a fetch-level network error (no ApiError). */
  function isNetworkError(err: unknown): boolean {
    return !(err instanceof ApiError);
  }

  async function handleCheckIn() {
    if (!staffId || !visitId) {
      toast.error('ユーザー情報を取得できませんでした');
      return;
    }
    const geo = await getGeolocation();
    if (geo.lat === undefined) {
      toast.warning('位置情報を取得できませんでした', {
        description: '位置情報なしで記録します',
      });
    }
    const at = new Date().toISOString();
    const payload: CheckInPayload = { ...geo, at };
    try {
      await checkIn.mutateAsync(payload);
      // Server accepted → drop the local fallback record.
      clearCheckin(staffId, visitId);
      setLocalStatus('checked_in');
      toast.success('チェックインしました');
    } catch (err) {
      if (isUnimplementedServerError(err)) {
        saveCheckin(staffId, visitId, {
          status: 'checked_in',
          at,
          ...(geo.lat !== undefined ? { lat: geo.lat } : {}),
          ...(geo.lng !== undefined ? { lng: geo.lng } : {}),
        });
        setLocalStatus('checked_in');
        toast.warning('現在オフライン保存中', {
          description: 'サーバ実装後に同期されます',
        });
      } else if (isNetworkError(err)) {
        toast.error('ネットワークに接続できませんでした', {
          description: '通信状態を確認して再度お試しください',
        });
      } else {
        toast.error('チェックインに失敗しました', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  async function handleCheckOut() {
    if (!staffId || !visitId) {
      toast.error('ユーザー情報を取得できませんでした');
      return;
    }
    const geo = await getGeolocation();
    const at = new Date().toISOString();
    const payload: CheckInPayload = { ...geo, at };
    try {
      await checkOut.mutateAsync(payload);
      clearCheckin(staffId, visitId);
      setLocalStatus('checked_out');
      toast.success('チェックアウトしました');
    } catch (err) {
      if (isUnimplementedServerError(err)) {
        saveCheckin(staffId, visitId, {
          status: 'checked_out',
          at,
          ...(geo.lat !== undefined ? { lat: geo.lat } : {}),
          ...(geo.lng !== undefined ? { lng: geo.lng } : {}),
        });
        setLocalStatus('checked_out');
        toast.warning('現在オフライン保存中', {
          description: 'サーバ実装後に同期されます',
        });
      } else if (isNetworkError(err)) {
        toast.error('ネットワークに接続できませんでした', {
          description: '通信状態を確認して再度お試しください',
        });
      } else {
        toast.error('チェックアウトに失敗しました', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  async function handlePhotoSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Always reset the input so re-selecting the same file fires `change`.
    e.target.value = '';
    if (!file) return;
    try {
      await uploadPhoto.mutateAsync({ file });
      toast.success('写真をアップロードしました', {
        description: file.name,
      });
    } catch (err) {
      toast.error('写真のアップロードに失敗しました', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  // The displayed status combines server truth with the optimistic local flag.
  const effectiveCheckedIn = isCheckedIn(visit) || localStatus === 'checked_in';
  const effectiveCompleted = isCompleted(visit) || localStatus === 'checked_out';
  const meta = effectiveCompleted
    ? statusLabel('done')
    : effectiveCheckedIn
      ? statusLabel('in_progress')
      : statusLabel(visit?.status ?? 'planned');

  return (
    <MobileSection
      title="訪問詳細"
      action={
        <Link
          href="/m/today"
          className="inline-flex h-10 w-10 items-center justify-center rounded-full text-text-secondary hover:bg-bg-muted"
          aria-label="戻る"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
      }
    >
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
                <p className="font-serif text-lg font-bold text-text-primary">
                  {visit.patient_name ?? '(患者名未設定)'}
                </p>
                <p className="text-xs text-text-muted">{visit.visit_date}</p>
              </div>
              <Badge variant={meta.variant}>{meta.label}</Badge>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2 text-text-secondary">
                <Clock className="h-4 w-4 shrink-0" />
                <span className="tnum">
                  {shortTime(visit.start_time)} - {shortTime(visit.end_time)}
                </span>
              </div>
              {visit.note && (
                <div className="flex items-start gap-2 text-text-secondary">
                  <StickyNote className="h-4 w-4 shrink-0 mt-0.5" />
                  <span className="whitespace-pre-wrap">{visit.note}</span>
                </div>
              )}
              {/* Address / phone live on the patient record; surfaced as
                  TODO until the patient relation is included in VisitRead. */}
              <div className="flex items-center gap-2 text-text-muted">
                <MapPin className="h-4 w-4 shrink-0" />
                <span>住所は患者マスタを参照</span>
              </div>
              <div className="flex items-center gap-2 text-text-muted">
                <Phone className="h-4 w-4 shrink-0" />
                <span>連絡先は患者マスタを参照</span>
              </div>
            </div>
          </Card>

          <div className="space-y-2">
            {!effectiveCheckedIn && !effectiveCompleted && (
              <CheckInButton onClick={handleCheckIn} loading={checkIn.isPending}>
                チェックイン
              </CheckInButton>
            )}
            {effectiveCheckedIn && !effectiveCompleted && (
              <CheckInButton onClick={handleCheckOut} loading={checkOut.isPending} tone="outline">
                チェックアウト
              </CheckInButton>
            )}
            {effectiveCompleted && (
              <Alert>
                <AlertTitle>訪問完了</AlertTitle>
                <AlertDescription>この訪問はチェックアウト済みです。</AlertDescription>
              </Alert>
            )}

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

            {photos && photos.length > 0 && (
              <div className="grid grid-cols-3 gap-2 pt-1">
                {photos.map((p) => (
                  <a
                    key={p.id}
                    href={p.download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="aspect-square overflow-hidden rounded-md border border-border-default bg-bg-muted"
                  >
                    {/* Thumbnail uses the same download URL — the route
                        serves the binary; browser handles decode. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={p.download_url}
                      alt={p.caption ?? '訪問写真'}
                      className="h-full w-full object-cover"
                      loading="lazy"
                    />
                  </a>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </MobileSection>
  );
}
