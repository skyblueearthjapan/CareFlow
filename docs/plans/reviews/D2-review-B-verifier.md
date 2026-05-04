# D2 Frontend Foundation クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 6
**Recommendation**: REQUEST_CHANGES

## 受入基準評価（§8 全16項目）

| # | 受入基準 | Status | Evidence |
|---|---|---|---|
| 1 | `pnpm dev` で9ルート雛形 | PARTIAL | 手段が「目視」のみ、自動煙テストなし |
| 2 | `pnpm build` 成功 standalone | PARTIAL | 条件明記、CIステップ組込未記載 |
| 3 | `pnpm typecheck` strict 0 | VERIFIED | strict設定明記 |
| 4 | `pnpm lint` 警告 0 | VERIFIED | Husky/ESLint/Prettier整備 |
| 5 | `pnpm test` 全パス | PARTIAL | 70%目標、テストケース・MSW未定義 |
| 6 | 配色・タイポ・影・半径が一致 | PARTIAL | 「目視」のみ、自動チェックなし |
| 7 | 外枠カード描画 | PARTIAL | 値一致、単体テスト未計画 |
| 8 | Sidebar 折りたたみ 200ms ease | PARTIAL | **設計書 1-10 と矛盾（300ms 記載あり）** |
| 9 | PCヘッダー60px・Sidebar60px・水平揃い | VERIFIED | 仕様完全一致 |
| 10 | モバイル ボトムナビ4タブ+safe-area | VERIFIED | 仕様明記 |
| 11 | AI入力FAB常駐・Cmd+K | PARTIAL | 単体テスト計画なし |
| 12 | NextAuth ログイン成功・未認証リダイレクト | **MISSING** | E2EをD3に延期、証明手段なし |
| 13 | TanStack Query Devtools / Bearer注入 | PARTIAL | 単体テスト未計画 |
| 14 | manifest.webmanifest / Lighthouse PWA通過 | PARTIAL | 「通過」のみ、合格スコア未定義 |
| 15 | Lucide 40アイコン import | PARTIAL | バレルexport確認テストなし |
| 16 | Docker イメージビルド可能 | PARTIAL | D5 と二重管理リスク |

## Gaps（6つ）

**Gap 1: サイドバーアニメーション 200ms/300ms 矛盾（HIGH）**
- 01-design-system.md §1-10 表 300ms vs 03-layouts.md §3-3 200ms vs D2 受入基準 200ms
- → 受入判定が曖昧
- 提案: 200ms に統一、`--duration-sidebar: 200ms` トークン化

**Gap 2: Auth E2Eが Foundation 範囲外に委ねられている（HIGH）**
- 受入基準12「ログイン成功」をD3完了後と延期。Foundation単体で証明不能
- 提案: D2 Phase 6 に Playwright 最小3本（login_success, login_fail, auth_guard）追加 +0.5d

**Gap 3: Lighthouse PWA 合格基準が「通過」のみ数値なし（MEDIUM）**
- 何点以上か、Installability必須かが不明
- 提案: 「Lighthouse PWA Installability 全項目グリーン」+ `pnpm lighthouse --only-categories=pwa` を CI 追加

**Gap 4: `/dev/tokens` ページの実用性検証手段が未定義（MEDIUM）**
- D3実装者がhover/focus/error状態を確認できるかの基準なし
- 提案: 必須表示項目リスト追加（Button 5×3、Badge 11、Avatar 12色、全状態）

**Gap 5: ブラウザマトリクスの検証戦略が不在（MEDIUM）**
- Chrome/Safari/Firefox/iOS Safari への言及なし、`safe-area`/`dvh`/CSS変数の Safari 確認手順なし
- 提案: 「Chrome最新・Safari最新・iOS Safari 16+ Smokeテスト通過」追加、Playwright config に webkit project

**Gap 6: D3 への引渡し基準が暗黙的（HIGH）**
- D3 §3 が要求する22コンポーネントのうち ModalShell/SegmentedControl/ConfirmDialog/Spinner が D2 計画書に未明記
- 提案: 後述の D3 引渡しチェックリストを D2 受入基準に組み込む

## マイルストーン受入ゲート提案

| M | 条件 |
|---|---|
| M1 (Day 4) 開発環境ゲート | dev/build/typecheck 0 errors / lint 0 warns / `/dev/tokens` 全量表示 / Tailwind theme.extend が CSS変数解決 |
| M2 (Day 6) レイアウトゲート | AppShell/MobileShell/Sidebar/BottomNav が 9 ルートで表示 / Sidebar 折りたたみ 200ms 計測 / ヘッダー60px 揃い ブラウザ計測 |
| M3 (Day 8) 認証・データ層ゲート | NextAuth Credentials ログイン成功 / 未認証→/login リダイレクト / Bearer 注入 Network 確認 / Devtools 開く |
| M4 (Day 10) D3 引渡しゲート | test 全パス / 70% カバレッジ / `/dev/tokens` で D3 必要全コンポ確認 / manifest 配信確認 / Lighthouse PWA 全項目グリーン / 引渡しチェックリスト全項目 |

## テスト戦略改善

**Vitest 追加対象**:
- ModalShell: open/close/ESC/外側クリック
- Sidebar: 232⇔72切替、staff時2項目/admin時4項目
- middleware: 未認証→/login、admin only→403
- FAB: Cmd+K → aiInputOpen=true
- API client: 401時 Bearer注入・エラーハンドリング
- density store: data-density 属性変更

**Playwright最小（Phase6追加 0.5d）**:
- login_success.spec.ts: 正常→/dashboard
- login_fail.spec.ts: 誤PW→エラーバナー
- auth_guard.spec.ts: 未認証→/login

## 他ドメイン整合性

**D1 との整合**: OpenAPI型生成パイプラインの artifact 受け渡し手段（Actions download? 手動?）未定義

**D3 との整合（Critical）**:
- D3が要求するコンポーネント7種が D2 §4 に未明記（ModalShell, SegmentedControl, ConfirmDialog, Spinner, Switch, Checkbox, Radio）
- D3 A-2/A-3 と D2 §4-17/19 の重複（二重実装リスク）

**D5 との整合**: Dockerfile 二重管理（D2 §4 Phase1 vs D5 §4 Phase1-3）担当分担未定義

## D3 への引渡し条件チェックリスト

【レイアウト系】AppShell / MobileShell / Sidebar / Header / BottomNav / OuterCard
【フィードバック系】Toast / **ModalShell ←未明記** / Skeleton / EmptyState / **Spinner ←未明記**
【入力系】Button(5×3) / Input(4種+error) / Select / **Switch ←未明記** / **Checkbox/Radio ←未明記** / **SegmentedControl ←未明記** / **ConfirmDialog ←未明記**
【表示系】Badge(11tone) / Avatar(12色×3size) / Card / Section / VisitChip雛形
【基盤系】CSS変数全トークン /dev/tokens / density 3段階反映 / Lucide 40 / openapi 型 / MSW dev / Bearer注入
【認証系】ログイン成功→/dashboard / 未認証→/login / RBAC middleware / JWT 期限切れ挙動

## リリースゲート提案

- G1: Playwright E2E最小3本 green
- G2: TypeScript strict 0 errors
- G3: build exit 0、バンドルサイズ計測
- G4: axe-core 0 violations（login）
- G5: Lighthouse PWA Installability 全項目
- G6: Chrome/Safari/iOS Safari Smoke
- G7: D3引渡しチェックリスト全項目

**REQUEST_CHANGES** — 認証E2E最小セットの D2 内追加、ModalShell/SegmentedControl/ConfirmDialog/Spinner のタスク明示、アニメーション200ms/300ms矛盾の設計書側修正、Lighthouse PWA 合格基準の数値化、の4点修正後再レビュー要求。
