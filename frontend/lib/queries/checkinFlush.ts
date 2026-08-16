/**
 * 未送信打刻の再送フック — 「電波が戻り次第、自動で送信します」の実装本体。
 *
 * 退避 (`checkin-queue`) した打刻は、**この フックを載せた画面が開かれたとき**と
 * **`online` イベント**で再送される。載せる画面:
 *   - `/m/today`            … 一覧。退避後に必ず戻る場所なので送信の主トリガー。
 *   - `/m/today/{visitId}`  … 訪問詳細 (退避の発生元)。
 *   - `/q/{token}`          … QR ランディング。予定外 (`adhoc_arrival`) は詳細を
 *                             経由せず退避されるため、次に QR を読んだ機会に送る。
 *
 * 4xx で破棄された分は**黙って消さず**必ずトーストで通知する (キューの契約)。
 * 呼び出し元がトースト処理を書き忘れる事故を防ぐため、通知はここに一本化した。
 */
'use client';

import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { countPending } from '@/lib/checkin-queue';
import { flushCheckinQueue } from '@/lib/checkin-flush';
import { toast } from '@/components/ui/sonner';

export interface UseCheckinFlushResult {
  /** 未送信のまま残っている件数 (表示用)。 */
  pendingCount: number;
  /** いま再送する (マウント時 / online 時は自動で走る)。 */
  flushNow: () => Promise<void>;
  /** 件数だけ数え直す (退避直後の表示更新用)。 */
  refreshPending: () => void;
}

export function useCheckinFlush(): UseCheckinFlushResult {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();
  const [pendingCount, setPendingCount] = useState(0);

  const refreshPending = useCallback(() => {
    if (!staffId) return;
    setPendingCount(countPending(staffId));
  }, [staffId]);

  const flushNow = useCallback(async () => {
    if (typeof window === 'undefined' || !staffId) return;
    // 同一スタッフの flush は checkin-flush 側で直列化される (画面が複数
    // 載っていても同じ entry を二重 POST しない)。
    const { remaining, dropped } = await flushCheckinQueue(staffId, accessToken, refreshToken);
    setPendingCount(remaining);
    // 4xx で破棄された未送信分は黙って消さず、利用者へ通知する。
    if (dropped.length > 0) {
      toast.error(`未送信の${dropped.length}件は送信できませんでした`, {
        description: dropped[0]?.reason ?? '無効なQR／対象外のため破棄しました',
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

  return { pendingCount, flushNow, refreshPending };
}
