# 患者様・スタッフの連絡先（電話番号とメール）設計 — 2026-11-05

PO 了承済みの見本：`docs/mockups/contact-phone-email-mock-2026-11-05.html`（「決まったこと」タブが正典）。

## 1. 目的
予定のお知らせ（SMS＝KDDI Message Cast／携帯がない方はメール）に向けて、患者マスターとスタッフマスターに連絡先を持たせる。**今回は欄と記録・取り込みまで。送信はしない**（送信の仕組みは別設計）。

## 2. 決まり（PO 11-05）
- 患者様の連絡先は 0〜3 件。送り先の種類 `relation`：`self`（ご本人）／`family`／`facility`／`care_manager`／`other`。
- `self` は患者 1 人に 1 件まで。お名前は持たない（画面は「本人（〇〇 様）」と自動で出す）。
- 「お知らせを送る」`is_notify_target` は連絡先ごと。複数に付けられる。
- **連絡先が 1 件以上ある患者は、送る先が少なくとも 1 件**。0 件になったら自動で移す：NG 回答でない連絡先のうち、ご本人を先に、次に送れる手段（SMS かメール）がある連絡先、同じなら並び順。
- 手段 `notify_channel`：`sms`／`email` だけ（「自動」「送らない」は無い）。選んだ手段が使えなければ、使える方へ落とす（`effective_channel`：携帯があれば sms、無くてメールがあれば email、どちらも無ければ none）。
- 同意 `consent_status`：`unconfirmed`（未確認）／`confirmed`（確認済み）／`ng`（NG 回答）。**画面の文言に「断られた」を使わない**。方法を書く欄は無い。`consent_on`（日付）はサーバーが自動で入れる：状態が変わって confirmed/ng になった日（JST の今日）、unconfirmed に戻ったら null、変わらなければそのまま。
- NG 回答の連絡先は `is_notify_target=false` に強制（その結果 0 件なら上の移し方で移す）。
- 連絡先は**管理者だけ**が見て編集する。看護師（staff ロール）の API 応答・スマホには出さない。
- スタッフ：電話 1 つ・連絡用メール 1 つ（ログイン用の users.email とは別）・`notify_channel`：`sms`／`email`／`none`（初期 none・今は使わない）。見られるのは管理者とご本人。

## 3. 電話番号の見分け（backend と frontend で同じ決まり）
入力を NFKC で半角へ → 全角ハイフン類を `-` に → `+81`（空白・ハイフン可）で始まれば先頭を `0` に → 数字だけ `digits`。

| digits | kind | sms_capable | phone_display |
|---|---|---|---|
| `0[789]0` + 8 桁（11 桁・`0800` を除く） | `mobile` | true | 3-4-4 |
| `060` + 8 桁 | `mobile_new` | null（未確認・2026-11 時点で未開始） | 3-4-4 |
| `050` + 8 桁 | `ip` | false | 3-4-4 |
| `020` + 8 桁 | `data` | false | 3-4-4 |
| `0120`+6 桁／`0800`+7 桁（**携帯より先に判定**） | `freedial` | false | 4-3-残り |
| `0[1-9]` + 8 桁（10 桁・上以外） | `landline` | false | 入力にハイフンがあればその位置（区切りの数字を除いた並びが digits と一致するとき）、無ければ digits のまま |
| それ以外 | エラー（桁が足りない／多い／形が違う） | — | — |

- 空文字・null は「番号なし」。
- 「SMS が使える」＝ `sms_capable is not False` かつ番号が正しい（060 は送れる扱いにはするが印は「確認中」）。※送信の実装時に KDDI 仕様で確かめる。
- メール：小文字・前後空白除去、`^[^\s@]+@[^\s@]+\.[^\s@]+$`。携帯会社のドメイン（docomo.ne.jp・ezweb.ne.jp・au.com・softbank.ne.jp・i.softbank.jp・ymobile.ne.jp）は `email_carrier=true`（画面で「迷惑メールの設定で届かないことがあります」）。

## 4. DB（alembic 0109_contacts）
### 新しい表 `patient_contacts`
| 列 | 型 | 備考 |
|---|---|---|
| id | uuid PK | |
| patient_id | uuid FK patients.id ON DELETE CASCADE, index | |
| sort_order | smallint not null | 0,1,2 |
| relation | varchar(16) not null | self/family/facility/care_manager/other |
| name | varchar(120) null | self は null |
| relation_note | varchar(120) null | 続柄・施設名 |
| phone | varchar(16) null | digits |
| phone_display | varchar(20) null | |
| phone_kind | varchar(16) null | 上の kind |
| email | varchar(255) null | |
| is_notify_target | bool not null default false | |
| notify_channel | varchar(8) not null default 'sms' | sms/email |
| consent_status | varchar(16) not null default 'unconfirmed' | |
| consent_on | date null | |
| source | varchar(16) not null default 'manual' | manual/excel |
| created_at/updated_at | TimestampMixin | |

- 部分一意 index：`(patient_id) WHERE relation='self'`（self は 1 件）。CHECK で relation・notify_channel・consent_status の値。
### `staff` に列を足す
`phone` varchar(16) null・`phone_display` varchar(20) null・`phone_kind` varchar(16) null・`contact_email` varchar(255) null・`notify_channel` varchar(8) not null default 'none'（CHECK sms/email/none）。

## 5. API
### 患者の連絡先（新ルーター `app/api/v1/patient_contacts.py`、すべて `require_role("admin")`）
- `GET /api/v1/patients/contacts/summary` → `{"items":[{"patient_id","contact_count","targets","sms","email","no_means","pending_consent","ng"}]}`。**`/{patient_id}` より先に登録**。
  - targets＝送る先の数、sms/email＝送る先のうち同意済みで effective_channel がそれぞれの数、no_means＝送る先で手段なし、pending_consent＝送る先で未確認、ng＝NG 回答の連絡先の数。
- `GET /api/v1/patients/{patient_id}/contacts` → `{"contacts":[ContactRead…]}`（sort_order 順）。
- `PUT /api/v1/patients/{patient_id}/contacts` body `{"contacts":[ContactWrite…]}` → 全部置きかえ。返り値は GET と同じ形＋ `"notices":[str]`（自動で移したときの日本語の知らせ）。
  - ContactWrite：`id?`（既存の連絡先なら付ける＝同意の日を引き継ぐため）, `relation`, `name?`, `relation_note?`, `phone?`（生の入力）, `email?`, `is_notify_target`, `notify_channel`, `consent_status`。
  - 422：4 件以上／self が 2 件／電話・メールの形が違う（`detail` に何件目のどの欄か日本語で）。
  - ContactRead：`id, sort_order, relation, name, relation_note, phone, phone_display, phone_kind, sms_capable, email, email_carrier, is_notify_target, notify_channel, effective_channel, consent_status, consent_on`。
- 削除済み患者は 404。監査ログ（audit_log）に「連絡先を変えた」を残す。**番号・メールそのものは書かない**（件数と変わった欄名だけ）。
- 共通の決まりは `app/services/contacts.py`（電話・メールの見分け、`normalize_patient_contacts(rows, previous)` で 0 件対策・NG の強制・consent_on を決める）に置き、API と Excel の両方から使う。

### スタッフ
- `StaffCreate/StaffUpdate` に `phone?`, `contact_email?`, `notify_channel?` を足す（電話・メールは同じ見分けで 422）。
- `StaffRead` に `phone, phone_display, phone_kind, sms_capable, contact_email, email_carrier, notify_channel` を足す。**管理者かご本人（users.staff_id が一致）でないときは null で返す**。

## 6. 患者マスターの Excel（今ある書き出し・取り込み）
- シート「連絡先」を足す。列：`patient_id (※システム用・編集不要)`／`patient_code`／`患者名`（見るだけ）／`送り先`（ご本人・ご家族・施設・ケアマネ・その他）／`お名前`／`続柄・施設名`／`電話番号`／`メールアドレス`／`お知らせを送る`（はい・いいえ）／`手段`（SMS・メール）／`同意`（未確認・確認済み・NG 回答）／`同意の日 (※自動・編集不要)`。ドロップダウンを付ける。
- 書き出し：連絡先がある方はその行、無い方はご本人の空の行を 1 行（お知らせを送る＝はい）。
- 取り込み：シートが無いファイル（古い形）は連絡先に触らない。`patient_id` でつなぐ。電話・メール・お名前・続柄が全部空の行は飛ばす。飛ばした後に行が 1 件も無い方は、今の連絡先を変えない（Excel では消さない）。行がある方は `normalize_patient_contacts` を通して置きかえ。同意の日は、同じ方の今の連絡先で電話かメールが同じものの状態が同じなら引き継ぎ、それ以外で confirmed/ng なら今日。
- 行の誤り（番号の形・4 件以上・self が 2 つ・知らない patient_id）は、今の取り込みの「確認（preview）」の誤りの一覧に出し、その方の連絡先は取り込まない。
- source='excel'。

## 7. 画面（frontend）
- `lib/contacts/phone.ts`：3 章の見分けを TS で（backend と同じ表の試験を書く）。
- 患者の編集（`PatientForm`）：「住所」の下に「連絡先」カード（見本のとおり：要約の帯＋「ご本人だけ」「登録した全員に送る」、連絡先ごとの四角・送り先・お名前（self は「本人（氏名 様）」で入力不可）・電話・メール・手段の 2 択・同意の選択＋「11/5 に確認（自動で記録）」、3 件まで、0 件にしない移し方と知らせ、外す）。保存は患者の保存が成功した後に PUT。新規作成の画面でも入れられる（作成後に PUT）。管理者だけに出す。
- 患者の詳細：連絡先の表（見本の「患者の詳細・一覧」）。管理者だけ。
- 患者の一覧：管理者だけ「お知らせ」列（SMS N 件／メール／同意を確認中／送り先なし／NG 回答）と、「送り先なし」「同意を確認中」で絞る。summary API を 1 回呼ぶ。
- スタッフの編集：「連絡先」（電話・連絡用メール・お知らせ SMS／メール／受け取らない＋「今は使いません」の説明＋ログイン用とは別の説明）。
- 文言は「合わせる／調整」。「断られた」「直す／修正」を使わない。

## 8. 試験の観点
- 電話の見分け：表の全行＋全角・+81・空白・桁違い（py と ts で同じ例）。
- normalize：最後の 1 件を外す→ご本人へ移る／ご本人が NG なら次へ／移す先が無い（1 件だけで外す）→その 1 件を送る先に残す／NG は送る先から外れる／consent_on の付け方（変わった・同じ・戻した・id 引き継ぎ）。
- API：admin 以外 403、4 件 422、self 2 件 422、削除済み 404、summary の数、`/contacts/summary` が `/{patient_id}` に食われない。
- staff：admin・本人には出る、他のスタッフには null。
- Excel：書き出しにシートがある・空の self 行／取り込みで置きかえ・空行は飛ばす・古い形は触らない・誤りは preview に出る・往復で変わらない。
- 画面：連絡先カードの移し方・同意の日の表示・管理者以外に出ない。

## 9. リスク
- 個人情報が増える：管理者だけ・監査ログに値を書かない・試験で実在の番号を使わない（`090-0000-0000` のような見本だけ）。
- 患者の保存と連絡先の保存が別の呼び出し：連絡先だけ失敗したら、その旨を出して画面に残す（入力を消さない）。
- 本番反映は migration 0109 を手で当てる（PO の了承の後）。
