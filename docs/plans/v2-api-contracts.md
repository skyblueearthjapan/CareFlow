# CareFlow v2 API 契約 (OpenAPI 風)

> **Status**: Wave 0-B 出力（v0.1）
> **対応設計仕様書**: `docs/plans/v2-allocation-redesign.md` v0.9
> **対応実装手順書**: `docs/plans/v2-implementation-plan.md` v0.2
>
> 本書は v2 で **新規追加 / 変更** される全 API エンドポイントのリクエスト / レスポンス契約を文書化する。
> フロント・バック両エージェントが **唯一の参照源**（single source of truth）として用いる。
>
> 既存 v1 API（変更なしのもの）は本書に含めない。
>
> 共有型の正式な定義は **`backend/app/schemas/v2/*.py`（pydantic）**
> および **`frontend/lib/schemas/v2/*.ts`（zod）** にある（Wave 0-C で作成）。
> 本書のリクエスト / レスポンス Schema 列は、それら Pydantic / zod スキーマ名で参照する。

---

## 0. 共通事項

### 0.1 ベース URL

- 全エンドポイントは `/api/v1/...` 配下に配置する
- v2 系で導入される新規エンドポイントも **path version は `v1` のまま**（API 互換戦略上、breaking change のときのみ v2 へ）

### 0.2 認証 / 認可

- 認証: 既存の OAuth Bearer トークン
- ロール: `admin` / `manager` / `staff`
- RBAC は各エンドポイントの **RBAC** 列に明示

### 0.3 共通レスポンス

- エラー: 既存の `{ "detail": "..." }` 形式（FastAPI 標準）
- ページネーション: 既存 `_pagination.py` の `PaginatedResponse[T]` 形式

### 0.4 RBAC 表記

- `admin` = 全権限
- `manager` = 業務管理権限（admin 同等の業務操作）
- `staff` = 自分軸のみ
- `Admin/Manager` のように「/」区切りはいずれかで OK の意

---

## 1. Patients API（W1-BE1）

### 1.1 `POST /api/v1/patients`

| 項目 | 内容 |
|---|---|
| 概要 | 患者の新規作成（v2 整理スキーマ） |
| 担当チケット | W1-BE1 |
| Request body | `PatientV2Create` |
| Response 200 | `PatientV2Read` |
| RBAC | Admin / Manager |

**変更点（v1 → v2）**:
- 削除: `age`, `ng_time_start`, `ng_time_end`, `required_staff_count`, `area`, `ng_staff_ids`, `preferred_staff_ids`, `specified_type`, `continuous_request`（10 項目）
- 追加: `weekly_pattern.staff_count`, `special_weekly_pattern`, `special_week_active`

### 1.2 `PATCH /api/v1/patients/{id}`

| 項目 | 内容 |
|---|---|
| 概要 | 患者の部分更新 |
| Request body | `PatientV2Update` |
| Response 200 | `PatientV2Read` |
| RBAC | Admin / Manager |

### 1.3 `GET /api/v1/patients/{id}`

| 項目 | 内容 |
|---|---|
| Response 200 | `PatientV2Read` |
| RBAC | Admin / Manager / Staff（自分が担当する患者のみ） |

### 1.4 `GET /api/v1/patients`

| 項目 | 内容 |
|---|---|
| Query | `status`, `office_id`, `q`, `limit`, `offset` |
| Response 200 | `PaginatedResponse[PatientV2Read]` |
| RBAC | Admin / Manager |

### 1.5 `DELETE /api/v1/patients/{id}`

| 項目 | 内容 |
|---|---|
| 概要 | 論理削除（既存挙動維持） |
| RBAC | Admin のみ（**AI 経由不可** — §3.5.2 対象外） |

---

## 2. Staff API（W1-BE2）

### 2.1 `POST /api/v1/staff`

| 項目 | 内容 |
|---|---|
| 概要 | スタッフの新規作成（v2 整理スキーマ） |
| 担当チケット | W1-BE2 |
| Request body | `StaffV2Create` |
| Response 200 | `StaffV2Read` |
| RBAC | Admin / Manager |

**変更点（v1 → v2）**:
- 削除: `can_double_team`, `home_address`, `home_lat`, `home_lng`, `areas`, `max_per_day`, `skill_level`, `assignment_volume`（6 項目 + `home_*` 3 項目）
- `status`: `在籍 / 休職 / 退職` の 3 値に正規化（v1 の `active` / `inactive` から移行）
- メンターフィールド (`mentor_id`) は維持（UI 上は詳細セクション扱い）

### 2.2 `PATCH /api/v1/staff/{id}`

| 項目 | 内容 |
|---|---|
| Request body | `StaffV2Update` |
| Response 200 | `StaffV2Read` |
| RBAC | Admin / Manager |

### 2.3 `GET /api/v1/staff/{id}`

| 項目 | 内容 |
|---|---|
| Response 200 | `StaffV2Read` |
| RBAC | Admin / Manager / Staff（自分のみ） |

### 2.4 `GET /api/v1/staff`

| 項目 | 内容 |
|---|---|
| Response 200 | `PaginatedResponse[StaffV2Read]` |
| RBAC | Admin / Manager |

### 2.5 `DELETE /api/v1/staff/{id}`

| 項目 | 内容 |
|---|---|
| 概要 | 論理削除 |
| RBAC | Admin のみ（**AI 経由不可**） |

---

## 3. Offices API（W1-BE3）

### 3.1 `POST /api/v1/offices/resolve`

| 項目 | 内容 |
|---|---|
| 概要 | 住所文字列から該当する拠点を自動判定（patient 自動紐付け用） |
| 担当チケット | W1-BE3 |
| Request body | `{ "address": string }` |
| Response 200 | `{ "office_id": uuid \| null, "office_name": string \| null, "matched_city_id": uuid \| null, "confidence": "exact" \| "fuzzy" \| "none" }` |
| RBAC | Admin / Manager |

### 3.2 `GET /api/v1/offices`

| 項目 | 内容 |
|---|---|
| Response 200 | `PaginatedResponse[OfficeV2Read]` |
| RBAC | Admin / Manager |

### 3.3 `POST /api/v1/offices`

| 項目 | 内容 |
|---|---|
| Request body | `OfficeV2Create` |
| Response 200 | `OfficeV2Read` |
| RBAC | Admin のみ（**AI 経由不可** — 拠点マスタは Web フォームから） |

### 3.4 `PATCH /api/v1/offices/{id}`

| 項目 | 内容 |
|---|---|
| Request body | `OfficeV2Update` |
| Response 200 | `OfficeV2Read` |
| RBAC | Admin のみ |

---

## 4. Courses API（W2-BE4 / W4-BE8 / W4-BE9）

### 4.1 `GET /api/v1/courses`

| 項目 | 内容 |
|---|---|
| 概要 | コース一覧。週・曜日でフィルタ |
| 担当チケット | W2-BE4 |
| Query | `iso_year`, `iso_week`, `weekday` (0-6), `status` |
| Response 200 | `PaginatedResponse[CourseV2Read]` |
| RBAC | Admin / Manager |

### 4.2 `POST /api/v1/courses`

| 項目 | 内容 |
|---|---|
| 概要 | コース手動作成（通常はあまり使わない） |
| Request body | `CourseV2Create` |
| Response 200 | `CourseV2Read` |
| RBAC | Admin / Manager |

### 4.3 `PATCH /api/v1/courses/{id}`

| 項目 | 内容 |
|---|---|
| Request body | `CourseV2Update` |
| Response 200 | `CourseV2Read` |
| RBAC | Admin / Manager |

### 4.4 `POST /api/v1/courses/generate`（Layer 2）

| 項目 | 内容 |
|---|---|
| 概要 | Layer 2 のコース分け案を生成（K-means + 制約後処理） |
| 担当チケット | W4-BE8 |
| Request body | `{ "iso_year": int, "iso_week": int, "weekday": int (0-6), "staff_count": int }` |
| Response 200 | `{ "courses": [CourseV2Read], "total_distance_km": float, "validity_score": float }` |
| RBAC | Admin / Manager |
| 状態遷移 | 生成された course は `course_status = "proposed"` で作成される |

### 4.5 `POST /api/v1/courses/{id}/fix`

| 項目 | 内容 |
|---|---|
| 概要 | コース構成を確定（proposed → course_fixed） |
| 担当チケット | W2-BE4 |
| Request body | `{}` |
| Response 200 | `CourseV2Read` |
| RBAC | Admin / Manager |
| 状態遷移 | `proposed` → `course_fixed`（`course_fixed_at` を埋める） |

### 4.6 `POST /api/v1/courses/assign-staff`（Layer 3）

| 項目 | 内容 |
|---|---|
| 概要 | Layer 3 のスタッフ割付（ハンガリアン法 + ローテーション） |
| 担当チケット | W4-BE9 |
| Request body | `{ "iso_year": int, "iso_week": int }` |
| Response 200 | `{ "assignments": [{ "weekday": int, "course_code": str, "course_id": uuid, "staff_id": uuid }], "rotation_score": float, "total_distance_km": float }` |
| RBAC | Admin / Manager |
| 状態遷移 | 関連する全 course を `course_fixed` → `staff_assigned` に進める（`staff_assigned_at` 埋める） |
| ハード制約 | 性別 / 勤務曜日 / 1 コース 1 スタッフ / マネージャー除外 |

---

## 5. Visits API（W2-BE4 拡張）

### 5.1 `POST /api/v1/visits`

| 項目 | 内容 |
|---|---|
| 概要 | 訪問インスタンス作成 |
| 担当チケット | W2-BE4 |
| Request body | `VisitV2Create` |
| Response 200 | `VisitV2Read` |
| RBAC | Admin / Manager |

**変更点**:
- 追加: `course_id` (FK NULL), `required_staff_count` (1 or 2), `visit_group_id` (UUID NULL)
- 2 名体制の場合: 同じ `visit_group_id` で 2 行を作成し、`visit_staff_assignments` 経由で 2 スタッフを紐付け

### 5.2 `PATCH /api/v1/visits/{id}`

| 項目 | 内容 |
|---|---|
| Request body | `VisitV2Update` |
| Response 200 | `VisitV2Read` |
| RBAC | Admin / Manager |

### 5.3 `GET /api/v1/visits`

| 項目 | 内容 |
|---|---|
| Query | `iso_year`, `iso_week`, `weekday`, `course_id`, `patient_id`, `staff_id` |
| Response 200 | `PaginatedResponse[VisitV2Read]` |
| RBAC | Admin / Manager / Staff（自分が担当の visit のみ） |

### 5.4 `POST /api/v1/visits/{id}/staff`

| 項目 | 内容 |
|---|---|
| 概要 | visit_staff_assignments の追加（2 名体制で 2 スタッフ目を入れる用途） |
| Request body | `{ "staff_id": uuid }` |
| Response 200 | `VisitV2Read`（assignments 含む） |
| RBAC | Admin / Manager |

### 5.5 `DELETE /api/v1/visits/{id}/staff/{staff_id}`

| 項目 | 内容 |
|---|---|
| 概要 | visit_staff_assignments の削除 |
| Response 204 | （body なし） |
| RBAC | Admin / Manager |

---

## 6. Schedule API（W3-BE-FIX / W4-BE7）

### 6.1 `POST /api/v1/schedule/fix`（W3-FE5 の BE 側）

| 項目 | 内容 |
|---|---|
| 概要 | 当該週のレイアウトを各患者の `weekly_pattern` に保存（「固定」ボタン） |
| 担当チケット | W3-BE-FIX |
| Request body | `{ "iso_year": int, "iso_week": int, "patient_layouts": [{ "patient_id": uuid, "weekday": 0-6, "start_time": "HH:MM", "service_minutes": int, "staff_count": 1 \| 2 }] }` |
| Response 200 | `{ "updated_count": int, "patients": [PatientV2Read] }` |
| RBAC | Admin / Manager |
| トランザクション | 全件 1 トランザクション。1 件でも失敗すれば全 rollback |

### 6.2 `POST /api/v1/schedule/generate-week`（Layer 1）

| 項目 | 内容 |
|---|---|
| 概要 | weekly_pattern から visits を生成。特別週判定。新規患者は保留プールへ |
| 担当チケット | W4-BE7 |
| Request body | `{ "iso_year": int, "iso_week": int }` |
| Response 200 | 下記 `GenerateWeekResponse` |
| RBAC | Admin / Manager |
| 冪等性 | 同一 (iso_year, iso_week) で再実行すると、当該週の auto-visit (status=planned, source=auto) のみ削除して再生成。`completed` / `cancelled` / `source != "auto"` の visit は保護される。 |

#### `GenerateWeekResponse` (W4-BE8 / W4-BE9 が消費する出力)

```jsonc
{
  "iso_year": 2026,
  "iso_week": 19,
  "visits_created": [
    {
      "visit_id": "uuid",
      "patient_id": "uuid",
      "weekday": 0,                // 0=Mon..6=Sun
      "visit_date": "2026-05-04",  // YYYY-MM-DD
      "start_time": "09:00",       // HH:MM
      "end_time":   "10:00",       // HH:MM
      "staff_count": 1,            // 1 or 2 (§3.3 2 名体制)
      "special_week_applied": false  // §3.4 適用週フラグ
    }
  ],
  "pool": [
    {
      "patient_id": "uuid",
      "patient_name": "田中 太郎",
      "preferred_weekdays": ["Mon", "Wed"],   // §4.1 weekly_pattern.preferred_weekdays
      "frequency_per_week": 2                  // §4.1 weekly_pattern.frequency_per_week
    }
  ],
  "summary": {
    "patients_processed":          50,  // 当該週で処理した active 患者数
    "visits_created":             120,  // 生成された visit 行数 (2 名体制は 2 行)
    "pool_count":                   3,  // 保留プールに積まれた患者数
    "special_week_applied_count":   2   // 特別週パターン適用された患者数
  }
}
```

> **2 名体制の表現**: `weekly_pattern.entries[].staff_count = 2` の entry は
> `visits_created` に同一 (weekday, start_time) で 2 行返る。
> 各行は同じ `visit_group_id` を共有して DB に保存されるが、
> レスポンス JSON には `visit_group_id` を露出しない (W4-BE8 が消費する
> 際は `(weekday, start_time, patient_id)` でグルーピング可能)。

---

## 7. PendingRequests API（W2-BE5）

### 7.1 `POST /api/v1/pending-requests`

| 項目 | 内容 |
|---|---|
| 概要 | 申請（業務リクエスト）の作成 |
| 担当チケット | W2-BE5 |
| Request body | `PendingRequestV2Create` |
| Response 200 | `PendingRequestV2Read` |
| RBAC | Admin / Manager / Staff（**Staff は自分軸のみ**） |
| 監査要件 | AI 経由の **即時反映** の場合も `status="approved"` で同時作成し、業務反映と同一 TX で処理 |

### 7.2 `GET /api/v1/pending-requests`

| 項目 | 内容 |
|---|---|
| Query | `status`, `request_type`, `target_staff_id`, `target_patient_id`, `target_date_from`, `target_date_to`, `limit`, `offset` |
| Response 200 | `PaginatedResponse[PendingRequestV2Read]` |
| RBAC | Admin / Manager / Staff（自分の申請 + 自分宛のみ） |

### 7.3 `GET /api/v1/pending-requests/{id}`

| 項目 | 内容 |
|---|---|
| Response 200 | `PendingRequestV2Read` |
| RBAC | Admin / Manager / Staff（自分の申請 + 自分宛のみ） |

### 7.4 `PATCH /api/v1/pending-requests/{id}/approve`

| 項目 | 内容 |
|---|---|
| 概要 | 申請を承認し、`PendingRequestApplier` で業務テーブルに反映 |
| Request body | `{ "edited_payload": {} \| null }`（編集して承認の場合に edited_payload を渡す） |
| Response 200 | `PendingRequestV2Read`（`status = approved`） |
| RBAC | Admin / Manager のみ |
| 冪等性 | 同一申請を 2 回 approve しても 1 回しか反映されない（applied_at で gate） |
| トランザクション | 業務テーブル更新と pending_requests 状態更新を同一 TX で実行 |

### 7.5 `PATCH /api/v1/pending-requests/{id}/reject`

| 項目 | 内容 |
|---|---|
| 概要 | 申請を却下 |
| Request body | `{ "rejection_reason": string }` (**必須**) |
| Response 200 | `PendingRequestV2Read`（`status = rejected`） |
| RBAC | Admin / Manager のみ |

---

## 7.6 patient_fixed_visits API（W9-BE1 / W9-BE2）

> Wave 9 Phase 5a 追記（2026-05-06）。設計仕様 §3.6.8 / §4.1b に対応する。
> W9-BE1 は Phase 1 で実装（GET / PUT / DELETE）、W9-BE2 は Phase 2 で実装
>（from-week 系 / fix-or-pattern）。

### 7.6.1 `GET /api/v1/patients/{patient_id}/fixed-visits`

| 項目 | 内容 |
|---|---|
| 概要 | 患者の固定枠一覧取得 |
| 担当チケット | W9-BE1 |
| Query | `mode=normal\|special`（省略時: 両 mode を返す） |
| Response 200 | `list[PatientFixedVisitV2Read]` |
| RBAC | Admin / Manager（全患者） / Staff（自担当患者のみ） |

### 7.6.2 `PUT /api/v1/patients/{patient_id}/fixed-visits`

| 項目 | 内容 |
|---|---|
| 概要 | 患者の固定枠を完全置換（1 TX で当該 mode の全行削除 → 挿入） |
| 担当チケット | W9-BE1 |
| Request body | `PatientFixedVisitsBulkPut` |
| Response 200 | `list[PatientFixedVisitV2Read]` |
| RBAC | Admin / Manager のみ |
| トランザクション | `(patient_id, mode)` の既存全行を DELETE し、items で指定した行を INSERT。1 TX で完結 |
| バリデーション | `items` 内に同一 `weekday` が重複する場合は 422 を返す |

### 7.6.3 `DELETE /api/v1/patients/{patient_id}/fixed-visits`

| 項目 | 内容 |
|---|---|
| 概要 | 患者の固定枠を全削除（希望ベースの自動展開に戻す） |
| 担当チケット | W9-BE1 |
| Query | `mode=normal\|special`（**必須**） |
| Response 204 | No Content |
| RBAC | Admin / Manager のみ |
| 効果 | 削除後の `generate-week` では `weekly_pattern` / `special_weekly_pattern` から visits を生成する（§3.6.8 Layer 1 hybrid 化のフォールバック） |

### 7.6.4 `POST /api/v1/patients/{patient_id}/fixed-visits/from-week`（Phase 2）

| 項目 | 内容 |
|---|---|
| 概要 | 当該週の visits を patient_fixed_visits に書き戻す（個別固定化） |
| 担当チケット | W9-BE2 |
| Query | `iso_year=2026&iso_week=20`（必須）、`mode=normal\|special`（省略可） |
| Response 200 | `list[PatientFixedVisitV2Read]` |
| RBAC | Admin / Manager のみ |
| mode 自動推定 | `mode` 省略時: `special_week_active` に `(iso_year, iso_week)` が含まれれば `'special'`、なければ `'normal'` |
| 書き戻し元 | `visits` + `visit_staff_assignments`（時刻・duration のみ取得。スタッフ情報は書き戻さない） |

### 7.6.5 `POST /api/v1/patients/fixed-visits/from-week-bulk`（Phase 2）

| 項目 | 内容 |
|---|---|
| 概要 | 全患者一括固定化 |
| 担当チケット | W9-BE2 |
| Query | `iso_year=2026&iso_week=20`（必須）、`mode=normal\|special`（省略可） |
| Response 200 | `{ "updated_count": int, "patients": [uuid] }` |
| RBAC | Admin / Manager のみ |
| 処理方式 | 全 active 患者に対して §7.6.4 と同等の書き戻しを 1 TX で実行 |

### 7.6.6 `POST /api/v1/schedule/fix-or-pattern`（Phase 2）

> D&D ダイアログの (a)/(b) 選択に対応するエンドポイント（§3.5.8 参照）。

| 項目 | 内容 |
|---|---|
| 概要 | スケジュール変更を「今週のみ」または「固定枠変更」のいずれかのモードで適用 |
| 担当チケット | W9-BE2 |
| Request body | `FixOrPatternRequest`（下記参照） |
| Response 200 | `FixOrPatternResponse`（下記参照） |
| RBAC | Admin / Manager のみ |

#### `FixOrPatternRequest`

```jsonc
{
  "mode":            "this_week_only" | "pattern_change",
  "visit_id":        "<UUID>",
  "new_weekday":     0,              // 0=Mon ... 6=Sun
  "new_start_time":  "HH:MM",
  "new_duration_min": 60,
  "iso_year":        2026,
  "iso_week":        20
}
```

#### `FixOrPatternResponse`

```jsonc
{
  "mode":                 "this_week_only" | "pattern_change",
  "updated_visit":        VisitV2Read | null,         // mode=this_week_only のとき返る
  "updated_fixed_visit":  PatientFixedVisitV2Read | null  // mode=pattern_change のとき返る
}
```

#### 処理フロー

- `mode = 'this_week_only'`: `POST /api/v1/schedule/fix`（§6.1）と同じ経路で
  当該 visit の時刻のみ更新。`patient_fixed_visits` は触らない
- `mode = 'pattern_change'`: `special_week_active` の状態に応じて
  `normal` / `special` を自動判定し、`patient_fixed_visits` の該当行を
  upsert する。翌週以降の `generate-week` から新しい固定枠が適用される

---

## 7.7 Pydantic / TypeScript 型定義（patient_fixed_visits）

> 共有型の正式定義は `backend/app/schemas/v2/patient_fixed_visits.py` および
> `frontend/lib/schemas/v2/patient_fixed_visits.ts` に配置する（W9-BE1 で作成）。
> 本節は両ファイルの **参照用サマリ**。

```python
# Python (Pydantic)

PatientFixedVisitMode = Literal['normal', 'special']

class PatientFixedVisitV2Base(BaseModel):
    weekday:      int   # 0..6 (0=Mon ... 6=Sun)
    start_time:   time  # HH:MM 形式
    duration_min: int = 30  # 1..480

class PatientFixedVisitV2Read(PatientFixedVisitV2Base):
    id:         UUID
    patient_id: UUID
    mode:       PatientFixedVisitMode
    created_at: datetime
    updated_at: datetime

class PatientFixedVisitsBulkPut(BaseModel):
    mode:  PatientFixedVisitMode
    items: list[PatientFixedVisitV2Base]  # 0..7 件。同一 weekday の重複は 422
```

```typescript
// TypeScript (zod)

export const PatientFixedVisitModeSchema = z.enum(['normal', 'special']);
export type PatientFixedVisitMode = z.infer<typeof PatientFixedVisitModeSchema>;

export const PatientFixedVisitV2BaseSchema = z.object({
  weekday:      z.number().int().min(0).max(6),
  start_time:   z.string().regex(/^\d{2}:\d{2}$/),  // HH:MM
  duration_min: z.number().int().min(1).max(480).default(30),
});

export const PatientFixedVisitV2ReadSchema = PatientFixedVisitV2BaseSchema.extend({
  id:         z.string().uuid(),
  patient_id: z.string().uuid(),
  mode:       PatientFixedVisitModeSchema,
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
});

export const PatientFixedVisitsBulkPutSchema = z.object({
  mode:  PatientFixedVisitModeSchema,
  items: z.array(PatientFixedVisitV2BaseSchema).max(7),
});
```

---

## 8. AI API（W2-BE6 / W5-FE10）

### 8.1 `POST /api/v1/ai/interpret`（既存拡張）

| 項目 | 内容 |
|---|---|
| 概要 | 自然言語入力を Gemini で構造化 JSON に変換 |
| 担当チケット | W2-BE6 |
| Request body | `{ "prompt": string, "context_type": AiContextType, "context": {} }` |
| Response 200 | `{ "interpreted": {}, "confidence": float, "raw_response": string, "log_id": uuid, "model": string, "latency_ms": int, "cost_usd": float, "context_type": AiContextType }` |
| RBAC | 認証済み全員（ただし対応する business 操作の RBAC は別途 pending_requests で適用） |

**変更点**:
- `context_type` enum 拡張（§9 対応表参照）
- プロンプトに `out_of_scope` アクションの選択肢を組み込み
- `interpreted.action` が `out_of_scope` の場合、UI 側で「対応していません」メッセージを表示

### 8.2 `GET /api/v1/ai/logs`（既存維持）

変更なし（参考用に列挙）

---

## 9. context_type ↔ request_type 対応表（**重要**）

> 設計仕様書 §3.5.2 / §4.4 / 実装手順書 §1 0-C に基づく **唯一の対応表**。
>
> AI の `context_type`（Gemini への hint）と pending_requests の `request_type`（業務種別）は
> ほぼ 1:1 対応するが、片方が増えても他方が即増えるわけではない。
> 本表を超える対応は **追加してはならない**（無秩序な拡張を防ぐ）。

| # | `request_type` (pending_requests) | `context_type` (ai/interpret) | 操作内容 | 入力チャネル | 即時反映可（PC admin/manager のみ） | scope 必須 |
|---|---|---|---|---|---|---|
| 1 | `staff_off` | `staff_off` | スタッフのその週だけの休み登録 | AI / 手動 | ✅ | ❌ |
| 2 | `staff_event` | `staff_event` | スタッフのイベント新規（会議・研修） | AI / 手動 | ✅ | ❌ |
| 3 | `staff_mentor` | `staff_mentor` | スタッフのメンター登録（鈴木さんのメンターを山田さんに） | AI / 手動 | ✅ | ❌ |
| 4 | `staff_create` | `staff_create` | スタッフ新規登録 | AI（不足情報補完モーダル経由） / 手動 | ✅ | ❌ |
| 5 | `patient_create` | `patient_create` | 患者新規登録 | AI（不足情報補完モーダル経由） / 手動 | ✅ | ❌ |
| 6 | `patient_cancel` | `patient_cancel` | 患者の訪問キャンセル | AI / 手動 | ✅ | ❌ |
| 7 | `patient_reschedule` | `patient_reschedule` | 患者の日時変更（**今週だけ / 今後固定** を選択） | AI / 手動 | ✅ | ✅（`one_time` / `permanent`） |
| 8 | `patient_special_week_on` | `patient_special_week` | 患者の特別訪問週間 ON | AI / 手動 | ✅ | ❌ |
| 9 | `patient_special_week_off` | `patient_special_week` | 患者の特別訪問週間 OFF | AI / 手動 | ✅ | ❌ |
| ─ | （N/A） | `general` | 汎用フォールバック（既存） | AI のみ | ─ | ─ |
| ─ | （N/A） | `out_of_scope` | AI が範囲外と判定 | AI のみ | ─ | ─ |

### 9.1 重要メモ

- `patient_special_week_on` と `patient_special_week_off` は **request_type は別**（業務操作として別個）だが、
  AI 側の `context_type` は **両方とも `patient_special_week`** に集約する（プロンプト負荷削減）。
  `interpreted.action` の中で `on` / `off` を判別する。
- `patient_reschedule` は **scope 必須**（§3.5.6）。AI 解釈時にも `scope` を含めた payload を返すよう
  プロンプトを設計する。scope が欠落していたら不足情報補完モーダルへ誘導。
- `general` と `out_of_scope` は context_type のみで、request_type には対応物を作らない
  （業務反映を伴わないため pending_requests には記録されない）。

### 9.2 各 request_type が承認時に触るテーブル（PendingRequestApplier の責務）

| request_type | 主に触るテーブル |
|---|---|
| `staff_off` | `staff_weekly_overrides`（または v2 で改名する場合は同等テーブル） |
| `staff_event` | `staff_events` |
| `staff_mentor` | `staff` (`mentor_id` 列の更新) |
| `staff_create` | `staff` （新規 INSERT） |
| `patient_create` | `patients`（新規 INSERT） |
| `patient_cancel` | `visits`（status を `cancelled` へ） |
| `patient_reschedule` (scope=one_time) | `visits`（時刻のみ更新） |
| `patient_reschedule` (scope=permanent) | `patients.weekly_pattern` 更新 + 当該以降の visits 再生成 |
| `patient_special_week_on` | `patients.special_week_active` に該当週を追加 |
| `patient_special_week_off` | `patients.special_week_active` から該当週を削除 |

---

## 10. 受入基準

- [x] patients / staff / offices / courses / visits / schedule / pending_requests / ai のすべてに新規 / 変更エンドポイントが列挙されている
- [x] 各エンドポイントに **path / method / 担当チケット / RBAC / Request schema / Response schema** が記載されている
- [x] context_type ↔ request_type の対応表が末尾にある
- [x] 各 request_type が承認時に触るテーブルが明記されている（PendingRequestApplier の実装契約として）
- [x] AI 経由不可の操作（patient/staff の delete、office の編集）が明示されている
