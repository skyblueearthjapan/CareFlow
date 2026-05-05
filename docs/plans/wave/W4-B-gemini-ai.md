# W4-B. Gemini 自然言語入力 → 構造化 JSON

**実装 commit**: `8f1ffa7` (2026-05-05) + hotfix `39a45c1`
**ドメイン**: D4 (Integrations) + D3 (AI モーダル) / Phase 3

## 概要

design 09 の Global AI Input を完成させる。AiFab.tsx 内の
`// TODO: D4 で Gemini 入力モーダルを実装` を解消し、4 つの context_type
(`patient_create` / `event_create` / `override_create` / `general`) で
自然言語を構造化登録形式に変換する。Gemini 出力は失敗ケース含めて全て
ai_interpret_logs に永続化し、admin/manager から監査できる状態にする。

## 実装範囲

- **Backend**:
  - `services/gemini_client.py`: SDK 遅延 import (未インストール環境でも
    import 失敗しない) + `GeminiClient.interpret()` + `build_prompt()` +
    `estimate_cost_usd()` + JSON 抽出ヘルパ (Markdown フェンス strip 救済)
  - `api/v1/ai.py`: `POST /api/v1/ai/interpret` (admin/manager/staff) +
    `GET /api/v1/ai/logs` (admin/manager)
  - `schemas/ai.py`: InterpretRequest/Response + AiLogRead
  - 失敗含む全結果を `ai_interpret_logs._meta` JSONB に永続化
    (alembic migration 不要 — 既存 W2-B の表を流用)
  - `requirements.txt`: `google-generativeai>=0.7,<1.0`
- **プロンプト戦略**:
  - `temperature=0.2`, `response_mime_type=application/json`
  - 当日日付 / ISO 週 / staff_list / patient_list を context に自動注入
  - general: design 09 の 6 アクション (override / event / cancel /
    postpone / add / unknown) から最適選択
  - コスト計算: 静的単価表 + token 数 (Gemini API はコスト返さない)
- **Frontend**: AiFab + AiInputDialog で context_type を切替、結果を
  ConfirmDialog で確認 → 各 form に pre-fill

## 関連 commit

- `8f1ffa7` feat(W4-B): 本体
- `39a45c1` fix(W4-B): Gemini モデル名を `gemini-2.0-flash` に更新 +
  404 → 503 error mapping 細分化 (1.5-flash が v1beta から retired)
- (本タスク内で更に `gemini-2.5-flash` への移行を W5-F として追記)

## テスト被覆

- `test_ai_interpret.py`: 10 ケース (mocked Gemini)
  - 各 context_type の happy path (general / event_create /
    override_create / patient_create)
  - エラー: invalid JSON 422、rate limit 429、unavailable 503、
    model_not_found 503、generic 502
  - RBAC: anonymous 401、staff 403 (logs)、404 → 503 unit
- 本番では Phase H 後 admin で実 API 1 回叩いて疎通確認

## 残課題 / 次 Wave 移譲

- `google-generativeai` SDK は deprecated (Google 公式は `google-genai` を
  推奨)。移行は別 sprint で検討 (TODO comment を追記済)
- gemini-2.0-flash が新規ユーザで 404 する事例があるため
  default を gemini-2.5-flash に変更 (W5-F の本作業で対応)
- AI 結果の confidence < 0.7 時の UI 警告強化は Wave 5 後検討
