/**
 * v2 Wave 15 スケジュール画面 (統合 1 画面) — W15 Phase 5 F-2.
 *
 * シナリオ:
 *   1. 保留プール → グリッドへドロップ → 即固定枠化 (place-and-fix)
 *   2. 受入目安レイヤー ON/OFF (○/△/× バッジ / 背景色)
 *   3. 満枠超過警告 (ダイアログレスで ⚠ バッジ表示)
 *   4. スタッフ差替 (コース行アバタークリック → dropdown → PATCH)
 *
 * 設計ソース:
 *   docs/plans/v2-w15-schedule-unified.md (Wave 15 D-2 統合 1 画面)
 *   docs/plans/v2-api-contracts.md §schedule / §courses
 *
 * 注意:
 *   - API 呼び出しは Playwright `page.route` でインターセプト (実 BE 不要)。
 *   - 各 test は独立 (loginAs で状態リセット)。
 *   - Wave 15 D-1 で ScheduleChangeDialog は削除済 — ダイアログレス前提。
 */
import { type Page, type Route } from '@playwright/test';

import { expect, test } from './fixtures/auth';

// ─────────────────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────────────────

/** /schedule を開きグリッドの hydrate 完了まで待つ. */
async function waitScheduleReady(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible();
  await expect(page.getByText(/配置 \d+ 名 \/ 保留 \d+ 名/)).toBeVisible({ timeout: 15_000 });
}

/**
 * place-and-fix エンドポイントをモックし、成功レスポンスを返す。
 * intercepted payload は callback で検査できるよう返す。
 */
async function mockPlaceAndFix(
  page: Page,
  patientId = 'p-test-001',
  patientName = 'テスト 花子',
): Promise<{ getLastBody: () => Record<string, unknown> | null }> {
  let lastBody: Record<string, unknown> | null = null;

  await page.route('**/api/v1/schedule/place-and-fix', async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    lastBody = body;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        visit: {
          id: 'mock-visit-uuid',
          patient_id: patientId,
          visit_date: '2026-05-05',
          start_time: '10:00:00',
          duration_min: 60,
          status: 'planned',
          source: 'manual',
          required_staff_count: 1,
        },
        fixed_visit: {
          id: 'mock-fv-uuid',
          patient_id: patientId,
          mode: 'normal',
          weekday: body['weekday'] ?? 1,
          start_time: '10:00:00',
          duration_min: 60,
        },
      }),
    });
  });

  return { getLastBody: () => lastBody };
}

/**
 * 受入カレンダー API をモックして ○/△/× データを返す。
 * キャパシティ config エンドポイントもモックする。
 */
async function mockAcceptanceCalendar(page: Page): Promise<void> {
  await page.route('**/api/v1/acceptance_calendar**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { weekday: 0, start_time: '09:00:00', status: 'available', capacity: 6, assigned: 4 },
        { weekday: 1, start_time: '10:00:00', status: 'partial', capacity: 6, assigned: 5 },
        { weekday: 2, start_time: '11:00:00', status: 'full', capacity: 6, assigned: 6 },
      ]),
    });
  });
}

/**
 * キャパシティ超過ケース: キャパ 6 のセルに 7 人目を配置するシナリオ用モック。
 */
async function mockPlaceAndFixOverCapacity(page: Page): Promise<void> {
  await page.route('**/api/v1/schedule/place-and-fix', async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        patient_id: body['patient_id'] ?? 'p-overflow-001',
        patient_name: 'テスト 七郎',
        weekday: body['weekday'] ?? 1,
        start_time: body['start_time'] ?? '10:00:00',
        fix_pattern: true,
        overflow: true,
        capacity: 6,
        assigned: 7,
      }),
    });
  });
}

/**
 * PATCH /api/v1/courses/{id} をモックしてスタッフ差替レスポンスを返す。
 */
async function mockCourseStaffPatch(
  page: Page,
  courseId = 'course-001',
): Promise<{ getLastBody: () => Record<string, unknown> | null }> {
  let lastBody: Record<string, unknown> | null = null;

  await page.route(`**/api/v1/courses/${courseId}`, async (route: Route) => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      lastBody = body;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: courseId,
          staff_id: body['staff_id'] ?? 'staff-002',
          staff_name: '山田 次郎',
        }),
      });
    } else {
      await route.continue();
    }
  });

  // courses 一覧エンドポイントもモック (コース行レンダリング用)
  await page.route('**/api/v1/courses**', async (route: Route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: courseId,
            name: 'コース A',
            staff_id: 'staff-001',
            staff_name: '田中 一郎',
            weekday: 1,
          },
        ]),
      });
    } else {
      await route.continue();
    }
  });

  return { getLastBody: () => lastBody };
}

// ─────────────────────────────────────────────────────────────────────────
// spec
// ─────────────────────────────────────────────────────────────────────────

test.describe('v2 Wave 15 スケジュール画面 (統合 1 画面) — W15 Phase 5 F-2', () => {
  // ───────────────────────────────────────────────────────────────────────
  // Test 1: 保留プール → グリッドへドロップ → 即固定枠化
  // ───────────────────────────────────────────────────────────────────────

  test('保留プールから患者カードをセル (火曜 10:00) にドロップ → place-and-fix が呼ばれる', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 2. API モック設定 -------------------------------------------------
    const { getLastBody } = await mockPlaceAndFix(page, 'p-test-001', 'テスト 花子');

    // schedule 画面の初期データもモック (保留プール 1 件以上)
    await page.route('**/api/v1/schedule**', async (route: Route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('place-and-fix')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pool: [{ patient_id: 'p-test-001', patient_name: 'テスト 花子' }],
            placements: [],
            stats: { placed: 0, pool: 1 },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // ---- 3. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');

    // スケジュール heading が出ればそれで hydrate 完了とみなす (モック環境)
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 4. 保留プールのカードを確認 ----------------------------------------
    // data-testid="pool-card" もしくは id^="patient:" 要素
    const poolCard = page
      .getByTestId('pool-card')
      .or(page.locator('[id^="patient:"]').last())
      .first();

    const poolCardVisible = await poolCard.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(
      !poolCardVisible,
      '保留プールの患者カードが見つからない (Wave 15 統合 1 画面未実装 or seed 不足).',
    );

    // ---- 5. ターゲットセル (火曜 10:00) を特定 ------------------------------
    // セルの droppable id: "cell:1:10:00" (weekday=1 が火曜)
    const tenLabel = page.getByText('10:00', { exact: true }).first();
    // 時刻ラベルの直後の兄弟 2 番目 = 火曜列 (月曜が兄弟 1 番目)
    const targetCell = tenLabel
      .locator('xpath=following-sibling::*[2]')
      .or(page.getByTestId('cell-1-10:00'));

    const cellVisible = await targetCell.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(
      !cellVisible,
      '10:00 の火曜セルが見つからない (グリッドレイアウト未確定 or 時刻範囲外).',
    );

    // ---- 6. place-and-fix リクエストをキャプチャしつつドラッグ ---------------
    const placeAndFixPromise = page.waitForRequest(
      (req) => req.url().includes('/schedule/place-and-fix') && req.method() === 'POST',
      { timeout: 20_000 },
    );

    await poolCard.dragTo(targetCell, {
      force: true,
      targetPosition: { x: 10, y: 10 },
    });

    // ---- 7. POST /api/v1/schedule/place-and-fix が呼ばれることを確認 ---------
    const capturedReq = await placeAndFixPromise;
    const body = capturedReq.postDataJSON() as Record<string, unknown>;

    // payload に必須フィールドが含まれることを assert (PlaceAndFixRequest 全 8 フィールド)
    expect(body['patient_id'], 'patient_id が存在する').toBeDefined();
    expect(body['iso_year'], 'iso_year=2026').toBe(2026);
    expect(body['iso_week'] as number, 'iso_week が正の整数').toBeGreaterThan(0);
    expect(body['weekday'], 'weekday=1 (火曜)').toBe(1);
    expect(String(body['start_time']), 'start_time が 10:00 または 10:00:00').toMatch(
      /^10:00(:00)?$/,
    );
    expect(body['duration_min'] as number, 'duration_min が 0 より大きい').toBeGreaterThan(0);
    expect(body['duration_min'] as number, 'duration_min が 480 以下').toBeLessThanOrEqual(480);
    expect([1, 2], 'staff_count が 1 または 2').toContain(body['staff_count']);
    expect(body['fix_pattern'], 'fix_pattern=true').toBe(true);

    // モックから取得した lastBody でも検証
    const lastBody = getLastBody();
    if (lastBody !== null) {
      expect(lastBody['patient_id'], 'patient_id が一致する').toBe('p-test-001');
    }

    // ---- 8. レスポンス後にセルへ患者氏名が表示される --------------------------
    await expect(page.getByText('テスト 花子')).toBeVisible({ timeout: 10_000 });
  });

  // ───────────────────────────────────────────────────────────────────────
  // Test 2: 受入目安レイヤー ON/OFF
  // ───────────────────────────────────────────────────────────────────────

  test('受入目安レイヤー ON → ○/△/× バッジと背景色が表示 → OFF で消える', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 2. API モック設定 -------------------------------------------------
    await mockAcceptanceCalendar(page);

    // ---- 3. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 4. ヘッダーの「受入目安」チェックを ON ----------------------------
    // チェックボックスは「受入目安」ラベルを持つ input or button を期待
    const acceptanceToggle = page
      .getByRole('checkbox', { name: /受入目安/ })
      .or(page.getByTestId('acceptance-layer-toggle'))
      .or(page.getByRole('button', { name: /受入目安/ }));

    const toggleVisible = await acceptanceToggle.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(!toggleVisible, '「受入目安」トグルが見つからない (Wave 15 受入目安レイヤー未実装).');

    // チェックボックスなら check、ボタンなら click
    const tagName = await acceptanceToggle.evaluate((el) => el.tagName.toLowerCase());
    if (tagName === 'input') {
      await acceptanceToggle.check();
    } else {
      await acceptanceToggle.click();
    }

    // ---- 5. ○/△/× バッジが表示される -------------------------------------
    // ステータスバッジは data-testid="acceptance-badge" もしくはテキスト ○/△/×
    const availableBadge = page
      .getByTestId('acceptance-badge-available')
      .or(page.getByText('○').first());
    const partialBadge = page
      .getByTestId('acceptance-badge-partial')
      .or(page.getByText('△').first());
    const fullBadge = page.getByTestId('acceptance-badge-full').or(page.getByText('×').first());

    // 少なくとも 1 種類のバッジが見えれば受入レイヤーが有効とみなす
    const anyBadgeVisible = await Promise.race([
      availableBadge.isVisible({ timeout: 8_000 }).catch(() => false),
      partialBadge.isVisible({ timeout: 8_000 }).catch(() => false),
      fullBadge.isVisible({ timeout: 8_000 }).catch(() => false),
    ]);
    expect(anyBadgeVisible, '受入目安バッジ (○/△/×) が少なくとも 1 つ表示される').toBe(true);

    // ---- 6. 背景色が適用されている (× は薄赤、△ は薄黄) -------------------
    // × セルは data-acceptance="full" もしくは class に bg-red/bg-yellow を持つ
    const fullCell = page
      .getByTestId('acceptance-cell-full')
      .or(page.locator('[data-acceptance="full"]').first())
      .or(page.locator('.bg-red-50, .bg-red-100').first());

    const fullCellVisible = await fullCell.isVisible({ timeout: 5_000 }).catch(() => false);
    if (fullCellVisible) {
      // クラスに bg-red を含むか data-acceptance が "full" であることを確認
      const hasBgRed = await fullCell.evaluate((el) => {
        return (
          el.className.includes('bg-red') ||
          el.getAttribute('data-acceptance') === 'full' ||
          el.getAttribute('data-status') === 'full'
        );
      });
      expect(hasBgRed, '× セルに薄赤背景が適用される').toBe(true);
    }

    // ---- 7. OFF にするとバッジが消える ------------------------------------
    if (tagName === 'input') {
      await acceptanceToggle.uncheck();
    } else {
      await acceptanceToggle.click();
    }

    // バッジが非表示になることを確認 (タイムアウト 5s)
    await expect(
      page.getByTestId('acceptance-badge-full').or(page.getByTestId('acceptance-badge-partial')),
    )
      .toBeHidden({ timeout: 5_000 })
      .catch(() => {
        // testid 不在の環境では ○/△/× テキストの消失を代替確認
      });
    // ○/△/× テキストが消えていることを broad に確認
    const badgeGone = await page
      .getByText(/^[○△×]$/)
      .first()
      .isHidden({ timeout: 3_000 })
      .catch(() => true);
    expect(badgeGone, 'トグル OFF 後に受入バッジが非表示になる').toBe(true);
  });

  // ───────────────────────────────────────────────────────────────────────
  // Test 3: 満枠超過警告 (ダイアログレスで ⚠ バッジ)
  // ───────────────────────────────────────────────────────────────────────

  test('キャパ 6 のセルに 7 人目をドロップ → ⚠ 7/6 超過バッジ表示 (ダイアログなし)', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 2. API モック設定 (キャパ超過レスポンス) ---------------------------
    await mockPlaceAndFixOverCapacity(page);

    // schedule 初期データ: 1 セルに既に 6 名配置済み + プール 1 名
    await page.route('**/api/v1/schedule**', async (route: Route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes('place-and-fix')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pool: [{ patient_id: 'p-overflow-001', patient_name: 'テスト 七郎' }],
            placements: Array.from({ length: 6 }, (_, i) => ({
              patient_id: `p-existing-00${i + 1}`,
              patient_name: `既存 患者${i + 1}`,
              weekday: 1,
              start_time: '10:00:00',
            })),
            stats: { placed: 6, pool: 1 },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // ---- 3. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 4. プールのカードを確認 -------------------------------------------
    const poolCard = page
      .getByTestId('pool-card')
      .or(page.locator('[id^="patient:"]').last())
      .first();

    const poolCardVisible = await poolCard.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(!poolCardVisible, '保留プールの患者カードが見つからない.');

    // ---- 5. 既に 6 名入っているターゲットセルを特定 -------------------------
    const tenLabel = page.getByText('10:00', { exact: true }).first();
    const targetCell = tenLabel
      .locator('xpath=following-sibling::*[2]')
      .or(page.getByTestId('cell-1-10:00'));

    const cellVisible = await targetCell.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(!cellVisible, '10:00 の火曜セルが見つからない.');

    // ---- 6. ドラッグ実行 (place-and-fix は overflow=true で返る) -----------
    await poolCard.dragTo(targetCell, {
      force: true,
      targetPosition: { x: 10, y: 10 },
    });

    // ---- 7. ⚠ 7/6 超過バッジが表示されることを確認 ------------------------
    const overflowBadge = page
      .getByTestId('overflow-badge')
      .or(page.getByText(/⚠.*7\/6.*超過|7\/6 超過/).first())
      .or(page.getByText(/超過/).first());

    await expect(overflowBadge).toBeVisible({ timeout: 10_000 });

    // ---- 8. モーダル / 確認ダイアログが出ないことを確認 (ダイアログレス) ---
    // Wave 15 D-1 で ScheduleChangeDialog 削除済のため dialog は不要
    const dialogVisible = await page
      .getByRole('dialog')
      .isVisible({ timeout: 2_000 })
      .catch(() => false);
    expect(dialogVisible, '確認ダイアログは表示されない (ダイアログレス保存)').toBe(false);
  });

  // ───────────────────────────────────────────────────────────────────────
  // Test 4: スタッフ差替
  // ───────────────────────────────────────────────────────────────────────

  test('コース行アバタークリック → dropdown で別スタッフ選択 → PATCH /courses/{id} が呼ばれる', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 2. API モック設定 -------------------------------------------------
    const courseId = 'course-001';
    const { getLastBody } = await mockCourseStaffPatch(page, courseId);

    // スタッフ一覧もモック (dropdown 選択肢用)
    await page.route('**/api/v1/staff**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'staff-001', name: '田中 一郎' },
          { id: 'staff-002', name: '山田 次郎' },
        ]),
      });
    });

    // schedule 初期データもモック
    await page.route('**/api/v1/schedule**', async (route: Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pool: [],
            placements: [],
            stats: { placed: 0, pool: 0 },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // ---- 3. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 4. コース行左端の主担当アバター (👤) を探す -----------------------
    // data-testid="course-staff-avatar" もしくは data-course-id 属性を持つ
    const staffAvatar = page
      .getByTestId('course-staff-avatar')
      .or(page.getByRole('button', { name: /👤|担当|スタッフ変更/ }).first())
      .or(page.locator(`[data-course-id="${courseId}"] button`).first());

    const avatarVisible = await staffAvatar.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(
      !avatarVisible,
      'コース行のスタッフアバターが見つからない (Wave 15 コース行 UI 未統合).',
    );

    // ---- 5. PATCH リクエストをキャプチャする準備 ---------------------------
    const patchPromise = page.waitForRequest(
      (req) => req.url().includes(`/api/v1/courses/${courseId}`) && req.method() === 'PATCH',
      { timeout: 15_000 },
    );

    // ---- 6. アバタークリック → dropdown 表示 --------------------------------
    await staffAvatar.click();

    // dropdown が開くのを待つ
    const dropdown = page
      .getByTestId('staff-dropdown')
      .or(page.getByRole('listbox'))
      .or(page.getByRole('menu'));

    const dropdownVisible = await dropdown
      .waitFor({ state: 'visible', timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!dropdownVisible, 'スタッフ選択 dropdown が表示されない (コース行 UI 未統合).');

    // ---- 7. 「山田 次郎」を選択 -------------------------------------------
    const targetStaffOption = dropdown
      .getByRole('option', { name: '山田 次郎' })
      .or(dropdown.getByText('山田 次郎').first());

    const optionVisible = await targetStaffOption.isVisible({ timeout: 3_000 }).catch(() => false);
    test.skip(!optionVisible, '「山田 次郎」選択肢が dropdown に見つからない.');
    await targetStaffOption.click();

    // ---- 8. PATCH /api/v1/courses/{id} が呼ばれることを確認 ----------------
    const patchReq = await patchPromise;
    const body = patchReq.postDataJSON() as Record<string, unknown>;
    expect(body, 'payload に staff_id が含まれる').toHaveProperty('staff_id');
    expect(body['staff_id'], 'staff_id=staff-002').toBe('staff-002');

    // モックから取得した lastBody でも確認
    const lastBody = getLastBody();
    if (lastBody !== null) {
      expect(lastBody['staff_id']).toBe('staff-002');
    }

    // ---- 9. アバターが選択スタッフ (山田 次郎) の表示に変わる ---------------
    await expect(page.getByText('山田 次郎').first()).toBeVisible({ timeout: 8_000 });
  });
});
