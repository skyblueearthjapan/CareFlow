# D1 Backend クロスレビュー A（技術観点）

## 1. 総評

**条件付き合格。** 計画書の構成・網羅性・粒度は水準が高く、アーキテクチャ選定も妥当。ただし D4 との責務境界に Geocode パス不一致と DB スキーマ重複の問題があり、設計仕様が要求する一部 API の欠落、および RBAC ロール定義の不整合が存在する。これらは着手前に解消すべき。工数 19.5人日は実質テーブル数の多さとテスト工数を考慮すると妥当。

## 2. 強み

1. **レイヤ構成が明快**: `api/v1 → services → repositories → models` の4層が明示
2. **API endpoint 一覧が設計仕様のデータソースセクションとほぼ完全対応**
3. **リスク認識の質が高い**: jsonb 採用判断、JWT 5キー固定、D4スタブ戦略

## 3. 重大な指摘

### [CRITICAL-1] Geocode エンドポイントのパス不一致 — D1 vs D4 vs 設計仕様
- D1: `POST /api/v1/geocode`
- D4: `GET /api/geocoding/forward?address=`
- 設計仕様: `POST /api/geocode`
- D3: `POST /api/geocode`
- → メソッドとパスの両方が異なる。Frontend が叩く先が確定不能
- **Fix**: 責務確定（D1 T23 か D4 Phase F）+ パス・メソッド・認可レベル統一

### [CRITICAL-2] D4 が追加する DB テーブル（GeocodingCache, AiInterpretLog, KaipokeJob, KaipokeJobItem）が D1 のデータモデル定義に存在しない
- D1 は `kaipoke_sync` のみ。D4 が想定する KaipokeJob / KaipokeJobItem とは名称・スキーマが異なる
- → Alembic マイグレーション管理主体が不明確、受入基準「12テーブル」も実態と合わなくなる
- **Fix**: D1 §6 に D4 向け予約定義 or multi-head 戦略を明示

### [MAJOR-1] RBAC ロール定義の不整合
- users: `admin/staff/manager`、staff: `staff/manager`、T16: `admin/staff` 2値、D5: `admin/staff/viewer`
- → manager は admin相当か staff相当か未定義、viewer の存在も計画書間で不一致
- **Fix**: 3値で確定（admin/manager/staff）、認可レベルを明示する表を追加

### [MAJOR-2] 設計仕様の API パスに `/api/v1` prefix がない — Frontend契約が曖昧
- D1: `/api/v1/...`、設計仕様 + D3: `/api/...`（v1 なし）
- **Fix**: nginx リライト or OpenAPI servers.url で吸収、明文化

### [MAJOR-3] D4 の差分プレビュー API 4本が D1 endpoint 一覧に欠落
- `GET /api/integration/correction-sheets/:id/items`、`POST /correction-sheets/:id/items/bulk`、`GET /api/integration/job-items/:id/screenshot` 等
- **Fix**: D1 §5 に「D4 forward 追加」明記 or T25 の完了条件拡張

### [MAJOR-4] `patient_allowed_offices` / `staff_secondary_offices` のテーブルに対応する API endpoint が存在しない
- マスタ詳細の「許容拠点 [+ 追加]」「兼務拠点 [+ 追加]」操作の API が未定義
- **Fix**: 患者/スタッフ PATCH の body schema に多対多 ID 配列を含める

## 4. 見落としリスク

- **Remember me セッション期間差分**（30日/24時間）: JWT exp の値分岐が D1 に未記載
- **pagination 未定義**: 全一覧 API に limit/offset/cursor 仕様がない
- **WebSocket / SSE 言及なし**: 設計仕様 6-13 にあるが D1 はスコープ外と明記すべき
- **`/api/v1/me` と `/api/v1/auth/me` の重複**

## 5. 改善提案

1. **ロール・認可マトリックス追加**: anonymous/staff/manager/admin × endpoint群
2. **D4 向けテーブルの予約定義をセクション6に追加**: kaipoke_jobs、kaipoke_job_items、geocoding_cache、ai_interpret_logs（空定義）
3. **API パス解決戦略の明文化**: Backend は `/api/v1/`、Frontend は OpenAPI 経由で baseURL 注入

## 6. 依存ドメイン整合性チェック

| 対象 | 状態 | 詳細 |
|------|------|------|
| D2 | 要調整 | API prefix 不整合、refresh 仕様の合意 |
| D3 | 要調整 | データソース表全て v1 なし、認証フロー区別必要 |
| D4 | **要調整（最重要）** | Geocode パス、DB テーブル管理、差分プレビュー API、ORM未確定 |
| D5 | 軽微 | ヘルスチェックパス（/api/health vs /healthz）、viewer ロール |

## 7. 再レビュー推奨ポイント

1. Geocode 責務確定後 — D1 T23 と D4 Phase F のどちらか
2. RBAC ロール確定後 — manager の権限範囲
3. D4 の DB テーブル追加方式決定後 — Alembic multi-head 戦略
4. API prefix 統一後 — 設計仕様データソース全体 grep
5. pagination/filtering 仕様追加後
