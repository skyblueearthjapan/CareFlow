/**
 * TanStack Query hooks for the v2 Course (Layer 2) API.
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.3 (Layer 2
 * コース分け) と API 契約 ``docs/plans/v2-api-contracts.md`` §4 に対応する
 * コース生成・CRUD・固定の HTTP 呼び出しをラップする (W4-FE8)。
 *
 * Endpoints:
 *   GET    /api/v1/courses                    (list)
 *   POST   /api/v1/courses                    (create)
 *   PATCH  /api/v1/courses/{id}               (update)
 *   POST   /api/v1/courses/generate           (Layer 2 案生成 — W4-BE8 並行作業)
 *   POST   /api/v1/courses/{id}/fix           (個別コース確定 — W2-BE4 / W4-BE8)
 *
 * RBAC:
 *   - Admin / Manager のみ。staff は 403 (BE で担保)。
 *
 * 実装メモ (W4-BE8 並行):
 *   - W4-BE8 完了前は POST /courses/generate が 404 を返す可能性がある。
 *     UI 側はエラーを toast 表示してフォールバック (空案) になるよう
 *     呼び出し側 (CourseProposal) でハンドリングする。
 *   - GenerateCourseProposalsResponse の `courses[].patient_ids` は
 *     CourseV2Read には含まれない拡張フィールド。BE 側で生成時に同行する
 *     visits の patient_id を集めて返す前提で型定義している。完了前は
 *     省略 / undefined が返る可能性があるため optional として扱う。
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { mondayOfIsoWeek } from '@/lib/format/isoWeek';

// ─────────────────────────────────────────────────────────────────────────
// Path constants
// ─────────────────────────────────────────────────────────────────────────

const COURSES_PATH = '/api/v1/courses';
const COURSES_GENERATE_PATH = '/api/v1/courses/generate';
const COURSES_ASSIGN_STAFF_PATH = '/api/v1/courses/assign-staff';
const VISITS_PATH = '/api/v1/visits';

const COURSES_KEY = ['courses'] as const;

// ─────────────────────────────────────────────────────────────────────────
// Types — CourseV2Read 系 (BE schema と同期)
// ─────────────────────────────────────────────────────────────────────────

/** §3.6.5: A〜D = 通常コース 4 つ + E = 拡張コース (W16) + M = マネージャー枠. */
export type CourseCodeV2 = 'A' | 'B' | 'C' | 'D' | 'E' | 'M';

/** BE `app/schemas/v2/enums.py::CourseStatus`. */
export type CourseStatusV2 = 'proposed' | 'course_fixed' | 'staff_assigned';

/** GET /api/v1/courses[/{id}] レスポンス (CourseV2Read).
 *
 * BE `app/schemas/v2/course.py::CourseV2Read` と同期。
 *  - W15-BE-FIXPATTERN (migration 0020) で `office_id` が必須化された。
 *  - W16 Phase B の course_template 逆引きで `office_id` を参照する。
 */
export interface CourseV2Read {
  id: string;
  iso_year: number;
  iso_week: number;
  /** 0=Mon..6=Sun. */
  weekday: number;
  code: CourseCodeV2;
  course_status: CourseStatusV2;
  assigned_staff_id: string | null;
  /** 所属拠点 (W15-BE-FIXPATTERN 以降必須). */
  office_id: string;
  note: string | null;
  course_fixed_at: string | null;
  staff_assigned_at: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

/** POST /api/v1/courses リクエスト (CourseV2Create).
 *
 * BE 仕様 (`app/schemas/v2/course.py`) では `office_id` は W15-BE-FIXPATTERN
 * (migration 0020) より必須化されているが、Wave 16 Phase B 時点では FE 側に
 * `office_id` 引数を取らない legacy 呼出 (`useFixCourses` 内) が残っており、
 * 型を完全 strict にすると当面コンパイルが落ちるため optional のままに
 * している。後続 Wave で legacy 呼出を整理した上で必須化する。
 */
export interface CourseV2Create {
  iso_year: number;
  iso_week: number;
  weekday: number;
  code: CourseCodeV2;
  office_id?: string;
  course_status?: CourseStatusV2;
  assigned_staff_id?: string | null;
  note?: string | null;
}

/** PATCH /api/v1/courses/{id} リクエスト (CourseV2Update). */
export interface CourseV2Update {
  course_status?: CourseStatusV2;
  assigned_staff_id?: string | null;
  note?: string | null;
  course_fixed_at?: string | null;
  staff_assigned_at?: string | null;
  /**
   * Wave U-3: 1 ユーザー操作 = 1 UUID。担当 dropdown 変更を op-log に記録するために
   * 使用。省略可 (旧 BE 互換)。
   */
  op_group_id?: string | null;
  /**
   * NG スタッフ / 性別制限に抵触する担当変更を「確認済み」として通す
   * (patient-ng-staff-design.md §7-2)。 未指定で違反があると BE が 422
   * `constraint_confirmation_required` を返すので、 確認ダイアログ後に true で再送する。
   */
  acknowledge_constraint_warnings?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// Types — POST /courses/generate (Layer 2 案生成, W4-BE8)
// ─────────────────────────────────────────────────────────────────────────

/** POST /api/v1/courses/generate リクエストボディ (API 契約 §4.4). */
export interface GenerateCourseProposalsRequest {
  iso_year: number;
  iso_week: number;
  /** 0=Mon..6=Sun. 1 曜日に対して 1 回の生成. */
  weekday: number;
  /** 稼働スタッフ数 (= 通常 4)。M コースは別枠 (§3.6.5)。 */
  staff_count: number;
}

/**
 * 案生成レスポンス内の 1 コース。
 *
 * `CourseV2Read` を継承しつつ、生成時に同行する `visits.patient_id` を
 * 集約した `patient_ids` を運ぶ。BE 実装 (W4-BE8) が完了するまでは
 * `patient_ids` は undefined / 空配列の可能性がある。
 */
export interface CourseProposalV2 extends CourseV2Read {
  /** 当該コースに割り当てられた患者 ID 一覧 (生成時の案). */
  patient_ids?: string[];
}

/** POST /api/v1/courses/generate レスポンス (API 契約 §4.4). */
export interface GenerateCourseProposalsResponse {
  courses: CourseProposalV2[];
  total_distance_km: number;
  validity_score: number;
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function authPair(session: ReturnType<typeof useSession>['data']): {
  accessToken: string | null;
  refreshToken: string | null;
} {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

// isoWeekToMonday は lib/format/isoWeek の mondayOfIsoWeek と完全同一のため削除し import に統一。

/** Date → 'YYYY-MM-DD' (UTC ベース). */
function toIsoDate(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/**
 * (iso_year, iso_week, weekday) → 当該曜日 1 日のみの 'YYYY-MM-DD'.
 *
 * weekday は 0=Mon..6=Sun (BE 仕様).
 */
function isoWeekdayToDate(isoYear: number, isoWeek: number, weekday: number): string {
  const monday = mondayOfIsoWeek(isoYear, isoWeek);
  monday.setUTCDate(monday.getUTCDate() + weekday);
  return toIsoDate(monday);
}

// ─────────────────────────────────────────────────────────────────────────
// Hooks
// ─────────────────────────────────────────────────────────────────────────

export interface CoursesListParams {
  iso_year?: number;
  iso_week?: number;
  weekday?: number;
  course_status?: CourseStatusV2;
  limit?: number;
  offset?: number;
}

/** GET /api/v1/courses — list (週・曜日でフィルタ). */
export function useCourses(params: CoursesListParams = {}): UseQueryResult<CourseV2Read[], Error> {
  const { data: session, status } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  const qs = new URLSearchParams();
  if (params.iso_year !== undefined) qs.set('iso_year', String(params.iso_year));
  if (params.iso_week !== undefined) qs.set('iso_week', String(params.iso_week));
  if (params.weekday !== undefined) qs.set('weekday', String(params.weekday));
  if (params.course_status !== undefined) qs.set('course_status', params.course_status);
  qs.set('limit', String(params.limit ?? 100));
  qs.set('offset', String(params.offset ?? 0));

  return useQuery<CourseV2Read[], Error>({
    queryKey: [...COURSES_KEY, 'list', Object.fromEntries(qs.entries())],
    enabled: status === 'authenticated',
    queryFn: () =>
      fetcher<CourseV2Read[]>(`${COURSES_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      }),
  });
}

/**
 * POST /api/v1/courses/generate — Layer 2 のコース分け案を生成 (W4-BE8).
 *
 * `weekday` を引数に取り、固定の (iso_year, iso_week) は呼び出し側で
 * リクエスト組み立て時に渡す形を採用 (W3-FE5 と同じ流儀)。
 *
 * UI 流れ:
 *   1. ユーザが「コース案を生成」を押下
 *   2. mutateAsync を呼び、結果 (CourseProposalV2[]) を CourseProposal の
 *      ローカル state に保持
 *   3. ドラッグで微調整後、「コース固定」で個別の create / update を順次発火
 *
 * onSuccess で `courses` クエリを invalidate するが、`/generate` は
 * (W4-BE8 仕様によっては) ステートレスな案生成のみで DB に書き込まない
 * 可能性もあるため、念のため list キャッシュも軽く触る。
 */
export function useGenerateCourseProposals(): UseMutationResult<
  GenerateCourseProposalsResponse,
  Error,
  GenerateCourseProposalsRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<GenerateCourseProposalsResponse, Error, GenerateCourseProposalsRequest>({
    mutationFn: (payload) =>
      fetcher<GenerateCourseProposalsResponse>(COURSES_GENERATE_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSES_KEY });
    },
  });
}

/** POST /api/v1/courses — 単一コース作成 (主にコース固定で使用). */
export function useCreateCourse(): UseMutationResult<CourseV2Read, Error, CourseV2Create> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CourseV2Read, Error, CourseV2Create>({
    mutationFn: (payload) =>
      fetcher<CourseV2Read>(COURSES_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSES_KEY });
    },
  });
}

/** PATCH /api/v1/courses/{id} — 単一コース更新. */
export function useUpdateCourse(): UseMutationResult<
  CourseV2Read,
  Error,
  { id: string; patch: CourseV2Update }
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CourseV2Read, Error, { id: string; patch: CourseV2Update }>({
    mutationFn: ({ id, patch }) =>
      fetcher<CourseV2Read>(`${COURSES_PATH}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSES_KEY });
      // 担当変更は BE 側で visits.primary_staff_id にも伝播する (courses.py)。
      // 職員スケジュール盤面は visits の primary で行を決めるため visits も再取得
      // (週空間 A1: 貼り付け直後に帯が移動して見えるように)。
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// useFixCourses — コース構成を一括確定する複合フック
// ─────────────────────────────────────────────────────────────────────────

/** 単一コースの「確定したい」状態 (CourseRow から渡される). */
export interface CourseFixEntry {
  /** A/B/C/D/M. */
  code: CourseCodeV2;
  /** そのコースに属する患者 ID 一覧. */
  patient_ids: string[];
  /** 既に DB に存在する場合のみ。新規生成案ならば undefined. */
  course_id?: string | null;
}

export interface FixCoursesRequest {
  iso_year: number;
  iso_week: number;
  weekday: number;
  entries: CourseFixEntry[];
}

export interface FixCoursesResponse {
  /** 作成 / 更新された course id (code 順). */
  course_ids: string[];
  /** 各 visit に course_id を埋めようとした件数 (試行ベース). */
  visits_updated: number;
  /** visit 紐付け時に発生した非致命的エラー数 (該当 visit が無い等). */
  visits_skipped: number;
}

interface VisitV2Lite {
  id: string;
  patient_id: string;
  course_id: string | null;
}

/**
 * useFixCourses — 当該 (iso_year, iso_week, weekday) の Course 構成を確定する。
 *
 * 流れ:
 *   1. 既存コースを GET /courses?iso_year=...&iso_week=...&weekday=... で取得
 *   2. 各 entry について
 *        - 既存コースが無ければ POST /courses (status=course_fixed)
 *        - 既存コースがあれば PATCH /courses/:id (status=course_fixed)
 *   3. 当該日の visits を GET /visits?iso_year=...&iso_week=...&weekday=... で取得し、
 *      patient_id ベースで `course_id` を更新 (PATCH /visits/:id)
 *
 * 注意 (受入基準):
 *   - 1 曜日 = 1 トランザクションを期待する設計だが、現行 BE は per-resource
 *     しか提供していないため、UI 側で逐次 fetcher() を呼ぶ。失敗時は
 *     toast でユーザに通知し、すでに成功した分はそのまま残す (= idempotent
 *     再試行を許容)。
 *
 * W4-BE8 で `/courses/{id}/fix` (status 遷移専用) や bulk 確定 endpoint が
 * 整ったらこのフックの内部実装は差し替え可能。外部 API は安定させる。
 */
export function useFixCourses(): UseMutationResult<FixCoursesResponse, Error, FixCoursesRequest> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<FixCoursesResponse, Error, FixCoursesRequest>({
    mutationFn: async (req) => {
      const { iso_year, iso_week, weekday, entries } = req;
      const courseIds: string[] = [];
      const fixedAt = new Date().toISOString();

      // 1. 既存コースを fetch
      const listQs = new URLSearchParams({
        iso_year: String(iso_year),
        iso_week: String(iso_week),
        weekday: String(weekday),
        limit: '100',
        offset: '0',
      });
      const existing = await fetcher<CourseV2Read[]>(`${COURSES_PATH}?${listQs.toString()}`, {
        accessToken,
        refreshToken,
      });
      const existingByCode = new Map<CourseCodeV2, CourseV2Read>();
      for (const c of existing) {
        existingByCode.set(c.code, c);
      }

      // 2. entries を逐次 create / update
      //    - status を course_fixed に遷移させる際は course_fixed_at も同送する
      //      (Reviewer Mid-1: BE 側で自動補完していないため FE から明示的に設定)
      for (const entry of entries) {
        const found = existingByCode.get(entry.code);
        if (found) {
          const updated = await fetcher<CourseV2Read>(`${COURSES_PATH}/${found.id}`, {
            method: 'PATCH',
            body: JSON.stringify({
              course_status: 'course_fixed',
              course_fixed_at: fixedAt,
            } satisfies CourseV2Update),
            accessToken,
            refreshToken,
          });
          courseIds.push(updated.id);
          existingByCode.set(entry.code, updated);
        } else {
          const created = await fetcher<CourseV2Read>(COURSES_PATH, {
            method: 'POST',
            body: JSON.stringify({
              iso_year,
              iso_week,
              weekday,
              code: entry.code,
              course_status: 'course_fixed',
            } satisfies CourseV2Create),
            accessToken,
            refreshToken,
          });
          // 新規作成直後は course_fixed_at が NULL のままになる可能性があるため
          // 続けて PATCH で同じ確定タイムスタンプを書き込む。
          if (created.course_fixed_at == null) {
            const stamped = await fetcher<CourseV2Read>(`${COURSES_PATH}/${created.id}`, {
              method: 'PATCH',
              body: JSON.stringify({
                course_fixed_at: fixedAt,
              } satisfies CourseV2Update),
              accessToken,
              refreshToken,
            });
            courseIds.push(stamped.id);
            existingByCode.set(entry.code, stamped);
          } else {
            courseIds.push(created.id);
            existingByCode.set(entry.code, created);
          }
        }
      }

      // 3. visits.course_id を patient_id ベースで紐付け
      //    BE は iso_year/iso_week/weekday を受け付けないため、当該曜日 1 日に
      //    絞った week_start/week_end (= 同一日) で問い合わせる
      //    (Reviewer M-1 修正: 別曜日 visit を誤って書き換えないようにする)。
      const dayDate = isoWeekdayToDate(iso_year, iso_week, weekday);
      const visitsQs = new URLSearchParams({
        week_start: dayDate,
        week_end: dayDate,
        limit: '500',
        offset: '0',
      });
      let visitsUpdated = 0;
      let visitsSkipped = 0;
      let dayVisits: VisitV2Lite[] = [];
      try {
        dayVisits = await fetcher<VisitV2Lite[]>(`${VISITS_PATH}?${visitsQs.toString()}`, {
          accessToken,
          refreshToken,
        });
      } catch {
        // visits がまだ無い (Layer 1 未確定) ケースもあり得るのでスキップ
        dayVisits = [];
      }

      // patient_id -> 目標 course_id を構築
      const patientToCourse = new Map<string, string>();
      for (const entry of entries) {
        const courseRow = existingByCode.get(entry.code);
        if (!courseRow) continue;
        for (const pid of entry.patient_ids) {
          patientToCourse.set(pid, courseRow.id);
        }
      }

      for (const v of dayVisits) {
        const targetCourseId = patientToCourse.get(v.patient_id);
        if (!targetCourseId) {
          visitsSkipped += 1;
          continue;
        }
        if (v.course_id === targetCourseId) {
          continue; // 既に正しい
        }
        try {
          await fetcher<unknown>(`${VISITS_PATH}/${v.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ course_id: targetCourseId }),
            accessToken,
            refreshToken,
          });
          visitsUpdated += 1;
        } catch {
          visitsSkipped += 1;
        }
      }

      return {
        course_ids: courseIds,
        visits_updated: visitsUpdated,
        visits_skipped: visitsSkipped,
      };
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: COURSES_KEY });
      // visits も invalidate (今後のタブ切替で最新化)
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────
// useAssignStaff — Layer 3 スタッフ割付実行 (W4-FE9 / 対応 BE: W4-BE9)
// ─────────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/courses/assign-staff リクエストボディ (API 契約 §4.6).
 *
 * 1 リクエスト = 1 週分。BE 側 (W4-BE9) で全曜日 × 全コースに対し
 * ハンガリアン法 + ローテーション分散でスタッフを割り当てる。
 */
export interface AssignStaffRequest {
  iso_year: number;
  iso_week: number;
}

/** 1 件の割付結果。`weekday × course_id` がキー。 */
export interface StaffAssignmentEntry {
  /** 0=Mon..6=Sun. */
  weekday: number;
  /** コース記号 (A/B/C/D/M)。 */
  course_code: string;
  /** Course ID (= CourseV2Read.id). */
  course_id: string;
  /** 割り当てられたスタッフ ID. */
  staff_id: string;
}

/** POST /api/v1/courses/assign-staff レスポンス (API 契約 §4.6 / W4-BE9 実装). */
export interface AssignStaffResponse {
  assignments: StaffAssignmentEntry[];
  /** ローテーション分散度 (Gini 係数等). 低いほど均等に分散. */
  rotation_score: number;
  /** 全コース合算の総直線距離 (km). */
  total_distance_km: number;
}

/**
 * useAssignStaff — Layer 3 スタッフ割付を実行する。
 *
 * 流れ:
 *   1. 「スタッフ割付を実行」ボタン (StaffAssignButton) 押下で mutateAsync
 *   2. BE が当該週の `course_fixed` 全 course に対して staff_id を埋め、
 *      状態を `staff_assigned` に進める (`staff_assigned_at` も埋まる)
 *   3. onSuccess で `courses` キャッシュを invalidate → 画面更新
 *
 * W4-BE9 並行作業中の挙動:
 *   - エンドポイント未実装の場合は 404 が返るため、呼び出し側 (StaffAssignButton)
 *     で toast.error にフォールバックする。
 */
export function useAssignStaff(): UseMutationResult<
  AssignStaffResponse,
  Error,
  AssignStaffRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<AssignStaffResponse, Error, AssignStaffRequest>({
    mutationFn: (payload) =>
      fetcher<AssignStaffResponse>(COURSES_ASSIGN_STAFF_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      // courses は staff_id / status / staff_assigned_at が変わるので必ず再取得
      void qc.invalidateQueries({ queryKey: COURSES_KEY });
      // visits も将来的に staff_id を持つ可能性があるため invalidate
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
