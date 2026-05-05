# W2-C モバイル4画面 — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus)
**Commit**: `7ef03fc feat(W2-C): mobile 4 screens + check-in flow`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### C1. admin/manager は `/api/v1/visits?limit=500` で全患者の訪問をクライアント引き寄せ
- `lib/queries/me.ts:97-102` で role フィルタ無し、`filterMyVisits` がクライアント側で絞る
- (a) 個人情報の不要転送 (プライバシー違反)、(b) 500件超で「自分の訪問」が応答に入らない可能性
- **Fix**: 常に `?staff_id={staffId}` を付け、backend が `staff_id` パラメータで visibility 絞る (admin/manager にも対応)

### C2. チェックイン/アウトの「楽観的ローカル記録」がページ離脱で消失
- `m/today/[visitId]/page.tsx:98-109` で useState のみ、永続化無し
- ホームへ戻って再アクセスで idle に戻る
- **Fix**: localStorage 永続化 (key: `checkin:{visitId}`)、または backend を先に実装

## Major Findings

- **M3**: `useMyVisit` の status 更新が無音 (mutateAsync が常に reject パス、UI は localStatus 100% 依存)
- **M4**: setLocalStatus が 404/405 のみ許容、5xx/network error は失敗扱い
- **M5**: mypage の「シフト希望提出」disabled UI に Alert で「準備中」明示が弱い
- **M6**: `currentWeekStartIso` の TZ/DST 跨ぎでバグ余地 (月曜0時に前週)
- **M7**: MobileShell の active 判定 `pathname?.startsWith(href)` で `/m/home123` も match

## Minor Findings

- m/this-week の Map 反復、`<h1>` 複数、CheckInButton の min-h、`__none__` magic、通知ボタン dead end、所属事業所が UUID 表示

## What's Missing

- オフライン対応、Geolocation 権限事前 UI、写真プレビュー保持、44px タッチ、エラーバウンダリ、テスト、i18n、aria-current

## Verdict Justification
C1 (プライバシー) + C2 (永続化なし) は本番投入不可。C1/C2 解消 + M3-M5 のいずれか 2 件以上で ACCEPT-WITH-RESERVATIONS 格上げ。
