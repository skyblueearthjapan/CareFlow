'use client';

/**
 * /acceptance — 受け入れ枠 (PC)。
 *
 * 「何曜日の何時台なら毎週受け入れられるか」を、今週を含む直近 4・6・8 週の
 * カイポケの記録 (終わった日は実績・今日から先は予定) から出す。
 * 正典: docs/plans/acceptance-strict-design-2026-10-20.md §5-1。
 *
 * 担い手・休みの扱い・見る週は画面で集計する (サーバーに問い合わせ直さない)。
 * 毎週の手での上書きは自動より優先し、角に「手」の印。取り直しは管理者だけ。
 * 拠点は「全て」(既定・office_id を付けない) か各拠点。選んだものはこのブラウザに残す。
 */
import { useEffect, useState } from 'react';

import { Printer } from 'lucide-react';
import { useSession } from 'next-auth/react';

import { StrictAcceptanceBoard } from '@/components/acceptance/StrictAcceptanceBoard';
import { RakusukeTitle } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { EVENING_CONFIRM, isEveningJst, officeNotice } from '@/lib/acceptanceStrict';
import { useClearStandingOverride, useSetStandingOverride } from '@/lib/queries/acceptance_matrix';
import {
  useStartStrictRefresh,
  useStrictAcceptance,
  useStrictRefreshJob,
} from '@/lib/queries/acceptance_strict';
import { useOffices } from '@/lib/queries/offices';
import { isAdminRole } from '@/lib/rbac';

const IS_DEV = process.env.NODE_ENV !== 'production';
const OFFICE_STORAGE_KEY = 'acceptance.strict.officeId';

export default function AcceptanceMatrixPage() {
  const { data: session } = useSession();
  const canEdit = isAdminRole(session?.user?.role);

  const officesQuery = useOffices({ limit: 50 });
  const offices = officesQuery.allOffices;
  /** null = 全て (全拠点)。 */
  const [storedOfficeId, setStoredOfficeId] = useState<string | null>(null);
  // 残した拠点を読み終えるまでは読み込まない (先に「全て」の重い計算を走らせないため)。
  const [storageRead, setStorageRead] = useState(false);
  useEffect(() => {
    try {
      setStoredOfficeId(window.localStorage.getItem(OFFICE_STORAGE_KEY) || null);
    } catch {
      // 読めないときは「全て」のまま
    }
    setStorageRead(true);
  }, []);
  // 残っていた拠点が一覧に無ければ「全て」で見る。
  const officeId =
    storedOfficeId && (offices.length === 0 || offices.some((o) => o.id === storedOfficeId))
      ? storedOfficeId
      : null;
  const chooseOffice = (id: string | null) => {
    setStoredOfficeId(id);
    try {
      if (id) window.localStorage.setItem(OFFICE_STORAGE_KEY, id);
      else window.localStorage.removeItem(OFFICE_STORAGE_KEY);
    } catch {
      // 残せなくても画面はそのまま使える
    }
  };
  const [planOnly, setPlanOnly] = useState(false);

  const query = useStrictAcceptance(officeId, {
    planOnly: IS_DEV && planOnly,
    allOffices: officeId === null,
    enabled: storageRead,
  });
  const jobQuery = useStrictRefreshJob();
  const startRefresh = useStartStrictRefresh();
  const setStanding = useSetStandingOverride();
  const clearStanding = useClearStandingOverride();

  const start = (scope: 'recent' | 'all') => {
    if (isEveningJst() && !window.confirm(EVENING_CONFIRM)) return;
    startRefresh.mutate(scope);
  };

  return (
    <section className="space-y-3">
      <header className="space-y-1 no-print">
        <RakusukeTitle
          pose="heart"
          title="受け入れ枠"
          subtitle="毎週通う新しい患者さんを、何曜日の何時台なら受け入れられるかを、直近の数週のカイポケの記録から出しています。"
        />
      </header>

      <Card className="flex flex-wrap items-center gap-3 p-3 no-print">
        <label className="flex items-center gap-2 text-sm text-text-secondary">
          拠点
          <select
            value={officeId ?? ''}
            onChange={(e) => chooseOffice(e.target.value || null)}
            className="rounded border border-border-default bg-bg-base px-2 py-1 text-sm"
            aria-label="拠点"
          >
            <option value="">全て</option>
            {offices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => window.print()}
          className="ml-auto gap-1.5"
        >
          <Printer className="h-4 w-4" />
          印刷
        </Button>
      </Card>

      {officeNotice(officesQuery) && (
        <p className="rounded bg-bg-muted px-3 py-2 text-sm text-text-secondary">
          {officeNotice(officesQuery)}
        </p>
      )}
      {query.isLoading && <p className="text-sm text-text-secondary">読み込み中…</p>}
      {query.isError && (
        <p className="text-sm text-rose-600">読み込みに失敗しました: {query.error.message}</p>
      )}
      {query.data && (
        <StrictAcceptanceBoard
          key={`${query.data.office_id ?? 'all'}-${query.data.plan_only ? 'plan' : 'real'}`}
          data={query.data}
          edit={
            canEdit
              ? {
                  busy: setStanding.isPending || clearStanding.isPending,
                  set: ({ weekday, hour, status, note }) =>
                    setStanding.mutate({
                      officeId,
                      weekday,
                      timeSlot: `${String(hour).padStart(2, '0')}:00:00`,
                      status,
                      notes: note,
                    }),
                  clear: ({ weekday, hour }) =>
                    clearStanding.mutate({
                      officeId,
                      weekday,
                      timeSlot: `${String(hour).padStart(2, '0')}:00:00`,
                    }),
                }
              : undefined
          }
          refresh={
            canEdit ? { job: jobQuery.data, starting: startRefresh.isPending, start } : undefined
          }
          devToggle={
            IS_DEV ? (
              <label className="flex items-center gap-2 text-xs text-text-secondary">
                <input
                  type="checkbox"
                  checked={planOnly}
                  onChange={(e) => setPlanOnly(e.target.checked)}
                />
                開発用: 予定だけで見る
              </label>
            ) : undefined
          }
        />
      )}
    </section>
  );
}
