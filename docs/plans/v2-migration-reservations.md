# CareFlow v2 マイグレーション番号予約表

> **Status**: Wave 0-A 出力（v0.1）
> **対応設計仕様書**: `docs/plans/v2-allocation-redesign.md` v0.9
> **対応実装手順書**: `docs/plans/v2-implementation-plan.md` v0.2 §1, §2, §3
>
> 本表は v2 系 Wave 1〜2 で追加する Alembic マイグレーションの番号 / 順序 / 依存を一元管理する。
> **Wave 0-A の目的**: 並行ブランチ間で同一番号を取り合う事故を物理的に排除する。

---

## 0. 既存番号の確認（master ブランチ時点）

| 番号 | revision id | 作成 Wave | 内容 |
|---|---|---|---|
| 0001 | `0001_initial` | v1 / Phase 1 | 初期スキーマ |
| 0002 | `0002_add_office_prefecture_code` | v1 | 拠点に都道府県コード追加 |
| 0003 | `0003_kaipoke_jobs_geocoding_ai` | v1 / Phase 4 | カイポケジョブ・geocoding・AI ログ |
| 0004 | `0004_w3_master_extensions` | v1 / W3 | マスタ拡張（W3-A） |
| 0005 | `0005_special_weeks_unique` | v1 / W3 | special_weeks ユニーク制約 |
| 0006 | `0006_w4d_mobile_features` | v1 / W4-D | モバイル機能 |
| 0007 | `0007_audit_log_extension` | v1 / W4-F | 監査ログ拡張 |
| 0008 | `0008_merge_w4d_w4f` | v1 / W4 | head 分岐 merge revision（no-op） |

> **重要**: 実装手順書 v0.2 §1 0-A の例示 `0007_v2_master_cleanup.py` は番号の例として記載されたものであり、
> 実コードベースでは 0007/0008 が既に v1 系で消費済みである。本予約表ではこれを踏まえ、
> v2 系の最初の番号を **0009** から開始する。

---

## 1. v2 で予約する番号（0009〜0016）

各番号は Wave 1〜2 のチケット 1 つに 1:1 で紐付く。実装エージェントは
**自分のチケットの番号だけを使い、他番号には触らない**。

| 番号 | revision id（予約） | down_revision | 担当チケット | 所有エージェント | スコープ |
|---|---|---|---|---|---|
| **0009** | `0009_v2_patient_master_cleanup` | `0008_merge_w4d_w4f` | W1-BE1 | 患者マスタ整理 | 患者の不要 10 項目 drop / `weekly_pattern.staff_count` 追加 / `special_weekly_pattern` JSONB 追加 / `special_week_active` JSONB 追加 |
| **0010** | `0010_v2_staff_master_cleanup` | `0009_v2_patient_master_cleanup` | W1-BE2 | スタッフマスタ整理 | スタッフの不要 6 項目 drop（自宅住所/lat/lng・can_double_team・得意エリア・1日最大訪問数・スキル・割付ボリューム）/ status を 在籍 / 休職 / 退職 の 3 値に正規化 |
| **0011** | `0011_v2_office_seed_and_assigner` | `0010_v2_staff_master_cleanup` | W1-BE3 | 拠点 (Office) 整備 | 稲毛 / 都賀 シード DML / 患者→拠点 自動紐付けに必要なインデックス（`office_cities` 既存テーブルに対する補助 idx、必要な場合のみ）|
| **0012** | `0012_v2_courses_and_visit_extension` | `0011_v2_office_seed_and_assigner` | W2-BE4 | Course / Visit 拡張 | `courses` 新設（status enum, UNIQUE (year,week,weekday,code), `course_fixed_at`, `staff_assigned_at`）/ `visits.course_id` FK / `visits.required_staff_count` / `visits.visit_group_id` UUID / `visit_staff_assignments` 新設（visit × staff M2M） |
| **0013** | `0013_v2_pending_requests` | `0012_v2_courses_and_visit_extension` | W2-BE5 | pending_requests | `pending_requests` 新設（§4.4 のスキーマ）/ `request_type` enum / `request_status` enum / `request_scope` enum / `(status, created_at)` 等のインデックス / `ai_interpret_log_id` FK |
| **0014** | `0014_v2_ai_context_type_extend` | `0013_v2_pending_requests` | W2-BE6 | AI scope 拡張 | `ai_interpret_logs` の context_type 拡張に伴う補助変更（CHECK 制約や idx の追加。実装上は JSONB `_meta` で持つため migration は no-op の可能性あり。ただし枠は予約） |
| **0015** | `0015_v2_drop_legacy_w3_master_fields` | `0014_v2_ai_context_type_extend` | W6-MIG1 | 既存データ移行 | W1-BE1/BE2 で残した backup column を本番削除（expand-contract 第 2 段）。Wave 6 で実行 |
| **0016** | `0016_v2_special_weeks_route_410` | `0015_v2_drop_legacy_w3_master_fields` | W6-MIG2 | /special-weeks 廃止 | 既存 `special_weeks` / `special_week_items` テーブルの将来削除のための準備 migration（テーブルそのものは Wave 6 で別 deploy にて drop） |

### 1.1 直線リニアモデル

Wave 1 の 3 BE チケット（W1-BE1/BE2/BE3）は **論理上は並行実装可能** だが、
Alembic は **head 分岐を許容しない単一直線**（`0008_merge_w4d_w4f` 以降は線形）で運用する。
そのため番号は **0009 → 0010 → 0011** の順に直列依存させる。

これは並行作業の妨げにはならない：
- 各チケットエージェントは worktree で独立に実装する
- マージ時のみ番号順を守る（先に W1-BE1 マージ → W1-BE2 を rebase → ...）
- 衝突したら本表に従って `down_revision` を rebase で書き直す

### 1.2 Wave 2 の依存方針

W2-BE4/BE5/BE6 は Wave 1 完了後にマージする。Wave 1 内は並行可、Wave 2 内も並行可だが、
Alembic 上は線形を保つため番号は **0012 → 0013 → 0014** の順に直列依存させる。

---

## 2. 各番号の予約ファイル

各エージェントは **自分の番号のファイルだけ** を新規作成すること。
雛形なし（空ファイル）で予約する場合の最小内容：

```python
"""<Wave/Ticket> <短い説明>

Revision ID: <revision_id>
Revises: <down_revision>
Create Date: <YYYY-MM-DD>
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision: str = "<revision_id>"
down_revision: Union[str, Sequence[str], None] = "<down_revision>"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    raise NotImplementedError("Wave 0-A reserved slot — implement in <ticket>")


def downgrade() -> None:
    raise NotImplementedError("Wave 0-A reserved slot — implement in <ticket>")
```

> **方針**: Wave 0-A の現時点では **空ファイルを敢えて作らない**。各 Wave 1〜2 のチケットエージェントが
> 本表の番号 / down_revision を厳守して **新規追加** する。空ファイルを先行コミットしないのは、
> マージ衝突時のリベース手順を簡素にするため（空ファイルが残っていると `git merge` で
> 偽の競合が発生しうる）。

---

## 3. 衝突回避ルール

### 3.1 番号衝突した場合

1. `git fetch origin develop`
2. 自分のブランチで `git rebase origin/develop`
3. 本表を確認し、自分の予約番号が空いているか再確認
4. 必要なら `down_revision` を最新の head（typically Wave 0-A merge 後の状態）へ書き直す

### 3.2 同 Wave 内での並行マージ

- Wave 1 は 3 つの BE migration（0009/0010/0011）が並行に開発される
- マージ順序は **チケット ID の若い順**（W1-BE1 → W1-BE2 → W1-BE3）
- 後続のチケットは前のチケットがマージされたら `down_revision` を最新化して PR を更新

### 3.3 expand-contract 方式の徹底（実装手順書 §11 リスク表より）

破壊的な drop column は **2 段階**に分ける：

1. **第 1 段**（Wave 1 の各 migration）: 列を **rename** で退避し、論理的には削除扱いにする（API/Schema からは消す）
2. **第 2 段**（0015 = Wave 6）: backup として退避した列を物理 DROP

これにより rollback 時のデータ喪失リスクを最小化する。Wave 1 の各エージェントは
「unused にする」までを担当し、物理 DROP は Wave 6 担当に委ねる。

---

## 4. ロールバック責任

| 番号 | rollback の主な責務 |
|---|---|
| 0009 | `weekly_pattern.staff_count` を削除し旧 schema の状態に戻す。退避した列（age, ng_time_start/end, area 他）は保持しているのでそのまま戻る |
| 0010 | 退避した列（home_address, home_lat, home_lng, can_double_team, areas, max_per_day, skill_level, assignment_volume）は保持しているのでそのまま戻る |
| 0011 | seed した office 行を id ベースで削除（idempotent） |
| 0012 | `courses`, `visit_staff_assignments` テーブル DROP / `visits` の追加列 DROP |
| 0013 | `pending_requests` テーブルおよび関連 enum DROP |
| 0014 | no-op（JSONB `_meta` 書き換えなので追加スキーマ変更なし） |
| 0015 | **不可逆** — 物理 DROP 後は元に戻せない。実行前に DB スナップショット必須 |
| 0016 | route の 410 化のみであれば schema 変更なし（no-op）。Wave 6 で本番テーブル drop する場合は別 revision として 0017 を予約する |

---

## 5. 受入基準

- [x] 0009〜0016 が **重複なく** Wave 1〜2 のチケットに 1:1 で割り振られている
- [x] 0001〜0008 は v1 で消費済みであることが明記されている
- [x] 各番号の `down_revision` が明示されている（線形モデル）
- [x] expand-contract 戦略が 0009/0010 と 0015 の関係で説明されている
- [x] 各 Wave 1〜2 チケットエージェントが、自分の番号 / 直前番号を **本表のみ** 参照すれば判断できる
