/**
 * QR ディープリンク解決 (GET /api/v1/visits/resolve-qr/{token})。
 *
 * 患者宅の固定 QR を標準カメラ / Chrome で読むと `/q/{token}` へ遷移する。
 * ランディングページ (`app/q/[token]/page.tsx`) が本フックでトークンを
 * 「本日 (JST) のその患者の visit 全件」へ解決する。
 *
 * 第2弾 (担当外・予定外の記録 / 設計 §4-1 凍結コントラクト) でレスポンスが拡張された:
 *   - `patient_name`  … 記録先を誤らないための確認表示 (氏名のみ・住所等は返さない)
 *   - `is_mine`       … 自分の担当集合 (primary/secondary/mentor/assignments/同行) か
 *   - `planned_staff_name` … 予定担当者名 (未割当は null)
 *   - `is_unplanned`  … 予定外訪問として生成された visit か (退出導線・二重生成の抑止)
 *
 * `is_mine` の候補があれば従来どおり自分の visit へ直行し、無ければランディングが
 * 「代行 / 予定外」の選択画面を出す。QR 所持 = 現地に居る証明を認可の鍵とする
 * (設計 §1 決定#1) ため、担当外にも氏名・時間帯・予定担当名までは開示する。
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
  /** 予定担当 (primary) 名。未割当は null。 */
  planned_staff_name: string | null;
  /** 自分の担当集合に含まれるか。 */
  is_mine: boolean;
  /** 予定外訪問として生成された visit か。 */
  is_unplanned: boolean;
}

export interface QrResolveRead {
  /** QR が指す患者の氏名 (確認表示用)。 */
  patient_name: string;
  candidates: QrResolveCandidate[];
}

/**
 * 旧デプロイ (第1弾: 自分の担当のみ・patient_name 無し) のレスポンスも壊れずに
 * 読めるよう既定値を補う。FE と BE のデプロイ順が前後しても、旧 BE では
 * 「全候補 = 自分の担当」なので `is_mine: true` 既定が第1弾の挙動に一致する。
 */
function normalize(raw: unknown): QrResolveRead {
  const obj = (raw ?? {}) as Record<string, unknown>;
  const list = Array.isArray(obj.candidates) ? obj.candidates : [];
  return {
    patient_name: typeof obj.patient_name === 'string' ? obj.patient_name : '',
    candidates: list.map((item) => {
      const c = (item ?? {}) as Record<string, unknown>;
      return {
        visit_id: String(c.visit_id ?? ''),
        start_time: String(c.start_time ?? ''),
        end_time: String(c.end_time ?? ''),
        status: String(c.status ?? 'planned'),
        planned_staff_name: typeof c.planned_staff_name === 'string' ? c.planned_staff_name : null,
        is_mine: c.is_mine === undefined ? true : c.is_mine === true,
        is_unplanned: c.is_unplanned === true,
      };
    }),
  };
}

/**
 * 候補から**自動遷移先**を 1 件選ぶ。
 * 自分の担当 (`is_mine`) のみが対象で、completed 以外 (= これから打刻する枠) の
 * 最初 → 全て completed なら先頭 → 該当なしは null。担当候補ゼロ (= 代行/予定外)
 * は呼び出し側が選択画面を出す。候補はサーバが start_time 昇順で返す前提。
 */
export function pickQrCandidate(candidates: QrResolveCandidate[]): QrResolveCandidate | null {
  const mine = candidates.filter((c) => c.is_mine);
  if (mine.length === 0) return null;
  return mine.find((c) => c.status !== 'completed') ?? mine[0]!;
}

/** GET /api/v1/visits/resolve-qr/{token} — token 無し / 未認証は disabled。 */
export function useResolveQr(token: string | null): UseQueryResult<QrResolveRead, Error> {
  const { data: session, status } = useSession();
  return useQuery<QrResolveRead, Error>({
    queryKey: ['qr-resolve', token],
    enabled: status === 'authenticated' && !!token,
    // 404/410/403 は再試行しても変わらない確定エラー。ランディングは 1 回で
    // 判定を出したいので retry しない (通信断は画面の「再試行」ボタンで手動)。
    retry: false,
    queryFn: async () => {
      const raw = await fetcher<unknown>(
        `/api/v1/visits/resolve-qr/${encodeURIComponent(token ?? '')}`,
        {
          accessToken: session?.accessToken ?? null,
          refreshToken: session?.refreshToken ?? null,
        },
      );
      return normalize(raw);
    },
  });
}
