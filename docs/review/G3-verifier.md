# G3 Verifier Report — CareLink Frontend D2 Foundation (commit `5ea86e4`)

## Verdict
**Status**: PASS (条件付き承認 / minor gaps)
**Confidence**: high
**Blockers**: 0

コミット `5ea86e4 feat(frontend): D2 foundation skeleton`（34 files / +1084 行）は、計画書 `docs/plans/D2-frontend-foundation-plan.md` の Foundation スコープを概ね満たす。受入 6 項目すべてに対し、フレッシュなソース証跡で VERIFIED または PARTIAL を確認した。FAIL 相当の欠落は無し。残課題は D3 以降で吸収可能な軽微なものに限定される。なお npm install / build は依頼により未実行。

## Evidence Table

| Check | Result | Command / Source | Output |
|-------|--------|------------------|--------|
| Git tree | clean | `git status` (develop) | nothing to commit, working tree clean |
| 対象コミット | 確認 | `git show --stat 5ea86e4` | 34 files changed, +1084 / -0 |
| ファイル列挙 | 30 files | `find frontend -type f` | tsx/ts/json/css/js が計画通り配置 |
| TypeScript strict | 有効 | `frontend/tsconfig.json` L7,L17,L18 | `strict: true`, `noUncheckedIndexedAccess: true`, `noImplicitOverride: true` |
| デザイントークン | 有効 | `frontend/styles/tokens.css` L8,L19,L21 | `#0d9488` / `#d97706` / `#faf7f2` 全て検出 |
| middleware | 有効 | `frontend/middleware.ts` | NextAuth `auth()` でガード、admin 分岐、matcher で api/login 除外 |
| NextAuth route | 有効 | `frontend/app/api/auth/[...nextauth]/route.ts` | `handlers` を re-export |
| NextAuth provider | Credentials | `frontend/lib/auth.ts` L18-L42 | Credentials Provider + zod、JWT セッション、role/accessToken コールバック |
| shadcn/ui | 規約準拠 | `components/ui/{button,input,card}.tsx` | 3 ファイルすべて `forwardRef` + `cn()` 使用 |
| 型補強 | 有効 | `types/next-auth.d.ts` | Session/User/JWT 拡張、`role: AppRole` 注入 |
| Build | 未実行 | (依頼により省略) | npm install / build はスコープ外 |
| Tests | 未実行 | (Vitest は本コミット未導入) | Phase 6 に持ち越し済み |

## 受入基準評価表

| # | 受入基準 | 状態 | 証跡 / 備考 |
|---|----------|------|------------|
| 1 | ファイル網羅（package/tsconfig/next/tailwind/postcss + app群 + middleware + tokens + lib3 + 5 components + 3 UI + types） | VERIFIED | `git show --stat` で全 30 ファイル確認。4 ページ = `dashboard/patients/staff/schedule/page.tsx`、5 components = `AppShell/Sidebar/Header/MobileShell/AiFab`、3 UI = `button/input/card`、lib3 = `auth/api-client/utils`、`types/next-auth.d.ts` 存在。 |
| 2 | TypeScript strict + noUncheckedIndexedAccess | VERIFIED | `tsconfig.json` L7 `"strict": true`、L17 `"noUncheckedIndexedAccess": true`、加えて L18 `noImplicitOverride` も有効。`paths: {"@/*": ["./*"]}` 正常。 |
| 3 | tokens.css に Teal #0D9488 / Terracotta #D97706 / Cream #FAF7F2 | VERIFIED | L8 `--brand-primary: #0d9488`、L12 `--brand-accent: #d97706`、L21 `--color-cream: #faf7f2`。L16-L29 で legacy alias（`--color-teal/terracotta/cream`）も併設、tailwind.config.ts L13-L33 で `var(--xxx)` 経由参照。density クラス（compact/standard/comfy）も完備。 |
| 4 | middleware: 未ログイン→/login、/admin/* は admin のみ、matcher で /api/* と /login 除外 | VERIFIED | `middleware.ts` L17-L21 で `!session` 時 `/login` リダイレクト + `callbackUrl` 付与、L23-L25 で `ADMIN_PREFIX` を `role !== 'admin'` の場合 `/dashboard` へ、L39 matcher `'/((?!api|_next/static|_next/image|favicon.ico|login).*)'` で要件を満たす。L27-L32 に common ロール（admin/manager/staff）チェックも追加実装。 |
| 5 | NextAuth: route.ts 存在 + Credentials Provider 設定 | VERIFIED | `app/api/auth/[...nextauth]/route.ts` で `handlers` を `GET/POST` re-export（v5 流儀、3 行）。`lib/auth.ts` L18-L42 で `Credentials` プロバイダ、zod スキーマ検証、JWT セッション、`role/accessToken` を JWT/Session コールバックに伝搬。**留意**: `authorize()` は placeholder 実装で常時成功する（L34-L41、コメントで TODO 明記、D1 backend 統合時に置換予定）。 |
| 6 | shadcn/ui: Button/Input/Card が forwardRef + cn() | VERIFIED | `button.tsx` L37 `React.forwardRef` + L43 `cn(buttonVariants(...))`、cva variants（default/outline/ghost/destructive × sm/md/lg/icon）+ `asChild` 対応。`input.tsx` L6 forwardRef + L11 cn()。`card.tsx` で Card/CardHeader/CardTitle/CardContent 4 種すべて forwardRef + cn()。 |

## Gaps

- **NextAuth `authorize()` の placeholder 実装** — Risk: medium — `lib/auth.ts` L34-L41 で email/password が空でなければ無条件にユーザを返す。本コミットは Foundation スコープ（D1 統合は次フェーズ）であり TODO コメント明記済みなので blocker ではないが、D3 マージ前に backend `/auth/login` 連携への置換が必須。
- **Login page が NextAuth `signIn()` を呼んでいない** — Risk: low — `app/(auth)/login/page.tsx` L19-L21 はダミー setTimeout 後に `window.location.href = '/dashboard'` するのみで、middleware ガードと結合していない。TODO コメント有り。実装上は middleware が未ログインを拾うため一見動くが、`callbackUrl` の handover が取れない。
- **計画書ファイル名のズレ** — Risk: low — レビュー依頼は `D2-frontend-plan.md` を参照していたが、実体は `D2-frontend-foundation-plan.md`。内容は依頼の受入観点と整合するため評価には影響なし。
- **計画書記載の依存（`@tanstack/react-query` / `zustand` / `react-hook-form` / `vitest` / `msw` / `next-auth-providers/credentials` 以外の Auth.js extras）が package.json に不在** — Risk: low — 計画 §3 の主要 npm のうち、本コミットには `next/react/next-auth/@auth/core/@radix-ui/react-slot/cva/clsx/lucide-react/tailwind-merge/tailwindcss-animate/zod` のみ。Phase 4-6（TanStack Query / Zustand / RHF / Vitest / MSW / PWA）は本コミットでは未着手で、D3 以降に持ち越し。スコープ宣言と整合。
- **Mobile レイアウト未配線** — Risk: low — `MobileShell.tsx` は実装済みだが `app/(mobile)/layout.tsx` および 4 ルート（home/today/this-week/mypage）は本コミット未含。受入 1 の指定範囲外なので blocker ではない。
- **`next.config.js` に `output: 'standalone'` 未設定** — Risk: low — 計画 §2/§4 では `output: 'standalone'` を要件としていたが本コミットは未指定。Docker パッケージング段階（D5）で追加可能。
- **`react@19.0.0-rc-...` を pin 採用** — Risk: low — RC 版依存は将来 npm install 時に DEPRECATED 警告の可能性。next 15.0.3 と整合は取れている。

## Recommendation

**APPROVE (with non-blocking follow-ups)** — 受入基準 1〜6 はすべて VERIFIED。コミット `5ea86e4` は D2 Foundation のスコープを十分に満たし、ディレクトリ構造・トークン・middleware・NextAuth・shadcn/ui 規約の 6 観点に欠落なし。上記 Gap は Foundation の TODO として明示済みのものか、計画書上 D3 以降フェーズに切られているもののみで、blocker は無し。次フェーズ（D3 個別画面）着手前に、`authorize()` の backend 連携と `signIn()` 呼び出しの 2 点を最優先で解消することを推奨する。
