# SMS のお知らせ — 段階 2 の API の決まり（backend と frontend の共通の約束）— 2026-11-16

設計: `sms-notify-design-2026-11-16.md`。段階 1 の API（`/sms/week-notices/preview`・`/send`・`/sms/batches`・`/sms/messages`・`/sms/messages/{id}`）は `backend/app/api/v1/sms.py` が正。
ここは段階 2 で足す分。prefix はすべて `/api/v1/sms`。管理者だけ（403）。旗 `sms_notify` が ON の拠点が 1 つも無ければ 404（`GET /status` だけは 200 で `enabled:false` を返す）。
日時は ISO 8601（UTC・`Z`）。日は `YYYY-MM-DD`。時刻は `HH:MM`。番号は一覧では末尾 4 桁だけ。

## 0. 設定に足すもの（backend）
- `SMS_SEND_MODE`: `test`（既定）／`live`。`test` のあいだは `SMS_TEST_ALLOWLIST` にある番号にだけ実際に送る（一覧が空なら 1 件も送らない・`skipped`／`skip_reason=test_mode`）。`live` は全員に送る。
- `SMS_STATION_SHORT_NAME`: 変更のお知らせの頭の【 】に入れる短い名前（空なら `business_profile.station_name`）。

## 1. `GET /status`
週の画面のボタンと印に使う。
```json
{"enabled": true, "provider": "mock", "send_mode": "test", "test_allowlist_count": 0,
 "within_send_hours": true, "send_hours": "8:00〜20:00",
 "pending_count": 3, "urgent_count": 1, "failed_recent_count": 0}
```
- `enabled:false` のときはほかの項目を返さなくてよい。`failed_recent_count`＝直近 7 日の届かなかった件数。

## 2. `GET /changes/pending`
お伝えした内容と今の予定が違う方（今日以降の日だけ）。
```json
{"within_send_hours": true, "send_hours": "8:00〜20:00", "count": 3, "urgent_count": 1,
 "items": [{
   "patient_id": "uuid", "patient_name": "見本 太郎", "week_start": "2026-11-30",
   "urgent": true, "detected_at": "2026-11-30T01:05:00Z", "waiting_minutes": 42,
   "changes": [{"kind": "time", "from": {"date": "2026-12-03", "start": "14:00"}, "to": {"date": "2026-12-03", "start": "15:30"}, "label": "12/3(木)14:00→15:30"}],
   "sendable": true, "not_sendable_reason": null, "not_sendable_label": null,
   "destinations": [{"contact_id": "uuid", "relation": "self", "relation_label": "ご本人", "phone_last4": "0001"}],
   "call_numbers": [{"relation_label": "ご本人", "phone_display": "090-0000-0001"}],
   "body": "【…】見本様 ご訪問の変更です。…", "char_count": 64, "segments_est": 1, "est_yen": 11
 }]}
```
- `changes[].kind`: `time`（同じ日の時刻の変更）／`date`（日の変更）／`cancel`（お休み・`to` は null）／`add`（追加・`from` は null）。
- `urgent`＝変更に今日か明日（日本時間）の訪問が関わる。
- `call_numbers`＝お電話でお伝えするときの番号（管理者だけの画面なので全桁）。お知らせを送る先でなくても、その方の連絡先で電話のあるものをすべて。
- 送れない方（`sendable:false`）も一覧に出す（理由は段階 1 と同じ `no_contact`・`consent_unconfirmed`・`consent_ng`・`not_mobile`・`too_long`）。

## 3. `POST /changes/send`
要求 `{"items": [{"patient_id": "uuid", "week_start": "2026-11-30"}], "expected_count": 2}`（ほかの項目は 422）。
応答 `{"batch_id": "uuid", "patient_count": 2, "message_count": 2, "segments_est_total": 2}`。
- 本文はサーバーで作り直す。送れる時間の外は 422、件数が違えば 409（「…一覧を開き直してください」）、送れる方が 0 なら 422。
- 送ると、その方の「お伝えした内容」が今の予定に替わる（手段 sms）。

## 4. `POST /changes/resolve`
SMS を送らずに「お伝え済み」にする。
要求 `{"patient_id": "uuid", "week_start": "2026-11-30", "channel": "phone", "reason": "phoned", "note": ""}`
- `channel`: `phone`（お電話でお伝えした）／`none`（お知らせしない）。
- `reason`: `phoned`（お電話でお伝えした）／`patient_request`（ご本人・ご家族からのお申し出）／`other`（その他・`note` 必須・200 字まで）。
応答 `{"ok": true, "version": 3}`。その方の「お伝えした内容」が今の予定に替わり、だれがいつ・手段・理由が残る。

## 5. cron から呼ぶ口（管理者のトークン・POST・本文なし）
- `POST /changes/detect/run` → `{"pending": 3, "urgent": 1, "new": 1, "bells": 1}`。変更を見つけて気づいた時刻を残し、ベルを出す（初回・60 分後・急ぎは 15 分後・同じ内容で重ねない）。
- `POST /send-due/run` → `{"sent": 2, "stuck_fixed": 0}`。送る時刻の来た分を送る。`sending` のまま 10 分たった行は `unknown` にする。
- `POST /status/poll/run` → `{"polled": 5, "delivered": 4, "failed": 1}`。届いたかの照会（送ってから 1 分・5 分・30 分・3 時間・24 時間・72 時間）。届かなかったらベル。
- 旗が OFF のときは 3 つとも 200 で `{"skipped": "disabled"}`（cron が赤くならないように）。

## 6. `GET /summary?month=YYYY-MM`
```json
{"month": "2026-12",
 "totals": {"messages": 320, "delivered": 300, "failed": 8, "unknown": 2, "pending": 6, "skipped": 4,
            "delivered_rate": 0.9375, "segments": 640, "est_yen": 7040},
 "by_kind": [{"kind": "week", "messages": 300, "segments": 610, "est_yen": 6710}, {"kind": "change", "messages": 20, "segments": 30, "est_yen": 330}],
 "by_carrier": [{"carrier": "au", "messages": 100, "delivered": 96, "failed": 3}],
 "by_fail_reason": [{"reason": "ESME_REGULATED", "label": "受信の設定で受け取れませんでした", "count": 2}],
 "by_segments": [{"segments": "1", "messages": 40}, {"segments": "2", "messages": 250}, {"segments": "3+", "messages": 30}],
 "changes": {"count": 22, "urgent_count": 5, "median_minutes_to_tell": 35,
             "by_channel": [{"channel": "sms", "count": 18}, {"channel": "phone", "count": 3}, {"channel": "none", "count": 1}]},
 "by_week": [{"week_start": "2026-11-30", "messages": 80, "delivered": 76, "failed": 2}]}
```
- 月は「作った日（日本時間）」で数える。`segments`＝`split_num` があればそれ、無ければ `segments_est`。`est_yen`＝届いた分の通数 × 11（見込み）。`carrier` が分からない分は `"unknown"`。`delivered_rate`＝届いた ÷（届いた＋届かなかった＋分からない）。分母が 0 なら null。

## 7. `GET /summary/export?month=YYYY-MM`
その月の 1 通ごとの一覧を Excel（`.xlsx`）で返す（日時・患者様・続柄・番号の末尾 4 桁・種類・状態・通数・キャリア・届かなかった理由・送った人）。本文は入れない。

## 8. `GET /patients/summary?month=YYYY-MM`
```json
{"month": "2026-12", "items": [{"patient_id": "uuid", "patient_name": "見本 太郎", "messages": 5, "segments": 10,
  "delivered": 5, "failed": 0, "last_delivered_at": "2026-12-01T00:10:00Z", "told_by_phone": 1}]}
```
## 9. `GET /patients/{patient_id}/recent?limit=5`
患者様の詳細の画面用。`{"items": [段階 1 の /messages の items と同じ形]}`。

## 10. `GET /numbers/attention`
```json
{"items": [{"patient_id": "uuid", "patient_name": "見本 太郎", "relation_label": "ご家族", "phone_last4": "0002",
  "kind": "repeated_failure", "kind_label": "続けて届いていません", "count": 2, "last_at": "2026-12-01T00:10:00Z"}]}
```
- `kind`: `repeated_failure`（直近 2 回続けて届かない）／`not_in_use`（使われていない番号＝キャリアが `sms`）／`blocked`（受信の設定で受け取れない＝理由が `REJECTD`・`ESME_REGULATED`）。

## 11. 状態の言い方（画面）
| status | 画面の言葉 |
|---|---|
| queued | 送る順番を待っています |
| sending | 送っています |
| accepted | 受け付けられました（まだ届いていません） |
| delivered | 届きました |
| failed | 届きませんでした |
| unknown | 分かりません |
| skipped | 送っていません（理由を添える: 試験の状態のため／ほか） |
