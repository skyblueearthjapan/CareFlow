# D1 Backend クロスレビュー B（受入・検証観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 7

---

## 受入基準の評価（§8 全10項目）

| # | 受入基準 | 評価 | 根拠 |
|---|---------|------|------|
| 1 | docker compose up で /healthz /readyz 200 | ○ | /readyz の DB/外部API確認範囲が未定義。「外部API含む」と書いているが Gemini/Maps も含むのか不明 |
| 2 | alembic upgrade head で12テーブル再現 | ○ | テーブル数は確認可能。ただし partial index（deleted_at IS NULL）の自動生成が autogenerate 対象外になる可能性があり明記なし |
| 3 | openapi.json が全エンドポイント網羅 + 型生成可 | △ | D4 の `/api/integration/correction-sheets/{id}/items`（GET）と `/correction-sheets/{id}/items/bulk`（POST）が D1 §5 endpoint 一覧に未掲載。D4 が「Backend に実装」前提か「D4 独自サービス」かが曖昧 |
| 4 | NextAuth から /auth/login 経由で JWT 取得・サインイン成功 | △ | JWT の5キー（exp/iat/sub/role/staff_id）がテスト仕様に含まれていない |
| 5 | admin/staff RBAC が 403/401 を返却 | ○ | T16 に対応タスクあり。ただし「staff が他 staff のデータを参照できないか」のスコープ制限が未記述 |
| 6 | 設計書 7-13 等に列挙された全 API が実装済み | ✗ | 参照先「設計書 7-13・8-10」のセクション番号が D1 計画書内に存在せず一意特定不能 |
| 7 | pytest 全グリーン・カバレッジ 80%・CI green | △ | 80% の測定対象（ブランチ/行）が未統一、T33 の「50ケース通過」とカバレッジ 80% の関係が不明確 |
| 8 | エラーレスポンス統一フォーマット | ○ | `{code, message, details?}` は明確。ただし D4 KaipokeBusyError（409）が同フォーマットに乗るかが未確認 |
| 9 | 構造化ログに request_id・user_id・latency_ms 含む | ○ | D5 §9 では latency_ms が欠落。計画書間で出力フィールドが不一致 |
| 10 | /openapi.json が CI artifact として D2 へ受け渡し | △ | 初期配布タイミングと仕様凍結基準が未定義 |

---

## 見落とされた検証項目

1. **staff スコープ制限の欠如** — `GET /visits?week=...` を staff で呼んだとき他スタッフの訪問が見えてはいけない制御が受入基準にない
2. **論理削除と Visit 参照の一貫性テスト** — 削除済み患者の過去 Visit の表示動作未定義
3. **レート制限の境界値テスト** — ウィンドウリセット後の再試行可否未テスト
4. **JWT refresh フロー** — `/auth/refresh` の正常系・失敗系（期限切れ refresh token）が E2E に未含
5. **Alembic ダウングレードテスト** — `downgrade -1` が機能するかのテストなし
6. **監査ログの完全性検証** — before/after jsonb の内容アサーションがない、PII 閲覧ログ要否が曖昧

---

## 検証可能性が曖昧なタスク

- **T02「`/openapi.json` 取得可」** → `assert len(schema['paths']) >= 30` のように具体化
- **T06〜T13「FK/Index 整合」** → `information_schema.table_constraints` で FK・Index 数アサート
- **T29「アクセスログに request_id・user_id 出力」** → `caplog`/`structlog.testing` で JSON parse + キー存在アサート
- **T31「Swagger UI が画面別に閲覧しやすい」** → 主観的。「全 endpoint に description と example」の客観条件に
- **T35「冪等」** → `python -m app.cli.seed` 2回実行で IntegrityError 不発生をアサート

---

## テスト戦略の改善提案

1. **カバレッジ測定対象の明確化** — D1 を正とし `--cov-fail-under=80`、D5 の「全体 60%」記述を 80% に統一
2. **factory_boy の共有戦略** — `tests/shared/factories.py` で D1/D4 共通化を明記
3. **N+1 検出の脆弱性** — `echo=True` から `pytest-sql-recorder` の `count_queries()` に変更
4. **E2E の業務本質欠落** — 「weekly_pattern 登録 → POST /allocate → /visits/unassigned が 0 件」を T34 に追加

---

## 他ドメイン整合性チェック

**D1 vs D4（最重要）**
D4 §3 で `KaipokeJob / KaipokeJobItem / GeocodingCache / AiInterpretLog` 等を追加するが、D1 §6 では `kaipoke_sync` のみ定義。所管が未定義。D1 受入基準「12テーブル」が合わなくなる。

**D1 vs D2（API 契約タイミング）**
T32（openapi.json 配布）の完了が D2 Phase 4 の前提条件として明示されていない。

**D1 vs D5（ログフィールド不一致）**
D1 §8-9 に「latency_ms」あるが、D5 §9 構造化ログ仕様に未掲載。

**D1 vs D3（スコープ境界）**
D3 §3 で `/api/kaipoke/status` 利用、D1 §5 では「D4 集約」と注記。実装責務が不明確。

---

## リリースゲート提案

- **Gate 1**: スキーマ凍結（pytest schema test + alembic downgrade/upgrade ループ pass）
- **Gate 2**: セキュリティ（JWT改ざん/期限切れ/役割昇格/staff スコープ全 pass）
- **Gate 3**: カバレッジ（行 80% 統一、core/services 90%）
- **Gate 4**: API 契約（openapi-typescript で型生成 exit 0、undefined/never 不在、endpoint ≥ 30）
- **Gate 5**: 運用健全性（/readyz が DB/Gemini/Maps を個別チェック、監査ログ before/after 検証、docker stats 80% 以下）

---

## 総評

D1 は API 設計・データモデル・テスト方針の骨格は堅固だが、**5つのブロッカー**（受入基準6の参照崩れ・D4 との4テーブル所管未定義・staff スコープ未テスト・論理削除Visit 整合性未テスト・計画書間ログフィールド不一致）が未解決。**REQUEST_CHANGES** — ブロッカー解消と Gate 1〜5 全クリアで prod 投入可。
