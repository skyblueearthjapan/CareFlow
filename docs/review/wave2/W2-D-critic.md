# W2-D ダッシュボード KPI — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, ADVERSARIAL)
**Commit**: `aecd811 feat(W2-D): dashboard KPI cards + 7-day trend chart (Recharts) + dashboard API`
**Date**: 2026-05-05

## VERDICT: REVISE

## Critical Findings

### 1. Timezone bug: 「今日」が UTC で計算される (JST データに対して)
- `dashboard.py:103, 172` で `datetime.utcnow().date()`
- VPS は UTC+8 だが JST (UTC+9) 時刻 00:00-08:59 で「昨日」の KPI を返す
- 朝勤の最も使われる時間帯で確実に間違う
- **Fix**:
  ```python
  from zoneinfo import ZoneInfo
  JST = ZoneInfo("Asia/Tokyo")
  today = datetime.now(JST).date()
  ```
  (line 103 と 172 両方)
- 単体テストで `2026-05-05T01:00:00+09:00` が `today=2026-05-05` を返すこと検証

## Major Findings

### 2. Staff role scope が secondary_staff_id, mentor_staff_id を無視
- `dashboard.py:57` `Visit.primary_staff_id == staff_id` のみ
- 同行スタッフの訪問が KPI 0 件扱い
- **Fix**: `or_(Visit.primary_staff_id == staff_id, Visit.secondary_staff_id == staff_id, Visit.mentor_staff_id == staff_id)`

### 3. Zero tests for two new aggregation endpoints
- `tests/test_dashboard*.py` 不在
- KPI 数学・boundary・TZ 全て無テスト
- **Fix**: `tests/test_dashboard.py` 追加 (admin/staff scope、overlap、completion_rate=0、trend backfill)

### 4. `today_overlapping` が cancelled visit を含む
- `dashboard.py:107-131` で `status != 'cancelled'` フィルタ無し
- **Fix**: overlap 計算に `Visit.status != 'cancelled'` を追加

### 5. `status` field に Literal/CHECK 制約無し
- `models/visit.py:69` `String(16)` のみ、enum 不在
- "completed" hard-code drift リスク
- **Fix**: `VisitStatus = Literal["planned", "in_progress", "completed", "cancelled"]` を共通 enum に

## Minor Findings

- `datetime.utcnow()` Python 3.12 deprecated、`from_attributes=True` 不要、refetchInterval 60s + staleTime 30s で extra refetch、completion_rate の二重計算、formatLabel `5/05` (1-padded)

## What's Missing

- テスト 0 件、rate limit、accessibility (Recharts SVG), staff-scope 文書化

## Verdict Justification
ADVERSARIAL escalate。CRITICAL 1 + MAJOR 4。timezone fix + 最小 tests で ACCEPT 格上げ。
