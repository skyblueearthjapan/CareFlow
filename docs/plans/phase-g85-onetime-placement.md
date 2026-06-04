# Phase G-85: 空き枠配置に「その週だけ単発 / 毎週固定」切替を追加

## 背景
G-84 で空き枠直接配置を実装したが、既存患者の配置は必ず **恒常 (normal PFV, 毎週)**
になる。ユーザー要望: 既存患者の配置時に「マスター変更(毎週固定) / その週だけ(単発)」
を選べるようにする。新規患者は恒常のみ (変更なし)。

設計根拠は architect 調査済 (本ファイルは実装コントラクト)。重要事実:
- 現場ボードは **Visit** 行を読む (`board_service.load_board_buckets`、`Visit INNER JOIN Course`
  を iso 週で絞る)。PFV は読まない。よって単発は **Visit を1件 INSERT** する。
- ボード表示の必須条件: `Visit.deleted_at IS NULL` / `status=="planned"` /
  `visit_date` が対象週内 / `course_id` が対象週の `Course`(deleted_at NULL) を指す
  (INNER JOIN なので course_id NULL は非表示) / `type=="regular"` で normal 表示。
- 担当者名はボードが `Course.assigned_staff_id` から解決 (OUTER)。**フロントに staff_id 不要**。

## 確定方針
- `patient_visit_add` ハンドラに `scope: "permanent"|"one_time"` を追加 (新 request_type は作らない。
  `patient_reschedule` の `scope` 前例・`RequestScope` enum・`pending_requests.scope` 列を流用)。
- `scope` 省略/`"permanent"` → 既存の normal PFV upsert (現行不変・後方互換)。
- `scope=="one_time"` → Visit を1件 INSERT (PFV は触らない)。
- 新規患者 (`patient_create`) は恒常のみ。単発トグルは **既存患者選択時のみ** 表示。

---

## A. バックエンド (`pending_request_applier.py`)

`_apply_patient_visit_add` を分岐。`scope` を読み (`request.scope` または payload):
- permanent/未指定 → 現状の `_apply_visit_add_permanent` 相当 (既存ロジックをそのまま)。
- one_time → 新 private `_apply_visit_add_onetime(db, payload)`。

### 単発 Visit の INSERT フィールド (確定マッピング)
| Field | 値 |
|---|---|
| `patient_id` | payload `patient_id` |
| `visit_date` | `date.fromisocalendar(iso_year, iso_week, weekday + 1)` (weekday 0=Mon→ISO day1) |
| `start_time` | `_coerce_time(proposed_visits[0].start_time)` |
| `end_time` | `start_time + duration_min` (>start を検証) |
| `type` | `"regular"` |
| `status` | `VISIT_STATUS_PLANNED` |
| `source` | **`"manual"`** (★Layer-1 の auto purge `source="auto"` から保護＋翌週複製を防ぐ) |
| `course_id` | payload `course_id` (= 週次 `Course.id`, フロント `ctx.courseId`) |
| `primary_staff_id` | その Course の `assigned_staff_id` を backend で引いて設定 (無ければ NULL 可) |
| `required_staff_count` | `1` (2名体制は本フェーズ対象外) |
| `visit_group_id`/`secondary_staff_id`/`mentor_staff_id` | NULL |
| `note` | 例 `"現場ボード単発配置 (G-85)"` |

### one_time 必須 payload
`course_id`, `iso_year`, `iso_week` が必須 (無ければ 422)。
**Course 検証**: `course_id` の Course が存在・`deleted_at IS NULL`・その `iso_year/iso_week`
(と weekday があれば) が payload と一致することを INSERT 前に検証。不一致は 422
(でないと board に出ない/別週に出る)。

### 単発の重複/満員 再判定 (精密版・恒常より強い)
その週の実 Visit を読んで判定し、`_validate_override_reason_for_red(..., extra_red_codes=...)` に流す:
- `time_overlap`: 対象 Course・`visit_date`・status=planned・deleted_at NULL の既存 Visit と
  `[start,end)` 区間重複 (端点接触は重複としない)。
- `capacity_full`: 対象 Course のその週 planned Visit 数 ≥ `MAX_PATIENTS_PER_COURSE`。
赤があるのに `override_reason` 空 → 422 (G-84 と同契約)。

### 冪等性
既存の applied_at ガード内で INSERT (再 approve で二重作成しない)。raw INSERT なので
ガードに依存する点をコメント明記。

### テスト
- permanent (scope 省略/"permanent") が現行と完全に同一挙動 (PFV upsert、Visit 非作成)。
- one_time: Visit が正しい `visit_date/type/status/source/course_id/end_time` で作成される。
- `load_board_buckets` でその週のボードに出る / **翌週には出ない (複製されない)**。
- Course 不在/別週/deleted → 422。
- 単発 time_overlap / capacity_full の赤再判定 + override_reason 空 → 422。
- iso_year/iso_week/course_id 欠落 → 422。
- ⚠️ 本番 container で pytest 厳禁 (ローカルのみ)。

---

## B. フロントエンド

### B-1 payload ビルダ (`lib/field/patientCreate.ts`)
`buildPatientVisitAddPayload` を拡張:
- 引数に `scope: 'permanent'|'one_time'` を追加 (既定 `'permanent'`)。
- `scope==='one_time'` のとき payload に `course_id`(=ctx.courseId), `iso_year`, `iso_week` を載せる。
- permanent のときは現状どおり (course_id/iso は載せない)。
純関数・単体テスト追加。

### B-2 PlacementSheet UI (`components/field/FieldSheets.tsx`)
- **既存患者選択時のみ** 「配置方法」トグルを表示: 「毎週 固定（マスタに追加）」/「この週だけ（単発）」。既定=毎週固定。
- 新規患者モードでは表示しない (恒常のみ)。
- `ctx.courseId == null` の枠では単発オプションを無効化 (materialize 先が無いため。
  G-84 で courseToken null 枠はそもそも空き枠カードを出していないが、念のためガード)。
- 提出時、選択 scope と (one_time のとき) courseId/isoYear/isoWeek を
  `buildPatientVisitAddPayload` に渡す。`isoYear/isoWeek` は FieldSheets が既に受領済。
- 承認画面 (ApprovePanel): `patient_visit_add` の表示に scope を反映 (「単発(YYYY-Www)」/「毎週固定」のラベル)。可能なら placement 表示に併記。

### テスト
- `buildPatientVisitAddPayload` の scope 分岐 (one_time で course_id/iso 同梱、permanent で非同梱)。
- PlacementSheet: 既存患者でトグル表示・新規で非表示・one_time 提出 payload。
- ApprovePanel: scope ラベル表示。

---

## スコープ外 (本フェーズでやらない)
- 新規患者の単発配置。
- 2名体制 (required_staff_count=2) の単発。
- 週3日希望の残り日一括配置 (ユーザー確定: 1枠=1コマのみ、現状維持)。
