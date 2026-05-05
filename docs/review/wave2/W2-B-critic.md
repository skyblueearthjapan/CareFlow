# W2-B 連携センター + KaipokeJob — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL)
**Commit**: `e66de7a feat(W2-B): kaipoke jobs + geocoding cache + AI logs (P-06) + integrations API + UI`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### C1. IntegrationsPage がタブ内で同じ page.tsx を直接 import → 二重マウント・hooks 不整合
- `app/(app)/integrations/page.tsx:8-10` で `import KaipokePage from './kaipoke/page'` 等
- TabsContent は default で hidden で残るため 全タブの useQuery が常時走り、admin 以外で 403 量産
- **Fix**: 共通の表示コンポーネント (`KaipokeJobsList` 等) に切り出し、page.tsx と区別

### C2. `cancel_kaipoke_job` の状態遷移が race condition + non-atomic
- `integrations.py:148-178` で SELECT → in-memory 判定 → UPDATE
- ワーカーが `running → completed` する瞬間と衝突
- **Fix**: `with_for_update()` または条件付き UPDATE で原子化

## Major Findings

- **M1**: `address_hash` の生成箇所が docstring のみ、実装ヘルパー不在 (sha256 + 正規化規約未定)
- **M2**: `datetime.utcnow()` 使用 (Python 3.12 deprecated + tz-aware カラム)
- **M3**: Sidebar の「連携」リンクが全ロールに無条件表示
- **M4**: `KaipokeJobUpdate` schema が dead code (API 未参照)
- **M5**: `KaipokeJobCreate.week_start` zod が `min(1)` のみ (regex で YYYY-MM-DD)
- **M7**: list endpoint が裸配列で `total` なし、pagination 不能
- **M9**: `kaipoke_job_items.seq` に UNIQUE(job_id, seq) ではなく Index のみ
- **M10**: `useSearchParams` が Suspense 境界なし (Next 14/15 build 警告)

## Minor Findings

- M-min1: IntegrityError SQLSTATE check 推奨
- M-min4: `error_msg` 長さトリム (max 4KB)
- M-min5: read schemas は `extra="ignore"` の方が forward-compat

## What's Missing

- rate limit / 同一 week_start 重複ジョブ防止
- AuditLog 連携
- AI logs PII redaction
- backend tests

## Verdict Justification
ADVERSARIAL escalate。C1/C2 + M1, M3, M9 の前倒し対応で ACCEPT-WITH-RESERVATIONS 格上げ可能。
