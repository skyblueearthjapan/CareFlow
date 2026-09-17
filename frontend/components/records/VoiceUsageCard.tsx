'use client';
/**
 * 「音声記録の利用状況」カード（設計 §11-3 の費用ダッシュボード）。
 *
 * 連携コンソールの既存カード群の末尾に置く。月を選ぶと BE の月次集計
 * （`GET /admin/visit-recordings/usage?month=`）を読み、件数・音声分・トークン・費用を出す。
 *
 * - 円は **固定レートの概算**（`lib/voice-usage-rate.ts`）。会計の正ではないので必ず注記する。
 * - 失敗件数は 0 でなければ warning トーン（見落とすと AI が空回りし続けるため）。
 * - 一般ロールは BE が 403 を返す。カードごと隠さず「管理者のみ表示できます」を出す
 *   （RBAC は「全ロール同一表示・権限外は disabled」＝ PO 決定）。なお現在の配置先
 *   （連携ページ）はページ自体が admin ガード済みなので、この非 admin 分岐は
 *   **部品としての契約**（別の画面に置き直したときに効く保険）。
 */
import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

import { RakusukeNote } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import { jstDateString } from '@/lib/format/patientStatus';
import { isAdminRole } from '@/lib/rbac';
import { useVoiceUsage, type VoiceUsage } from '@/lib/queries/visit-recordings';
import { formatJpyApprox, formatUsd, formatUsdTotal } from '@/lib/voice-usage-rate';

/** 円は固定レート換算である旨（PO への説明を毎回口頭でしないための注記）。 */
const RATE_NOTE = '円は固定レート（1 USD = 150 円）の概算です。実際の請求額とは一致しません。';

/** `YYYY-MM` → 「2026年9月」。 */
export function formatUsageMonthLabel(month: string): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  return m ? `${Number(m[1])}年${Number(m[2])}月` : month;
}

/** `YYYY-MM` に月を足す。 */
export function shiftUsageMonth(month: string, delta: number): string {
  const [y = 0, m = 1] = month.split('-').map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, '0')}`;
}

/** 選べる月 = 当月から過去 11 か月（新しい順）。基準は JST の今日。 */
export function voiceUsageMonthOptions(current = jstDateString().slice(0, 7)): string[] {
  return Array.from({ length: 12 }, (_, i) => shiftUsageMonth(current, -i));
}

/** BE は Decimal を文字列で返すことがある。読めない値は 0 に落とす。 */
function toNumber(value: number | string | null | undefined): number {
  if (value == null) return 0;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function formatInt(value: number | null | undefined): string {
  return Math.round(value ?? 0).toLocaleString('ja-JP');
}

/** 合計分は「1,234 分（20.6 時間）」— 分だけだと規模が掴めない。 */
function formatMinutes(minutes: number): string {
  const hours = minutes / 60;
  return `${formatInt(minutes)} 分（${hours.toFixed(1)} 時間）`;
}

function Stat({
  label,
  value,
  sub,
  testId,
}: {
  label: string;
  value: string;
  sub?: string;
  testId?: string;
}) {
  return (
    <div className="rounded-md bg-bg-muted px-3 py-2" data-testid={testId}>
      <p className="text-xs text-text-muted">{label}</p>
      <p className="tnum text-sm font-semibold text-text-primary">{value}</p>
      {sub && <p className="tnum text-xs text-text-secondary">{sub}</p>}
    </div>
  );
}

/** 管理者以外・403 のときの表示（カードの枠は残す）。 */
function AdminOnly({ note }: { note?: string }) {
  return (
    <p className="text-sm text-text-muted" data-testid="voice-usage-admin-only">
      管理者のみ表示できます{note ? `（${note}）` : ''}
    </p>
  );
}

function UsageBody({ usage }: { usage: VoiceUsage }) {
  const recordings = usage.recordings ?? 0;
  const failed = usage.failed ?? 0;
  const byStaff = (usage.by_staff ?? []).filter(
    (r) => (r.recordings ?? 0) > 0 || toNumber(r.cost_usd) > 0,
  );

  if (recordings === 0 && failed === 0) {
    return (
      <RakusukeNote
        pose="think"
        title="この月はまだ記録がありません"
        comment="録音が保存されると、件数と費用がここに出ます。"
        size="sm"
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="件数" value={`${formatInt(recordings)} 件`} testId="voice-usage-recordings" />
        <Stat
          label="合計分"
          value={formatMinutes(toNumber(usage.minutes_total))}
          testId="voice-usage-minutes"
        />
        <Stat
          label="トークン"
          value={`入 ${formatInt(usage.tokens_in ?? 0)}`}
          sub={`出 ${formatInt(usage.tokens_out ?? 0)}`}
          testId="voice-usage-tokens"
        />
        <Stat
          label="費用"
          // 月合計は通貨として読むので 2 桁（明細＝スタッフ別は 4 桁のまま）。
          value={formatUsdTotal(usage.cost_usd)}
          sub={formatJpyApprox(usage.cost_usd)}
          testId="voice-usage-cost"
        />
      </div>

      {failed > 0 && (
        <p
          className="rounded-md bg-warning-bg px-3 py-2 text-sm font-medium text-warning"
          data-testid="voice-usage-failed"
        >
          失敗 {formatInt(failed)} 件 —
          文字起こしか要約が止まっています。記録一覧で理由をご確認ください。
        </p>
      )}

      {byStaff.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="voice-usage-by-staff">
            <thead>
              <tr className="border-b border-border-default text-left text-xs text-text-muted">
                <th scope="col" className="py-1 pr-2 font-normal">
                  スタッフ
                </th>
                <th scope="col" className="py-1 pr-2 text-right font-normal">
                  件数
                </th>
                <th scope="col" className="py-1 pr-2 text-right font-normal">
                  分
                </th>
                <th scope="col" className="py-1 text-right font-normal">
                  費用
                </th>
              </tr>
            </thead>
            <tbody>
              {byStaff.map((row, i) => (
                <tr key={row.staff_id ?? `row-${i}`} className="border-b border-border-default/60">
                  <td className="py-1 pr-2 text-text-primary">{row.staff_name ?? '（不明）'}</td>
                  <td className="tnum py-1 pr-2 text-right text-text-primary">
                    {formatInt(row.recordings ?? 0)}
                  </td>
                  <td className="tnum py-1 pr-2 text-right text-text-primary">
                    {formatInt(toNumber(row.minutes))}
                  </td>
                  <td className="tnum py-1 text-right text-text-primary">
                    {formatUsd(row.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs text-text-secondary" data-testid="voice-usage-rate-note">
        {RATE_NOTE}
      </p>
    </div>
  );
}

export function VoiceUsageCard() {
  const { data: session } = useSession();
  const isAdmin = isAdminRole(session?.user?.role);

  // 長時間開きっぱなしのタブでも日付が進めば選択肢が入れ替わるよう、毎レンダーで
  // JST の当月を取り直す（PlanActualReportCard と同じ流儀）。
  const todayMonth = jstDateString().slice(0, 7);
  const options = useMemo(() => voiceUsageMonthOptions(todayMonth), [todayMonth]);
  const [month, setMonth] = useState(todayMonth);

  // 一般ロールは BE が 403 を返すので問い合わせない（無駄な 403 を積まない）。
  const usageQuery = useVoiceUsage(isAdmin ? month : null);
  const forbidden = usageQuery.error instanceof ApiError && usageQuery.error.status === 403;

  return (
    <Card className="p-5" data-testid="voice-usage-card">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-serif text-lg font-bold text-text-primary">音声記録の利用状況</h2>
        <span className="text-sm text-text-secondary">文字起こし・要約にかかった費用（月次）</span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label htmlFor="voice-usage-month" className="text-sm text-text-secondary">
          対象月
        </label>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label="前の月"
          disabled={!isAdmin}
          onClick={() => setMonth((m) => shiftUsageMonth(m, -1))}
          data-testid="voice-usage-prev"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <select
          id="voice-usage-month"
          value={month}
          disabled={!isAdmin}
          onChange={(e) => setMonth(e.target.value)}
          className="h-9 rounded-md border border-border-default bg-bg-base px-3 text-sm tabular-nums text-text-primary disabled:opacity-60"
          data-testid="voice-usage-month-select"
        >
          {/* 選択中の月が選択肢の外（12 か月より前）へ動いても表示が消えないようにする。 */}
          {(options.includes(month) ? options : [month, ...options]).map((m) => (
            <option key={m} value={m}>
              {formatUsageMonthLabel(m)}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label="次の月"
          // 未来の月は集計が存在しない。当月で止める。
          disabled={!isAdmin || month >= todayMonth}
          onClick={() => setMonth((m) => shiftUsageMonth(m, 1))}
          data-testid="voice-usage-next"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>

      {!isAdmin && <AdminOnly />}
      {isAdmin && forbidden && <AdminOnly note="この操作は許可されていません" />}
      {isAdmin && !forbidden && usageQuery.isPending && (
        <div className="space-y-2" data-testid="voice-usage-loading">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}
      {isAdmin && !forbidden && usageQuery.isError && (
        <p className="text-sm font-medium text-error" data-testid="voice-usage-error">
          {apiErrorMessage(usageQuery.error, '利用状況を読み込めませんでした')}
        </p>
      )}
      {isAdmin && usageQuery.data && <UsageBody usage={usageQuery.data} />}
    </Card>
  );
}
