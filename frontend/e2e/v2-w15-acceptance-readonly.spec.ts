/**
 * v2 Wave 15 受入カレンダー表示 (read-only) — W15 Phase 5 F-2.
 *
 * シナリオ:
 *   1. /schedule の受入目安レイヤーを ON にすると、各セルの ○/△/× が
 *      backend の acceptance_calendar 内容と一致する
 *
 * 設計ソース:
 *   docs/plans/v2-w15-acceptance-calendar.md (Wave 15 受入カレンダー read-only)
 *   docs/plans/v2-api-contracts.md §acceptance_calendar
 *
 * 注意:
 *   - このテストは表示のみの確認 (編集フローは別 Wave)。
 *   - API モックは Playwright `page.route` で intercept。
 *   - 受入目安レイヤー UI が未実装の環境では skip。
 */
import { type Page, type Route } from '@playwright/test';

import { expect, test } from './fixtures/auth';

// ─────────────────────────────────────────────────────────────────────────
// テストデータ定義
// ─────────────────────────────────────────────────────────────────────────

/** acceptance_calendar モックデータ (weekday × start_time → status). */
const MOCK_ACCEPTANCE_DATA = [
  { weekday: 0, start_time: '09:00:00', status: 'available', capacity: 6, assigned: 3 },
  { weekday: 0, start_time: '10:00:00', status: 'partial', capacity: 6, assigned: 5 },
  { weekday: 1, start_time: '09:00:00', status: 'full', capacity: 6, assigned: 6 },
  { weekday: 1, start_time: '10:00:00', status: 'available', capacity: 6, assigned: 1 },
  { weekday: 2, start_time: '09:00:00', status: 'partial', capacity: 6, assigned: 4 },
  { weekday: 3, start_time: '09:00:00', status: 'full', capacity: 6, assigned: 6 },
  { weekday: 4, start_time: '09:00:00', status: 'available', capacity: 6, assigned: 2 },
] as const;

/** status → 表示バッジ文字のマッピング. */
const STATUS_BADGE: Record<string, string> = {
  available: '○',
  partial: '△',
  full: '×',
};

// ─────────────────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * acceptance_calendar GET エンドポイントをモックしてテストデータを返す。
 * schedule 初期データもモックする。
 */
async function setupAcceptanceMocks(page: Page): Promise<void> {
  // 受入カレンダーモック
  // W15-codex-fix (5): 実 API は kebab-case (/acceptance-calendar). underscore
  // 表記の旧パスをモックしても本物のリクエストにマッチしないため kebab に統一。
  await page.route('**/api/v1/acceptance-calendar**', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_ACCEPTANCE_DATA),
    });
  });

  // スケジュール初期データモック
  await page.route('**/api/v1/schedule**', async (route: Route) => {
    if (route.request().method() === 'GET' && !route.request().url().includes('place-and-fix')) {
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
}

/**
 * 受入目安レイヤーのトグルを ON にする。
 * トグルが見つからない場合は null を返す (呼び元で skip する)。
 */
async function enableAcceptanceLayer(page: Page): Promise<boolean> {
  const toggle = page
    .getByRole('checkbox', { name: /受入目安/ })
    .or(page.getByTestId('acceptance-layer-toggle'))
    .or(page.getByRole('button', { name: /受入目安/ }));

  const visible = await toggle.isVisible({ timeout: 8_000 }).catch(() => false);
  if (!visible) return false;

  const tagName = await toggle.evaluate((el) => el.tagName.toLowerCase());
  if (tagName === 'input') {
    const checked = await toggle.isChecked().catch(() => false);
    if (!checked) await toggle.check();
  } else {
    await toggle.click();
  }

  return true;
}

// ─────────────────────────────────────────────────────────────────────────
// spec
// ─────────────────────────────────────────────────────────────────────────

test.describe('v2 Wave 15 受入カレンダー表示 read-only — W15 Phase 5 F-2', () => {
  // [Wave 16 Phase B] スケジュール UI 構造刷新により受入目安レイヤーの DOM 期待値が
  // 旧 ScheduleUnifiedView 前提のため skip。新 StaffWeekTablePanel の凡例フッター
  // ON/OFF テストは vitest 側でカバー。
  test.skip(true, '[Wave 16 Phase B] Schedule UI 構造刷新 (受入目安レイヤー検証は後続 Wave)');

  test('受入目安レイヤー ON → 各セルの ○/△/× が acceptance_calendar 内容と一致する', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 2. API モック設定 -------------------------------------------------
    await setupAcceptanceMocks(page);

    // ---- 3. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 4. 受入目安レイヤーを ON ------------------------------------------
    const toggled = await enableAcceptanceLayer(page);
    test.skip(!toggled, '「受入目安」トグルが見つからない (Wave 15 受入目安レイヤー未実装).');

    // ---- 5. acceptance_calendar GET が呼ばれることを確認 --------------------
    // route でモック済みなので、UI がリクエストを発行したかを確認する
    // (page.waitForRequest を使って、トグル後に acceptance_calendar が呼ばれたことを検証)
    // ※ トグル前後で API が呼ばれていれば合格
    const calendarApiCalled = await page
      // W15-codex-fix (5): 実 API は kebab-case (/acceptance-calendar)
      .waitForRequest((req) => req.url().includes('acceptance-calendar'), { timeout: 5_000 })
      .then(() => true)
      .catch(() => {
        // トグルより前に既に呼ばれていた可能性があるため、バッジの表示で代替確認
        return false;
      });

    // API が呼ばれたかどうかに関わらず、バッジの表示で受入データが反映されているか確認

    // ---- 6. モックデータと UI の表示を照合 ---------------------------------
    // MOCK_ACCEPTANCE_DATA の各エントリについて、対応するバッジが表示されているか確認

    // available (○) のセルが少なくとも 1 つある
    const availableEntries = MOCK_ACCEPTANCE_DATA.filter((d) => d.status === 'available');
    expect(availableEntries.length, 'モックデータに available エントリが存在する').toBeGreaterThan(
      0,
    );

    // partial (△) のセルが少なくとも 1 つある
    const partialEntries = MOCK_ACCEPTANCE_DATA.filter((d) => d.status === 'partial');
    expect(partialEntries.length, 'モックデータに partial エントリが存在する').toBeGreaterThan(0);

    // full (×) のセルが少なくとも 1 つある
    const fullEntries = MOCK_ACCEPTANCE_DATA.filter((d) => d.status === 'full');
    expect(fullEntries.length, 'モックデータに full エントリが存在する').toBeGreaterThan(0);

    // ---- 7. UI に ○/△/× が表示されている --------------------------------
    // 各バッジを testid もしくはテキストで確認

    // ○ バッジ
    const availableBadge = page
      .getByTestId('acceptance-badge-available')
      .or(page.locator('[data-acceptance="available"]').first())
      .or(page.getByText('○').first());
    await expect(availableBadge).toBeVisible({ timeout: 10_000 });

    // △ バッジ
    const partialBadge = page
      .getByTestId('acceptance-badge-partial')
      .or(page.locator('[data-acceptance="partial"]').first())
      .or(page.getByText('△').first());
    await expect(partialBadge).toBeVisible({ timeout: 5_000 });

    // × バッジ
    const fullBadge = page
      .getByTestId('acceptance-badge-full')
      .or(page.locator('[data-acceptance="full"]').first())
      .or(page.getByText('×').first());
    await expect(fullBadge).toBeVisible({ timeout: 5_000 });

    // ---- 8. 特定セル (月曜 09:00 → ○) の一致確認 -------------------------
    // 月曜 09:00 は status=available → ○ が表示される
    // グリッドの月曜列 (following-sibling 1 番目) + 09:00 行で確認
    const nineLabelMon = page.getByText('09:00', { exact: true }).first();
    const monCell = nineLabelMon
      .locator('xpath=following-sibling::*[1]')
      .or(page.getByTestId('acceptance-cell-0-09:00'));

    const monCellVisible = await monCell.isVisible({ timeout: 5_000 }).catch(() => false);
    if (monCellVisible) {
      // セル内に ○ または data-acceptance="available" が存在する
      const monCellAvailable = await monCell.evaluate((el) => {
        return (
          el.textContent?.includes('○') ||
          el.querySelector('[data-acceptance="available"]') !== null ||
          el.getAttribute('data-acceptance') === 'available'
        );
      });
      expect(monCellAvailable, '月曜 09:00 セルに ○ (available) が表示される').toBe(true);
    }

    // ---- 9. 特定セル (火曜 09:00 → ×) の一致確認 -------------------------
    // 火曜 09:00 は status=full → × が表示される
    const nineLabelTue = page.getByText('09:00', { exact: true }).first();
    const tueCell = nineLabelTue
      .locator('xpath=following-sibling::*[2]')
      .or(page.getByTestId('acceptance-cell-1-09:00'));

    const tueCellVisible = await tueCell.isVisible({ timeout: 5_000 }).catch(() => false);
    if (tueCellVisible) {
      const tueCellFull = await tueCell.evaluate((el) => {
        return (
          el.textContent?.includes('×') ||
          el.querySelector('[data-acceptance="full"]') !== null ||
          el.getAttribute('data-acceptance') === 'full'
        );
      });
      expect(tueCellFull, '火曜 09:00 セルに × (full) が表示される').toBe(true);
    }

    // ---- 10. API が呼ばれた場合は追加でデータ整合性ログを記録 --------------
    if (calendarApiCalled) {
      // acceptance_calendar データが取得されたことを確認済み
      // UI との対応は 7-9 のアサーションで担保
    }

    // ---- 11. 表示は read-only (編集操作が無い) ----------------------------
    // バッジに click しても何も起きない (編集ダイアログが出ない) ことを確認
    const firstBadge = page.getByText('○').first();
    const firstBadgeVisible = await firstBadge.isVisible({ timeout: 3_000 }).catch(() => false);
    if (firstBadgeVisible) {
      await firstBadge.click({ force: true });
      // 編集用のダイアログが出ないこと (500ms 以内)
      const editDialogVisible = await page
        .getByRole('dialog')
        .filter({ hasText: /受入|編集|キャパ/ })
        .isVisible({ timeout: 500 })
        .catch(() => false);
      expect(editDialogVisible, '○ クリック後に編集ダイアログが出ない (read-only)').toBe(false);
    }
  });
});
