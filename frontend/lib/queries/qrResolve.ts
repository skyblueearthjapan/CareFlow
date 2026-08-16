/**
 * QR ディープリンク解決 (GET /api/v1/visits/resolve-qr/{token})。
 *
 * 患者宅の固定 QR を標準カメラ / Chrome で読むと `/q/{token}` へ遷移する。
 * ランディングページ (`app/q/[token]/page.tsx`) が本フックでトークンを
 * 「本日 (JST) の自分の担当 visit」へ解決し、`/m/today/{visitId}?qr={token}`
 * へディープリンクする。アプリ内スキャナ経由の従来フローは変更なし。
 *
 * サーバは患者名・住所を返さない (visit_id と時刻・status のみ)。候補ゼロ
 * (担当外患者のトークン含む) は 200 + 空配列 = 患者・担当関係を漏らさない。
 */
'use client';

import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

/** resolve-qr 候補 1 件 (BE `QrResolveCandidate` の写像)。 */
export interface QrResolveCandidate {
  visit_id: string;
  /** `HH:MM:SS`。 */
  start_time: string;
  end_time: string;
  status: string;
}

export interface QrResolveRead {
  candidates: QrResolveCandidate[];
}

/**
 * 候補から遷移先を 1 件選ぶ。
 * completed 以外 (= これから打刻する枠) の最初 → 全て completed なら先頭 →
 * 空は null。候補はサーバが start_time 昇順で返す前提。
 */
export function pickQrCandidate(candidates: QrResolveCandidate[]): QrResolveCandidate | null {
  if (candidates.length === 0) return null;
  return candidates.find((c) => c.status !== 'completed') ?? candidates[0]!;
}

/** GET /api/v1/visits/resolve-qr/{token} — token 無し / 未認証は disabled。 */
export function useResolveQr(token: string | null): UseQueryResult<QrResolveRead, Error> {
  const { data: session, status } = useSession();
  return useQuery<QrResolveRead, Error>({
    queryKey: ['qr-resolve', token ?? '__none__'],
    enabled: status === 'authenticated' && !!token,
    // 404/410/403 は再試行しても変わらない確定エラー。ランディングは 1 回で
    // 判定を出したいので retry しない (通信断は画面の「再試行」ボタンで手動)。
    retry: false,
    queryFn: () =>
      fetcher<QrResolveRead>(`/api/v1/visits/resolve-qr/${encodeURIComponent(token ?? '')}`, {
        accessToken: session?.accessToken ?? null,
        refreshToken: session?.refreshToken ?? null,
      }),
  });
}
