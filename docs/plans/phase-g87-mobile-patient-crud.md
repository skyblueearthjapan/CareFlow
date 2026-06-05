# Phase G-87: 現場ボードから提案を介さない患者 新規登録 / 検索編集

## 背景 / 要望
モバイル現場ボード `/m` から、**提案(SuggestSheet)を介さずに** 患者カルテの
**新規登録**・**既存患者の編集**を容易にしたい。

## 現状
- 既存編集: 患者カードタップ→`KarteSheet`→「編集」→`KarteEditSheet`（**直接 PATCH・承認なし**、
  manager/admin のみ）。ただし**今日のボードに表示中の患者しか入口が無い**。
- 新規登録: ヘッダ「提案」→ SuggestSheet で住所/希望入力→**枠採用**→ pending_request→**承認**
  しか経路が無い（枠採用＋承認が必須）。
- G-86 で患者コードは任意（空欄で自動採番 P095…）。POST/PATCH /patients は実装済。

## 確定方針（ユーザー合意）
- **新規登録は直接（承認なし）**: `POST /api/v1/patients`（既存 `useCreatePatient`）。既存編集の
  直接 PATCH と一貫。氏名のみ必須、コードは任意（空欄で自動採番）。
- **範囲: 新規登録 ＋ 患者検索編集の両方**。今日のボードに居ない患者も検索して編集可能に。
- 本フェーズは **フロントのみ**（バックエンド変更なし）。

---

## 実装（フロントエンド）

### B-1 KarteEditSheet を create/edit 両対応に一般化
`frontend/components/field/FieldSheets.tsx` の `KarteEditSheet`:
- props に `mode?: 'create' | 'edit'`（既定 'edit'）を追加し、`patient?: PatientRead`（create では無し）に。
- **edit モード**: 現状維持（`useUpdatePatient(id, initial)` で PATCH、初期値 `patientReadToFormValues(patient)`）。
- **create モード**:
  - 初期値 = 空のデフォルト `PatientFormValues`（メインアプリ新規登録 `frontend/app/(app)/patients/new/page.tsx` 等の既定値を流用。無ければ `patient.ts` のデフォルト生成を再利用）。
  - 保存 = `useCreatePatient()` で POST。成功で onSaved＋トースト「✓ 患者を登録しました」。
  - ヘッダ「新規患者を登録」、ボタン「登録」。
  - **患者コード欄を任意化**（「任意・空欄で自動採番」）。※edit モードは現状の必須維持でよい
    （既存患者は必ずコードを持つ）。create のときだけ任意。
  - 住所→ジオコード/拠点解決の best-effort は edit と同様に流用（任意）。
- 氏名空欄はトーストで弾く（両モード共通）。コード必須チェックは create では外す。

### B-2 新規 PatientManageSheet（新規登録＋検索編集ハブ）
`FieldSheets.tsx` に `PatientManageSheet` を追加（または同等の新ファイル）:
- 最上部に「**＋ 新規患者を登録**」ボタン → `KarteEditSheet` を create モードで開く。
- 患者検索ボックス（氏名/カナ/コード部分一致）→ `usePatients({ search, limit })`（既存）で候補表示。
  `PatientLinkPanel`/`SuggestSheet` の検索 UI を参考に。
- 候補タップ → `usePatient(id)` で `PatientRead` を取得 → `KarteEditSheet` を edit モードで開く
  （`KarteSheet` が患者詳細を取得しているのと同じ経路）。
- 戻る/閉じるで一覧へ。Warm パレット（`theme.ts`/既存 Sheet 様式）を踏襲。

### B-3 入口（ヘッダ）
`frontend/components/field/FieldBoard.tsx`:
- ヘッダに「**患者**」ボタンを追加（`canEditKarte`=manager/admin のときのみ表示。`提案`/`承認` と並べる。
  375px 幅で収まるようコンパクトに。アイコン例 `UserPlus`/`Users`）。
- 押下で `PatientManageSheet` を開く state（`manage`）を追加し、scrim/レンダを既存 `karte`/`sheet`/
  `placement` と同様に配線。
- 既存の「提案」「承認」「空き枠タップ→PlacementSheet」「カードタップ→KarteSheet」は不変。

### B-4 テスト
- KarteEditSheet create モード: 氏名のみで登録できる（コード空→POST 送信、auto-number 前提）／
  edit モードは従来どおり PATCH。
- PatientManageSheet: 新規登録ボタン→create、検索→候補→タップ→edit が開く。
- FieldBoard: 患者ボタンが manager/admin で表示・staff で非表示、開閉。

---

## 非対象（スコープ外）
- staff への編集/登録権限付与（manager/admin 限定を維持）。
- 患者の論理削除 UI（既存 admin 機能のまま）。
- 登録後の枠配置（PFV）は別途、空き枠タップ/提案/自動割当で行う（登録と配置を分離）。
- バックエンド変更（POST/PATCH /patients・コード自動採番は G-86 で実装済）。
