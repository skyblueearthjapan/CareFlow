/**
 * 親機 (デスクトップ) 統合提案モーダル (ProposeNewModal) 用の確定フック群。
 *
 * 親機方針は manager 直接確定 (承認不要)。モバイル現場ボードの pending
 * `patient_create` フローとは異なり、ここでは:
 *   - 既存 / プール患者 : PUT /api/v1/patients/{id}/fixed-visits で採用枠を normal PFV へ確定。
 *   - 新規患者          : POST /api/v1/patients (作成) → PUT fixed-visits の 2 段階。
 *
 * いずれも patientId が確定したあとに採用枠 (= proposed_visits 相当) を
 * `PatientFixedVisitsBulkPut` (mode='normal') へ変換して PUT する。バックエンドは
 * 既存 PUT /fixed-visits と同じ「当該 (patient_id, mode='normal') を全削除 → INSERT」
 * の冪等上書きで確定する (backend 無改変)。
 *
 * 認証は他の v2 フックと同じく NextAuth セッションの access/refresh token を
 * `fetcher` に渡す。RBAC は呼び出し側 (CourseDayTablePanel canEdit) でガード済み。
 */
'use client';

import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  parsePatientFixedVisitsBulkPutResponse,
  type PatientFixedVisitsBulkPut,
  type PatientFixedVisitsBulkPutResponse,
} from '@/lib/schemas/v2/patient_fixed_visit';

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

export interface ConfirmFixedVisitsVars {
  patientId: string;
  body: PatientFixedVisitsBulkPut;
}

/**
 * PUT /api/v1/patients/{id}/fixed-visits — 採用枠を normal PFV へ確定 (患者非依存版)。
 *
 * `useUpdateFixedVisits` は patientId を引数に取るためモーダル内で患者が後から
 * 決まる (新規作成後) ケースに使いづらい。本フックは `mutate({patientId, body})`
 * の形で患者を実行時に渡せるようにし、成功後に当該患者と全患者の fixed-visits /
 * patients / visits / courses クエリを invalidate して親機テーブルへ即時反映する。
 */
export function useConfirmFixedVisits(): UseMutationResult<
  PatientFixedVisitsBulkPutResponse,
  Error,
  ConfirmFixedVisitsVars
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<PatientFixedVisitsBulkPutResponse, Error, ConfirmFixedVisitsVars>({
    // P0-2 Commit 3: レスポンスを `{items, warnings}` エンベロープとして寛容パースし、
    // onSuccess / mutateAsync の戻り値経由で呼出側が warnings を表示できるようにする。
    // パース失敗時も throw せず空エンベロープにフォールバックし、採用フローは止めない。
    mutationFn: async ({ patientId, body }) => {
      const raw = await fetcher<unknown>(`/api/v1/patients/${patientId}/fixed-visits`, {
        method: 'PUT',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
      return parsePatientFixedVisitsBulkPutResponse(raw);
    },
    onSuccess: (_data, vars) => {
      // 当該患者の固定枠キャッシュ。
      void qc.invalidateQueries({ queryKey: ['patients', vars.patientId, 'fixed-visits'] });
      // 患者一覧 (プール判定 / 検索)。
      void qc.invalidateQueries({ queryKey: ['patients'] });
      // 親機テーブルの実 visit / コース (固定枠確定で週次が変わりうる)。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
    },
  });
}
