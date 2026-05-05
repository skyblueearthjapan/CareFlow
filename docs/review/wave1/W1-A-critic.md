# W1-A 患者マスタ — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, THOROUGH→検出時 ADVERSARIAL 検討も省略可)
**Commit**: `e3aee00 feat(frontend): W1-A patient master CRUD`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### 1. zod schema コメント vs 実装の事実誤認 — `weekly_pattern` / `special_week`
- **Evidence**: `frontend/lib/schemas/patient.ts:10-13` で「weekly_pattern と special_week は backend schema に存在しない」と書かれているが、`backend/app/models/patient.py:53-54` には `weekly_pattern: Mapped[dict | None]` (JSONB), `special_week: Mapped[dict | None]` (JSONB) が**既に存在**。`queries/patients.ts:75-83` の `stripWave2` がこれらを削除して送信。
- **Confidence**: HIGH
- **Why this matters**: 既存 DB カラムを意図的に握り潰している。型も食い違い (FE: string/boolean ↔ DB: dict)。Pydantic schema にも両フィールドが無いので Backend API 経由では永続化できない構造で、ユーザー入力が黙って消える。
- **Fix**: (a) Pydantic schema に追加、(b) FE zod を `z.record(z.unknown()).optional()` に、(c) `stripWave2` 削除、(d) コメント更新。

### 2. `special_week` の型ミスマッチ (`boolean` vs `JSONB dict`)
- **Evidence**: `schemas/patient.ts:82` `special_week: z.boolean().optional()` ↔ `models/patient.py:54` `Mapped[dict | None]`
- **Confidence**: HIGH
- **Fix**: 仕様確定 — ON/OFF なら DB を Boolean に migrate、構造体なら FE を `z.record(z.unknown())`。

### 3. `lat` / `lng` の coerce が `0` を捨てる経路
- **Evidence**: `schemas/patient.ts:62-69` の `z.union([z.coerce.number()..., z.literal('')])` で空文字が `0` に coerce される可能性
- **Confidence**: MEDIUM
- **Why this matters**: 患者の住所未入力で空のまま送信すると緯度経度が **`0,0` (ギニア湾沖)** で永続化され、地図/距離計算で全患者がギニア湾扱い → W1-D アロケーションエンジンを壊す
- **Fix**: `z.preprocess((v) => (v === '' ? undefined : v), z.coerce.number().min(-90).max(90).optional())`

## Major Findings

- **M-1**: 検索の client-side フィルタ + `limit=500` の暗黙の上限 (患者 > 500 でサイレント切捨)
- **M-2**: RHF validation を擦り抜ける `required_staff_count` の PATCH 事故 (`patientUpdateSchema.partial()` と default の相互作用)
- **M-3**: edit 画面で `patientCreateSchema` を resolver に使用 (`as never` 型 hack)
- **M-4**: delete dialog の Esc / focus trap 無し (W1-F の Dialog primitive 利用すべき)
- **M-5**: `_retried` recursion の `headers` immutability 仕様化が必要

## Minor Findings

m-1 (`as never` 型 hack), m-2 (UUID 8桁表示), m-3 (`alert()` 多用 — Toast 利用可), m-4 (created_at ロケール表示なし), m-5 (weekly_pattern textarea の JSON unsafe), m-6 (`patientReadSchema` の datetime validation), m-7 (table に caption / aria-label なし)

## What's Missing

- 削除済み患者の復元 UI / API 不整合
- 重複コード 409 エラーハンドリング
- 同時編集 (optimistic concurrency) — `updated_at` での If-Match なし
- URL `?q=` シェアリング/復元なし
- `primary_office_id` UUID 手入力 (W1-C の Combobox 利用すべき)
- frontend テスト 0 件
- 削除確認の入力テキスト二段階確認なし

## Verdict Justification

3 件の CRITICAL があるため REVISE。Critical #1, #2 は schema/model 整合の根幹で、放置すると W1-D アロケーションエンジンに潜在バグ伝播。#3 は地理データ汚染。修正後 ACCEPT-WITH-RESERVATIONS。

## Open Questions

- `patientUpdateSchema.partial()` で `default(1)` が効くか実機検証推奨 (M-2)
- `lat/lng` の `union` 動作を `parse('')` で確認 (Critical #3)
- 削除済み除外フラグの仕様意図 (バグ vs intentional)
- `weekly_pattern` を JSONB のままか / Boolean / 構造化型に統一するかの設計確認
