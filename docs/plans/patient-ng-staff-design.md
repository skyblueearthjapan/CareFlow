# NGスタッフ（患者×スタッフ割当禁止）+ 手動経路の確認フロー — 設計書

作成: 2026-08-11（ドラフト）
ステータス: **実装完了（2026-08-11・Phase 1〜3 全部）**

実装コミット（時系列）: 9f3d9ef 基盤(mig 0070) / 31097a2 Layer3ハード制約 /
f71b7b6 提案系警告 / 6e9bbbf 手動経路確認フロー+管理者通知 / 01364a6 FEコア /
cb0812e FE面展開+逆引き / 9f8d0bc 賢いマスタV9+取込dry-run警告 / 3e9e4e7 連携画面表示。

実装時の主な確定事項（本文の「実装時に決定」の解決）:
- 段階式reason決定: 性別緩和→NG緩和→両方緩和(reason='gender'+`also_violates=['ng_staff']`)
- 残留警告は別リスト `unresolved_ng_warnings`（既存 `unresolved_warnings` は不変）
- secondary検証は**割当を維持して警告のみ**（`secondary_constraint_warnings`・ペア構造を壊さない）
- 提案系のNG集合は「バケット→NG指定患者」の逆引き1クエリ（全経路に自動適用）
- 通知の冪等キーは `op_group_id`（None時は毎回通知。checkin版の `IS NULL` 罠を回避して再実装）
- 賢いマスタはV9ファミリ2コード（`course_staff_ng` / `course_staff_sex_mismatch`）

---

## 0. 背景と経緯（重要）

- v1 時代には `patients.ng_staff_ids`（UUID 配列）とレガシーエンジン
  （`services/allocation/engine.py`）の NG 除外ロジックが存在した。
- **2026-05-05 の PO 決断3で「廃止。例外運用は現場で口頭調整」と確定**
  （`docs/plans/v2-allocation-redesign.md:1252-1253`）。migration 0009 でカラム物理削除、
  0015 でバックアップ表も削除。**旧 NG データは復旧不可能（移行元なし）**。
- 本設計は **2026-08-11 の PO 指示によりこの決断を正式に上書きし、NG スタッフを
  v2 アーキテクチャで復活させる**もの。
- レガシーエンジンの NG コード（engine.py 内 15 箇所）は入力が常に空・フロント未使用の
  デッドコード。**本実装では触らない**（削除は別途の棚卸しで）。

## 1. PO 確定事項（2026-08-11）

| # | 論点 | 決定 |
|---|---|---|
| 1 | NG の強度 | **性別制限と同格**。エンジンではハード制約（INF 除外）、候補ゼロ時は赤レビューで管理者 override 可 |
| 2 | 手動経路の扱い | **性別制限・NG とも「素通し」をやめる**。操作ユーザーに確認を出し、OK なら通す。通した事実を**管理者へお知らせ通知**し、気づかないまま放置されない仕組みにする |
| 3 | 一時的 NG | **不要。恒久 NG のみ**（1 テーブル構成で足りる） |
| 4 | 2名体制 secondary の検証漏れ | **性別制限とセットで塞ぐ**（NG 実装時に同時対応） |

## 2. 概念モデル

- **NG スタッフ** = 患者ごとに設定する「このスタッフは割り当てない」恒久ルール。
  患者 1 人に複数名設定可。理由メモ（任意）を残せる。
- 制約の性格は性別制限（`Patient.sex_restriction`）と完全に同格:
  - **エンジン（Layer3 自動スタッフ割当）**: ハード制約。候補から除外（INF）。
    Stage3 拠点跨ぎ救援でも緩和しない。適合候補ゼロのコースは赤レビューへ。
  - **提案系（propose-slots / 改善提案 / 範囲最適化）**: 除外せず理由付き警告 + スコア降格
    （方針 N-3 の L1.5、`docs/plans/scheduling-logic-normalization.md:180`）。
  - **人手操作**: ブロックしない。確認ダイアログで「⚠ NG スタッフです」を明示し、
    OK なら通す + 管理者へお知らせ（§7）。ピンモデルの
    「エンジンだけ縛り、人手は自由（ただし見える化）」の思想に整合。

## 3. データモデル（migration 0070 想定）

`patient_same_address_links`（mig 0037）の型を踏襲。「NG のみ行を作る」方式。

```
patient_ng_staff
  patient_id   UUID  FK patients.id  ON DELETE CASCADE  ┐ 複合PK
  staff_id     UUID  FK staff.id     ON DELETE CASCADE  ┘
  note         TEXT      NULL         -- 理由メモ（任意・現場向け文言不要、内部管理用）
  decided_by_user_id UUID FK users.id ON DELETE SET NULL -- 設定者（監査）
  created_at / updated_at (TimestampMixin)
```

- インデックス: PK で patient→staff は充足。**逆引き用に `ix_patient_ng_staff_staff` (staff_id)** を追加
  （エンジンの一括ロード・スタッフ詳細の逆引きサマリで使用）。
- **soft-delete の罠（必須対応）**: 患者/スタッフの削除は `deleted_at` の soft delete のため
  FK CASCADE は発火しない（`models/patient_same_address_link.py:16-23` で明文化済みの既知罠）。
  患者削除 API・スタッフ削除 API の両方で NG 行をアプリ層から明示 DELETE すること。
- ORM 側は `Patient` に relationship を張らず、利用側が明示クエリ（N+1 禁止・一括ロード）。

## 4. API

### 4-1. NG スタッフ CRUD（`patient_same_address_links.py` 流儀）

```
GET    /api/v1/patients/{patient_id}/ng-staff            → [{staff_id, staff_name, note, created_at, decided_by}]
PUT    /api/v1/patients/{patient_id}/ng-staff/{staff_id} → upsert {note}
DELETE /api/v1/patients/{patient_id}/ng-staff/{staff_id}
```

- 権限: 編集は `require_role("admin")`。閲覧 GET は staff にも開放
  （RBAC UI 統一「全ロール同一表示・操作は権限どおり」の原則。閲覧 GET 開放の先例
  = monitor/nearby/checkin-settings）。
- バリデーション: 削除済みスタッフの新規指定は 422。既存行のスタッフが退職済みでも
  行は残す（表示側で「（退職）」注記）。
- 患者読み出し API（`GET /patients/{id}` 等）には**埋め込まない**。
  正典は本テーブルのみ（新人同行の教訓: visits 側へ二重化しない。読み手が JOIN/別 API で解決）。
  一覧でのバッジ用に `GET /patients` へ `ng_staff_count` を載せるのは任意（Phase 2）。

### 4-2. エンジン API 契約の拡張

- `POST /api/v1/schedule/assign-staff-only`（`schedule.py`）:
  - `ReviewItemSchema.reason: Literal["consecutive","gender"]` に **`"ng_staff"`** を追加
    （`schedule.py:1416-1441`）。
  - 残留違反: `unresolved_warnings` に性別と並ぶ **`UnresolvedNgWarningSchema`** を追加
    （または既存スキーマに `kind: 'gender' | 'ng_staff'` を持たせて一本化。実装時に
    FE zod の後方互換を確認して選択）。
- FE zod: `frontend/lib/queries/assign_staff_only.ts:112-113` の
  `reason: z.enum(['consecutive','gender'])` に `'ng_staff'` 追加。

## 5. エンジン変更（Layer3 = 本丸）

`backend/app/services/scheduling/layer3_assignment.py`。全て性別制限の実装（手本）に相乗り。

| 変更 | 内容 | 手本（性別） |
|---|---|---|
| 一括ロード | `_load_ng_staff_pairs(db, patient_ids) → dict[patient_id, frozenset[staff_id]]` を 1 クエリで | `_load_same_address_pair_modes`（`auto_allocator_v2.py:1650-1672`）の型 |
| コース単位集約 | コース所属患者の NG スタッフ集合を union して `ng_staff_ids: frozenset` をコース制約に追加 | `gender_restrictions` 集約 `:2784-2816`, `:3100-3120` |
| コスト関数 | `_cost_single_cell` で `staff.id in course.ng_staff_ids → HUNGARIAN_INFINITY` | 性別 INF `:2441-2447` |
| Stage3 非緩和 | 拠点跨ぎ救援でも INF のまま（性別・シフト・イベント・新人と同列） | `:2422-2424` |
| 固定割当ルート | Phase1 固定・M コース manager 固定・`l3_fix_primary_staff` 固定の各ルートで NG 検査（違反する固定は free へ回す/スキップ） | `:2015-2018`, `:3279-3298`, `:3364-3372` |
| 赤レビュー | 候補ゼロ時、「NG だけ無効化した」擬似コースで最有力候補 1 名を算出し `review_items(reason='ng_staff')` へ。DB 未割当のまま管理者承認待ち | `_compute_gender_candidate_for_course` `:2580-2690` |
| 残留違反 | 候補ゼロ かつ 現担当が NG 該当のとき warning（自動クリアしない） | W-11 `:1395-1462` |
| **2名体制 secondary 検証（決定4）** | `:3558-3576` の partner course → secondary 解決時に **性別と NG の両方**を検証。違反時は secondary を立てず warning（新設 `secondary_constraint_warnings` か既存 warning 枠に相乗り。実装時に決定） | 現状は性別も未検証（今回セットで塞ぐ） |

性別と NG が同一コースで同時に候補ゼロになるケース: reason は 1 つに絞らず、
先に検出した方を採用しつつ確認文言に両方を併記（レビュー UI の説明文で吸収。
review_item を 2 枚に分けない）。

## 6. 提案系・周辺エンジン

| 対象 | 変更 | 手本 |
|---|---|---|
| `propose_slots_service.py` | 警告コード **`staff_ng_mismatch`** + 降格ペナルティ（`_PENALTY_STAFF_SEX_MISMATCH=60.0` と同値・同層 `:623-641`）。2名体制ペア枠は OR 判定（`:1108-1110` と同型）。NG 集合は患者単位に一括ロード | `staff_sex_mismatch` |
| `improvement_engine.py:549-558` / `scope_optimizer.py:653-655` | `_staff_warnings_for_bucket()` に NG 判定を追加 | 同関数の性別判定 |
| `pool_bulk_inserter.py` | propose_slots 委譲のため自動で警告が付く（変更なしの見込み・テストで確認） | |
| `auto_allocator_v2.py` | **変更なし**。配置段階はスタッフを見ない設計原則（H7=呼出側責務）を維持 | `scheduling-logic-normalization.md:47-51` |
| `proposal_solver.py` / `unblock_search.py` | **スコープ外**（性別も参照ゼロ。既知の限界として §11 に記載） | |
| 賢いマスタ `fixed-visits/validate` | Phase 3。コース担当が NG/性別不適合なら警告 1 種追加 | |
| カイポケ取込 | Phase 3 候補。dry-run 結果への NG 衝突警告（取込自体はブロックしない） | |

## 7. 手動経路の「確認して通す + 管理者お知らせ」（決定2・性別と NG 共通）

### 7-1. 対象経路

| Phase | 経路 | 現状 |
|---|---|---|
| 1 | コース担当変更 `PATCH /api/v1/courses/{course_id}`（担当ドロップダウン・一括変更 `CourseDayTablePanel.tsx:2646` / `TimelineDayBoard.tsx:1908-1969`） | 性別・NG ともノーチェック |
| 1 | 割当レビュー承認 `POST /schedule/apply-staff-review` | 2 ステップ確認 UI が既にあるため**確認はそのまま**。NG reason の受け入れ + §7-3 の通知のみ追加 |
| 2 | 患者の枠移動で新たに NG/性別違反の組が生まれる経路（タイムライン DnD・プール投入 apply 等） | 未検査。Phase 2 で警告追加を検討（apply 系は propose 段階の警告で大半カバーされる） |

### 7-2. プロトコル（acknowledge 方式・BE が正）

```
PATCH /courses/{id} {assigned_staff_id: X}
  → BE がコース所属患者 × X の性別制限・NG を検査
  → 違反あり かつ acknowledge_constraint_warnings が無い/false
      → 422 {detail: {code: 'constraint_confirmation_required',
                       warnings: [{kind: 'ng_staff'|'gender', patient_id, patient_name,
                                   staff_id, staff_name, note}]}}
  → FE が 422 detail をパースして確認ダイアログ表示
      「⚠ ○○さんは患者△△様の NG スタッフです（メモ: …）。それでも割り当てますか？」
  → OK なら {assigned_staff_id: X, acknowledge_constraint_warnings: true} で再送 → 適用
```

- 422 + 構造化 detail + FE パーサは新人同行の先例
  （`parseOverlapDetail`, `lib/schemas/trainee_accompaniment.ts:124-129`）に倣う。
- 検査対象は「そのコースに今週載っている患者全員 × 新担当」。一括変更（週 5 日分）は
  曜日ごとに集約して 1 ダイアログにまとめる。
- `week_pinned`（青ピン）の 422 ブロックとは別物: あちらは絶対ブロック、こちらは確認付き通過。

### 7-3. 管理者へのお知らせ（既存 notifications 基盤に相乗り）

- producer: `services/checkin/notify.py` の `_active_admin_manager_users()` +
  `_create_idempotent()` パターンを流用（新モジュール `services/constraint_override_notify.py` 等）。
- 発火点: acknowledge 付き適用が commit される時（コース担当変更・apply-staff-review の
  gender/ng_staff reason 承認時）。
- 内容例: `type='constraint_override'` /
  title「NG スタッフ割当を承認: △△様 ← ○○」 /
  body に 週・コース・操作者名・理由メモ。
- 冪等キー: 同一操作の重複通知を防ぐため `reference_type='constraint_override'`,
  `reference_id=当該 visit/course の op_group_id 相当`（一括変更で使う `op_group_id` を流用。
  無い経路は reference_id=NULL で毎回通知）。
- 通知先: active な admin 全員（操作者本人も含める = 監査ログを兼ねるため。
  実装時にうるさければ本人除外に変えられる構造にしておく）。

## 8. UI/UX

### 8-1. 設定 UI（B案: 独立セクション・即時 CRUD）

- 新規 `frontend/components/patients/PatientNgStaffSection.tsx`
  （`SameAddressLinksSection.tsx` をテンプレに。日本語トーンも同じく）。
- `PatientForm.tsx:498-499` の同住所セクションの隣に 1 行追加 →
  **患者マスタ編集 / 新規 / スケジュールの PatientEditDialog / CreatePatientDialog の
  4 画面へ同時反映**（`patientId` がある編集モードのみ描画）。
- 部品: `StaffCombobox`（検索付き単一選択）で 1 名ずつ追加 + 理由メモ input →
  行リスト表示（名前 / メモ / 削除ボタン / 退職者は「（退職）」注記）。
- queries: 新規 `lib/queries/patient_ng_staff.ts`。キーは
  `['patients', id, 'ng-staff']`（`PATIENTS_KEY` prefix にぶら下げて
  `useUpdatePatient` の invalidate に相乗り = `patient_fixed_visits.ts` の流儀）。

### 8-2. 表示（閲覧系）

| Phase | 場所 | 内容 |
|---|---|---|
| 1 | 患者詳細「訪問条件」Card（`patients/[id]/page.tsx:282-296`） | 性別制限の隣に NG スタッフ名列挙 |
| 1 | 患者詳細ダイアログ（`PatientScheduleDetailDialog.tsx:476-508`） | 基本情報に同上 |
| 1 | `AssignWarningDialog.tsx` | **⛔ NG スタッフセクション新設**。性別（🔴）と同じ 2 ステップ確認・一斉承認対象外。残留違反は 🟧 に相乗り |
| 2 | プール患者カード `PatientCard.tsx:319-362` | 「NGあり」バッジ |
| 2 | 提案系警告ラベル `fieldBoard.ts:196-217` | `staff_ng_mismatch: 'NGスタッフに該当'` |
| 2 | モバイル現場カルテ `FieldSheets.tsx:359-376` | **表示のみ**（編集は PC 限定。KarteEditSheet は手書きフォームで二重実装コストが高い） |
| 2 | スタッフ詳細の逆引き | 「この方を NG 指定している患者」閲覧サマリ + 患者編集への誘導リンク（`TraineeAccompanimentSummary` の流儀） |

## 9. フェーズ分割

- **Phase 1（コア）**: mig 0070 + モデル + CRUD API / PatientNgStaffSection + 4 画面反映 /
  Layer3 ハード制約一式（レビュー・残留・固定ルート）/ **secondary の性別+NG 検証（決定4）** /
  AssignWarningDialog ⛔ / 詳細・ダイアログ表示 / **手動コース担当変更の確認フロー（§7-2）+
  管理者お知らせ（§7-3）** / soft-delete 明示 DELETE
- **Phase 2（面展開）**: 提案系警告（propose-slots / 改善提案 / 範囲最適化 / プール投入確認）/
  DnD・apply 系の確認 / バッジ / モバイル表示 / スタッフ側逆引き / `ng_staff_count`
- **Phase 3（周辺）**: 賢いマスタ検査 / カイポケ取込 dry-run 警告

## 10. テスト計画（先例に倣う）

- BE: `test_layer3_phase1_fixed_gender.py` / `test_layer3_w11_gender_unresolved.py` の
  NG 版（INF 除外・固定ルート・レビュー・残留・secondary 検証・性別との複合）。
  `test_propose_slots_api.py:786-836` の NG 版（警告付与・退職スタッフ・ペア OR）。
  コース PATCH の 422→acknowledge→適用→通知作成の一連。soft-delete 時の行削除。
- FE: `AssignWarningDialog.test.tsx` に ⛔ セクション（一斉承認対象外の検証含む）。
  PatientNgStaffSection の CRUD。422 パーサ + 確認ダイアログ。
- 既知のベースライン失敗（handoff §9-5）には触らない。

## 11. スコープ外・既知の限界（明記して引き継ぐ）

- proposal_solver / unblock_search（詰まり解消スワップ）は性別と同様 NG も見ない。
- カイポケ取込は外部データが正のため NG 違反でも取り込む（Phase 3 で警告のみ検討）。
- レガシー `services/allocation/engine.py` / `/api/v1/allocate` は本実装の対象外
  （NG コード 15 箇所 + 文字列不一致で死んでいる性別チェック 9 箇所を含む。別途棚卸しで削除検討）。
- 一時的 NG（週限定）は不要と確定（決定3）。要件が変わったら新人同行の 2 テーブル型
  （既定 + 週実効）へ拡張する。
