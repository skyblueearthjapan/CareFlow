# Phase G-86: 患者コードの自動採番（必須→任意）

## 背景 / 要望
新規患者登録で `患者コード` が手入力**必須**になっており、現場で真新しい患者を登録する際に
摩擦になっている。コードを**任意**化し、空欄なら**サーバが自動採番**する。
適用範囲: **現場ボード `/m` の新規患者フロー** ＋ **メインアプリの患者登録フォーム** の両方。

## 確認済みの事実
- DB は単一 (現場ボードもメインアプリも同一 Postgres・同一 backend)。GAS 等の外部連携や
  自動取り込みは無し (`P002`〜`P094` は過去の一度きりの移行取り込みの名残)。
- `patient.code`: `String(32) unique NOT NULL` (`models/patient.py:61`)。
- 本番の既存コード形式: **`P` + 3桁ゼロ埋め** (`P002`,`P046`,`P088`,`P094`…)。現最大 `P094`、
  番号は飛び飛び (移行由来)。
- 既存に採番ロジックは**無い** (メインアプリ `PatientForm.tsx` も手入力必須)。
- 書き込み: 現場の新規患者/枠配置は `pending_request → 承認` 経由、カルテ編集は直接 PATCH。
  メインアプリの患者登録は直接 `POST /api/v1/patients`。

## 採番仕様
- 形式: `P` + ゼロ埋め3桁 (`f"P{n:03d}"`)。n≥1000 は自然桁 (`P1000`)。
- 次番号 n: **全 `patients` 行 (deleted_at 問わず)** のうち `^P(\d+)$` に一致するコードの
  数値部の最大 + 1。一致が無ければ `1` (=`P001`)。
  ※ deleted も含めるのは unique 制約が soft-delete 行にも効くため (衝突回避)。
- 一意性/並行性: 採番→INSERT を同一 TX 内で行い、`IntegrityError`(unique 衝突) 時は
  数回リトライ (max+1 を再計算)。

---

## A. バックエンド

### A-1 共通ヘルパ
`backend/app/services/patient_code.py` (新規) に:
```python
async def generate_next_patient_code(db: AsyncSession) -> str
```
- `select(Patient.code)` 全行 (deleted_at フィルタ無し) から `^P(\d+)$` 一致を抽出し
  数値最大+1 を `f"P{n:03d}"` で返す。`importer.py:_load_patient_codes` の lookup を参考に。
- 純粋に「次コード文字列」を返すだけ (INSERT はしない)。単体テスト可能に。

### A-2 承認フロー (現場の新規患者)
`pending_request_applier.py:_apply_patient_create`:
- 現状 `if not code or not name: raise(...code and name required)` を変更。
  - `name` は必須のまま。
  - `code` が空/None なら `code = await generate_next_patient_code(db)` で採番。
- INSERT (flush) で `IntegrityError` (code unique 衝突) のとき、採番からやり直すリトライ
  (最大 3〜5 回)。手入力コードが既存と衝突した場合は従来どおり 409/422 (採番ではなく
  ユーザー入力エラーなので、リトライは「自動採番した場合のみ」)。

### A-3 メインアプリ直接登録
`backend/app/api/v1/patients.py` の create_patient (`POST /api/v1/patients`):
- payload.code が空/None なら `generate_next_patient_code` で採番してから INSERT。
- 同様に自動採番時のみ unique 衝突リトライ。

### A-4 スキーマ
`backend/app/schemas/v2/patient.py`: 患者**作成**スキーマの `code` を任意化
(`code: str = Field(min_length=1,...)` → `code: str | None = Field(default=None, max_length=64)`)。
空文字は None 扱い。Read/Update スキーマや他用途を壊さないよう、Create 変種のみ緩める
(継承構造を確認し、Base を緩めて他に波及するなら Create だけ override)。

### A-5 テスト
- `generate_next_patient_code`: `P094` 群 → `P095`／飛び番でも max+1／`P` 系が無ければ `P001`／
  deleted 行のコードも考慮／3桁ゼロ埋め・1000 超の桁。
- `_apply_patient_create`: code 空 → 自動採番で患者作成 (proposed_visits 併用時も)。
  code 指定あり → そのまま。自動採番が既存と衝突 → リトライで別番。name 空は従来どおり 422。
- `POST /patients`: code 空 → 自動採番。code 指定 → そのまま。
- ⚠️ 本番 container で pytest 厳禁 (ローカルのみ)。

---

## B. フロントエンド

### B-1 現場ボード新規患者フロー (`components/field/FieldSheets.tsx`)
- SuggestSheet `canCreate` (1254 付近) から `karteCode.trim().length > 0` 要件を**外す**
  (氏名 + 採用枠は維持)。`submitKarte` の患者コード必須 toast/guard を外す。
- PlacementSheet の新規患者 submit ゲート (2876 付近) からも患者コード必須を外す。
- ラベル `患者コード（必須）` → `患者コード（任意・空欄で自動採番）`。placeholder 調整。
- payload: `buildPatientCreatePayload` 等で code が空のとき空文字/省略で送る (backend が採番)。
  ※ `lib/field/patientCreate.ts` の `code: karte.code.trim()` が空文字を送れば backend 採番が効く。

### B-2 メインアプリ患者登録 (`app/(app)/patients/_components/PatientForm.tsx`)
- code 欄を任意化。`frontend/lib/schemas/patient.ts` / `frontend/lib/schemas/v2/patient.ts` の
  `code: z.string().min(1, 'コードは必須です')` を任意化 (`.optional()` か `.min(0)` + 空許容)。
- ラベル/placeholder を「任意・空欄で自動採番」に。送信時 code 空なら省略/空で送る。

### B-3 テスト
- 現場: code 空でも「提案を作成」/配置提出が活性化・送信 payload に空 code (or 省略)。
- メインアプリ: code 空で送信できる (バリデーションが通る)。
- 既存 (code 入力あり) は従来どおり。

---

## スコープ外
- Excel 取り込み (importer) は対象外 (コードは取り込み元が持つ)。
- 既存患者のコード一括振り直し。
- 採番フォーマットの設定化 (固定 `P`+3桁)。
