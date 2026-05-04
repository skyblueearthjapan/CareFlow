# D2 Frontend Foundation クロスレビュー A（技術観点）

## 1. 総評

**REVISE**。Next.js 15 + shadcn/ui + Warm & Human トークンの土台構築として骨格は堅実。tokens.css のカラー値は01-design-system.mdとほぼ完全一致し、フォント・影・半径の転記も正確。しかし、D1 が提供する `/auth/refresh` への対応欠落、D3 が前提とする ModalShell/SegmentedControl/ConfirmDialog/Spinner の未記載、`--bg-surface` トークン脱落、サイドバー幅の仕様書間矛盾（232 vs 240）未解決、モバイルルートパスの D3 との不一致など、ドメイン間契約とトークン網羅性に複数の穴がある。

## 2. 強み

1. **トークン転記精度が高い** — 全HEX値が `01-design-system.md` と一致
2. **スコープ管理が明確** — VisitChip/FABモーダル本体を D3/D4 に正しく逃がす
3. **PWA戦略が現実的** — next-pwa を避け独自最小SW、過剰投資回避

## 3. 重大な指摘

### [CRITICAL-1] D1 `/auth/refresh` への対応が完全欠落
- Task 16 に Credentials/JWT/middleware の記載のみ、トークンリフレッシュ仕組みへの言及ゼロ
- → JWT 期限切れで強制ログアウト、Remember me 30日との整合性破綻
- **Fix**: NextAuth `jwt` callback 内で exp チェック → 残り5分以内で `/api/v1/auth/refresh` 呼び出し → 新トークン差し替え。失敗時 signOut フォールバック

### [CRITICAL-2] D3 が前提とする共通コンポーネントが D2 成果物一覧に未定義
- D3 §3 が再利用前提とする: ModalShell, ConfirmDialog, Spinner, SegmentedControl
- D2 §6 のコンポーネント方針表にも Task 20-22 にも未記載
- → D3 開始時にブロック、特に SegmentedControl は週ビュー必須
- **Fix**: §6 表と Phase 5 (Task 22) に追加、工数 +0.5d

## 4. 見落としリスク

### [MAJOR-1] `--bg-surface` トークンの脱落
- 01-design-system.md 1-2 に `--bg-surface: #FFFFFF` あり、05/08/09 で実使用
- D2 tokens.css マッピングに未記載
- **Fix**: `--bg-surface: #FFFFFF;` 追加

### [MAJOR-2] `--c-manager` データカラーの脱落
- 01-design-system.md 1-4 に `--c-manager: #6B7280 / #F3F4F6` あり
- D2 tokens.css に未記載
- **Fix**: `--c-manager: #6B7280; --c-manager-bg: #F3F4F6;` 追加

### [MAJOR-3] サイドバー幅の仕様矛盾
- 00-overview.md: 「240/72px」、03-layouts.md + D2 Task 10: 「232/72px」
- **Fix**: 03-layouts.md の 232px を正に統一

### [MAJOR-4] モバイルルートパスが D2 と D3 で不一致
- D2: `app/(mobile)/home|today|this-week|mypage/page.tsx`
- D3: `/m/home`, `/m/today`, `/m/week`, `/m/me`
- **Fix**: D3 の `/m/` prefix方式に統一、D2 を `app/(mobile)/m/home|today|week|me` に修正

### [MINOR-1] サイドバーアニメーション 200ms / 300ms 矛盾
- 01-design-system.md 1-10 表: `duration-slow 300ms / サイドバー折りたたみ`
- 03-layouts.md + D2: 「200ms ease」
- **Fix**: 設計書 1-10 の表を 200ms に統一

### [MINOR-2] `manager` ロールへの対応が不明
- D1 users に `admin/staff/manager`、D2 middleware は admin/staff 2値のみ
- **Fix**: manager の扱い（staff相当か独自か）を明示

### [MINOR-3] Tailwind v4 言及なし
- 2026年5月時点で v4 リリース済み可能性、v3.4 固定理由を一言

## 5. 改善提案

### 提案1: Task 16 書き換え（auth/refresh 対応追記、+0.5d）
```
16. NextAuth.js 統合 (1.5d)
- Credentials Provider: authorize で /auth/login → JWT から {sub, role, staff_id, exp, iat}
- JWT callback: exp チェック → 残り5分で /auth/refresh、失敗時 signOut
- middleware: 未認証→/login、staff→admin only 遮断、manager は staff 相当
- Remember me: maxAge 30日/24時間切替
```

### 提案2: §6 表に追加
```
| ModalShell | shadcn Dialog + 自作ラッパ | D3 モーダル4本の共通シェル
| SegmentedControl | 完全自作 | 週ビュー・患者パターン必須
| ConfirmDialog | shadcn AlertDialog + Warm化 | 削除確認等
| Spinner | 自作 | loading表示
```
Phase 5 Task 22 を 0.5d → 1.0d、合計 10d → 10.5d

### 提案3: tokens.css に追加
```css
--bg-surface: #FFFFFF;
--c-manager: #6B7280;
--c-manager-bg: #F3F4F6;
```

## 6. 依存ドメイン整合性チェック

| D1 → D2 項目 | 状態 |
|---|---|
| `/auth/login` | OK |
| `/auth/me` | OK（暗黙利用） |
| **`/auth/refresh`** | **NG: 言及なし** |
| **JWT 5キー契約** | **NG: 転記なし** |
| OpenAPI → openapi-typescript | OK |
| **manager ロール** | **NG: 2値のみ** |
| API prefix `/api/v1` | OK（openapi-fetch で吸収可） |

| D2 → D3 項目 | 状態 |
|---|---|
| AppShell / Sidebar / Header | OK |
| **ModalShell / SegmentedControl / ConfirmDialog / Spinner** | **NG: 未定義** |
| **モバイルルートパス** | **NG: 不一致** |
| Badge tone 11種 | OK（manager 除く） |
| Avatar 24/32/54px | OK |

## 7. 再レビュー推奨ポイント

1. CRITICAL-1, CRITICAL-2 修正後に D1/D3 契約成立確認
2. tokens.css 完全性 — 全トークン diff
3. モバイルルートパス D3 統一後の受入基準9ルート再確認
4. 工数再計算（+1.0〜1.5d、合計 11〜11.5d）
5. manager ロール設計判断の明文化

**Verdict Justification**: CRITICAL 2件は D2 単体では動くが D1/D3 結合で壊れるドメイン間契約の穴。auth/refresh 欠落は本番強制ログアウトに直結。コンポーネント欠落は D3 着手初日ブロッカー。両件修正後に再レビュー ACCEPT 可。
