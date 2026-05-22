'use client';

/**
 * /admin/feature-flags — Phase G-21 T4.
 *
 * Office × feature_key 単位で feature flag を有効/無効化するページ. admin only.
 *
 * 現在の feature_key は `g21_new_algorithm` のみ. 拠点 (office) ごとに toggle.
 *
 * - GET  /api/v1/office-feature-flags                    (一覧)
 * - POST /api/v1/office-feature-flags                    (upsert)
 * - DELETE /api/v1/office-feature-flags/{office}/{key}   (削除 = 無効 + 履歴クリア)
 *
 * UI:
 *   - 拠点ごとに 1 行
 *   - 列: 拠点名 / g21_new_algorithm enabled / enabled_at / enabled_by / 操作
 *   - toggle 操作はその場で POST (enabled の反転). DELETE は「履歴消去」ボタンから.
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { toast } from '@/components/ui/sonner';

import {
  useDeleteOfficeFeatureFlag,
  useOfficeFeatureFlags,
  useSetOfficeFeatureFlag,
} from '@/lib/queries/g21';
import { useOffices } from '@/lib/queries/offices';
import { OFFICE_FEATURE_KEYS, type OfficeFeatureKey } from '@/lib/schemas/g21';

// ─────────────────────────────────────────────────────────────────────────
// Labels
// ─────────────────────────────────────────────────────────────────────────

const FEATURE_KEY_LABEL: Record<OfficeFeatureKey, string> = {
  g21_new_algorithm: 'G-21 新アルゴリズム',
};

const FEATURE_KEY_DESC: Record<OfficeFeatureKey, string> = {
  g21_new_algorithm:
    '完全固定 / 同住所紐付け / 拠点間移動コスト を考慮した新自動最適化エンジンを有効化します.',
};

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export default function AdminFeatureFlagsPage() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const isAdmin = session?.user?.role === 'admin';

  // Soft client-side guard; BE も RBAC を enforce する.
  useEffect(() => {
    if (status === 'authenticated' && !isAdmin) {
      router.replace('/dashboard');
    }
  }, [status, isAdmin, router]);

  const { offices, isLoading: officesLoading, isError: officesError } = useOffices({ limit: 200 });
  const {
    data: flags = [],
    isLoading: flagsLoading,
    isError: flagsError,
    error: flagsErrorObj,
  } = useOfficeFeatureFlags();

  const setFlag = useSetOfficeFeatureFlag();
  const deleteFlag = useDeleteOfficeFeatureFlag();

  // Phase G-21 T4 reviewer H4: DELETE は不可逆操作 (= enabled_at / enabled_by の audit
  // 痕跡も消える) なので window.confirm 1 段階ではなく Dialog で
  //   - 「無効化のみ (toggle OFF)」: enabled=false 上書き (= 履歴は残る)
  //   - 「履歴ごと削除」          : DELETE エンドポイント (= レコード物理削除)
  // の 2 択を提示する.
  const [confirmState, setConfirmState] = useState<{
    officeId: string;
    officeName: string;
    featureKey: OfficeFeatureKey;
  } | null>(null);

  // 拠点 × feature_key → flag の lookup を作る.
  const flagByKey = useMemo(() => {
    const m = new Map<string, (typeof flags)[number]>();
    for (const f of flags) {
      m.set(`${f.office_id}::${f.feature_key}`, f);
    }
    return m;
  }, [flags]);

  if (status === 'loading' || (status === 'authenticated' && !isAdmin)) {
    return null;
  }

  return (
    <section className="space-y-4" data-testid="admin-feature-flags-page">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">機能フラグ</h1>
        <p className="text-sm text-text-secondary">
          拠点ごとに新機能 (G-21 新アルゴリズム等) の有効/無効を切り替えます.
        </p>
      </header>

      {flagsError ? (
        <Alert variant="destructive">
          <AlertTitle>フラグ取得に失敗しました</AlertTitle>
          <AlertDescription>
            {flagsErrorObj instanceof Error ? flagsErrorObj.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      ) : null}

      {OFFICE_FEATURE_KEYS.map((featureKey) => (
        <Card key={featureKey} className="p-5 space-y-3">
          <div>
            <h2 className="font-serif text-lg font-bold text-text-primary">
              {FEATURE_KEY_LABEL[featureKey]}
            </h2>
            <p className="text-xs text-text-muted">{FEATURE_KEY_DESC[featureKey]}</p>
          </div>

          {officesLoading || flagsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : officesError ? (
            <Alert variant="destructive">
              <AlertTitle>拠点取得に失敗しました</AlertTitle>
            </Alert>
          ) : offices.length === 0 ? (
            <p className="text-sm text-text-muted">拠点が登録されていません</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-border-default text-left text-text-secondary">
                  <tr>
                    <th className="px-3 py-2 font-medium">拠点</th>
                    <th className="px-3 py-2 font-medium">状態</th>
                    <th className="px-3 py-2 font-medium">変更日時</th>
                    <th className="px-3 py-2 font-medium">変更者</th>
                    <th className="px-3 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {offices.map((o) => {
                    const flag = flagByKey.get(`${o.id}::${featureKey}`) ?? null;
                    // Phase G-21 T4 reviewer C1: BE は `enabled` を返さない. 「レコードが存在し
                    // かつ enabled_at が立っている」ことを「有効」と派生計算する.
                    // (`flag?.enabled === true` を見ていた旧実装は常に false で Switch が
                    // 描画されていなかった.)
                    const enabled = flag?.enabled_at != null;
                    const isMutating = setFlag.isPending || deleteFlag.isPending;
                    return (
                      <tr
                        key={o.id}
                        className="border-b border-border-default last:border-0"
                        data-testid={`feature-flag-row-${o.id}-${featureKey}`}
                      >
                        <td className="px-3 py-2 font-medium">{o.name}</td>
                        <td className="px-3 py-2">
                          <label className="flex items-center gap-2">
                            <Switch
                              checked={enabled}
                              onCheckedChange={async (next) => {
                                try {
                                  await setFlag.mutateAsync({
                                    office_id: o.id,
                                    feature_key: featureKey,
                                    enabled: next,
                                  });
                                  toast.success(
                                    next
                                      ? `${o.name}: ${FEATURE_KEY_LABEL[featureKey]} を有効化`
                                      : `${o.name}: ${FEATURE_KEY_LABEL[featureKey]} を無効化`,
                                  );
                                } catch (e) {
                                  const msg = e instanceof Error ? e.message : '不明なエラー';
                                  toast.error(`フラグ更新失敗: ${msg}`);
                                }
                              }}
                              disabled={isMutating}
                              aria-label={`${o.name} の ${FEATURE_KEY_LABEL[featureKey]} を切替`}
                              data-testid={`feature-flag-toggle-${o.id}-${featureKey}`}
                            />
                            <span className="text-xs text-text-secondary">
                              {enabled ? '有効' : '無効'}
                            </span>
                          </label>
                        </td>
                        <td className="px-3 py-2 text-text-muted tnum">
                          {flag?.enabled_at ? new Date(flag.enabled_at).toLocaleString() : '--'}
                        </td>
                        <td className="px-3 py-2 text-text-muted tnum">
                          {flag?.enabled_by_user_id
                            ? `${flag.enabled_by_user_id.slice(0, 8)}…`
                            : '--'}
                        </td>
                        <td className="px-3 py-2 text-right">
                          {flag ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={isMutating}
                              onClick={() => {
                                // Phase G-21 T4 reviewer H4: window.confirm 1 段階 → Dialog
                                // 2 択 (無効化のみ / 履歴ごと削除) に変更. 操作の不可逆性 +
                                // 監査情報消失をユーザに明示的に意識させる.
                                setConfirmState({
                                  officeId: o.id,
                                  officeName: o.name,
                                  featureKey,
                                });
                              }}
                              title="無効化 (OFF) または 履歴ごと削除"
                              data-testid={`feature-flag-delete-${o.id}-${featureKey}`}
                            >
                              削除
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      ))}

      {/* Phase G-21 T4 reviewer H4: 「無効化のみ」 vs 「履歴ごと削除」 2 択ダイアログ. */}
      <Dialog
        open={confirmState !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmState(null);
        }}
      >
        <DialogContent
          aria-describedby="feature-flag-confirm-desc"
          data-testid="feature-flag-confirm-dialog"
        >
          <DialogHeader>
            <DialogTitle>
              {confirmState
                ? `${confirmState.officeName}: ${FEATURE_KEY_LABEL[confirmState.featureKey]}`
                : ''}
            </DialogTitle>
            <DialogDescription id="feature-flag-confirm-desc">
              「無効化 (toggle OFF)」 は履歴 (有効化日時 / 操作者)
              を残したまま機能のみを停止します。 「履歴ごと削除」 は audit
              レコードも完全に削除し、過去の有効化情報を二度と確認できなくなります。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmState(null)}
              data-testid="feature-flag-confirm-cancel"
            >
              キャンセル
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={setFlag.isPending || deleteFlag.isPending}
              onClick={async () => {
                if (!confirmState) return;
                try {
                  await setFlag.mutateAsync({
                    office_id: confirmState.officeId,
                    feature_key: confirmState.featureKey,
                    enabled: false,
                  });
                  toast.success(
                    `${confirmState.officeName}: ${FEATURE_KEY_LABEL[confirmState.featureKey]} を無効化しました (履歴は残ります)`,
                  );
                  setConfirmState(null);
                } catch (e) {
                  const msg = e instanceof Error ? e.message : '不明なエラー';
                  toast.error(`無効化失敗: ${msg}`);
                }
              }}
              data-testid="feature-flag-confirm-disable"
            >
              無効化のみ (履歴は残す)
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={setFlag.isPending || deleteFlag.isPending}
              onClick={async () => {
                if (!confirmState) return;
                try {
                  await deleteFlag.mutateAsync({
                    office_id: confirmState.officeId,
                    feature_key: confirmState.featureKey,
                  });
                  toast.success('レコードを削除しました (有効化日時 / 操作者の情報も消失)');
                  setConfirmState(null);
                } catch (e) {
                  const msg = e instanceof Error ? e.message : '不明なエラー';
                  toast.error(`削除失敗: ${msg}`);
                }
              }}
              data-testid="feature-flag-confirm-delete"
            >
              履歴ごと削除 (不可逆)
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
