# W1-B スタッフマスタ — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL モード)
**Commit**: `c197def feat(frontend): W1-B staff master CRUD`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### C-1. `useStaff` が ID 未確定でも fetcher を起動しうる
- **Evidence**: `frontend/lib/queries/staff.ts:65-77` — `staffId: string | null | undefined` で `enabled: isAuthenticated && !!staffId` ガードはあるが StrictMode 二重呼びで `/api/v1/staff/undefined` を撃つ可能性
- **Confidence**: HIGH
- **Fix**: `queryFn` に `if (!staffId) throw new Error('id required')` を入れる、または `queryKey` から undefined を除外

### C-2. staff ロールの UI 制約がゼロ
- **Evidence**: backend `staff.py:53-57` で staff ロールは自レコードのみ返るが、frontend の `staff/page.tsx`, `[id]/page.tsx`, `new/page.tsx` に role 判定が一切ない (grep 0件)。「新規スタッフ」「編集」「削除」が全ロール無条件レンダリング
- **Confidence**: HIGH
- **Why this matters**: backend は 403 を返すが UI で「押せるのに失敗」状態になり混乱
- **Fix**: `useSession()` で role 取得、staff ロール時に新規/削除非表示、編集も自分以外は非表示

## Major Findings

### M-1. `next-auth.d.ts` に `staffId` がない → 自レコード判定が UI で書けない
- **Evidence**: `auth.ts:58-65` の `authorize` 戻り値で `user.staff_id` を読んでいない、`types/next-auth.d.ts` に `staffId` フィールドなし
- **Fix**: `authorize` で `staffId: user.staff_id`、jwt/session callback で伝搬、`next-auth.d.ts` に追加

### M-2. UUID 入力フィールドの placeholder が不正な UUID 形式
- **Evidence**: `StaffFormFields.tsx:114-119` placeholder が `"000-0000-..."` で正しい UUID 形式ではない
- **Fix**: `例: 11111111-2222-3333-4444-555555555555` に直す、または Wave 2 までフィールド hide / disabled

### M-3. office 一覧フィルタが UUID 先頭8文字で表示
- **Evidence**: `staff/page.tsx:58-66` `seen.set(row.primary_office_id, row.primary_office_id.slice(0, 8))`
- **Why this matters**: W1-C はマージ済みで `useOffices` 存在 → 即修正可能
- **Fix**: `useOffices()` から名前マップを引いて `id → name` で表示

### M-4. 一覧の `limit=500` 暗黙上限 (501件以降サイレント切捨)
- **Evidence**: `staff/page.tsx:51-52` `useStaffList({ limit: 500 })` + 警告 alert なし
- **Fix**: 500 件達したら警告バナー、または useInfiniteQuery

### M-5. invalidate query key の重複指定 (冗長 refetch)
- **Evidence**: `useUpdateStaff:121-124` で `['staff']` と `['staff', 'detail', id]` 両方 invalidate (前者が後者を含む)
- **Fix**: 2行目削除 or `[...staffKey, 'list']` に絞る

### M-6. Edit 時の form reset が初回しか走らない
- **Evidence**: `[id]/edit/page.tsx:73-77` `useEffect(() => { if (data && form === null) setForm(...) })`
- **Fix**: 当面 TODO コメント、Wave 2 で `updated_at` 比較

## Minor Findings

m-1 (mentor 自己参照防御なし), m-2 (UUID をそのまま表示), m-3 (Esc キー / focus trap 無し), m-4 (formatDate dead code), m-5 (W1-F primitives 未利用), m-6 (zod schemas alias 名揺れ)

## What's Missing

- 権限ガード (`[id]/edit/page.tsx`, `new/page.tsx` の role check)
- 自己削除防止
- 削除済み staff の表示・復元 UI
- `secondary_offices` (StaffSecondaryOffice) 完全未対応
- shifts 編集 UI なし (W1-D allocation 動作前提に不可欠)
- mentor_id self-FK バリデーション
- frontend テスト 0 件

## Verdict Justification

CRITICAL 1 件 + MAJOR 6 件で ADVERSARIAL モード。動作はするが C-1, C-2 + M-1 の連鎖で staff ロール本番投入前に必ず塞ぐ必要あり。

## Open Questions

- W1-B のスコープに「staff ロール UI 制約」が含まれていたか
- `secondary_offices` の Wave 2 範囲は設計判断 vs 漏れ
- `useStaffList` の userId をキーに含める意図
