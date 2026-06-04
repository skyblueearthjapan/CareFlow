# Phase G-84: 現場ボード 空き枠への直接配置 (Slot Direct Placement)

## 背景 / 課題

モバイル現場ボード (`/m`) で空き枠 (`EmptySlot`) をタップすると、現状は汎用の
`SuggestSheet`(自動提案) が開くだけで、**タップした枠の座標 (拠点/コース/曜日/時刻) を
捨てている** (`FieldBoard.tsx:285 onEmpty={() => setSheet(true)}`)。
そのため「狙った空き時間に直接入れる」ことができない。

本フェーズで「タップした枠に、新規または既存の患者を直接配置する」導線を追加する。

## 確定仕様 (ユーザー合意済み)

- **配置方式**: 空き枠タップ → 「この枠に入れる」シート (新規/既存 両対応)。
- **開始時刻**: 案2(移動時間提案) を初期値 + 案3(5分刻み手動調整) + 案1(直前訪問が
  無い枠は枠頭をフォールバック初期値)。
- **書き込み経路**: 既存の `pending_request → 承認` を必ず経由 (即時反映しない)。
- **赤警告** (満員/時刻重複): **理由付きで強行可** (二段確認)。
- **黄警告** (移動不足/性別制限/複数スタッフ): 承知チェックで許可。
- **承認画面**: 本フェーズ scope に含む。payload に枠座標 + 警告を載せ、承認カードに
  「どの枠に・誰の隣に・どの警告か」を転記する。

---

## A. バックエンド

### A-1. 移動時間エンドポイント (案2の根拠)

`POST /api/v1/schedule/v2/travel-estimate` (require_role admin/manager, read-only)

既存ユーティリティを再利用する (新規ロジック禁止):
`app.services.scheduling.proposal_solver` の `haversine_km` / `haversine_minutes` /
`VISIT_BUFFER_MINUTES`。

Request (`schemas/v2` に新規 Pydantic):
```
{
  from_patient_id: UUID | null,   # 直前訪問の患者 (board visit.patient_id)
  from_lat: float | null,         # from_patient_id 無いとき用フォールバック
  from_lng: float | null,
  to_address: str | null,         # 新規患者: 住所
  to_lat: float | null,           # 既存患者: 既知座標 (geocode 省略)
  to_lng: float | null,
}
```
解決:
- from 座標: `from_patient_id` → `patients.lat/lng`、無ければ `from_lat/lng`。
- to 座標: `to_lat/lng` 優先、無ければ `geocode_address(to_address)` (best-effort)。
- 両方確定: `travel_minutes = haversine_minutes(haversine_km(...))`,
  `total_minutes = travel_minutes + VISIT_BUFFER_MINUTES`。
- どちらか未確定: minutes は null (フロントは案1=枠頭にフォールバック)。

Response:
```
{
  from_resolved: bool,
  to_resolved: bool,
  travel_minutes: int | null,
  buffer_minutes: int,           # = VISIT_BUFFER_MINUTES
  total_minutes: int | null,     # travel + buffer
}
```

### A-2. payload 拡張 (承認カード用の枠座標 + 警告)

`patient_create` / 新規 `patient_visit_add` の両 payload に共通の任意ブロックを追加:
```
placement: {
  office_id: UUID,
  office_name: str,
  course_template_id: UUID | null,
  course_code: str,              # 例 "稲A" (拠点付きトークン)
  course_label: str,
  weekday: int,                  # 0=Mon..6=Sun
  start_time: "HH:MM",
  duration_min: int,
  prev_visit: { patient_name: str, end_time: "HH:MM" } | null,
  next_visit: { patient_name: str, start_time: "HH:MM" } | null,
}
warnings: [
  { level: "red"|"yellow", code: str, message: str }
]
override_reason: str | null      # 赤警告を強行した場合の理由 (必須)
```
- applier は `placement` / `warnings` / `override_reason` を**業務反映には使わない**
  (監査/表示用メタ)。ただし `override_reason` は赤警告 warnings があるのに空なら 422。
- サーバ側でも warnings を再計算して**改ざん検証**する (クライアント値を盲信しない)。
  最低限「容量超過」「時刻重複」の赤2種はサーバで再判定し、赤があるのに
  `override_reason` 空なら拒否。

### A-3. 新規患者の枠配置 — `patient_create` 流用

既存 `_apply_patient_create` をほぼ流用。`proposed_visits` には**タップした1枠のみ**
(複数曜日選択ではなく、その枠の weekday/start_time/duration/course_template_id) を載せる。
新規患者は normal PFV が空なので「全削除→INSERT」でも問題なし (現行不変)。

### A-4. 既存患者の枠配置 — 新ハンドラ `patient_visit_add` (★重要)

`RequestType` に `PATIENT_VISIT_ADD = "patient_visit_add"` を追加
(`schemas/v2/enums.py`)。applier に `_apply_patient_visit_add` を追加し `_HANDLERS` に登録。

payload:
```
{ patient_id: UUID, patient_name: str, proposed_visits: [1枠], placement, warnings, override_reason }
```
挙動 (★`apply_proposed_visits_as_normal_pfv` をそのまま使うと既存 PFV を全消しするため不可):
1. 患者の既存 normal PFV を読む。
2. 既存 PFV を `[{weekday,start_time(HH:MM),duration_min,course_template_id}]` に変換。
3. 新規1枠を**マージ** (同一 weekday があれば置換、無ければ追加)。
   - 同一 weekday が既存にある場合は「時刻重複」相当 → 赤警告対象 (フロントで判定済の想定。
     サーバでも検出し override_reason 無しは 422)。
4. マージ後の配列で `apply_proposed_visits_as_normal_pfv(db, patient, merged)` を呼ぶ
   (PUT 同等の冪等上書き = 既存維持 + 1枠追加)。
- 患者が見つからない → 404。

### A-5. テスト

- `travel-estimate`: from_patient_id 解決 / lat-lng フォールバック / geocode 失敗 → null。
- `patient_visit_add`: 既存 PFV 維持 + 1枠追加 / 同一 weekday 置換 / 患者不在404 /
  赤警告 override_reason 空 → 422。
- `patient_create` + placement: warnings/placement が業務反映に影響しないこと。

⚠️ **本番 container での pytest は厳禁** (デプロイ memory)。テストはローカル/CI のみ。

---

## B. フロントエンド

### B-1. `onEmpty` に枠コンテキストを渡す

`FieldBoard.tsx` の `onEmpty` を引数付きに変更し、`CourseSlots`/`AgendaBoard` から
以下を伝播 (情報は既に手元にある):
```ts
interface SlotPlacementContext {
  officeId: string;
  officeName: string;
  courseId: string | null;        // BoardCourse.course_id
  courseCode: string;             // 拠点付きトークン (applier の course_code 規約に合わせる)
  courseLabel: string;
  weekday: number;                // 0=Mon..6=Sun (その日の曜日)
  gapStartMin: number;            // FreeGap.startMin
  gapEndMin: number;              // FreeGap.endMin
  prevVisit: { patientId: string; patientName: string; endTime: string } | null; // gap 直前 visit
  nextVisit: { patientName: string; startTime: string } | null;                  // gap 直後 visit
  capacityRemaining: number;      // co.capacity.remaining
  existingVisits: { startTime: string; endTime: string }[]; // 重複判定用 (そのコースの占有)
}
```
`courseCode` は applier の `parse_course_token` が解釈できる拠点付きトークン形式
(例 "稲A") にすること。BoardCourse の course_code が裸 "A" の場合は office_code を前置。
※ ここは既存 `propose` の `course_code` 規約 (`ProposeSlotItem.course_code`) を確認し
揃える。不明なら `courseId` (course_template_id) を主キーにし、code は表示用に留める。

### B-2. 新規 `PlacementSheet` コンポーネント (`FieldSheets.tsx` に追加)

- ヘッダ: 枠 (officeName / courseLabel / 曜日 / gap ラベル)。
- 開始時刻: 5分刻みステッパー。初期値ロジック:
  1. `prevVisit` があれば `travel-estimate` を呼び、`prevVisit.endTime + total_minutes`
     を5分切上げ → 初期値 (案2)。失敗/`prevVisit` 無し → `gapStartMin` (案1)。
  2. 範囲は `[gapStartMin, gapEndMin - duration]` にクランプ (案3)。
- タイムライン可視化: gap 帯の中に配置 visit を帯表示 (ワイヤー参照)。
- 患者入力: 新規/既存トグル。
  - 新規: `KarteFormSection` (氏名/コード/カナ/性別/保険) + 住所 + 性別制限 + 複数スタッフ
    + サービス時間 を流用。
  - 既存: `PatientLinkPanel` で患者検索・選択 (lat/lng/属性をプリフィル)。
- 警告帯 (`computePlacementWarnings` をフロントに新規):
  - 🔴 red: 容量超過 (`capacityRemaining<=0`) / 時刻重複 (`[start,start+dur]` が
    `existingVisits` と重なる)。→ **理由入力必須**で「警告を承知して強行」ボタン。
  - 🟡 yellow: 移動不足 (`start < prevVisit.end + total_minutes`) / 性別制限ミスマッチ /
    複数スタッフ必須だが未対応 / 営業枠/昼休み逸脱。→ **承知チェック**必須。
- 提出:
  - 新規 → `patient_create` payload (`buildPatientCreatePayload` 拡張) に
    proposed_visits=[1枠] + placement + warnings + override_reason。
  - 既存 → `patient_visit_add` payload (新規ビルダ) に patient_id + proposed_visits=[1枠]
    + placement + warnings + override_reason。
  - `useCreatePendingRequest` で送信 → 成功トースト → close。

### B-3. `patientCreate.ts` 拡張

- `buildPlacementMeta(ctx, startTime, durationMin)` → placement オブジェクト。
- `buildPatientCreatePayload` に placement/warnings/override_reason を追加 (任意)。
- 新規 `buildPatientVisitAddPayload(patientId, patientName, proposedVisit, placement, warnings, reason)`。
- 純関数で単体テスト。

### B-4. `ApprovePanel.tsx` 強化

- `placement` を持つ申請 (patient_create / patient_visit_add) で枠座標カードを描画:
  拠点 / コース / 曜日 / 開始時刻・所要、prev/next visit (誰の隣か)、warnings を
  色付き (赤/黄) で転記、override_reason があれば表示。
- `REQUEST_TYPE_LABEL_JA` に `patient_visit_add: '訪問追加'` を追加。
- `patient_visit_add` の `PatientCreatePreview` 相当 (患者名 + 枠) を出す。

### B-5. テスト

- `patientCreate` ビルダ (placement/visit_add payload 構造)。
- `computePlacementWarnings` (赤/黄判定の境界)。
- `PlacementSheet` 主要動作 (開始時刻クランプ / 赤は理由必須 / 提出 payload)。
- `ApprovePanel` placement 描画。

---

## 警告判定ルール (フロント=サーバ 共通の真実)

| level | code | 条件 | UI 挙動 |
|---|---|---|---|
| 🔴 red | `capacity_full` | `capacityRemaining <= 0` | 理由必須・強行ボタン |
| 🔴 red | `time_overlap` | 選択枠が既存 visit と時刻重複 | 理由必須・強行ボタン |
| 🟡 yellow | `travel_shortage` | `start < prevEnd + travel + buffer` | 承知チェック必須 |
| 🟡 yellow | `sex_restriction` | コース/担当の性別制限と不一致 (判定可能なら) | 承知チェック必須 |
| 🟡 yellow | `multi_staff` | `requires_multiple_staff` だが単独想定 | 承知チェック必須 |
| 🟡 yellow | `outside_hours` | 営業枠 (09:30-12:00/13:00-18:00) 逸脱・昼休み跨ぎ | 承知チェック必須 |

サーバは最低限 `capacity_full` / `time_overlap` の赤2種を再判定し、赤があるのに
`override_reason` 空なら 422。

## 未確定 (ユーザーに最終確認する点)

- **既存患者の枠配置は「恒常的 normal PFV (毎週)」として扱う** (既存 propose→PFV 基盤に
  合わせた既定)。「その週だけの単発」ではない。要レビュー。
