# W1-D Allocation Engine + API — Critic Review

**Reviewer**: oh-my-claudecode:critic (Opus, THOROUGH)
**Commits**: `d1e7ba9 feat(backend): W1-D port allocation engine` + `3d169ce feat(backend): W1-D-continue allocation API + 71 tests port`
**Date**: 2026-05-05

## VERDICT: ACCEPT-WITH-RESERVATIONS

エンジン/utils/models 移植は PT1 と byte-for-byte 一致 (logger 置換のみ)、55 テスト全 collect 可能で behavior 保持。一方 API 配線が「W1-G で完全マッピング」と docstring で明記 + 多くの engine input が default 値のため、production rollout の前に強化が必要。

## Critical Findings

### 1. /allocate/run に rate-limit 無し → compute DoS 余地
- **Evidence**: `allocate.py:116-125` に `@limiter.limit(...)` 無し。`auth.py:50` と `diff.py:30` には適用されている。最重要計算 endpoint
- **Why this matters**: 多試行 + 2-opt + ejection_chain は O(staff × visits × iterations)
- **Fix**: `@limiter.limit("3/minute")` を追加

### 2. Engine 入力が lossy default で本番 allocation が誤る
- **Evidence**: `allocate.py:78-87` で全 Staff に lat/lng なし、`shift_start_min=540`/`shift_end_min=1080`、work_days=全7日、max_per_day=999、areas=空。docstring (line 8-12) で「W1-G で richer mapping」と defer 明記
- **Why this matters**: C-7 area filter / soft_cap / work_days / 距離スコア / 2-opt が**全て無効**
- **Fix**: (a) `mapping_phase: "minimal"` を `AllocateResponse.summary` に追加 で caller に通知、(b) 端末公開を W1-G feature flag で gate

### 3. コミットメッセージの "71 tests" が誤り (実数 55)
- **Evidence**: `pytest --collect-only` で 55 collected (PT1 も 55)
- **Why this matters**: 36 functions × parametrize 展開で誤計算。"Bug D" もコードに参照無し (silent unaddressed の可能性)
- **Fix**: コミットメッセージ訂正 or 不足 16 ケースを追加

## Major Findings

### 4. `_to_minutes` が `Time | None` 受けるが `Visit.start_time/end_time` は NOT NULL
- **Evidence**: `models/visit.py:65-66` で `nullable=False`、`allocate.py:47-50, 95-97` で None 防御
- **Why this matters**: NULL → 60 分 default visit でデータ corruption を mask
- **Fix**: 防御 None 削除 or model を nullable に

### 5. Payload/result サイズ上限なし → 大規模週で OOM
- **Evidence**: `AllocateRequest` に上限無し、全 patients/staff/visits をロード、結果全件返却
- **Why this matters**: 300×50×7日で 10MB+、エンジン pipeline は数秒級
- **Fix**: timeout (`asyncio.wait_for`)、visit 数上限 (5000)、ジョブ + ポーリングモデル化

### 6. CPU-bound エンジンが asyncio event loop で動作 (worker stall)
- **Evidence**: `allocate.py:170` で `engine.allocate(requests)` を `async def` 内で同期呼び
- **Why this matters**: 1 リクエスト中、同 worker の他リクエストが全停止
- **Fix**: `asyncio.get_running_loop().run_in_executor(None, engine.allocate, requests)`

### 7. テストファイルの docstring に旧 PT1 path
- **Evidence**: `tests/test_allocation_engine.py:8-10` `pytest lib/test_allocation_engine.py -v`
- **Fix**: `backend/tests/test_allocation_engine.py` に置換

## Minor Findings

- `engine.py:1` docstring で "GAS UnifiedCode.js" 言及 (PT1 由来、CareFlow には不要)
- `_build_inputs` で `Patient(pid="", name="")` sentinel — match しない時に空で進む
- `models.py` でインポート整理 (date/time 削除済) は良い改善
- `AllocateOptions` 空 + `extra="forbid"` — 将来拡張で frontend 同期必須

## What's Missing

- request_id correlation の structured log なし
- 成功率/trial 数/pipeline latency メトリクスなし
- idempotency なし (同一 week_start 二重起動で再計算)
- partial/streaming response なし
- AuditLog 連携なし
- Bug D の所在不明 (コード参照無し)

## Verdict Justification

エンジン port 自体は clean、Codex C-1/C-2/C-3/C-4/C-7/C-9/C-10 + Bug A/B 全保持。API 配線が docstring で「W1-G defer」と明示しているため REVISE ではなく ACCEPT-WITH-RESERVATIONS。Production rollout は #1 + #2 + #6 を解消してから。

## Open Questions

- Bug D の正体 (user's prompt にあるがコード未参照)
- W1-G で full mapping の JIRA 連携
- uvicorn 単一 worker か multi-worker かで #6 の深刻度変動
- production RPS が <1/min なら #1 は MAJOR ダウングレード余地
- Apache 2.0 LICENSE / NOTICE の CareFlow 側追加確認
