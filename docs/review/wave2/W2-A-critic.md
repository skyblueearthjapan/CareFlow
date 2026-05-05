# W2-A 週ビュー — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL)
**Commit**: `e9637a5 feat(W2-A): week view + visit edit dialog + allocation run UI`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### C1. 編集ダイアログに role guard が無い (admin/manager only バイパス可能)
- `schedule/page.tsx:50` で `canRunAllocate` を計算しているが、編集ダイアログには role 制限を渡していない
- staff role が VisitChip クリック → 編集 UI → 保存 → 403 で UX 故障
- **Fix**: `canEditVisit = role === 'admin' || role === 'manager'` を WeekGrid に渡し、staff role 時は chip クリック no-op

### C2. 500件 hard cap で発生する week silent loss
- `visits.ts:104` の `truncated` 判定はクライアントフィルタ前
- 対象週の visit が末尾にあれば silent loss
- order-by 保証なし
- **Fix**: backend `list_visits` に `week_start`/`week_end` クエリパラメータを追加し、週内件数で取得

### C3. `unassigned` の意味論ずれ
- `useVisits` の effectiveStaffId フィルタで `primary_staff_id === effectiveStaffId` 必須にして、staff filter 時の "未割当" は常に 0 になる
- **Fix**: unassigned 用に staff_id フィルタを通さない別フックで取得

## Major Findings

- **M1**: `staff role + staffId 無し` で UI が無音停止 (specific Alert で気付かせる)
- **M2**: WeekGrid が secondary/mentor 担当を表示しない (3 slot 全部レンダリング or 別セクション)
- **M3**: 重複(overlap)判定の境界ケース (秒切捨で偽陰性)
- **M4**: テーブル a11y 欠落 (caption / scope / role="grid")
- **M5**: AllocateRunDialog が rate-limit (429) を special case しない
- **M6**: office filter は「行のフィルタ」で「visit のフィルタ」ではない

## What's Missing
- CSV/印刷エクスポート、TZ考慮、D&D予告、未割当ドリルダウン、unsaved changes 検出、楽観的ロック、キーボードショートカット、テスト

## Verdict Justification
ADVERSARIAL に escalate (3 CRITICAL)。C1〜C3 + M2 を直し、a11y で同時対応した版を再提出してほしい。
