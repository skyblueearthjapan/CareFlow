# W-7 地域ルールの学習 設計書 v1

作成 2026-07-04 / 前提: `docs/plans/pool-bulk-insert-HANDOFF.md`（W-6 拠点調査）・
メモリ careflow-office-region-model（主担当拠点=正典 / 担当エリア=入口のヒント）。PO 承認済み。

## 0. 目的と設計原則

未カバー地域（どの拠点の担当エリアにも紐付いていない市区町村）を、**ユーザーの自然な操作から学ぶ**。
地図の完成を要求しない・脅迫的警告を作らない。

> ユーザーが患者登録で「拠点エリア外」の住所に手動で拠点を選んだ**その瞬間**に、一度だけ、
> 断れる形で「この地域を担当エリアに登録しますか？」と聞く。断られたら**地域単位で記憶して二度と聞かない**。

禁止事項（PO 明言）: グローバルバナー・赤い常時警告・繰り返しトースト・保存のブロック。

## 1. 発火条件（FE: PatientForm）

以下がすべて成立したとき、主担当拠点フィールドの直下に小さなインライン呼びかけを表示する:

1. 住所の resolve 結果が `confidence='none'`（= どの拠点の担当エリアにもマッチしない）
2. ただし住所から**市区町村（City）自体は特定できた**（`matched_city` が返る — §2）
3. その City が**未却下**（`prompt_dismissed=false`）
4. ユーザーが主担当拠点を**手動で選択した**（officeMode='manual' かつ値あり）

表示例:
「この地域（千葉市美浜区）は、まだどの拠点の担当エリアにも登録されていません。
**〇〇拠点の担当地域として登録**しますか？ 次からこの地域の患者様は自動で振り分けられます。
**[担当地域に登録する] [今回だけ]**」

- **登録する** → §3 の追加 API を呼び、成功トースト「千葉市美浜区を〇〇拠点の担当地域に登録しました。次からこの地域は自動で振り分けられます。」→ 呼びかけを閉じる
- **今回だけ** → §3 の却下 API を呼び、静かに閉じる。**その City については全ユーザー・全画面で二度と聞かない**（組織の運用判断として記憶）
- 何も押さずに保存 → 何も記憶しない（次の同地域患者で再度出る。明示的に断られていないため）
- 対象画面: PatientForm を使う全箇所（患者マスタ新規/編集・スケジュール画面の CreatePatientDialog）。現場ボード（FieldSheets）は v1 対象外

**City が特定できない住所**（表記ゆれ・マスタ外の市区町村）は呼びかけ自体を出さない（学べないものは聞かない）。将来課題としてバックログに記録。

## 2. API 契約

### 拡張: POST /api/v1/offices/resolve（既存）

`confidence='none'` のとき、レスポンスに追加（後方互換 — 既存フィールドは不変）:

```jsonc
{
  "office_id": null, "office_name": null, "confidence": "none",
  "matched_city": { "id": "…", "name": "千葉市美浜区", "prefecture": "千葉県" } | null,
  "prompt_dismissed": false   // matched_city が非null のときのみ意味を持つ
}
```

matched_city の判定 = OfficeAssigner の既存住所パース（都道府県検出→市区町村名の最長一致）を
**office_cities 紐付けの有無に関係なく** cities 全体に対して行う（既存ロジックの拡張・コピー禁止）。

### 新設: POST /api/v1/offices/{office_id}/area-cities

```jsonc
// Request:  { "city_id": "…" }
// Response: { "office_id": "…", "city_id": "…", "city_name": "…" }
```

- office_cities に **1件追加**（既存の全置換 PUT とは別の additive API — 患者登録の途中で
  拠点フォーム全体を触らせないため）。既に紐付済みなら 200 冪等。
- その City に**別拠点が既に紐付いている場合も追加を許す**（複数拠点担当は既存モデルで合法。
  自動判定は created_at 先勝ちの既存仕様のまま）。RBAC: admin/manager。
- 追加成功時、その City の却下記憶があれば**削除**（登録されたのに「聞かない」記憶が残ると
  将来のエリア解除時に混乱するため）。

### 新設: POST /api/v1/offices/area-prompt-dismissals

```jsonc
// Request:  { "city_id": "…" }
// Response: 204
```

冪等（既に却下済みでも 204）。RBAC: admin/manager。

## 3. データ（migration 0053）

新テーブル `office_area_prompt_dismissals`:

| 列 | 型 | 制約 |
|---|---|---|
| id | UUID | PK |
| city_id | UUID | FK cities.id・**UNIQUE**（組織全体で City ごとに1行） |
| created_at | timestamptz | server_default now() |

- migration 規約: 単一 head 維持・PG/SQLite 両対応・制約は命名規約の実名・ヘッド名をテストで固定しない
- 削除想定: area-cities 追加時に該当行 DELETE（§2）。管理 UI は作らない（v1）

## 4. 非ゴール・バックログ

- City 自体の自動作成（マスタ外の市区町村）— v1 は呼びかけを出さないだけ
- 担当エリア変更時の既存患者への影響プレビュー（「判定が変わる患者 N名」）— 別バックログ
- 却下記憶の管理画面（一覧・取り消し）— 要望が出たら。取り消しは当面 DB 直接 or エリア登録で自動解除
- FieldSheets（現場ボード）への展開

## 5. テスト要点

- BE: resolve が matched_city/prompt_dismissed を正しく返す（カバー済み地域では従来レスポンス不変）/
  area-cities の冪等・却下記憶の自動削除・RBAC / dismissals の冪等・UNIQUE
- FE: 発火4条件（confidence=none × matched_city × 未却下 × 手動選択）の組合せ /
  登録する→API→トースト→閉じる / 今回だけ→API→閉じる / カバー済み地域では出ない
