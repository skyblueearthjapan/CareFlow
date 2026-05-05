'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import {
  ArrowLeft,
  Camera,
  Clock,
  MapPin,
  Phone,
  StickyNote,
} from 'lucide-react';

import { ApiError } from '@/lib/api-client';
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
  return (
    visit.status === 'in_progress' ||
    visit.status === 'checked_in'
  );
}

function isCompleted(visit: MyVisit | undefined): boolean {
  if (!visit) return false;
  return (
    visit.status === 'done' ||
    visit.status === 'completed' ||
    visit.status === 'checked_out'
  );
}

export default function MobileVisitDetailPage() {
  const params = useParams<{ visitId: string }>();
  const visitId = params?.visitId ?? '';
  const { data: visit, isLoading, isError, error } = useMyVisit(visitId);

  const checkIn = useCheckIn(visitId);
  const checkOut = useCheckOut(visitId);

  // Local optimistic state — backend endpoint is not implemented yet, so we
  // keep an in-memory "checked in" flag to drive the UI. Once the server
  // returns a fresh visit with `status === 'checked_in'`, this can be
  // removed in favor of `visit.status`.
  const [localStatus, setLocalStatus] = useState<
    'idle' | 'checked_in' | 'checked_out'
  >('idle');

  // Reset local status when the visit id changes.
  const lastVisitIdRef = useRef(visitId);
  useEffect(() => {
    if (lastVisitIdRef.current !== visitId) {
      lastVisitIdRef.current = visitId;
      setLocalStatus('idle');
    }
  }, [visitId]);

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function handleCheckIn() {
    const geo = await getGeolocation();
    if (geo.lat === undefined) {
      toast.warning('位置情報を取得できませんでした', {
        description: '位置情報なしで記録します',
      });
    }
    const payload: CheckInPayload = {
      ...geo,
      at: new Date().toISOString(),
    };
    try {
      await checkIn.mutateAsync(payload);
      setLocalStatus('checked_in');
      toast.success('チェックインしました');
    } catch (err) {
      // TODO(W2-C+): backend `/api/v1/visits/{id}/checkin` 未実装。
      // 暫定でローカル状態のみ更新する。
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setLocalStatus('checked_in');
        toast.warning('サーバー未対応のため端末のみ記録', {
          description: '記録はバックエンド実装後に同期されます',
        });
      } else {
        toast.error('チェックインに失敗しました', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  async function handleCheckOut() {
    const geo = await getGeolocation();
    const payload: CheckInPayload = {
      ...geo,
      at: new Date().toISOString(),
    };
    try {
      await checkOut.mutateAsync(payload);
      setLocalStatus('checked_out');
      toast.success('チェックアウトしました');
    } catch (err) {
      // TODO(W2-C+): backend 未実装。同上のローカル fallback。
      if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
        setLocalStatus('checked_out');
        toast.warning('サーバー未対応のため端末のみ記録', {
          description: '記録はバックエンド実装後に同期されます',
        });
      } else {
        toast.error('チェックアウトに失敗しました', {
          description: err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  function handlePhotoSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    // TODO(W2-C+): /api/v1/visits/{id}/photos に POST する。
    toast.info('写真を選択しました', {
      description: `${file.name} (アップロードは準備中)`,
    });
    e.target.value = '';
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
                <p className="text-xs text-text-muted">
                  {visit.visit_date}
                </p>
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
              <CheckInButton
                onClick={handleCheckIn}
                loading={checkIn.isPending}
              >
                チェックイン
              </CheckInButton>
            )}
            {effectiveCheckedIn && !effectiveCompleted && (
              <CheckInButton
                onClick={handleCheckOut}
                loading={checkOut.isPending}
                tone="outline"
              >
                チェックアウト
              </CheckInButton>
            )}
            {effectiveCompleted && (
              <Alert>
                <AlertTitle>訪問完了</AlertTitle>
                <AlertDescription>
                  この訪問はチェックアウト済みです。
                </AlertDescription>
              </Alert>
            )}

            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-dashed border-border-default bg-bg-base p-3 text-sm text-text-secondary hover:bg-bg-muted"
            >
              <Camera className="h-4 w-4" />
              写真を撮影 / 選択
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={handlePhotoSelected}
            />
          </div>
        </>
      )}
    </MobileSection>
  );
}
