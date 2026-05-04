# D1: Backend Infra & Domain 実装計画書

## 1. 概要・目的

CareLink の Backend（FastAPI + SQLAlchemy 2.0 + asyncpg + PostgreSQL）を新規構築し、フロントエンド（Next.js）から参照される認証・マスタ・訪問・連携センター系 API を提供する。本ドメインは API 契約・DB スキーマ・認証基盤の確立により、他ドメイン（Frontend / Integrations / DevOps）の実装着手を可能にする土台レイヤーである。

## 2. アーキテクチャ図

```
┌──────────────────────────────────────────────────────────────────┐
│ carelink.kaipoke-api.net (Hostinger VPS / Caddy)                 │
│                                                                  │
│  ┌── Next.js (NextAuth) ─────────┐  ┌── FastAPI (本ドメイン) ──┐ │
│  │  /                            │  │  /api/v1/...             │ │
│  │  /api/auth/[...nextauth]      │──│   ├ auth/                │ │
│  │   credentials provider        │  │   ├ patients/, staff/    │ │
│  └────────────┬──────────────────┘  │   ├ visits/, allocate    │ │
│               │ Bearer JWT          │   ├ master, geocode      │ │
│               ▼                     │   ├ ai/interpret         │ │
│  ┌── PostgreSQL 16 (Docker) ───┐    │   └ integration/*        │ │
│  │  carelink DB / 10 tables   │◄───│  Pydantic v2 / SQLAlchemy│ │
│  │  + users / audit_logs      │    │  v2 (async) / asyncpg    │ │
│  └────────────────────────────┘    └────────┬─────────────────┘ │
│                                              │ HTTP               │
│  ┌── Integrations Worker (別ドメイン) ◄──────┘                    │
│  │  Playwright / Allocation engine                               │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
            ▲                          ▲
            │ Google Maps Geocoding    │ Gemini API
            └──────────────────────────┘
```

レイヤ構成（FastAPI 内）：

```
app/
├── main.py             FastAPI app, lifespan, middleware
├── core/               settings(pydantic-settings), security, deps
├── db/                 engine(async), session, base, alembic env
├── models/             SQLAlchemy 2.0 ORM (10 + Users + AuditLog)
├── schemas/            Pydantic v2 (request / response)
├── api/v1/
│   ├── auth.py         /auth/login, /auth/me, /auth/refresh
│   ├── patients.py     /patients CRUD + weekly_pattern + special_week
│   ├── staff.py        /staff CRUD + shift + weekly_overrides + events
│   ├── offices.py      /offices, /cities
│   ├── visits.py       /visits, /visits/today, /visits/summary
│   ├── allocate.py     /allocate (proxy to integrations)
│   ├── geocode.py      /geocode (Google Maps 中継)
│   ├── ai.py           /ai/interpret (Gemini 中継)
│   ├── dashboard.py    /alerts, /activity, /me
│   └── integration.py  /integration/* (proxy to integrations)
├── services/           ビジネスロジック（DB操作の薄ラッパ）
├── repositories/       SQLAlchemy クエリ集約
└── utils/              audit_log, error_codes, time_utils
```

## 3. 依存関係

### 他ドメインへの依存（受け取る）
- **DevOps（D5）**: VPS 上 Docker Compose / Caddy リバースプロキシ / 環境変数管理 / TLS 証明書
- **Integrations（D4）**: `POST /allocate`, `POST /integration/expand|export|diff|apply`, `GET /jobs/{id}` の内部 HTTP API（Backend から forward する）

### 他ドメインへ提供（IF）
- **Frontend（D2）**: OpenAPI スキーマ（`/openapi.json`）→ openapi-typescript で型生成
- **Integrations（D4）**: `correction_sheets`/`correction_sheet_items`/`patients`/`visits` テーブル参照権限、Backend からの内部 API 呼び出し
- **DevOps（D5）**: `Dockerfile`, `docker-compose.yml`, `alembic.ini`, ヘルスチェック `/healthz`、構造化ログ JSON 出力

### 提供する固定値
- JWT 署名アルゴリズム: HS256（共通シークレット）／必要なら RS256 への切替も初期から ENV 切替可
- API バージョン prefix: `/api/v1`
- 文字コード: UTF-8、日時: ISO8601 / TZ Asia/Tokyo

## 4. タスク分解

| ID | タスク | 想定 | 前提 | 完了条件 |
|----|--------|------|------|----------|
| T01 | リポジトリ初期化（pyproject、ruff、mypy、pytest、pre-commit） | 0.5d | - | `make lint test` が空 OK で通る |
| T02 | FastAPI スケルトン（main、settings、healthz、CORS） | 0.5d | T01 | `GET /healthz` 200 / `/openapi.json` 取得可 |
| T03 | docker-compose（postgres16, app, adminer） + .env.sample | 0.5d | T02 | `docker compose up` で DB と app が両方 healthy |
| T04 | SQLAlchemy 2.0 async engine + session 依存注入 | 0.5d | T03 | `Depends(get_session)` で 1件 SELECT が成功 |
| T05 | Alembic 初期化 + 命名規約 + autogenerate 設定 | 0.5d | T04 | `alembic upgrade head` でクリーン DB に空マイグレーション適用可 |
| T06 | ORM モデル：Office, City, OfficeCity（多対多） | 0.5d | T05 | マイグレ生成 + FK/Index 整合 |
| T07 | ORM モデル：Patient（住所・拠点・条件・通常週・特別週 FK） | 0.5d | T06 | 同上 |
| T08 | ORM モデル：Staff, MentorAssignment, StaffShift（固定） | 0.5d | T06 | 同上 |
| T09 | ORM モデル：StaffWeeklyOverride, StaffEvent | 0.25d | T08 | 同上 |
| T10 | ORM モデル：Visit（patient/staff FK・時刻・状態・補助 FK） | 0.5d | T07,T08 | 同上 |
| T11 | ORM モデル：CorrectionSheet, CorrectionSheetItem | 0.5d | T10 | 同上 |
| T12 | ORM モデル：KaipokeSync（後回し、空テーブル定義のみ） | 0.25d | T11 | 同上 |
| T13 | ORM モデル：User, AuditLog（追加2テーブル） | 0.5d | T05 | 同上 |
| T14 | Pydantic v2 schemas（全エンティティの Read/Create/Update） | 1.0d | T06-T13 | OpenAPI に正しく出る |
| T15 | 認証：bcrypt ハッシュ、JWT 発行・検証、依存注入 | 0.5d | T13 | `/auth/login` が JWT 返却、`/auth/me` で検証成功 |
| T16 | RBAC：admin / staff ロール decorator + テスト | 0.5d | T15 | admin only エンドポイントに staff token で 403 |
| T17 | レート制限（slowapi）：ログイン 5/15min ロック | 0.25d | T15 | テストで 6回目 429 |
| T18 | 患者 CRUD + 通常週パターン + 特別週パターン API | 1.0d | T14 | OpenAPI 反映、e2e テスト通過 |
| T19 | スタッフ CRUD + 固定シフト + weekly-overrides + events | 1.0d | T14 | 同上 |
| T20 | Office / City / 多対多 CRUD | 0.5d | T14 | 同上 |
| T21 | Visit CRUD + 検索（week, staff_id, patient_id） | 1.0d | T14 | `GET /visits?week=2026-W18` が機能 |
| T22 | Visit 集計 API：summary, unassigned, today | 0.5d | T21 | dashboard / mobile 用クエリ通過 |
| T23 | Geocode 中継（Google Maps API key、結果キャッシュ） | 0.5d | T02 | 失敗時 502 + リトライなし |
| T24 | AI Interpret 中継（Gemini API、JSON Mode、stub あり） | 0.5d | T02 | 入力 → 構造化 JSON 返却 |
| T25 | Allocate / Integration エンドポイント（D4 への forward） | 1.0d | T11 | プロキシ経由で job_id 取得 |
| T26 | Correction sheet 関連（latest 取得 / item PATCH） | 0.5d | T11,T25 | UI 仕様通りに include / comment 更新 |
| T27 | Job 履歴・job-item PATCH（manually_handled） | 0.5d | T25 | ジョブ履歴 UI が必要なデータ取得可能 |
| T28 | 例外ハンドラ統一（ValidationError / IntegrityError / 4xx/5xx 整形） | 0.5d | T02 | エラーレスポンス JSON 仕様統一 |
| T29 | 構造化ログ（structlog/JSON）＋ request_id ミドルウェア | 0.25d | T28 | アクセスログに request_id・user_id 出力 |
| T30 | 監査ログ（AuditLog 自動記録：書込系のみ） | 0.5d | T13,T28 | PATCH/DELETE で 1行記録 |
| T31 | OpenAPI タグ整理 + descriptions + examples | 0.25d | T18-T27 | Swagger UI が画面別に閲覧しやすい |
| T32 | OpenAPI 型配布スクリプト（fastapi → openapi.json artifact） | 0.25d | T31 | CI で `openapi.json` を生成・コミット |
| T33 | pytest フィクスチャ（DB ロールバック / factory_boy） | 0.5d | T04 | `pytest -k crud` で 50ケース通過 |
| T34 | E2E テスト（httpx AsyncClient で主要シナリオ） | 0.75d | T15-T22 | 認証→マスタ作成→訪問作成→検索 |
| T35 | seed スクリプト（拠点2、患者30、スタッフ12、users seed） | 0.25d | T20 | `python -m app.cli.seed` が冪等 |

合計：約 **15.0 人日**（バッファ前）

## 5. API endpoint 一覧

### Auth
| メソッド | パス | 認可 | 概要 |
|----|----|----|----|
| POST | /api/v1/auth/login | public | email+password 検証 → JWT |
| GET | /api/v1/auth/me | bearer | 自ユーザー情報 |
| POST | /api/v1/auth/refresh | bearer | JWT リフレッシュ |
| POST | /api/v1/auth/logout | bearer | クライアント側破棄補助（任意） |

### Master（admin only）
| メソッド | パス | 概要 |
|----|----|----|
| GET/POST | /api/v1/patients | 一覧 / 新規 |
| GET/PATCH/DELETE | /api/v1/patients/{id} | 詳細 / 更新 / 論理削除 |
| GET/PUT | /api/v1/patients/{id}/weekly-pattern | 通常週パターン |
| GET/PUT | /api/v1/patients/{id}/special-week | 特別週パターン |
| GET/POST | /api/v1/staff | 一覧 / 新規 |
| GET/PATCH/DELETE | /api/v1/staff/{id} | 詳細 / 更新 / 論理削除 |
| GET/PUT | /api/v1/staff/{id}/shift | 固定シフト |
| GET/POST/PUT | /api/v1/staff/{id}/weekly-overrides | その週だけ休み等 |
| GET/POST/PATCH | /api/v1/staff-events | スタッフイベント |
| GET/POST/PATCH/DELETE | /api/v1/offices, /offices/{id} | 拠点 |
| GET/POST/PATCH/DELETE | /api/v1/cities, /cities/{id} | 市区町村 |
| POST | /api/v1/geocode | 住所→緯度経度 (admin) |

### Visits / Schedule（admin + 自分のみ staff）
| メソッド | パス | 概要 |
|----|----|----|
| GET | /api/v1/visits?week=2026-W18 | 週ビュー本体 |
| GET | /api/v1/visits/today?staff_id=me | 今日の訪問 |
| GET | /api/v1/visits/summary?week=current | サマリ件数 |
| GET | /api/v1/visits/unassigned?week=... | 未割当 |
| POST | /api/v1/visits | 訪問追加 |
| PATCH/DELETE | /api/v1/visits/{id} | 編集 / キャンセル |
| POST | /api/v1/allocate?week=... | 自動割当起動（D4 forward） |

### Dashboard / Me
| メソッド | パス | 概要 |
|----|----|----|
| GET | /api/v1/me | 自分（staff/admin） |
| GET | /api/v1/staff/{me}/shift | 自分のシフト |
| GET | /api/v1/alerts | 警告一覧 |
| GET | /api/v1/activity?limit=5 | 最近のアクティビティ |
| GET | /api/v1/kaipoke/status | カイポケ同期状態（D4 集約） |

### AI / Geocode 中継
| メソッド | パス | 概要 |
|----|----|----|
| POST | /api/v1/ai/interpret | Gemini 中継（音声/テキスト→構造化） |
| POST | /api/v1/staff-weekly-overrides | AI 経由：休み登録 |
| POST | /api/v1/staff-events | AI 経由：イベント |
| POST | /api/v1/visits | AI 経由：訪問追加（既出と統合） |

### Integration（admin only、多くは D4 への forward）
| メソッド | パス | 概要 |
|----|----|----|
| GET | /api/v1/integration/status | カイポケ稼働状況 |
| POST | /api/v1/integration/expand | 月間展開 |
| POST | /api/v1/integration/export | CSV 出力 |
| POST | /api/v1/integration/diff | 差分検出 |
| POST | /api/v1/integration/apply | 差分適用（非同期 / job_id 返却） |
| GET | /api/v1/integration/jobs/{id} | 進捗ポーリング |
| POST | /api/v1/integration/jobs/{id}/stop | 中断 |
| GET | /api/v1/integration/jobs?limit=20 | 履歴 |
| GET | /api/v1/integration/vnc-url | VNC URL（短命 token） |
| GET | /api/v1/integration/correction-sheets/latest | 最新 sheet |
| PATCH | /api/v1/integration/correction-items/{id} | include/comment |
| PATCH | /api/v1/integration/job-items/{id} | manually_handled |

### Health
| メソッド | パス | 認可 | 概要 |
|----|----|----|----|
| GET | /healthz | public | liveness |
| GET | /readyz | public | DB/外部 API 含めた readiness |

## 6. データモデル定義

### users（追加）
- id PK uuid / email unique / password_hash / role enum('admin','staff','manager') / staff_id FK staff(id) NULL / failed_login_count / locked_until / created_at / updated_at
- index: (email)

### audit_logs（追加）
- id PK / actor_user_id FK / action enum / target_table / target_id / before jsonb / after jsonb / created_at
- index: (actor_user_id, created_at), (target_table, target_id)

### offices
- id PK / name / address / lat / lng / note / deleted_at
- index: (name)

### cities
- id PK / prefecture / name / jis_code unique nullable / deleted_at

### office_cities（多対多）
- (office_id, city_id) PK / created_at

### patients
- id PK / code unique（"P001" 等） / name / kana / sex enum / age int / status enum('active','suspended','admitted','pending') / insurance enum('medical','care')
- address / lat / lng / primary_office_id FK offices / required_staff_count smallint / sex_restriction enum / ng_time_start / ng_time_end
- weekly_pattern jsonb（曜日→[{start,end,type}]） / special_week jsonb（適用週リスト + パターン）
- note / deleted_at / created_at / updated_at
- index: (status, primary_office_id), (kana)

### patient_allowed_offices（多対多）
- (patient_id, office_id) PK

### staff
- id PK / code / name / kana / sex / status enum / role enum('staff','manager') / primary_office_id FK / can_double_team bool / mentor_id FK staff(id) NULL / note / deleted_at
- index: (status, primary_office_id)

### staff_secondary_offices（多対多）
- (staff_id, office_id) PK

### staff_shifts
- staff_id PK FK / weekday smallint PK (0=月) / is_on bool / start_time / end_time
- 7行/staff の固定シフト

### staff_weekly_overrides
- id PK / staff_id FK / iso_year / iso_week / weekday smallint / override_type enum('off','custom_time') / start_time / end_time / reason / created_at
- unique: (staff_id, iso_year, iso_week, weekday)

### staff_events
- id PK / staff_id FK / event_type enum('meeting','training','other') / starts_at / ends_at / title / note / created_at

### mentor_assignments
- id PK / mentor_id FK staff / mentee_id FK staff / start_date / end_date NULL
- unique: (mentor_id, mentee_id, start_date)

### visits
- id PK / patient_id FK / primary_staff_id FK staff NULL / secondary_staff_id FK staff NULL（2名体制） / mentor_staff_id FK staff NULL（同行）
- visit_date date / start_time / end_time / type enum('medical','care','event','coupled','mentor','special') / status enum('planned','done','cancelled','postponed') / source enum('pattern','manual','ai','allocate')
- note / kaipoke_id text NULL / created_at / updated_at
- index: (visit_date), (patient_id, visit_date), (primary_staff_id, visit_date), (status)

### correction_sheets
- id PK / target_month text(YYYY-MM) / created_at / created_by_user_id FK users / status enum('pending','applying','done','partial','failed')

### correction_sheet_items
- id PK / sheet_id FK / patient_id FK / visit_id FK NULL / action enum('edit','add','delete','date_change','companion_change')
- before jsonb / after jsonb / include bool default true / comment text / created_at
- index: (sheet_id, action), (sheet_id, include)

### kaipoke_sync（後回し / 空定義）
- id PK / job_type / target_month / status / started_at / finished_at / payload jsonb / result jsonb

### 共通 Index 方針
- 論理削除カラム `deleted_at` の partial index `WHERE deleted_at IS NULL`
- 多対多テーブルは複合 PK 採用、片側からの SELECT を高速化するため逆向き index を別途貼る

## 7. テスト方針

- フレームワーク: **pytest + pytest-asyncio + httpx.AsyncClient**
- フィクスチャ:
  - `db_session`: テスト開始時にトランザクション開始 → 終了時 ROLLBACK（fastapi の DI を上書き）
  - `factory`: factory_boy / faker で Office, Patient, Staff, Visit を生成
  - `client_admin`, `client_staff`: 役割別に JWT を仕込んだ AsyncClient
- カバレッジ目標: **行カバレッジ 80% 以上**、core / services は 90% 以上、CRUD endpoint は 100%
- テスト階層:
  - **unit**: services / 認証ユーティリティ / バリデータ（pure）
  - **integration**: 各 endpoint × ロール（admin/staff/anon × 200/401/403/404/422）
  - **scenario(e2e)**: ログイン → 患者作成 → 訪問作成 → 週ビュー検索 → 集計
- ロード/性能: 本フェーズはスキップ。N+1 検出は SQLAlchemy `echo` + `pytest-mock` でクエリ件数アサート
- セキュリティ: JWT 改ざん / 期限切れ / 役割昇格 / レート制限 / SQL インジェクション風入力テスト
- CI: GitHub Actions（lint → mypy → pytest → openapi 検証 → docker build）

## 8. 受入基準（このドメインの完了定義）

- [ ] `docker compose up` で local 環境が単一コマンドで起動し、`/healthz` `/readyz` ともに 200
- [ ] `alembic upgrade head` で 12 テーブル（10 + users + audit_logs）が再現できる
- [ ] OpenAPI スキーマ `/openapi.json` が全エンドポイントを網羅し、`openapi-typescript` で型生成可能
- [ ] NextAuth Credentials Provider から `/auth/login` 経由で JWT を取得しサインイン成功
- [ ] admin / staff のロールで RBAC が機能（403 / 401 を返却）
- [ ] 設計書 7-13・8-10・5-* / 6-* / 9-* / 10-* に列挙された全 API が実装済（D4 forward 含む）
- [ ] pytest 全グリーン、カバレッジ 80% 以上、CI green
- [ ] エラーレスポンスが統一フォーマット `{code, message, details?}`
- [ ] 構造化 JSON ログに `request_id`・`user_id`・`latency_ms` が含まれる
- [ ] `/openapi.json` が CI artifact として Frontend ドメインへ受け渡される

## 9. リスク + 対策

1. **データモデル不整合（Visit と CorrectionSheetItem の関連付け）**
   - リスク: 連携センター差分仕様（編集/追加/削除/同行変更）の after/before の表現が ORM だけでは捉えきれず後で大改造になる
   - 対策: 早期に `before/after = jsonb` で安全側に固定、Pydantic で discriminated union を `action` キーで切替（schemas に union 型を集中管理）。仕様変更時は jsonb なら無停止
2. **NextAuth 連携での JWT 仕様ズレ**
   - リスク: Frontend が NextAuth の JWE を期待、Backend が HS256 JWT を返す等のミスマッチ
   - 対策: D2 と早期に「Backend 発行 JWT を NextAuth Credentials の `authorize` 戻り値に詰める」契約をモック化したテストで担保。`exp / iat / sub / role / staff_id` の5キーに固定し変更しない
3. **D4（Integrations）API 仕様の遅延と forward 先未定**
   - リスク: integration/* の 11 endpoint が D4 未実装で結合不能
   - 対策: D4 用に固定スタブサービス（`INTEGRATION_BASE_URL` 切替で localhost のフェイクに向ける）を T25 と同タイミングで提供。OpenAPI に `x-fake: true` を残し、本番接続切替を環境変数のみで完了
4. **Geocoding / Gemini API 料金とレート制限**
   - リスク: 開発中の連打による無料枠超過、本番ピーク時の 429
   - 対策: 結果キャッシュ（住所単位 30日 / patient.address 変更時のみ再取得）、開発環境はモック。429 は指数バックオフ最大1回のみ、UI には「自動取得失敗、後で再試行」を返す
5. **論理削除と過去訪問データ整合**
   - リスク: 患者・スタッフ削除時に過去 Visit の参照が壊れる、または見えなくなる
   - 対策: `deleted_at` のみ立て、FK は維持。一覧取得は default で `deleted_at IS NULL`、訪問・履歴は inner join せず `LEFT JOIN`。マスタ詳細では「削除済」表示

## 10. 想定全体工数

- 純実装: **15.0 人日**
- バッファ（学習・仕様調整・レビュー対応 30%）: **+4.5 人日**
- **合計: 19.5 人日 ≒ 約 4 週間（1人 / 専任換算）**

並列化が可能な箇所（T06-T13 のモデル定義、T18-T22 の各リソース endpoint）は 2人体制で 12 営業日（≒2.5週間）まで短縮可能。
