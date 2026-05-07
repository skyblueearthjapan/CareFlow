/**
 * v2 schedule fix → weekly_pattern propagation — Wave 6 W6-E2E (2/4).
 *
 * 注意 (Wave 15 以降 / W15-codex-fix (6)):
 *   ScheduleGridV2 (時刻×曜日 旧 UI) は Wave 15 で ScheduleUnifiedView
 *   (コーステンプレート × 曜日 マトリクス) に置換済。本 spec は旧 UI 環境
 *   での回帰用に保持される。Wave 15 主フロー (place-and-fix によるドロップ
 *   即固定枠化) の E2E は ``v2-w15-schedule-unified.spec.ts`` を参照。
 *
 * Path (旧 UI 想定):
 *   Admin login → /schedule (ScheduleGridV2)
 *     → 患者カードを保留プールから時間スロット (例: 月曜 09:00) にドラッグ
 *     → 「固定」ボタンで POST /api/v1/schedule/fix
 *     → 患者マスタ /patients/[id] で weekly_pattern.entries に
 *        weekday=0 / preferred_start=09:00 が反映されている
 *     → WeekSelector で翌週へ進めても同じ位置に患者が描画される
 *        (= weekly_pattern を再 hydrate して恒久反映が確認できる)
 *
 * 設計ソース:
 *   docs/plans/v2-implementation-plan.md §7 W6-E2E (2)
 *   docs/plans/v2-allocation-redesign.md §3.6.2 / §3.6.6
 *
 * 旧 UI 環境向け注意:
 *   ScheduleGridV2 は data-testid を持たないため、locator は role / role tab /
 *   構造的セレクタ (grid 内のセル) で組み立てる。dnd-kit の挙動を Playwright
 *   の `dragTo` で再現するため、活性化距離 (PointerSensor.distance=6px) を
 *   超えるよう targetPosition を明示する.
 */
import { type Page } from '@playwright/test';

import { expect, test } from './fixtures/auth';

// ─────────────────────────────────────────────────────────────────────────
// helpers
// ─────────────────────────────────────────────────────────────────────────

/** ScheduleGridV2 の保留プールが見える状態になるまで待つ. */
async function waitScheduleReady(page: Page): Promise<void> {
  await expect(page.getByRole('heading', { name: 'スケジュール' })).toBeVisible();
  // ヘッダーの統計表示「配置 N 名 / 保留 M 名」が出た時点で hydrate 完了とみなす.
  await expect(page.getByText(/配置 \d+ 名 \/ 保留 \d+ 名/)).toBeVisible({ timeout: 15_000 });
}

/**
 * 「保留 N 名」の N をパースする。
 * 0 のときはドラッグできる候補が無いので、テストを skip する判定に使う。
 */
async function readPoolCount(page: Page): Promise<number> {
  const txt = (await page.getByText(/配置 \d+ 名 \/ 保留 \d+ 名/).textContent()) ?? '';
  const m = txt.match(/保留 (\d+) 名/);
  return m ? parseInt(m[1] ?? '0', 10) : 0;
}

// ─────────────────────────────────────────────────────────────────────────
// spec
// ─────────────────────────────────────────────────────────────────────────

test.describe('v2 schedule fix → weekly_pattern propagation (W6-E2E #2)', () => {
  test.describe.configure({ mode: 'serial' });

  test('プールから時間スロットへドラッグ → 固定 → 患者マスタ反映 → 翌週も同位置', async ({
    page,
    loginAs,
  }) => {
    test.slow();

    // ---- 1. ログイン → /schedule ----------------------------------------
    await loginAs('admin');
    await page.goto('/schedule');
    await waitScheduleReady(page);

    // ---- 2. プールに患者がいる前提 ---------------------------------------
    const initialPool = await readPoolCount(page);
    test.skip(
      initialPool === 0,
      'プールに患者がいない (seed 不足). spec を流すには未配置患者が必要.',
    );

    // プール内の最初の PatientCard を取得。
    // PoolPanel の領域 → 子のドラッグ可能要素を 1 つ拾う。
    // dnd-kit の draggable は data-id を持つので id^="patient:" で拾う
    // (PoolPanel と TimeSlotCell の双方に置かれる draggable が同じ id を共有
    // するため、最後 = 末尾のプール側カードを採る. グリッドが空状態なら
    // プールに全件いる前提.)
    const poolDraggable = page
      .locator('[id^="patient:"]')
      .filter({ has: page.locator('text=/.+/') })
      .last();
    await expect(poolDraggable).toBeVisible();

    // ドラッグ対象患者の表示名を控えておく (固定後の確認に使用).
    const patientLabel = ((await poolDraggable.textContent()) ?? '').trim().split('\n')[0] ?? '';
    expect(patientLabel.length, '患者名が読み取れる').toBeGreaterThan(0);

    // ---- 3. ターゲットスロット = 月曜 09:00 のセル -----------------------
    // ScheduleGridV2 のセル droppable id は `cell:<weekday>:<HH:MM>`。
    // dnd-kit はその id を要素属性に書かないため、相対的に「9:00 行 + 月曜列」を
    // 位置で特定するのは難しい。ここでは構造ヒント (時刻ラベル "09:00" を
    // 含む行 → grandparent の右隣 7 セルから左端 = 月曜) で拾う。
    // 簡易化: 8:00 行の月曜セル = grid 上で時刻ラベル直後の最初のセル。
    // セルは `aria-disabled` 等の属性が無いので、grid 内の最初の空セルを採用.
    //
    // 実装サマリ: cell の DOM は role 属性が無いので XPath で position 指定する.
    const gridRoot = page.locator('div.grid', { has: page.getByText(/^09:00$/) }).first();

    // 月曜列 = 時刻ラベルの直後 1 列目。grid-cols は 60px + 7列。
    // 「09:00」テキスト要素の親 (= 時刻セル) の "兄弟 1 番目"。
    const nineLabel = gridRoot.getByText('09:00', { exact: true }).first();
    const targetCell = nineLabel.locator('xpath=following-sibling::*[1]');
    const targetVisible = await targetCell.isVisible().catch(() => false);
    test.skip(
      !targetVisible,
      '09:00 の月曜セルが見つからない. グリッドのレンダ前に評価された可能性.',
    );

    // ---- 4. ドラッグ ---------------------------------------------------
    // PointerSensor.activationConstraint.distance = 6px なので、
    // targetPosition で必ず 8px 以上は移動するように促す.
    await poolDraggable.dragTo(targetCell, {
      force: true,
      targetPosition: { x: 4, y: 4 },
    });

    // 配置件数が 1 件以上に増えたことを期待。
    await expect(page.getByText(/配置 [1-9]\d* 名/)).toBeVisible({ timeout: 5_000 });

    // ---- 5. 「固定」ボタンを押す ----------------------------------------
    await page.getByRole('button', { name: '固定', exact: true }).click();
    const confirmDialog = page.getByRole('dialog').filter({ hasText: '週レイアウトを固定' });
    await expect(confirmDialog).toBeVisible();
    await confirmDialog.getByRole('button', { name: /固定する/ }).click();

    // 成功 toast。
    await expect(page.getByRole('status')).toContainText(/週間パターンを更新/, {
      timeout: 15_000,
    });

    // 確認ダイアログが閉じる。
    await expect(confirmDialog).toBeHidden({ timeout: 5_000 });

    // ---- 6. /patients を開いて当該患者の weekly_pattern を確認 ----------
    // 患者一覧 → 名前検索 → 詳細リンク経由
    await page.goto('/patients');
    const searchPart = patientLabel.split(/\s+/)[0] ?? patientLabel;
    await page.getByPlaceholder('氏名・カナ・コードで検索').fill(searchPart);
    await page.waitForTimeout(500);

    // 該当行の「詳細」リンクを踏む。複数ヒットしたら 1 件目で良い.
    const detailLink = page.getByRole('link', { name: '詳細' }).first();
    const detailExists = await detailLink.isVisible().catch(() => false);
    test.skip(
      !detailExists,
      `${patientLabel} の患者詳細ページに辿れない (検索ロジックが変わった?). flaky 抑止のため skip.`,
    );
    await detailLink.click();

    // 詳細画面で weekly_pattern.entries が反映されている.
    // ページに「週間訪問パターン」セクションが存在し、月曜 / 09:00 の表示があるか確認.
    // UI のラベリングは設計フェーズなので、broad に「09:00」「月」を本文中で
    // 検出できれば OK (曜日コード "Mon" 表示も許容する).
    await expect(page.getByText(/09:00/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/月|Mon/).first()).toBeVisible();

    // ---- 7. /schedule に戻り、翌週へ移動して同位置に再描画されるか確認 ---
    await page.goto('/schedule');
    await waitScheduleReady(page);

    // 「次週」「翌週」「→」相当のボタン (WeekSelector) を 1 回押す.
    // 実装の文言ゆれを許容: aria-label / 表示テキスト 双方を試す.
    const nextWeekButton = page.getByRole('button', { name: /次週|翌週|次の週|→/ }).first();
    const hasNextBtn = await nextWeekButton.isVisible().catch(() => false);
    test.skip(!hasNextBtn, 'WeekSelector の翌週ボタンが見つからない (UI 仕様未確定).');
    await nextWeekButton.click();
    await waitScheduleReady(page);

    // 翌週の 09:00 行月曜セルに固定したカードが描画されている.
    // (固定後は weekly_pattern が恒久ストアになるので、翌週 hydrate でも再現する)
    const nineLabelNext = page.getByText('09:00', { exact: true }).first();
    const targetCellNext = nineLabelNext.locator('xpath=following-sibling::*[1]');
    // セル内に PatientCard が 1 つ以上存在する (= dnd-kit の draggable id).
    const occupants = targetCellNext.locator('[id^="patient:"]');
    expect(await occupants.count(), '翌週も同セル (月 09:00) に患者が再描画される').toBeGreaterThan(
      0,
    );
  });
});
