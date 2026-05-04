# D3 Screens クロスレビュー B（受入観点）

## 1. 総評

D3計画は画面網羅・コンポーネント階層・データフェッチ方針・リスク対策のいずれも丁寧で、設計ドキュメント群との整合性は概ね良好。ただし**受入基準（§8）が「代表例」止まり**で定量的な完了条件になっておらず、E2Eシナリオに業務フロー全体を貫くシナリオが欠けている。DnD 60fps の計測方法、200訪問/週での性能基準、ロール別E2E分岐の明示、iOS PWA の safe-area・dvh 検証が具体的に指示されていない。マイルストーンは日付のみで「何を満たせばPASS」の基準が不在。**受入品質管理の枠組みが不足**。

## 2. 画面別受入基準評価

| # | 画面/モーダル | 評価 | 根拠 |
|---|---|---|---|
| 1 | ログイン | ○ | 設計doc(04)整合、レート制限受入なし |
| 2 | ダッシュボード | ○ | admin/staff差分整合、空状態テスト未明示 |
| 3 | 週ビュー(中核) | △ | **60fps計測手段「維持」のみ、200訪問/週ベンチマーク基準なし、NG時間拒否・同住所確認の受入未明示** |
| 4 | マスタ管理 | ○ | CRUD + 未保存遷移警告整合、拠点/市区町村タブの受入省略 |
| 5 | 連携センター | △ | **差分適用→進捗→JobHistory結合フロー不在、CSP拒否フォールバック受入なし** |
| 6 | AI入力モーダル | ○ | 設計doc(09)整合、解釈失敗時UI・マイク権限拒否フォールバック未明示 |
| 7 | 患者固定枠モーダル | △ | **長押し450ms循環の受入テスト手段未定義（pointerdown/pointerup指定が必要）** |
| 8 | スタッフ週休みモーダル | △ | 状態循環クリックの受入テスト未明示 |
| 9 | スタッフ固定シフトモーダル | ○ | OFF行グレーアウトの視覚確認テスト未明示 |
| 10 | 訪問詳細モーダル | △ | **受入基準に具体項目ゼロ**、設計docのE-4相当が設計群に未記載 |
| 11 | モバイルホーム | ○ | 設計doc(10)整合 |
| 12 | モバイル今日 | ○ | VisitCard・borderLeft teal・3ボタン整合 |
| 13 | モバイル今週 | ○ | DayCard 7枚・今日帯整合 |
| 14 | モバイルマイページ | △ | **ログアウト後のSession削除・リダイレクトの受入不在** |

## 3. 見落とされた検証項目

**E2E業務フロー全体（最重要欠落）**
個別シナリオは列挙されているが、「ログイン→週ビュー→DnD→AI入力→保存→カイポケ反映」という業務フルフロー結合シナリオが存在しない。

**性能計測の具体的手段**
- 「60fps維持」の検証方法未定義 → Playwright `page.tracing` / Chrome DevTools Timeline / PerformanceObserver
- 「200訪問/週でカクつかない」客観基準 → `LCP < 2.5s`・`INP < 200ms`・`FPS >= 55`
- react-window 導入閾値（何件から仮想化）が不明

**a11y検証の粒度**
- axe-core 対象画面が不明
- DnD のキーボードアクセシビリティ未登場
- スクリーンリーダーでの週ビューグリッド読み上げ可否未定義

**ロール別検証の抜け**
- staff で `/master`・`/integration` アクセス時の **403リダイレクト先と表示** E2E不在
- staff のダッシュボードで「割当待ち」「カイポケ同期」が **DOM上に存在しない** ことの確認が未指定

**iOS PWA 固有検証**
- `env(safe-area-inset-bottom)` 確認方法
- `dvh` 単位使用時のキーボード表示時レイアウト崩れ
- FAB がボトムナビに隠れない（下88px）
- PWA standalone モードでの `start_url=/dashboard` 初回起動

**TanStack Query invalidate連鎖**
- AI入力登録後に `['weekly','visits']` と `['mobile','today']` 両方invalidateの統合テスト不在
- 差分適用完了後のキャッシュ更新が連携センター画面に反映の確認なし

**楽観的更新ロールバック**
- DnD後にAPIエラーで元の位置に戻るかの受入テスト不在（リスクには記載あるが対応テスト未策定）

## 4. マイルストーン受入ゲート提案

| M | 条件 |
|---|---|
| M1 (Day 5) | ログインE2E合格(正/誤/Remember me) + ダッシュボード両ロールで差分表示 + axe-core 0 violations + Vitest green |
| M2 (Day 12) | 週ビュー3モード表示 + マスタCRUD全操作 + 未保存遷移警告 + staff /master 403リダイレクト |
| M3 (Day 18) | DnD E2E（成功/NG/同住所）+ AI入力E2E（テキスト/音声/複数/失敗）+ 楽観更新ロールバック確認 |
| M4 (Day 23) | 連携センター6カード + 差分適用→進捗→JobHistory結合 + モバイル4画面 iPhone viewport + safe-area |
| M5 (Day 27) | 業務フルフロー E2E + FPS >= 55 @ 200訪問 + axe-core 0 全主要画面 + TS strict 0 + ビルド green |

## 5. テスト戦略改善

**問題点**: Phase J「テスト実装 (0.5d)」は工数不足。

**改善案**:
1. **テスト先行** — Phase A-B 時点で Vitest/Playwright 基盤・Page Object Model 確立、Phase J は補完のみ
2. **60fps 計測自動化** — Playwright tracing API + FPS 閾値 CI チェック
3. **MSW 明示** — API 依存コンポーネントテストに MSW
4. **Storybook + a11y アドオン** — D2 で導入されるなら D3 もコンポーネント登録
5. **テスト工数再配分** — J-5 を 2d → 4d、Phase D 完成後並走

## 6. 他ドメイン整合性

**D1 Backend**:
- D3の `/api/visits/today` が D1 API 依存表に未記載
- `/api/staff-weekly-overrides` POST が §3 依存リストに未記載

**D2 Foundation**:
- D2 → D3 への変更通知プロセス未定義
- D2 トークン変更時の自動検出手段（Chromatic 等）が未計画

**D4 Integrations**:
- D4 完成前の連携センター受入をどうするか（モック戦略）未記載
- VNC カードの Bearer Token 取得フローが D3 計画に未記載（設計doc §8-6 にはある）

## 7. リリースゲート提案

| # | ゲート条件 |
|---|---|
| G1 | Playwright E2E 全シナリオ green（業務フルフロー含む） |
| G2 | TypeScript strict 0 errors |
| G3 | `npm run build` exit 0、バンドルサイズ回帰なし |
| G4 | axe-core violations: ログイン/ダッシュボード/週ビュー/AI入力 で 0 |
| G5 | DnD FPS >= 55 @ 200訪問/週 |
| G6 | admin/staff ロール分岐：DOM 非存在レベル確認 |
| G7 | iPhone 375px viewport + safe-area + FAB位置 + 音声フォールバック 実機/BrowserStack |
| G8 | 楽観更新ロールバック E2E |
| G9 | D1 `/api/visits/today` 存在確認 + D4モック差し替えなしで連携6カード表示 |
| G10 | マスタ未保存遷移警告（beforeunload + useRouter intercept）E2E |
