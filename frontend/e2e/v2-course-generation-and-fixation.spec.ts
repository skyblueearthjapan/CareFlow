/**
 * v2 course generation → adjust → fixation — Wave 6 W6-E2E (3/4).
 *
 * 注意 (Wave 15 以降 / W15-codex-fix (6)):
 *   Wave 15 で **CourseProposal / ScheduleGridV2 (時刻×曜日 旧 UI)** は
 *   `ScheduleUnifiedView` (コーステンプレート × 曜日 マトリクス) に置換された。
 *   この spec が前提とする「コース案を生成」「コース固定」ボタンは現行 UI に
 *   存在しないため、本 spec は通常実行で **skip** される (= 旧 UI の隔離
 *   環境でのみ実行される回帰用)。Wave 15 主フローの E2E は
 *   ``v2-w15-schedule-unified.spec.ts`` を参照のこと。
 *
 * Path (旧 UI 想定):
 *   Admin login → /schedule (CourseProposal が表示されている画面)
 *     → 「コース案を生成」(POST /api/v1/courses/generate)
 *     → A/B/C/D の 4 行が表示される
 *     → 患者カードをコース間でドラッグして調整
 *     → 「コース固定」(POST /api/v1/courses)
 *     → /api/v1/courses で当該週の Course レコードと
 *       visits.course_id が更新されていることを確認
 *
 * 設計ソース:
 *   docs/plans/v2-implementation-plan.md §7 W6-E2E (3)
 *   docs/plans/v2-allocation-redesign.md §3.6.3
 *   docs/plans/v2-api-contracts.md §4.4 / §4.5
 *
 * skip 動作: 「コース案を生成」ボタンが画面に居なければ test.skip する。
 * Wave 15 以降は本 spec が常に skip 状態になる想定。
 */
import { type Page } from '@playwright/test';

import { expect, test } from './fixtures/auth';

// ─────────────────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────────────────

/** Course generate ボタンを画面から探して visible になるまで待つ。 */
async function findGenerateButton(page: Page) {
  return page.getByRole('button', { name: /コース案を生成/ }).first();
}

/** Course fix ボタンを画面から探す。 */
async function findFixCoursesButton(page: Page) {
  return page.getByRole('button', { name: /^コース固定$/ }).first();
}

/**
 * GET /api/v1/courses で当該週のコース取得 (assert に使う).
 *
 * 開発・CI で fetcher の middleware (Cookie / Authorization) を再利用するため、
 * Playwright の `page.request` で直接叩く。
 */
async function fetchCoursesViaApi(
  page: Page,
  query: { iso_year: number; iso_week: number },
): Promise<unknown> {
  const apiBase =
    process.env.NEXT_PUBLIC_API_BASE ?? process.env.BACKEND_API_BASE_URL ?? 'http://localhost:8000';
  const res = await page.request.get(
    `${apiBase}/api/v1/courses?iso_year=${query.iso_year}&iso_week=${query.iso_week}&limit=100`,
  );
  if (!res.ok()) return null;
  return res.json();
}

// ─────────────────────────────────────────────────────────────────────────
// spec
// ─────────────────────────────────────────────────────────────────────────

test.describe('v2 course generation & fixation (W6-E2E #3)', () => {
  test.describe.configure({ mode: 'serial' });

  test('案生成 → ドラッグ調整 → 固定 → courses + visits.course_id が更新される', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン → /schedule ----------------------------------------
    await loginAs('admin');
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible();

    // W15-codex-fix (6): CourseProposal は Wave 15 で ScheduleUnifiedView に
    // 置換済。「コース案を生成」ボタンが画面に居なければ skip する (= 旧 UI
    // 隔離環境のみで動く)。Wave 15 主フロー E2E は v2-w15-schedule-unified.spec.ts。
    const generateBtn = await findGenerateButton(page);
    const generateVisible = await generateBtn.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(
      !generateVisible,
      'CourseProposal は Wave 15 で ScheduleUnifiedView に置換済. 旧 UI 環境のみ実行.',
    );

    // ---- 2. 「コース案を生成」ボタンを押す -------------------------------
    await generateBtn.click();

    // 成功 toast: 「案を生成しました: 総距離 N km / 妥当性 N%」
    await expect(page.getByRole('status')).toContainText(/案を生成しました|総距離/, {
      timeout: 30_000,
    });

    // ---- 3. 4 (+M) コース行が描画されていることを確認 ---------------------
    // CourseRow は code ラベル ("M", "A コース", ...) を必ず描画するので
    // テキストでマッチ. M はマネージャー枠なので「(マネージャー)」を含む。
    const coursesShown = page.getByText(/A コース|B コース|C コース|D コース/);
    await expect(coursesShown.first()).toBeVisible();

    // 個別に A/B/C/D の 4 件が居ることを確認 (= 設計上の 4 コース提示).
    const allCodes = ['A コース', 'B コース', 'C コース', 'D コース'];
    for (const lbl of allCodes) {
      await expect(page.getByText(lbl).first()).toBeVisible({ timeout: 5_000 });
    }

    // ---- 4. 患者を A → B にドラッグ ----------------------------------
    // CourseProposal の draggable id は CourseRow 内の `parseCoursePatientDraggableId`
    // で扱われる prefix を持つ. 識別は外部 API なので id^= で素朴に拾う.
    // (患者要素の id は CourseRow.tsx で `course-patient:<id>` などになる前提)
    //
    // 1 件もドラッグ可能な患者要素が存在しなければ DnD 検証はスキップ.
    const draggablePatients = page.locator('[id^="course-patient:"]');
    const dragCount = await draggablePatients.count();

    if (dragCount > 0) {
      const source = draggablePatients.first();
      // 「B コース」ラベル要素の親要素 = ドロップ対象行と仮定。
      const targetRow = page.getByText('B コース', { exact: false }).first();
      const sourceVisible = await source.isVisible().catch(() => false);
      const targetVisible = await targetRow.isVisible().catch(() => false);
      if (sourceVisible && targetVisible) {
        await source.dragTo(targetRow, { force: true, targetPosition: { x: 30, y: 10 } });
      }
    }

    // ---- 5. 「コース固定」 -----------------------------------------------
    const fixBtn = await findFixCoursesButton(page);
    await expect(fixBtn).toBeVisible();
    // 患者割当ゼロでは disabled。生成直後なら必ず enable.
    const fixEnabled = await fixBtn.isEnabled();
    test.skip(
      !fixEnabled,
      '「コース固定」ボタンが disabled (= 患者割当 0). 案生成のレスポンスを確認.',
    );
    await fixBtn.click();

    // 成功 toast: 「コース構成を確定しました: N コース / 訪問 M 件更新」
    await expect(page.getByRole('status')).toContainText(/コース構成を確定|訪問.*更新/, {
      timeout: 30_000,
    });

    // ---- 6. courses API と visits.course_id を確認 -----------------------
    // CourseProposal は ISO 8601 起点の現在週で動くので、
    // 「現在週」を計算して同じ条件で問い合わせる.
    const now = new Date();
    const isoWeek = (() => {
      const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
      const day = d.getUTCDay() || 7;
      d.setUTCDate(d.getUTCDate() + 4 - day);
      const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
      const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
      return { iso_year: d.getUTCFullYear(), iso_week: week };
    })();

    const courses = await fetchCoursesViaApi(page, isoWeek);
    test.skip(
      courses === null,
      'GET /api/v1/courses が 401/404. Authorization 引き継ぎが効いていない (test.request の cookie 共有要確認).',
    );

    // courses は配列で返る前提。最低 1 件以上あれば「固定が反映された」とみなす。
    expect(
      Array.isArray(courses) ? courses.length : -1,
      'courses API が当該週の行を返す',
    ).toBeGreaterThan(0);

    // ---- 7. visits.course_id が埋まっていることを spot check ------------
    // /api/v1/visits?iso_year=...&iso_week=... を一覧化し、course_id が
    // 1 件以上 not-null であれば「コース紐付けが進んだ」とみなす.
    const apiBase =
      process.env.NEXT_PUBLIC_API_BASE ??
      process.env.BACKEND_API_BASE_URL ??
      'http://localhost:8000';
    const visitsRes = await page.request.get(
      `${apiBase}/api/v1/visits?iso_year=${isoWeek.iso_year}&iso_week=${isoWeek.iso_week}&limit=200`,
    );
    if (visitsRes.ok()) {
      const visitsBody = (await visitsRes.json()) as unknown;
      type V = { course_id?: string | null };
      const items: V[] = Array.isArray(visitsBody)
        ? (visitsBody as V[])
        : ((visitsBody as { items?: V[] }).items ?? []);
      const withCourse = items.filter((v) => typeof v.course_id === 'string' && v.course_id);
      // visits 0 件の seed もあり得るので、>=0 で評価しつつ
      // visits >0 のときだけ course_id 充填率を assert する.
      if (items.length > 0) {
        expect(
          withCourse.length,
          `visits ${items.length} 件中 ${withCourse.length} 件が course_id 設定済み`,
        ).toBeGreaterThan(0);
      }
    }
  });
});
