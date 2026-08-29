# Mac 対応（クロスプラットフォーム UI）— 調査・設計書

作成: 2026-08-29 / ステータス: **案2 実装中**（PO 決定 2026-08-29: 案1 は保留・案2 を実装）

## 0. 背景（PO 報告）

クライアント（Mac）でスケジュールの患者名が「2 文字以上表示されない／最悪 1 文字も出ず潰れる」。
PO も Mac 非所持で実機確認ができない（LambdaTest 無料枠は 2 分で切れ実用不可・BrowserStack 無料は 30 分）。

## 1. 調査結果（2026-08-29・コード全数確認 + Chromium 実測）

### 1-a. 実装状況
- **OS / ブラウザ判定コードは 0 件**。Windows/Mac 共通の単一 UI。氏名を JS で短縮する処理も 0 件（見た目の切れは全て CSS）。
- フォント: `styles/tokens.css` `--font-sans: 'Inter','Hiragino Sans','Noto Sans JP',-apple-system,system-ui`
  - `Inter` は next/font で読み込むが生成変数 `--font-inter` を**どこも参照せず無効**。
  - `Noto Sans JP` は `(field)` レイアウト（現場ボード）だけが Google Fonts `<link>` で読込。管理画面では未読込。
    （`next/font/google` は CJK サブセットをオフラインのビルドコンテナが取得できず失敗するため不使用 — `app/(field)/layout.tsx` の注記）
  - → 日本語は **Mac = Hiragino Sans / Windows = system-ui = Yu Gothic UI** と**別フォント**で描画。
- 盤面は**固定 px 設計**（`lib/scheduling/timeline.ts` ほか）: 日タイムライン列 `COL_MIN_W=172`（`flex:1; minWidth`）/ 週タイムライン列 `150` / 週盤面セル `minmax(120px,1fr)` / 行高 `TL_ROW_PX=52` / 最小カード高 `30` / 運転席バー `48` / 訪問モニター実績チップ高 `14`（line-height 未指定）。
- 盤面ルートは `overflow-auto` なので列は切れず**最小幅に張り付いて横スクロール**する。
- 同時刻の重なり訪問は 1 列を `calc(100%/lanes)` で**等分**（日・週とも）。
- E2E は Playwright に webkit プロジェクトの定義があるが CI 未実行・視覚スナップショット無し。

### 1-b. 幅の試算（サイドバー 232 + AppShell 余白 28 + main 余白 48 + ペイン gap 16 + プール 320）
| 画面幅 | 盤面幅 | 日列幅（6 コース） | 重なり 2 lanes の 1 カード幅 |
|---|---|---|---|
| Windows FHD 1920 | ≈1276 | ≈212px | ≈100px → 13px 太字で 6〜7 文字 |
| MacBook 1440×900 | ≈796 | **172（最小）**・横スクロール | ≈80px → **4 文字** |
| MacBook 1280×800 | ≈636 | **172（最小）** | ≈80px / 3 lanes ≈ 52px → **2 文字** |

Chromium 実測: 漢字の字幅は Yu Gothic UI / Meiryo / Noto Sans JP とも **1em で同一**（幅差は主因ではない）。
一方 line-height normal は Yu Gothic UI ≈1.33em、Hiragino Sans ≈1.5em（既知値）→ **line-height 未指定の固定高要素は Mac のほうが上下に欠けやすい**。

### 1-c. 原因候補（確度順）
1. **画面幅**（MacBook の論理解像度が FHD より 25〜35% 狭い → 列が最小幅に張り付き、重なりで等分）— 最有力
2. フォントの縦メトリクス差（固定高チップの上下欠け）
3. Safari 固有差（未確認）

## 2. 対応方針（案2）— 「Mac を見なくても正しくなる」修正

### A. フォント統一
- 管理画面のルートレイアウト（`app/layout.tsx`）でも `(field)` と同じ方式で **Noto Sans JP を Google Fonts `<link>` から読込**（preconnect 付き・`display=swap`）。next/font の Inter は撤去（未使用かつビルド時取得のリスク）。
- `--font-sans: 'Noto Sans JP', 'Hiragino Sans', 'Yu Gothic UI', 'Meiryo', system-ui, sans-serif`。
- line-height 未指定の固定高チップ（訪問モニター実績チップ `actH=14`）に `leading-none` を明示。
- 期待効果: OS による見た目の差が消える（Windows も Noto Sans JP に変わる — 現場ボードと同じ書体になり統一感が出る）。

### B. 狭い画面への追従（Mac だけでなく 13 インチ Windows ノートにも効く）
| # | 変更 | 効果（1280px 時） |
|---|---|---|
| B1 | 重なり訪問（lanes ≥ 2）のカード: 氏名 13→12px（3 lanes 以上は 11px）・左右 padding 縮小・**高さ 46px 以上なら氏名を 2 行まで折り返し**（`line-clamp-2`）・2 行目は `HH:MM・N分` のみ（種別は省略） | 等分カードでも氏名が 4〜8 文字読める |
| B2 | サイドバーを **画面幅 1400px 未満の初回表示時に自動で畳む**（利用者が開閉した後は尊重・永続フラグ） | +160px |
| B3 | プールペインを `320px` 固定 → `clamp(248px, 22vw, 320px)` | +72px |
| B4 | `<main>` の余白 `p-6` → `p-4 2xl:p-6`（1536px 未満のみ縮小） | +16px |
合計で盤面 ≈ +248px（1280px で 636 → 884px = 日列 ≈ 147→172px 超えで最小幅から脱出）。

### 見送り（今回スコープ外）
- 案1 環境診断画面 `/diag`（PO: 今は不要・記録のみ → session-2026-08-25b-HANDOFF §2-3）
- OS 別 CSS（`data-os`）— A/B で差が消えるため不要。必要になっても同一 URL で属性切替可能
- 同行者バッジの氏名重なり解消（PO 決定「右上」を維持）
- `font-feature-settings: "palt"`（詰め組み）— 見た目が変わるため PO 確認後

## 3. 検証
- Playwright **webkit**（Safari 相当エンジン・Windows 上で実行可）で `1280×800 / 1440×900 / 1920×1080` の盤面スクリーンショットを修正前後で比較（フォントは Hiragino にならないが、幅・レイアウトの再現には十分）。
- ローカル: Docker Postgres + backend + frontend（データはローカル seed。本番ダンプの持ち込みは PO 判断）。
- 既存 vitest（TimelineDayBoard / WeekTimelineBoard / MonitorTimeline / AppShell）の回帰。

## 4. 変更ファイル（2026-08-29 実装）

| 区分 | ファイル | 変更 |
|---|---|---|
| A | `frontend/app/layout.tsx` | next/font Inter 撤去 → Google Fonts `<link>`（Noto Sans JP 400-700 / Noto Serif JP 500-700・preconnect・swap） |
| A | `frontend/styles/tokens.css` | `--font-sans: 'Noto Sans JP','Hiragino Sans','Yu Gothic UI','Meiryo',system-ui,sans-serif` |
| A | `frontend/components/monitor/MonitorTimeline.tsx` | 実績チップ／ペア待ちチップ（高さ 14px 固定）に `leading-none` |
| B1 | `frontend/components/schedule/timeline/TimelineDayBoard.tsx` | `nameTwoLine / showSvcLine / showAddrLine`。lanes≥2: 氏名 12px（≥3: 11px）・`line-clamp-2`・`px-1.5`・種別省略・時刻行は h≥62・住所は h≥74 |
| B1 | `frontend/components/schedule/timeline/WeekTimelineBoard.tsx` | 同上（11px / 10.5px・`px-1`） |
| B1 | `frontend/lib/scheduling/timeline.ts` | `TL_TWOLINE_SVC_PX=62 / TL_TWOLINE_ADDR_PX=74 / TL_TWOLINE_PILLS_PX=90`（2 行化カードの段階しきい） |
| B1 | `TimelineDayBoard.tsx` `PairMemberRowView` / `DraggablePairMemberRow` / `PairBox`、`WeekTimelineBoard.tsx` ペア枠 | **同住所ペア枠のメンバー行**にも `lanes` を通し、lanes≥2 なら 11px（週 10.5px）・2 行・時刻バッジと右上バッジの逃がし余白を捨てる（レビュー HIGH-2） |
| B2 | `frontend/lib/stores/ui.ts` / `components/AppShell.tsx` | `sidebarAutoCollapsedApplied`（永続）+ `applySidebarAutoCollapse()`。`innerWidth < 1536` の初回表示で 1 度だけ畳む（MacBook Air M1=1440 / M2=1470 / Pro14=1512 を全て含める・レビュー HIGH-1 で 1400→1536） |
| B3 | `frontend/components/schedule/v2/CourseDayTablePanel.tsx` | プール列 `320px` → `clamp(248px,22vw,320px)` |
| B4 | `frontend/components/AppShell.tsx` | `<main>` 余白 `p-6` → `p-4 2xl:p-6` |
| test | `TimelineDayBoard.narrow-lanes.test.tsx` / `WeekTimelineBoard.narrow-lanes.test.tsx` / `lib/stores/__tests__/ui.test.ts` | B1 4 件 + 3 件 / B2 3 件 |

既存回帰: tsc 0 エラー / lint 新規警告 0 / vitest 38 ファイル 410 件 + 新規 10 件 緑。

レビュー（code-reviewer/opus・2026-08-29）: HIGH 2 / MED 5 / LOW 7 → HIGH 2・MED 1/2/3/5・LOW 1/2/5/7 是正済み。
据え置き: MED-4（下記）/ LOW-3（MonitorTimeline の diff は既存 2 行の prettier 再整形・実体 4 行）/ LOW-4（視覚検証は §5）/ LOW-6（(field) の重複 link は JetBrains Mono を含むため残す）。
見出し用の Noto Serif JP は**読み込まない**（管理画面の見出し書体を変えないため・MED-5）。

## 5. 残タスク・確認事項
- **視覚検証（WebKit 1280×800 / 1440×900）**: ローカルにデータが必要。本番ダンプのローカル復元は PO 判断（個人情報）。代替は合成 seed。
- **フォントの自己ホスト（MED-4）**: 現状は Google Fonts 到達が前提で、オフライン/遮断時は従来の OS フォールバックに戻る（悪化はしない）。`public/sw.js` は `/fonts/` を cache-first で許可済みなので、woff2 サブセットを `/fonts/` に置けば PWA オフラインでも統一できる。次の改修候補。
- Windows 側の見た目も Noto Sans JP に変わる（現場ボードと同じ書体）。違和感があれば `--font-sans` の先頭を戻すだけで元に戻せる。
- 案1 環境診断画面 `/diag` は保留（session-2026-08-25b-HANDOFF §2-3）。
