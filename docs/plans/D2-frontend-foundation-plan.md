# D2: Frontend Foundation 実装計画書

## 1. 概要・目的

CareLink フロントエンドの「土台」を構築する。Next.js 15 (App Router) + TypeScript strict + Tailwind CSS + shadcn/ui を基盤に、Warm & Human デザイントークン、共通レイアウト（外枠カード/サイドバー/ヘッダー/ボトムナビ/FAB）、認証・状態管理・APIクライアント・PWA・テスト基盤までを整備し、D3 以降の画面実装が即座に着手できる状態にする。個別画面の作り込みは含めず、Foundation のみに限定する。

## 2. ディレクトリ構造案

```
carelink-frontend/
├── app/
│   ├── (auth)/login/page.tsx
│   ├── (app)/
│   │   ├── layout.tsx                # AppShell
│   │   ├── dashboard/page.tsx        # 雛形のみ
│   │   ├── weekly/page.tsx
│   │   ├── master/page.tsx
│   │   └── integration/page.tsx
│   ├── (mobile)/
│   │   ├── layout.tsx                # MobileShell
│   │   └── home|today|this-week|mypage/page.tsx
│   ├── api/auth/[...nextauth]/route.ts
│   ├── layout.tsx                    # RootLayout (font, providers)
│   ├── globals.css
│   ├── manifest.webmanifest
│   └── not-found.tsx / error.tsx
├── components/
│   ├── ui/                # shadcn/ui ベース
│   ├── layout/            # AppShell, Sidebar, Header, BottomNav, OuterCard, FAB
│   ├── common/            # Avatar, EmptyState, Skeleton, VisitChip 等
│   ├── feedback/          # Toast, Modal, Tooltip
│   └── icons/             # lucide ラッパ
├── lib/
│   ├── api/               # OpenAPI 生成型 + fetch クライアント
│   ├── auth/              # NextAuth 設定 + helpers
│   ├── stores/            # Zustand (UI状態)
│   ├── utils/             # cn, formatDate, tabularNum
│   └── config/            # env.ts (zod), constants.ts
├── styles/
│   ├── tokens.css         # Warm & Human CSS Custom Properties
│   └── density.css
├── public/icons/
├── hooks/
├── types/
├── tests/
│   ├── setup.ts
│   └── unit/
├── middleware.ts          # 認証ガード + ロール分岐
├── next.config.mjs (output:'standalone')
├── tailwind.config.ts
├── postcss.config.mjs
├── tsconfig.json (strict)
├── vitest.config.ts
├── components.json (shadcn/ui)
├── Dockerfile / docker-compose.yml
└── package.json
```

## 3. 依存関係

### 上流
- D1 Backend OpenAPI（`openapi.json`）→ openapi-typescript で型生成
- D1 認証 endpoint（`/auth/login`, `/auth/me`）
- 設計ドキュメント `docs/design/00-03`

### 下流
- D3 個別画面実装
- D4 グローバルAI入力モーダル
- D5 PWA本実装・通知

### 主要 npm
- `next@15`, `react@19`, `typescript@5`
- `tailwindcss@3.4+`, `tailwindcss-animate`, `clsx`, `tailwind-merge`, `class-variance-authority`
- `@radix-ui/react-*`, `lucide-react`, `sonner`
- `@tanstack/react-query@5`, `@tanstack/react-query-devtools`, `zustand@4`
- `openapi-typescript`, `openapi-fetch`
- `next-auth@5`
- `react-hook-form`, `@hookform/resolvers`, `zod`
- `vitest`, `@testing-library/react`, `jsdom`, `msw`

## 4. タスク分解

### Phase 1: プロジェクト基盤（2日）

1. **Next.js 15 初期化** (0.5d) — App Router + TS strict + Tailwind + ESLint
2. **ESLint / Prettier / Husky** (0.5d) — Conventional Commits、pre-commit
3. **環境変数スキーマ** (0.25d) — `lib/config/env.ts` で zod バリデーション
4. **Dockerfile / compose 雛形** (0.25d) — multi-stage、`output: standalone`

### Phase 2: デザインシステム（2日）

5. **デザイントークン CSS** (0.5d) — `styles/tokens.css` に Warm & Human 全トークン
6. **Tailwind 設定（トークン連携）** (0.5d) — `theme.extend.colors` を `var(--xxx)` 参照
7. **フォント設定（next/font）** (0.5d) — Noto Sans/Serif JP + JetBrains Mono
8. **shadcn/ui 導入と Warm 化** (1d) — 必須コンポーネント追加、cva で Button variants

### Phase 3: レイアウト・ルーティング（2日）

9. **OuterCard** (0.25d) — radius 20、shadow合成、margin 14
10. **Sidebar** (0.5d) — 232/72px、ブランド領域 60px、ハート+Serif、フッターカード
11. **Header** (0.5d) — 60px、ハンバーガー + Serif タイトル + 通知 + ユーザー
12. **AppShell** (0.5d) — `app/(app)/layout.tsx` で構成
13. **MobileShell + BottomNav** (0.5d) — h48 + h64、4タブ、safe-area
14. **AI入力 FAB** (0.25d) — gradient teal、リング光彩、Cmd+K
15. **ルーティング雛形** (0.25d) — 各 page.tsx に `<h1>`、not-found/error/loading

### Phase 4: 認証・状態・API（2日）

16. **NextAuth.js 統合** (1d) — Auth.js v5 Credentials、JWT セッション、middleware
17. **TanStack Query Provider** (0.25d) — QueryClientProvider、staleTime 30s
18. **Zustand UI Store** (0.25d) — sidebarCollapsed、density、aiInputOpen
19. **API クライアント** (0.5d) — openapi-typescript + openapi-fetch、Bearer 自動注入

### Phase 5: 横断機能（1日）

20. **Toast 基盤** (0.25d) — sonner、tone マッピング
21. **Lucide アイコンラッパ** (0.25d) — 必要40個、strokeWidth 1.75
22. **共通コンポーネント** (0.5d) — Avatar、EmptyState、Skeleton、Tooltip、Section、VisitChip雛形

### Phase 6: PWA・テスト・仕上げ（1日）

23. **PWA 基盤** (0.5d) — manifest.webmanifest、独自 SW（オフラインフォールバックのみ）
24. **Vitest セットアップ** (0.25d) — jsdom、@testing-library/jest-dom
25. **MSW** (0.25d) — テスト用ハンドラ + dev時の API モック

合計 **10人日**

## 5. デザイントークン CSS マッピング

```css
:root {
  /* ブランド */
  --brand-primary: #0D9488;
  --brand-primary-hover: #0F766E;
  --brand-primary-light: #CCFBF1;
  --brand-primary-50: #F0FDFA;
  --brand-accent: #D97706;
  --brand-accent-light: #FEF3C7;

  /* ニュートラル（クリーム） */
  --bg-base: #FFFFFF;
  --bg-app: #FAF7F2;
  --bg-muted: #F5F1EA;
  --bg-window: #E7E2D8;
  --border-default: #E7E5E4;
  --border-subtle: #F0EDE8;
  --border-strong: #D6D3D1;
  --text-primary: #1C1917;
  --text-secondary: #57534E;
  --text-muted: #A8A29E;
  --text-inverted: #FFFFFF;

  /* セマンティック */
  --success: #059669;  --success-bg: #D1FAE5;
  --warning: #D97706;  --warning-bg: #FEF3C7;
  --error: #DC2626;    --error-bg: #FEE2E2;
  --info: #0D9488;     --info-bg: #CCFBF1;

  /* データ系 */
  --c-medical: #0F766E;  --c-medical-bg: #CCFBF1;
  --c-care: #92400E;     --c-care-bg: #FEF3C7;
  --c-event: #9D174D;    --c-event-bg: #FCE7F3;
  --c-coupled: #9A3412;  --c-coupled-bg: #FFEDD5;
  --c-mentor: #0F766E;   --c-mentor-bg: #CCFBF1;
  --c-special: #BE185D;  --c-special-bg: #FCE7F3;

  /* VisitChip 専用 */
  --vc-medical-bg: #EFF6FF;  --vc-medical-bd: #BFDBFE;  --vc-medical-fg: #1D4ED8;
  --vc-care-bg:    #ECFDF5;  --vc-care-bd:    #A7F3D0;  --vc-care-fg:    #047857;
  --vc-event-bg:   #F5F3FF;  --vc-event-bd:   #DDD6FE;  --vc-event-fg:   #6D28D9;
  --vc-coupled-bg: #FFFBEB;  --vc-coupled-bd: #FDE68A;  --vc-coupled-fg: #92400E;
  --vc-special-bg: #FDF2F8;  --vc-special-bd: #FBCFE8;  --vc-special-fg: #BE185D;

  /* タイポ */
  --font-sans:  'Noto Sans JP', -apple-system, system-ui, sans-serif;
  --font-serif: 'Noto Serif JP', 'Hiragino Mincho ProN', serif;
  --font-mono:  'JetBrains Mono', ui-monospace, monospace;

  /* 半径 */
  --radius-sm: 8px; --radius: 10px; --radius-md: 12px;
  --radius-lg: 16px; --radius-xl: 20px;

  /* 影（warm） */
  --shadow-xs: 0 1px 2px rgba(28,25,23,0.04);
  --shadow-sm: 0 1px 3px rgba(28,25,23,0.06), 0 1px 2px rgba(28,25,23,0.04);
  --shadow-md: 0 4px 12px rgba(28,25,23,0.06), 0 2px 4px rgba(28,25,23,0.04);
  --shadow-lg: 0 12px 24px rgba(28,25,23,0.08), 0 4px 8px rgba(28,25,23,0.04);
  --shadow-xl: 0 24px 48px rgba(28,25,23,0.10);
  --shadow-outer-card: 0 1px 2px rgba(28,25,23,0.04), 0 8px 24px rgba(28,25,23,0.05);
  --shadow-fab: 0 8px 24px rgba(13,148,136,0.40), 0 0 0 6px rgba(13,148,136,0.10);
}

[data-density="compact"]  { --row-h:36px; --pad-card:16px; --gap:8px; }
[data-density="standard"] { --row-h:44px; --pad-card:20px; --gap:12px; }
[data-density="comfy"]    { --row-h:56px; --pad-card:28px; --gap:16px; }
```

## 6. 共通コンポーネント実装方針

| コンポーネント | 方針 | 理由 |
|---|---|---|
| Button / Input / Select / Card / Dialog / Dropdown / Tabs / Tooltip / Accordion / Calendar / Skeleton / Table / Separator | shadcn/ui ベース + Warm 化 | a11y 枯れている、cva で variant 拡張 |
| Toast | sonner | shadcn 推奨、軽量 |
| Badge | 自作（shadcn 上書き） | tone マッピングが業務独自 |
| Avatar | 自作 | initials + シード色12種ロジック固有 |
| EmptyState / Section / VisitChip | 完全自作 | プロジェクト固有 |
| OuterCard / AppShell / Sidebar / Header / BottomNav / FAB | 完全自作 | Warm & Human の核 |
| Form | react-hook-form + zod | shadcn の Form ラッパも採用 |

スタイリング戦略：トークンは CSS Custom Property、Tailwind は `theme.extend` で参照。インラインスタイル原則禁止。

## 7. テスト方針

### Vitest
- 共通コンポーネント単体（Button variants、Badge tone、Avatar initials、Sidebar collapse、middleware）
- 目標カバレッジ 70%（共通コンポーネント85%）
- Testing Library ベース、スナップショットは最小限

### Storybook
- Phase 1〜5 では導入しない（コスト過大）
- 代替: dev環境のみの `/dev/tokens` ページで配色・タイポ・コンポーネント一覧表示

### E2E
- Foundation 範囲では実施しない（D3 完了後 Playwright を別計画で）

## 8. 受入基準

- [ ] `pnpm dev` で起動、9ルート（login/dashboard/weekly/master/integration/home/today/this-week/mypage）が雛形表示
- [ ] `pnpm build` 成功、`output: standalone` で `.next/standalone` 生成
- [ ] `pnpm typecheck` strict 違反 0
- [ ] `pnpm lint` 警告 0
- [ ] `pnpm test` 全パス
- [ ] 配色・タイポ・影・半径が `01-design-system.md` と一致
- [ ] 外枠カード（radius 20、margin 14）が `(app)` で描画
- [ ] Sidebar 折りたたみ（232⇔72）が 200ms ease、staff時に項目2件
- [ ] PCヘッダー60px、Sidebarブランド領域も60px、罫線水平揃い
- [ ] モバイル経路でボトムナビ4タブ + safe-area
- [ ] AI 入力 FAB が右下常駐、Cmd+K で `aiInputOpen=true` 発火
- [ ] NextAuth Credentials ログイン成功、未認証→`/login` リダイレクト
- [ ] TanStack Query Devtools が dev で開く、API client に Bearer 注入
- [ ] manifest.webmanifest 配信、Lighthouse PWA 通過
- [ ] Lucide 主要40アイコン import可能
- [ ] フォント Noto Sans/Serif JP + Mono が CSS変数経由で適用
- [ ] Docker イメージビルド・起動可能

## 9. リスク + 対策

1. **Backend OpenAPI 未確定** — 仮 schema で先行、確定後 `pnpm gen:api` 再生成、MSW でモック並行開発
2. **Noto Serif JP のロード重い** — `font-display: swap`、Serif は見出し限定 weight:700 のみ、必要ならサブセット self-host
3. **Auth.js v5 ベータ仕様変動** — stable release を待つ or migration ガイド準拠、Credentials 最小実装
4. **shadcn/ui 既定色とトークン衝突** — `theme.extend.colors` をトークン参照に統一、生成後の置換スクリプト
5. **PC/モバイル経路分岐** — `(app)` `(mobile)` を別ルートグループ、トップ `/` で UA 判定リダイレクト
6. **density 切替が CSS変数で全コンポ反映しない** — shadcn 既定 sm/md/lg を `--row-h` 連動カスタム
7. **PWA SW と Next.js の相性** — next-pwa 避け、独自最小 SW（オフラインフォールバックのみ）
8. **Windows 環境差** — `pnpm` 採用、`packageManager` 固定、Volta/nvm-windows で Node 20 LTS
9. **TypeScript strict サードパーティ衝突** — `skipLibCheck: true`、生成型は `lib/api/generated`
10. **D3 とのスコープ衝突** — 「画面=h1のみ」を明確に約束。VisitChip/FABモーダル本体は D3/D4 担当

## 10. 想定全体工数

- Phase 1〜6 合計：**10人日**（2週間、1人体制）
- 並列化可能：Phase 2（デザイン）と Phase 4（認証）は独立、2人体制で 6〜7日に短縮
- 段階的マージ：Phase 1+2 / Phase 3 / Phase 4 / Phase 5+6 の4段階
