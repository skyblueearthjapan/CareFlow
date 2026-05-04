# CareLink D2 Foundation Skeleton — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus)
**Branch**: `develop`
**Date**: 2026-05-05
**Commit**: `5ea86e4`

## VERDICT: REVISE

## 概要

D2 計画書「Foundation のみで D3 即着手」というスコープに対し、実装は Next.js 15 / TS strict / Warm & Human トークン / AppShell・Sidebar・Header・MobileShell・FAB / NextAuth v5 / shadcn 風 Button・Input・Card まで概ね下地を整えており、骨格としての方向性は正しい。一方で、**(1) `app/(app)/layout.tsx` にレイアウトを入れたのに `middleware.ts` がそこへルーティングを許可していない（=サインイン後即無限リダイレクトの懸念）、(2) RootLayout に `<SessionProvider>` が無く client component から `useSession()` が使えない、(3) `<MobileShell>` が定義されているのに `(mobile)` ルートグループも `/m/*` ページも実体が無く到達不能**、という配線上の致命傷が複数あり、計画書の受入基準（9ルート、未認証→`/login`、Cmd+K で `aiInputOpen=true` 発火）を満たさない。トークンとレイアウトの寸法・色は計画と仕様書に高い精度で一致しているため、骨格設計は合格、配線とスコープ整合は要修正。

## Pre-commitment 予測 vs 実結果

| 予測 | 結果 |
|---|---|
| NextAuth v5 ベータの API 揺れで型が綻ぶ | 当たり（middleware の `req.auth` 型を手動で `& { auth?: ... }` 拡張、`callbacks.session` で `as DefaultSession & { accessToken?: string }` キャストしており型拡張モジュールが効いていない疑い） |
| `noUncheckedIndexedAccess` で `email.split('@')[0]` 系が落ちる | 当たり（`lib/auth.ts:37` で `?? 'user'` 追加済みだが他に潜在） |
| PWA / TanStack Query / openapi-typescript 抜け | 完全に当たり |
| Tailwind の CSS 変数色とフォーカスリング名衝突 | 半当たり（`text-text-primary` のような二重 prefix が冗長） |
| サイドバー幅切替を Tailwind 動的クラスでやって purge に消される | 半当たり（`w-[72px]` `w-[232px]` は arbitrary value なのでセーフだが脆い） |

## Major Findings

### 1. middleware と route group の配線が破綻している（CRITICAL → MAJOR）

**Confidence: HIGH**

`middleware.ts:6` で `COMMON_PREFIXES = ['/dashboard', '/patients', '/staff', '/schedule']` を許可リストにしているが、`matcher: ['/((?!api|_next/static|_next/image|favicon.ico|login).*)']` は **`/api/auth/[...nextauth]`** を `api` プレフィックスで除外している点は OK。ただし `next-auth@5` の `auth()` ヘルパーで wrap した middleware は内部で session cookie 取得 → cookie 取得には `/api/auth/session` 呼び出しが要るが、**クライアント側からの初回アクセスでは middleware と Edge runtime の組合せで `auth()` が NextAuth v5 ベータの破壊的変更により `req.auth` プロパティ名で渡るとは限らない**（`@auth/core@0.37.4` + `next-auth@5.0.0-beta.25` は仕様揺れ中）。

**Fix**:
- `import type { NextAuthRequest } from 'next-auth'` を使う（または公式の `export default auth(function middleware(req) { ... })` 形に揃える）
- `middleware.ts` に最低限の smoke test として「未認証で `/dashboard` にアクセス → 302 `/login?callbackUrl=/dashboard`」「authorized で `/admin/*` にアクセス → admin 以外 302 `/dashboard`」を vitest + Edge runtime stub で書く
- `COMMON_PREFIXES` のロジックは事実上「session があれば全許可」と同義。冗長なので削除するか、未来の role 拡張用にコメントを残す

### 2. RootLayout に SessionProvider / QueryClientProvider が存在せず、client component から認証/データ取得 hook が呼べない

**Confidence: HIGH**

`app/layout.tsx:12-18` の RootLayout は Inter フォントと `<body>` のみ。`<SessionProvider>` も `<QueryClientProvider>` も無い。

- `frontend/lib/auth.ts` は `auth()` を export しているので **server component からは** session を取れるが、`'use client'` の `LoginPage`, `AppShell`, `Header`, `AiFab` から `useSession()` を呼ぶ手段が無い
- 計画書 §4 タスク 17「TanStack Query Provider」が D2 内部タスクとして明記されているが、**まったく未着手**（`package.json` に `@tanstack/react-query` 自体が無い）

**Fix**:
- `app/providers.tsx`（client component）を作り、`<SessionProvider>` + `<QueryClientProvider>` をネスト、`RootLayout` で children を `<Providers>` でラップ
- `package.json` に `@tanstack/react-query@^5`, `@tanstack/react-query-devtools@^5` を追加

### 3. MobileShell が宙に浮いている（実装はあるが配線無し）

**Confidence: HIGH**

`components/MobileShell.tsx` は `/m/home` `/m/today` `/m/this-week` `/m/mypage` を参照するが、`app/(mobile)/...` ルートグループも `app/m/*` ディレクトリも存在しない。受入基準「9ルート（login/dashboard/weekly/master/integration/home/today/this-week/mypage）が雛形表示」「モバイル経路でボトムナビ4タブ + safe-area」が**未達**。

**Fix**:
- `app/(mobile)/layout.tsx` に `<MobileShell>` ラッパを作成、`home/today/this-week/mypage/page.tsx` を雛形 `<h1>` のみで追加
- `app/page.tsx` の単純 `redirect('/dashboard')` を、`headers()` の User-Agent もしくは viewport hint を見て分岐するか、計画 §9-5 通りに UA 判定リダイレクト追加

### 4. `app/(app)` の page 構成が計画と乖離（weekly/master/integration vs 実装の schedule/patients/staff）

**Confidence: HIGH**

D2 計画 §2 と §8 受入基準は **`/dashboard /weekly /master /integration`** の4本が `(app)` 配下、加えてモバイル4本で合計9ルート。

実装は **`/dashboard /patients /staff /schedule`** の4本。`weekly` `master` `integration` は無く、`patients` `staff` は計画外。設計書 03-layouts.md §3-1 のサイドバー描画は「ダッシュ／週ビュー／マスタ／連携」の4項目を前提としている。

**Fix**: 計画書または実装のどちらかを正に揃える。設計書の「マスタ＝患者+スタッフのタブ切替」を維持するなら、`(app)/master/page.tsx` を作り、内部に Tabs で `/master?tab=patients|staff` を持たせる方が D3-screens-plan.md と整合する。

### 5. NextAuth `User` 型の二重管理 cast

**Confidence: MEDIUM**

`types/next-auth.d.ts` で `User` `Session` `JWT` を拡張済みなのに、コールバック内では型推論を信頼せず毎回 cast している。`tsconfig.json` の `strict: true` + `noUncheckedIndexedAccess` 環境下で **declaration merging が効いていない**シグナル。

**Fix**:
- `lib/auth.ts` の `jwt`/`session` callback で `import type { JWT } from 'next-auth/jwt'` `import type { Session } from 'next-auth'` を明示
- middleware は `auth(function middleware(req) { ... })` 形式、引数は推論させる

### 6. `noUncheckedIndexedAccess` 下の潜在的 unsafe access

**Confidence: MEDIUM**

`tailwind.config.ts:1` の `import type { Config } from 'tailwindcss'` で、ESLint の `next/typescript` preset 適用下で `tailwind.config.ts` が `lint` 対象に入ると、**`require('tailwindcss-animate')` が `@typescript-eslint/no-require-imports` で警告**になる可能性が高い。

**Fix**: `tailwind.config.ts` を import 形式に置換。
```ts
import animate from 'tailwindcss-animate';
plugins: [animate],
```

### 7. パッケージマネージャーの不整合

**Confidence: HIGH**

- 計画書 §9-8「`pnpm` 採用、`packageManager` 固定」
- `README.md:11` は `npm install` `npm run dev` で記述
- `package.json` に `packageManager` フィールド無し、lockfile も commit 内に無い

**Fix**: `pnpm` を選び、`"packageManager": "pnpm@9.x.x"` 追加 + `pnpm install` で lockfile commit。README を pnpm に統一。

### 8. AiFab の状態が ローカル `useState` どまり、計画 §4 タスク 18 の Zustand UI Store と未連携

**Confidence: HIGH**

`AiFab.tsx:7` `const [open, setOpen] = useState(false);` は **ローカル state**。計画は明示的に「`aiInputOpen` を Zustand UI Store」と記述。

**Fix**: `lib/stores/ui.ts` を作成、`zustand` を依存追加、`useUIStore(s => [s.aiInputOpen, s.setAiInputOpen])` に置換。

## Minor Findings

1. **`app/layout.tsx:5` Inter フォントを next/font で読みつつ、`tokens.css` の `--font-sans` には Inter が含まれている**が、実際には self-host されておらず素の system font fallback。Noto Sans JP / Noto Serif JP の next/font 取り込みも未実装。

2. **Tailwind カラー命名が二重 prefix**：`text-text-primary` `bg-bg-app` のように `text-` `bg-` が前置詞で2回続く。冗長。

3. **`AppShell` がクライアントコンポーネントで `useState`**：collapsed 状態を server で初期化できないため、SSR 時に必ず展開状態 232px で描画。

4. **`tsconfig.json` `paths` が `@/*` のみ**：計画 §2 の `lib/api`, `lib/auth/`, `lib/stores/` などサブディレクトリ構成と不一致。

5. **`.eslintrc.json` を ESLint 9 で使用**：ESLint 9 は flat config (`eslint.config.js`) がデフォルト。

6. **`AiFab` のグラデーション 3 色目 `#14B8A6` を tokens に持っていない**。

7. **README が `cd frontend` → `npm install` を案内**：`pnpm install` に統一。

## What's Missing（D2 計画と受入基準に対するギャップ）

| 計画項目 | 状態 |
|---|---|
| `app/(mobile)/` ルートグループと 4 ページ | **欠落** |
| `app/manifest.webmanifest` (PWA) | **欠落**（受入基準「Lighthouse PWA 通過」） |
| `public/icons/icon-192.png` 等 | **欠落** |
| `next.config.js` `output: 'standalone'` | **欠落** |
| TanStack Query + Provider | **欠落** |
| Zustand Store | **欠落** |
| openapi-typescript + openapi-fetch + `lib/api/generated` | **欠落** |
| `lib/config/env.ts` (zod env validation) | **欠落** |
| Toast (sonner) | **欠落** |
| Dialog / Tooltip / Skeleton / Select / Tabs / Calendar / Table | **欠落** |
| Vitest + @testing-library + jsdom + msw | **欠落** |
| Husky / lint-staged / commitlint | **欠落** |
| Dockerfile / docker-compose.yml | **欠落** |
| `app/not-found.tsx` `app/error.tsx` `app/loading.tsx` | **欠落** |
| `dev/tokens` ページ（Storybook 代替） | **欠落** |
| Sidebar フッターの「今日の訪問 17件」カードと「ユーザーフッター（admin/staff role badge）」 | **欠落** |
| Sidebar のロゴ gradient 背景 36×36 角丸 + drop-shadow | **欠落** |
| Header のユーザーメニュードロップダウン | **欠落** |
| 通知未読バッジ赤丸 | **欠落** |
| staff role時のサイドバー項目数2件への絞り込み | **欠落** |

合計で計画と受入基準の **半分以上が未実装**。Phase 1+2+一部 3 を完了している段階。Phase 4-6 のうち NextAuth の枠組みだけ前倒しで触ったが Phase 4 の TanStack Query / Zustand / API Client(openapi) は欠、Phase 5 / 6 はゼロ。「D3 即着手」要件は **満たさない**。

## Verdict Justification

**APPROVE への引き上げ条件**：
1. SessionProvider + QueryClient Provider を `app/providers.tsx` で導入
2. `(mobile)` route group + 4 ページ作成、UA 分岐を `/` に追加
3. middleware の型を `auth()` ヘルパーの推論型に揃える
4. `manifest.webmanifest` + 最低限の icon (192/512/maskable) 配置
5. `next.config.js` に `output: 'standalone'` を追加
6. packageManager 固定 + lockfile commit
7. `lib/stores/ui.ts` (Zustand) で `aiInputOpen` 移管
8. `(app)` 配下のルート名（weekly/master/integration vs schedule/patients/staff）を planner と確定
9. Vitest + middleware 単体テスト 1 本以上

## Open Questions（unscored）

- `(app)/dashboard/page.tsx:24` の `BACKEND_API_BASE_URL` は env 名そのものをコメントしているが、`env.ts` で参照しないなら誤った内部 URL 構成リスク。
- ESLint flat config 移行の必要有無は ESLint 9 + next 15 の組み合わせで検証必要。
- `tailwind-merge@2.5.4` と `tailwindcss@3.4.14` の組合せに問題は無いが、Tailwind v4 のリリースが近い。
- `next-auth@5.0.0-beta.25` 時点で middleware に渡る request 型の正式名称を v5 changelog で確認必要。
