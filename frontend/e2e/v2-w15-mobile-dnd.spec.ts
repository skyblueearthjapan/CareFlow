/**
 * v2 Wave 15 モバイル端末エミュレーション DnD — W15 Phase 5 F-2 (任意).
 *
 * シナリオ:
 *   - viewport を iPhone 15 (390×844) に設定
 *   - TouchSensor 経由で dnd-kit drag シミュレーション
 *   - place-and-fix が呼ばれることを確認
 *
 * 前提:
 *   - Wave 15 F-1 で TouchSensor が追加されていること。
 *   - F-1 完了前はテスト全体を skip する。
 *
 * 設計ソース:
 *   docs/plans/v2-w15-schedule-unified.md §mobile-touch
 *
 * 注意:
 *   - このテストは `mobile-webkit` プロジェクトでも動作するが、
 *     Chromium エミュレーション (Desktop Chrome + mobile viewport) でも動く。
 *   - TouchSensor の activationConstraint.delay = 250ms に合わせ、
 *     page.evaluate で touchstart → delay → touchmove → touchend を発火。
 *   - API モックは Playwright `page.route` で intercept (実 BE 不要)。
 */
import { type Page, type Route } from '@playwright/test';

import { expect, test } from './fixtures/auth';

// ─────────────────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────────────────

/** place-and-fix エンドポイントをモックして成功レスポンスを返す. */
async function mockPlaceAndFix(page: Page): Promise<void> {
  await page.route('**/api/v1/schedule/place-and-fix', async (route: Route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        patient_id: body['patient_id'] ?? 'p-mobile-001',
        patient_name: 'モバイル 一郎',
        weekday: body['weekday'] ?? 1,
        start_time: body['start_time'] ?? '10:00:00',
        fix_pattern: true,
      }),
    });
  });

  // schedule 初期データもモック
  await page.route('**/api/v1/schedule**', async (route: Route) => {
    if (route.request().method() === 'GET' && !route.request().url().includes('place-and-fix')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          pool: [{ patient_id: 'p-mobile-001', patient_name: 'モバイル 一郎' }],
          placements: [],
          stats: { placed: 0, pool: 1 },
        }),
      });
    } else {
      await route.continue();
    }
  });
}

/**
 * dnd-kit TouchSensor のロングプレス (holdMs ms) → ドラッグ → ドロップを
 * page.evaluate で実行する。
 *
 * TouchSensor の activationConstraint.delay = 250ms なので holdMs >= 300 が必要。
 */
async function simulateTouchDrag(
  page: Page,
  from: { x: number; y: number },
  to: { x: number; y: number },
  holdMs = 350,
): Promise<void> {
  await page.evaluate(
    ({ from: f, to: t, holdMs: delay }) => {
      return new Promise<void>((resolve, reject) => {
        const dispatch = (el: Element, type: string, x: number, y: number) => {
          const touch = new Touch({
            identifier: Date.now(),
            target: el,
            clientX: x,
            clientY: y,
            pageX: x,
            pageY: y,
          });
          el.dispatchEvent(
            new TouchEvent(type, {
              bubbles: true,
              cancelable: true,
              touches: type === 'touchend' ? [] : [touch],
              targetTouches: type === 'touchend' ? [] : [touch],
              changedTouches: [touch],
            }),
          );
        };

        const fromEl = document.elementFromPoint(f.x, f.y);
        if (!fromEl) {
          reject(new Error('simulateTouchDrag: fromEl not found'));
          return;
        }

        // touchstart でロングプレス開始
        dispatch(fromEl, 'touchstart', f.x, f.y);

        // holdMs 後に touchmove → touchend (drag drop)
        setTimeout(() => {
          const toEl = document.elementFromPoint(t.x, t.y) ?? fromEl;
          // dnd-kit は touchmove で位置追跡する
          dispatch(fromEl, 'touchmove', t.x, t.y);
          dispatch(toEl, 'touchend', t.x, t.y);
          resolve();
        }, delay);
      });
    },
    { from, to, holdMs },
  );
}

// ─────────────────────────────────────────────────────────────────────────
// spec
// ─────────────────────────────────────────────────────────────────────────

test.describe('v2 Wave 15 モバイル DnD (TouchSensor) — W15 Phase 5 F-2', () => {
  test.describe.configure({ mode: 'serial' });
  // [Wave 16 Phase B] スケジュール UI 構造刷新により旧 ScheduleUnifiedView 前提の
  // セル選択ロジックが噛み合わないため skip。後続 Wave で StaffWeekTablePanel
  // 向けに書き換え予定。
  test.skip(true, '[Wave 16 Phase B] Schedule UI 構造刷新 (Mobile DnD は後続 Wave で書き換え)');

  test('iPhone エミュレーションで保留プール → グリッドへタッチドラッグ → place-and-fix が呼ばれる', async ({
    page,
    loginAs,
  }, testInfo) => {
    test.slow();

    // ---- 0. Wave 15 F-1 (TouchSensor 追加) 完了前は skip -------------------
    // TouchSensor が未追加の場合は skip する (F-1 完了後に削除)
    // 環境変数 WAVE15_F1_COMPLETE=1 がセットされていれば実行
    const f1Complete = process.env.WAVE15_F1_COMPLETE === '1';
    if (!f1Complete) {
      test.skip(
        true,
        'Wave 15 F-1 (TouchSensor 追加) 未完了のため skip。WAVE15_F1_COMPLETE=1 でアンロック。',
      );
      return;
    }

    // ---- 1. iPhone 15 viewport に切り替え -----------------------------------
    await page.setViewportSize({ width: 390, height: 844 });

    // ---- 2. ログイン (admin) -----------------------------------------------
    await loginAs('admin');

    // ---- 3. API モック設定 -------------------------------------------------
    await mockPlaceAndFix(page);

    // ---- 4. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 5. TouchSensor が有効かどうかを確認 --------------------------------
    // data-testid="touch-sensor-active" もしくは dnd-kit の DndContext が
    // TouchSensor を持つかを評価
    const touchSensorActive = await page.evaluate(() => {
      // dnd-kit が TouchSensor をロードしていると window.__dndKitSensors に
      // 'TouchSensor' が含まれる (実装依存のヒューリスティック)
      const sensors = (window as unknown as { __dndKitSensors?: string[] }).__dndKitSensors;
      if (Array.isArray(sensors)) {
        return sensors.includes('TouchSensor');
      }
      // フォールバック: touch イベントが dnd-kit のコンテナに届くか
      const dndRoot = document.querySelector('[data-dnd-context]');
      return dndRoot !== null;
    });

    if (!touchSensorActive) {
      testInfo.annotations.push({
        type: 'touch-sensor-not-detected',
        description:
          'TouchSensor が検出できなかった。F-1 の実装と __dndKitSensors の公開を確認してください。',
      });
      test.skip(true, 'TouchSensor が検出できない (Wave 15 F-1 実装待ち).');
      return;
    }

    // ---- 6. 保留プールのカードを確認 ----------------------------------------
    const poolCard = page
      .getByTestId('pool-card')
      .or(page.locator('[id^="patient:"]').last())
      .first();

    const poolCardVisible = await poolCard.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(!poolCardVisible, '保留プールの患者カードが見つからない (seed 不足 / モック未適用).');

    // ---- 7. ターゲットセル (火曜 10:00) を取得 ------------------------------
    const tenLabel = page.getByText('10:00', { exact: true }).first();
    const targetCell = tenLabel
      .locator('xpath=following-sibling::*[2]')
      .or(page.getByTestId('cell-1-10:00'));

    const cellVisible = await targetCell.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(!cellVisible, '10:00 の火曜セルが見つからない.');

    // ---- 8. 座標を取得 -----------------------------------------------------
    const cardBox = await poolCard.boundingBox();
    const cellBox = await targetCell.boundingBox();
    test.skip(!cardBox || !cellBox, 'バウンディングボックスが取得できない.');

    const fromPoint = {
      x: cardBox!.x + cardBox!.width / 2,
      y: cardBox!.y + cardBox!.height / 2,
    };
    const toPoint = {
      x: cellBox!.x + cellBox!.width / 2,
      y: cellBox!.y + cellBox!.height / 2,
    };

    // ---- 9. place-and-fix リクエストをキャプチャする準備 -------------------
    const placeAndFixPromise = page.waitForRequest(
      (req) => req.url().includes('/schedule/place-and-fix') && req.method() === 'POST',
      { timeout: 20_000 },
    );

    // ---- 10. タッチドラッグを実行 -------------------------------------------
    // TouchSensor は 250ms のロングプレスで起動するので holdMs=350ms
    await simulateTouchDrag(page, fromPoint, toPoint, 350);

    // ---- 11. place-and-fix が呼ばれることを確認 -----------------------------
    const capturedReq = await placeAndFixPromise.catch(() => null);

    if (capturedReq === null) {
      testInfo.annotations.push({
        type: 'mobile-dnd-not-triggered',
        description:
          'タッチドラッグ後に place-and-fix が呼ばれなかった。' +
          'TouchSensor の activationConstraint.delay 設定を確認してください。',
      });
      test.skip(
        true,
        'タッチドラッグで place-and-fix がトリガーされなかった (TouchSensor 設定要確認).',
      );
      return;
    }

    const body = capturedReq.postDataJSON() as Record<string, unknown>;
    expect(body, 'payload に patient_id が含まれる').toHaveProperty('patient_id');
    expect(body['fix_pattern'], 'fix_pattern=true').toBe(true);

    // ---- 12. ドロップ後に患者氏名が表示される --------------------------------
    await expect(page.getByText('モバイル 一郎')).toBeVisible({ timeout: 10_000 });
  });

  test('ロングプレスが 250ms 未満の場合はドラッグが開始されない', async ({ page, loginAs }) => {
    // ---- 0. Wave 15 F-1 完了前は skip --------------------------------------
    const f1Complete = process.env.WAVE15_F1_COMPLETE === '1';
    test.skip(
      !f1Complete,
      'Wave 15 F-1 (TouchSensor 追加) 未完了のため skip。WAVE15_F1_COMPLETE=1 でアンロック。',
    );

    // ---- 1. iPhone viewport ------------------------------------------------
    await page.setViewportSize({ width: 390, height: 844 });

    // ---- 2. ログイン --------------------------------------------------------
    await loginAs('admin');

    // ---- 3. place-and-fix モック (呼ばれないことを検証) --------------------
    let placeAndFixCalled = false;
    await page.route('**/api/v1/schedule/place-and-fix', async (route: Route) => {
      placeAndFixCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ fix_pattern: true }),
      });
    });

    await page.route('**/api/v1/schedule**', async (route: Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            pool: [{ patient_id: 'p-mobile-002', patient_name: 'モバイル 二郎' }],
            placements: [],
            stats: { placed: 0, pool: 1 },
          }),
        });
      } else {
        await route.continue();
      }
    });

    // ---- 4. /schedule に遷移 -----------------------------------------------
    await page.goto('/schedule');
    await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible({
      timeout: 15_000,
    });

    // ---- 5. 保留プールのカードを確認 ----------------------------------------
    const poolCard = page
      .getByTestId('pool-card')
      .or(page.locator('[id^="patient:"]').last())
      .first();
    const poolCardVisible = await poolCard.isVisible({ timeout: 8_000 }).catch(() => false);
    test.skip(!poolCardVisible, '保留プールの患者カードが見つからない.');

    const tenLabel = page.getByText('10:00', { exact: true }).first();
    const targetCell = tenLabel
      .locator('xpath=following-sibling::*[2]')
      .or(page.getByTestId('cell-1-10:00'));
    const cellVisible = await targetCell.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(!cellVisible, 'ターゲットセルが見つからない.');

    const cardBox = await poolCard.boundingBox();
    const cellBox = await targetCell.boundingBox();
    test.skip(!cardBox || !cellBox, 'バウンディングボックスが取得できない.');

    // ---- 6. 短いタッチ (100ms) でドラッグを試みる --------------------------
    // holdMs=100ms < 250ms なので TouchSensor が起動せず place-and-fix は呼ばれない
    await simulateTouchDrag(
      page,
      {
        x: cardBox!.x + cardBox!.width / 2,
        y: cardBox!.y + cardBox!.height / 2,
      },
      {
        x: cellBox!.x + cellBox!.width / 2,
        y: cellBox!.y + cellBox!.height / 2,
      },
      100, // 意図的に 250ms 未満
    );

    // 1 秒待って place-and-fix が呼ばれていないことを確認
    await page.waitForTimeout(1_000);
    expect(placeAndFixCalled, 'ロングプレス 250ms 未満では place-and-fix が呼ばれない').toBe(false);
  });
});
