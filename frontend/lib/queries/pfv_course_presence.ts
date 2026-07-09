'use client';

/**
 * usePfvCoursePresence — 固定訪問スケジュール (PFV) に含まれるコースを「正」として
 * 週/日ビューの列を出すための read-only query hook.
 *
 * GET /api/v1/schedule/v2/pfv-course-presence
 *
 * PO 決定 (2026-07-09): スタッフ削除等で稼働 0 になっても、PFV がコース指定済みなら
 * 列を隠さず (= 既存訪問を可視に保ち) 別途警告を出す、という新原則の表示側根拠.
 * 列の表示条件は「スタッフ数連動 (effectiveCapacity>0)」と本 presence の和集合で判定する.
 *
 * レスポンス: (course_template_id, weekday) ごとの PFV 件数.
 *   - course_template_id IS NULL の PFV / 削除済み患者の PFV は除外済み (BE 側).
 *   - pfv_count > 0 のキーのみ含む.
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 */
import { useMemo } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

const PFV_COURSE_PRESENCE_PATH = '/api/v1/schedule/v2/pfv-course-presence';

/** BE ``PfvCoursePresenceItem`` とミラー. */
export interface PfvCoursePresenceItem {
  course_template_id: string;
  /** 0=Mon..6=Sun. */
  weekday: number;
  pfv_count: number;
}

/** BE ``PfvCoursePresenceResponse`` とミラー. */
export interface PfvCoursePresenceResponse {
  items: PfvCoursePresenceItem[];
}

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * (course_template_id, weekday) → pfv_count を引く軽量ルックアップ.
 * `pfvCountFor(templateId, weekday)` は未存在キーを 0 として返す.
 */
export interface PfvCoursePresenceLookup {
  pfvCountFor: (templateId: string, weekday: number) => number;
  isLoading: boolean;
}

/** GET /api/v1/schedule/v2/pfv-course-presence. */
export function usePfvCoursePresence(): UseQueryResult<PfvCoursePresenceResponse, Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  // BE は admin/manager 限定。staff は RBAC統一でページ自体は閲覧できるため、
  // 役割ゲートで 403 の無駄撃ち(+retry)を防ぐ (レビューMED)。staff は
  // pfvCountFor=0 のフォールバック + 「visit実在」条件で列が見える。
  const role = session?.user?.role;
  return useQuery<PfvCoursePresenceResponse, Error>({
    queryKey: ['pfv-course-presence'],
    enabled: status === 'authenticated' && (role === 'admin' || role === 'manager'),
    // PFV は編集頻度が低いのでフォーカス毎の refetch を抑える (レビューLOW)。
    staleTime: 5 * 60 * 1000,
    queryFn: () =>
      fetcher<PfvCoursePresenceResponse>(PFV_COURSE_PRESENCE_PATH, {
        accessToken,
        refreshToken,
      }),
  });
}

/**
 * usePfvCoursePresence を (course_template_id, weekday) → pfv_count ルックアップに
 * 変換した便利フック. 列の表示条件 (和集合) 判定に直接渡せる.
 */
export function usePfvCoursePresenceLookup(): PfvCoursePresenceLookup {
  const query = usePfvCoursePresence();

  return useMemo(() => {
    const data = query.data;
    const m = new Map<string, number>();
    for (const it of data?.items ?? []) {
      m.set(`${it.course_template_id}:${it.weekday}`, it.pfv_count);
    }
    return {
      pfvCountFor: (templateId: string, weekday: number) => m.get(`${templateId}:${weekday}`) ?? 0,
      isLoading: query.isLoading,
    };
  }, [query.data, query.isLoading]);
}
