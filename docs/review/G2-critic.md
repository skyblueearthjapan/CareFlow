# CareLink Backend D1 Skeleton レビュー (`59b542e`)

**Reviewer**: oh-my-claudecode:critic (Opus)
**Branch**: `develop`
**Date**: 2026-05-05

## VERDICT: REVISE

## 概要

D1 計画書 Phase 1 の骨格 (FastAPI + SQLAlchemy 2.0 async + Alembic + JWT + bcrypt + 5回ロック) は **構造としては高品質に実装されている**。ファイル分割、命名規約、TimestampMixin、async session lifecycle、lockout の TOCTOU 回避、enumeration 対策の generic 401、`type=refresh` の混入防止 (`deps.py:60`)、`ondelete` 戦略の差別化 (Visit→Patient は RESTRICT、Visit→Staff は SET NULL) など、随所にシニア級の判断が出ている。

ただし、**マイグレーションが SQLite では実行不能**、**Alembic 同期ドライバが requirements に欠落**、**Patient↔Office の M:N 命名が D1 設計書と乖離**、**MASTER-PLAN P-06 で D1 所管に移管された 4 テーブル (KaipokeJob/JobItem/GeocodingCache/AiInterpretLog) が完全欠落**、という結合フェーズで露見する地雷が複数ある。Phase 1 受入基準 (`alembic upgrade head` でクリーン DB に適用) を本番 PG では満たすが、テスト用 SQLite では `0001_initial.py` を適用できない構造が `conftest.py` の `Base.metadata.create_all` で隠蔽されている点が最大の懸念。

審査モード: 1 件の CRITICAL を発見した時点で **ADVERSARIAL モード** に切替済 (Realist Check 後、最終的には MAJOR×4・MINOR 多数で着地)。

## Pre-commitment Predictions vs 実際

| 予測 | 実際 |
|---|---|
| Async/sync 不整合 (Alembic) | **的中** — `psycopg` が requirements 未記載 (env.py:33) |
| Pydantic settings の CORS list parsing | 的中せず — string + property で逃げており健全 |
| Lockout race / counter リセット忘れ | 的中せず — `auth.py:78-80` で適切にリセット |
| Migration drift | **的中** — Patient ↔ Office M:N の名称不一致 + DESC index の sqlalchemy ハック |
| JWT refresh rotation | 的中 — refresh ローテートなし・jti なし (Phase 1 範囲では受容可) |

予測外で発見した重要事項: **MASTER-PLAN P-06 の D1 所管 4 テーブル欠落**、**`postgresql_where` の文法誤り**、**Pydantic v2 の `BaseSettings` で CORS を string にすべきか list にすべきか**。

---

## Major Findings

### M1. Alembic 用同期ドライバ `psycopg` が requirements.txt に未記載

- **証拠**: `backend/alembic/env.py:33` で URL を `postgresql+psycopg://` に書き換えるが、`backend/requirements.txt` (1-23行目) と `backend/pyproject.toml` (10-24行目) のいずれにも `psycopg`・`psycopg2` が無い。Dockerfile (13-14行目) は `libpq-dev` を builder 段階のみインストールし runtime には残らない。
- **Confidence**: HIGH
- **影響**: `make migrate` (Makefile:31) と `alembic upgrade head` がコンテナ内で `ModuleNotFoundError: psycopg` で失敗。**D1 受入基準「`docker compose up` で `/healthz` `/readyz` が 200」**は通っても、初期マイグレーションが実行できないので実質的に DB が空のままサービス起動する。
- **Realist Check**: 即時検出される (起動直後の migrate ステップで落ちる) ため "silent corruption" にはならず、また rollback も `pip install psycopg[binary]` を 1 行追加するだけ。CRITICAL から MAJOR にダウングレード。
- **Fix**: `requirements.txt` と `pyproject.toml` に `psycopg[binary]>=3.1,<4.0` を追加。または env.py の置換を `postgresql+psycopg2://` に変えて `psycopg2-binary` を追加。

### M2. `postgresql_where=func.coalesce(deleted_at, None).is_(None)` が無効な partial index 表現

- **証拠**: `backend/app/models/office.py:33-40` で `Index("ix_offices_active", "id", postgresql_where=func.coalesce(deleted_at, None).is_(None))`。SQL では `COALESCE(deleted_at, NULL)` を生成し常に元の `deleted_at` と等価。さらに `D1-backend-plan.md:263` の方針は「`deleted_at IS NULL` の partial index」であり、本式は意味的に等価だが冗長で、`text("deleted_at IS NULL")` または `deleted_at.is_(None)` が正解。**他テーブル (patients/staff/visits) には同等の partial index が一切無い**ため、計画書の「論理削除カラムに partial index」方針が offices だけ部分実装されている状態。
- **Confidence**: HIGH (式の等価性) / HIGH (他テーブル未実装)
- **影響**: PG の partial index が論理的にはほぼ全行を含むため最適化が効かない可能性 + マイグレーションでは `0001_initial.py` にこの index が**そもそも書かれていない** (276-378行目で offices に `ix_offices_active` 作成なし) ため、ORM 定義とマイグレーションの drift。`alembic upgrade head` 後の DB 状態と `Base.metadata.create_all` (テスト) の状態が異なる。
- **Fix**: (a) `office.py` の Index 式を `text("deleted_at IS NULL")` に変更、(b) `0001_initial.py` に `op.create_index("ix_offices_active", "offices", ["id"], postgresql_where=sa.text("deleted_at IS NULL"))` を追加、(c) もしくは partial index を全テーブル (patients/staff/visits) に拡張するか offices からも撤去するか方針統一。

### M3. MASTER-PLAN P-06 で D1 所管に移管された 4 テーブルが完全欠落

- **証拠**: `docs/plans/MASTER-PLAN.md:82` `P-06 KaipokeJob/Item/GeocodingCache/AiInterpretLog を D1 所管に移管`。`backend/app/models/__init__.py:7-21` には該当クラス無し。
- **Confidence**: HIGH
- **影響**: D4 (Integrations) チームが先行作業に入った瞬間、この 4 テーブル不在で結合不能。コミットメッセージは「14 tables」を主張するが、実際に作られているのは 16 tables で、P-06 の 4 表は別途追加が必要。
- **Fix**: (a) コミットメッセージの「14 tables」を実態 (16 tables) に修正、(b) Phase 1 の責務範囲を PR description に明記し、P-06 の 4 表は Phase 2 マイグレーションで追加する旨を Open Issue 化、(c) `kaipoke_sync` 空テーブルだけは T12 完了基準として今コミットに含めるべきだった。

### M4. `0001_initial.py` が SQLite では適用不能 — テスト経路と本番経路の divergence

- **証拠**: `0001_initial.py` 全域で `postgresql.UUID(as_uuid=True)`・`postgresql.JSONB` を直接使用 (29, 30, 50, 137 行目など計 41 箇所)。SQLite には JSONB が存在せず UUID は TEXT 互換のみ。`tests/conftest.py:46-48` は migration を**通さず** `Base.metadata.create_all` を呼ぶことでこれを回避している。
- **Confidence**: HIGH
- **影響**: モデル定義 (Python class) とマイグレーション (handwritten) の整合性は **どこのテストでも検証されていない**。M2 で挙げた drift (offices の partial index) はその実例。
- **Fix**: (a) GitHub Actions に `services: postgres:16-alpine` を持つ migration smoke job を追加、または (b) 各 model 追加 PR で `alembic check` (1.13+ 機能) または autogenerate diff が空であることを検証するワークフローを追加。

---

## Minor Findings

- **m1**: `Office` 側に `relationship("PatientAllowedOffice", back_populates="patient")` の逆向きが無い。後続タスクで書く想定でも、相互参照の方針を README で明示すると review が早い。
- **m2**: `models/audit_log.py:30-37` の `Index("ix_audit_logs_recent", text("created_at DESC"))` を ORM に書きつつ、`0001_initial.py:324` で `if dialect == postgresql` ガード経由の生 SQL に置換。SQLAlchemy の expression-based index は autogenerate と問題を起こしやすい。
- **m3**: `core/deps.py:19` で `settings = get_settings()` をモジュールトップで呼び出している。テスト fixture の `get_settings.cache_clear()` より前に import されると古い設定を保持する。
- **m4**: `core/config.py:30` の `cors_origins: str` (string→property で list 化) は Pydantic v2 ネイティブの `list[str]` + `field_validator(mode="before")` で comma-split する方が標準。
- **m5**: `passlib 1.7.4 + bcrypt 4.x` の組合せは `AttributeError: __about__` 警告を出すことが既知。`bcrypt<4.1` または `bcrypt>=4,<5` を pin するか、 passlib 代替の `bcrypt` 直接使用への移行検討。
- **m6**: `models/user.py:42` で `staff: Mapped["Staff | None"] = relationship("Staff", lazy="selectin")` だが、Staff 側に逆向き relationship が無い。`back_populates` か `viewonly=True` 明記推奨。
- **m7**: `tests/conftest.py:31-35` の `event_loop` fixture は `pytest-asyncio>=0.23` で deprecated。`asyncio_mode=auto` なら不要かつ警告が出る。
- **m8**: `app/api/v1/auth.py:91` の `/refresh` は **refresh token ローテーション無し**。Phase 1 受容範囲だが PR description で「Phase 2 で jti + Redis blacklist 化」を明記推奨。
- **m9**: `app/api/v1/auth.py:81` の `await db.refresh(user)` は不要。
- **m10**: `Dockerfile:36` で `tests/` がコピーされていない。本番イメージで `make test` を走らせると `No tests collected`。

---

## D1 計画との差分

| D1 計画の要求 | 実装状況 |
|---|---|
| FastAPI + SQLAlchemy 2.0 async + Alembic + JWT | ✅ 完備 |
| `/healthz`/`/readyz` | ✅ |
| 12 テーブル (10 + users + audit_logs) | △ 16 テーブル (M:N 4 つ + StaffShift/Override/Event/Mentor 含む) |
| ロール 3 値 (admin/manager/staff) | ✅ |
| bcrypt + JWT HS256 + 5 回ロック | ✅ |
| Refresh token | ✅ ローテーションなし (Phase 1 受容) |
| Naming convention | ✅ |
| Partial index `WHERE deleted_at IS NULL` | ❌ offices だけ部分実装 + drift (M2) |
| RBAC decorator | ✅ |
| Rate limit (slowapi) | ❌ 未実装 (T17 予定) |
| 構造化ログ | ❌ structlog は requirements にあるが未配線 |
| 例外ハンドラ統一 | ❌ 未実装 (T28 予定) |
| 監査ログ自動記録 | ❌ AuditLog モデルのみ・サービス未配線 |
| seed スクリプト | ❌ 未実装 (T35 予定) |
| `/openapi.json` 配布 | ✅ `scripts/export_openapi.py` |
| KaipokeJob/Item/GeocodingCache/AiInterpretLog (P-06) | ❌ 完全欠落 (M3) |
| Pydantic v2 schemas 全エンティティ | ❌ auth のみ (T14 予定) |

**判定**: Phase 1 (skeleton) としてのスコープは概ね達成。Phase 2 で T14-T35 を着実に積めば計画通り。

---

## What's Missing (Gap Analysis)

1. **CI ワークフロー設定 (.github/workflows/*.yml) が一切存在しない**
2. **README または backend/README.md が無い**
3. **`security headers` (CSP, X-Frame-Options, HSTS) を設定する middleware が無い**
4. **Request ID middleware が無い**
5. **JWT secret の min-length 検証が無い** — `@field_validator("jwt_secret")` で `len >= 32` を拒否する 5 行を入れるべき。
6. **`tests/test_refresh.py` が存在しない**
7. **`tests/test_security.py` 相当が無い** — JWT 改ざん / 期限切れ / `type=access` を refresh に使った場合 等の negative test が網羅されていない。
8. **OpenAPI tag の `description`/`examples`** — Frontend 着手が楽になる。

---

## Verdict Justification

**REVISE**: 構造的品質は Phase 1 skeleton として高水準で、コア機能 (FastAPI + ORM + Alembic + JWT + bcrypt + lockout) は健全に動く。ただし以下を満たすまで Phase 2 着手は **保留すべき**:

1. **M1 (psycopg 欠落)** — 1 行追加で解消、ただしマージ前必須。
2. **M2 (offices の partial index drift)** — モデルかマイグレーションのどちらかに統一する 5 分の修正。
3. **M3 (P-06 の 4 テーブル + コミットメッセージの 14→16 表)** — Phase 2 で扱うことを明示するか今コミットに含めるか方針確定。
4. **M4 (migration が SQLite で実行不能 + CI 不在)** — PG コンテナで `alembic upgrade head` を回す GitHub Actions ジョブを 1 つ追加。

**APPROVE への昇格条件**: M1-M4 を解消した次回 push、または PR description に Phase 2 で扱う旨の明示 (M3) と CI ジョブ追加 PR (M4) のリンク添付。

---

## Open Questions (unscored)

1. `kaipoke_sync` 空テーブル (D1-plan T12) を本コミットに含めない判断は意図的か?
2. `staff_shifts` PK が `(staff_id, weekday)` だが、毎 staff 作成時に 7 行を seed する責務 (T35 seed か repository default か) が未定。
3. `core/deps.py:60` の `payload.get("type") not in {"access", None}` で `None` を許容している意図 — 古い token 形式との互換性? 明示的に `== "access"` にすべきでは。
4. `jwt_refresh_ttl_seconds=2592000` (30 日) は MASTER-PLAN CL-2 「remember me 30 日 / 24h」のうち 30 日側のみ。24h 側 (短命 access) のフロント連携は D2 担当だが、Backend 側の access TTL 1 時間 (config.py:41) との整合確認は済んでいるか。
5. テストが PG 由来の JSONB / UUID を SQLite フォールバックで動かしているため、JSONB クエリを将来導入したら CI で検出できない。Postgres ベースの integration test ジョブを併設する方針か。
