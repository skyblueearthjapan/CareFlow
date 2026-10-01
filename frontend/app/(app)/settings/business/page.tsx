'use client';

/**
 * /settings/business — 事業所の情報 (患者 QR カードのお問い合わせ先とロゴ)。
 *
 *   GET /api/v1/business-profile → 5 項目 (未設定は null)
 *   PUT /api/v1/business-profile → 変えた項目だけ送る (空欄 = 未設定に戻す)
 *
 * 以前はお客様の値をコード (`lib/qr-print-contact.ts`) に直接書いていた。別の事業所へ
 * 提供できるよう設定に移した (mig 0089・multi-office-readiness-audit #1)。
 * 閲覧は全ロール、保存は管理者 (BE の RBAC が最終防衛。/settings/checkin と同じ)。
 * ロゴはファイルのアップロードは持たず、画像のパス (`/brand/...`) か https の URL を入れる。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useSession } from 'next-auth/react';
import { Building2, Info } from 'lucide-react';

import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/sonner';
import { useBusinessProfile, useUpdateBusinessProfile } from '@/lib/queries/businessProfile';
import {
  BUSINESS_PROFILE_MAX_LENGTH,
  isAllowedLogoUrl,
  type BusinessProfile,
  type BusinessProfileUpdate,
} from '@/lib/schemas/businessProfile';
import { isAdminRole } from '@/lib/rbac';

type Draft = Record<keyof BusinessProfile, string>;

const FIELDS: { key: keyof BusinessProfile; label: string; hint: string; placeholder: string }[] = [
  {
    key: 'station_name',
    label: '事業所名',
    hint: 'カードの下に署名として載せます。',
    placeholder: '例: 訪問看護ステーション ○○',
  },
  {
    key: 'contact_tel',
    label: '電話番号',
    hint: 'カードで最も大きく出す情報です。',
    placeholder: '例: 03-1234-5678',
  },
  {
    key: 'contact_hours',
    label: '対応時間',
    hint: '電話を受けられる時間です。',
    placeholder: '例: 9:00〜18:00',
  },
  {
    key: 'contact_days',
    label: '対応日',
    hint: 'お休みの注記も含めて書けます。',
    placeholder: '例: 日曜・年末年始を除く',
  },
  {
    key: 'logo_url',
    label: 'ロゴ画像',
    hint: 'アプリに入っている画像のパス（「/」から始まる）か、https から始まる URL を入れてください。空欄にするとロゴは載せません。',
    placeholder: '例: /brand/logo.svg',
  },
];

function toDraft(p: BusinessProfile): Draft {
  return {
    station_name: p.station_name ?? '',
    contact_tel: p.contact_tel ?? '',
    contact_hours: p.contact_hours ?? '',
    contact_days: p.contact_days ?? '',
    logo_url: p.logo_url ?? '',
  };
}

export default function BusinessSettingsPage() {
  const { data: session, status } = useSession();
  const canEdit = isAdminRole(session?.user?.role);

  const profileQuery = useBusinessProfile();
  const updateMutation = useUpdateBusinessProfile();
  const server = useMemo(
    () => (profileQuery.data ? toDraft(profileQuery.data) : null),
    [profileQuery.data],
  );
  const [draft, setDraft] = useState<Draft | null>(null);

  // 変えた項目だけ送る (空欄は null = 未設定に戻す)。
  const changedPayload = useMemo<BusinessProfileUpdate>(() => {
    if (!draft || !server) return {};
    const out: BusinessProfileUpdate = {};
    (Object.keys(draft) as (keyof BusinessProfile)[]).forEach((k) => {
      if (draft[k].trim() !== server[k]) out[k] = draft[k].trim() || null;
    });
    return out;
  }, [draft, server]);
  const isDirty = Object.keys(changedPayload).length > 0;
  const logoInvalid = draft != null && !isAllowedLogoUrl(draft.logo_url);

  // サーバ値が変わったら draft を入れ替える (編集中は上書きしない)。
  const isDirtyRef = useRef(isDirty);
  isDirtyRef.current = isDirty;
  useEffect(() => {
    if (!server) return;
    setDraft((d) => (d === null || !isDirtyRef.current ? server : d));
  }, [server]);

  const handleSave = async () => {
    if (!isDirty || logoInvalid) return;
    try {
      await updateMutation.mutateAsync(changedPayload);
      toast.success('事業所の情報を保存しました');
    } catch {
      toast.error('保存できませんでした。入力内容をご確認ください');
    }
  };

  if (status === 'loading') return null;

  return (
    <section className="mx-auto w-full max-w-3xl space-y-6 pb-24">
      <header className="space-y-1">
        <RakusukeTitle
          pose="think"
          title="事業所の情報"
          subtitle="患者さま宅に貼る QR カードに載せる、お問い合わせ先とロゴです。"
        />
      </header>

      {profileQuery.isError && (
        <Alert variant="destructive">
          <AlertDescription>設定を読み込めませんでした。再読み込みしてください。</AlertDescription>
        </Alert>
      )}

      {profileQuery.isLoading || !draft ? (
        <Skeleton className="h-80 w-full" />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Building2 className="h-5 w-5 text-brand-primary" strokeWidth={1.75} />
              QR カードのお問い合わせ先
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            {FIELDS.map((f) => (
              <div key={f.key}>
                <label
                  htmlFor={`business-${f.key}`}
                  className="mb-1 block text-sm font-medium text-text-primary"
                >
                  {f.label}
                </label>
                <Input
                  id={`business-${f.key}`}
                  value={draft[f.key]}
                  maxLength={BUSINESS_PROFILE_MAX_LENGTH[f.key]}
                  placeholder={f.placeholder}
                  onChange={(e) => setDraft((d) => (d ? { ...d, [f.key]: e.target.value } : d))}
                  disabled={!canEdit || updateMutation.isPending}
                  aria-invalid={f.key === 'logo_url' && logoInvalid ? true : undefined}
                />
                <p className="mt-1 text-xs text-text-muted">{f.hint}</p>
              </div>
            ))}
            {logoInvalid && (
              <p className="text-xs font-medium text-error" data-testid="logo-url-warning">
                ロゴは「/」から始まるパスか、https から始まる URL で入れてください。
              </p>
            )}
            {draft.logo_url.trim() !== '' && !logoInvalid && (
              <div className="rounded-lg border border-border-default bg-white p-3">
                <span className="mb-2 block text-xs text-text-muted">ロゴの見え方</span>
                {/* eslint-disable-next-line @next/next/no-img-element -- 設定したパス/URL をそのまま確認するため */}
                <img
                  src={draft.logo_url.trim()}
                  alt="ロゴの見え方"
                  className="h-12 w-auto"
                  data-testid="logo-preview"
                />
              </div>
            )}
            <p className="flex items-start gap-1.5 text-xs text-text-muted">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
              空欄の項目はカードに載りません。変えたあとに印刷するカードから反映されます（貼ってあるカードは差し替えが必要です）。
            </p>
            <p className="text-xs">
              <Link href="/patients/qr-print?mode=bulk" className="text-brand-primary underline">
                QR 印刷の画面で見え方を確かめる
              </Link>
            </p>
          </CardContent>
        </Card>
      )}

      {draft && (
        <div className="sticky bottom-0 flex items-center justify-end gap-3 border-t border-border-default bg-bg-base/95 px-1 py-3 backdrop-blur">
          <Button
            type="button"
            onClick={handleSave}
            disabled={!canEdit || !isDirty || logoInvalid || updateMutation.isPending}
            data-testid="save"
          >
            {updateMutation.isPending ? '保存中…' : '保存'}
          </Button>
        </div>
      )}
    </section>
  );
}
