# W2-E PWA + alert→toast — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL)
**Commit**: `3467fe3 feat(W2-E): PWA manifest + minimal SW + icons + alert→toast migration`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### 1. SW の `/api/auth/*` 除外がプロジェクトの実認証経路にマッチしない
- 実認証は `${env.BACKEND_API_BASE_URL}/api/v1/auth/login` (別オリジン or `/api/v1/auth/`)
- `sw.js:38` `url.pathname.startsWith('/api/auth/')` は到達しない
- 将来 BFF を同一オリジン化したら PII 入り auth レスポンスが SW キャッシュに residual
- **Fix**: `if (url.pathname.startsWith('/api/')) { event.respondWith(fetch(req)); return; }` で `/api/` 全般を network-only に

### 2. Maskable icon の safe-zone 違反 + ロゴが左寄り
- `maskable-512.png` の "C" が x=193..213、左マージン 37.7% / 右マージン 58.2% で非対称
- Android の円形マスクで欠ける可能性
- **Fix**: PIL `ImageDraw.text(..., anchor='mm')` で中心配置、80% safe zone (内側 410×410) に収まる "C"

## Major Findings

### 3. オフライン fallback ページ無し
- `sw.js:64-78` で network-first cached なしなら throw
- インストール直後オフラインで真っ白
- **Fix**: `/offline.html` プリキャッシュ + navigation request の catch で返す

### 4. キャッシュバージョンが固定文字列
- `sw.js:8` `'careflow-v1'` でデプロイ毎の更新が手動
- **Fix**: `next.config.js generateBuildId` 結果を sw.js に注入

### 5. ランタイムキャッシュが無制限
- `sw.js:71-77` で TTL/max-entries 制御なし
- 数百MB 到達リスク
- **Fix**: LRU or workbox-strategies

### 6. ログアウト後の HTML キャッシュ残留可能性
- `lib/api/fetcher.ts:87` の `signOut()` GET ナビ直後、RUNTIME_CACHE に旧 HTML
- **Fix**: `/login`, `/api/v1/auth/logout` を network-only リスト

## Minor Findings

- theme_color 大文字小文字、SW 登録の二重 listener、`--color-bg-window` と `--bg-app` 重複、icon RGB (alpha無)、install で waitUntil なし

## What's Missing

- offline.html、`/_next/data/*` 戦略、navigationPreload 有効化、scope 明示、orientation/lang/dir、SW テスト、CSP worker-src

## Multi-Perspective Notes

- **Security (SW)**: `/api/v1/*` が SW キャッシュに residual → 訪問看護 HIPAA 相当配慮で全 network-only 推奨
- **Ops**: SW 登録失敗が `console.error` で握り潰し → Sentry 連携必要

## Verdict Justification
ADVERSARIAL escalate。Critical 2 + Major 4。Critical 解消で ACCEPT-WITH-RESERVATIONS 格上げ。
