# 予定のお知らせ (SMS) 定期ジョブ cron 設定手順

設計: `docs/plans/sms-notify-design-2026-11-16.md` §5-2・§7・§7-3。
API の決まり: `docs/plans/sms-notify-api-contract-2026-11-16.md` §5。

`checkin_jobs_cron.md` と同じ「VPS 側 cron + 管理 API (admin Bearer token)」方式で、
3 つの定期ジョブを登録する。トークン・ユーザー・秘密のファイルは checkin と同じものを使う
(`carelink-cron` user・`/home/carelink-cron/.secrets/carelink.env` の `ADMIN_TOKEN`)。

| ジョブ | エンドポイント | 間隔 (cron は UTC) |
|---|---|---|
| 変更の検知とベル | `POST /api/v1/sms/changes/detect/run` | 5 分おき |
| 送る時刻の来た分を送る | `POST /api/v1/sms/send-due/run` | 毎分 |
| 届いたかの照会 | `POST /api/v1/sms/status/poll/run` | 5 分おき |

- 3 つとも admin のトークン・POST・本文なし。
- 3 つとも PostgreSQL の advisory lock で、同じジョブが重ならない。重なったときは
  `{"skipped": "locked"}` を返して何もしない。
- 機能の旗 `sms_notify` が OFF のときは 200 で `{"skipped": "disabled"}` を返す
  (cron が赤くならない)。旗を ON にする前に cron を入れておいてよい。
- 送れる時間帯 (`SMS_SEND_HOURS`・既定 8:00〜20:00 日本時間) の外では、send-due は何も送らない
  (cron は 1 日中回してよい)。時間帯は 1 通ごとに確かめるので、送っている途中で 20:00 を
  過ぎたら、残りは待ちのまま止まる。

## エンドポイントの中身

### 1. 変更の検知 — `POST /api/v1/sms/changes/detect/run`

- 応答: `{"pending": 3, "urgent": 1, "new": 1, "bells": 1}`
  - `pending` = お知らせ待ちの方の数 / `urgent` = うち今日・明日の訪問が関わる方 /
    `new` = この回で新しく見つけた変更 / `bells` = この回で出したベルの回数 (0 か 1)。
- 「お伝えした内容」と今の予定 (日と開始時刻・まだ始まっていない訪問) を比べ、気づいた時刻を残す。
  比べるのは、お伝えした内容がある方 (SMS が 1 通でも受け付けられた・電話などで済ませた) だけ。
  いま送っている途中 (待ち・送信中) の方は比べない。
- ベル (管理者全員・`notifications.type='sms_change_pending'`・患者様の名前は書かない):
  - 違いの中身を、後の回 (最初に見てから 4 分以上たった回) でも同じに見て初めて出す。
    カイポケの取込の途中で予定が揺れているあいだは鳴らない。cron の時刻が数秒揺れても
    1 回分 (5 分) 遅れないよう、5 分でなく 4 分で見る。
  - 待ちのままなら 60 分ごと、急ぎ (今日・明日の訪問) は 15 分ごとに出し直す。
  - どの方の分でも、変更のベルとベルのあいだは 15 分以上あける。**急ぎのベルはこの間隔に
    縛られない** (急ぎの方ごとの 15 分ごとは守る)。
  - 違いが消えて 30 分の内に戻ったら、最初に気づいた時刻をそのまま使う。

### 2. 送る — `POST /api/v1/sms/send-due/run`

- 応答: `{"sent": 2, "stuck_fixed": 0}` (`sent` = 送った・送らずに終えた件数)
- `queued` で時刻の来た分を 1 件ずつ送る (毎秒 1 件まで・**1 回で 30 件まで**。残りは次の分)。
- 送る直前に 1 通ずつ確かめ、当てはまれば送らず `skipped` にする (`skip_reason`):
  | skip_reason | いつ |
  |---|---|
  | `expired` | 期限 (`expires_at` = 作った時刻か `not_before` の遅い方 + 60 分) を過ぎた・作った日 (日本時間) と今日が違う |
  | `superseded` | その方へ新しいお知らせを作った (版が新しい) |
  | `contact_changed` | 今の連絡先が「お知らせを送る・同意は確認済み・携帯」を満たさない・番号が違う |
  | `patient_inactive` | 患者様が稼働中でない |
  | `test_mode` | 試験の状態で、`SMS_TEST_ALLOWLIST` に無い番号 |
- `test_mode` 以外で送らなかった分があれば、ベル「送れなかったお知らせが N 件あります」
  (`type='sms_unsent'`・名前は書かない)。
- 同じ番号への 600 秒は、DB の最後に送った時刻 (`sent_at`) で確かめる。早ければ `not_before` を
  延ばして待ちのまま。
- `sending` へ取ってから (`claimed_at`) 10 分たった行は `unknown` にする (送ったか分からないので
  送り直さない)。502/503/504 などで待ちに戻した行は `sent_at`・`claimed_at` を消すので、
  次に取ったときに古い時刻で「止まった」と見誤らない。
- 届かなかった・分からない (`failed`・`unknown`) 行があればベル (`type='sms_delivery_failed'`・
  1 通につき 1 回)。`unknown` は、送った後に返事が途切れた・受付の番号が無い・思わぬ失敗・
  `sending` のまま止まった・72 時間たっても決まらない、のとき。
- 「お伝えした内容」は、その版の SMS が 1 通でも受け付けられた (`accepted`) ときに替わる。
  送らなかった・受け付けられなかった版では替わらない (週の一覧で「届いていない」と出る)。

### 3. 届いたかの照会 — `POST /api/v1/sms/status/poll/run`

- 応答: `{"polled": 5, "delivered": 4, "failed": 1}`
- 受け付けた時刻から 1 分・5 分・30 分・3 時間・24 時間・72 時間に照会する。届いた / 届かなかったに
  決まったら止める。6 回目でも決まらなければ `unknown`。
- 届かなかったらベル (1 通につき 1 回)。お伝えした版の SMS がすべて届かなかった方は、週の一覧で
  「届いていない」(`not_delivered`) に戻る。
- 照会の時刻が来た行だけを DB で選ぶ (1 回 200 件まで)。

## VPS 側 cron 設定 (初回のみ)

```bash
sudo -u carelink-cron crontab -e
```

以下 3 行を足す (今ある checkin・geocoding の行はそのまま残す):

```cron
# 予定のお知らせ (SMS) 変更の検知とベル (5 分おき)
*/5 * * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/sms/changes/detect/run >> /home/carelink-cron/sms_detect.log 2>&1

# 予定のお知らせ (SMS) 送る時刻の来た分を送る (毎分)
* * * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -m 300 -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/sms/send-due/run >> /home/carelink-cron/sms_send_due.log 2>&1

# 予定のお知らせ (SMS) 届いたかの照会 (5 分おき)
*/5 * * * *  set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json" https://carelink.kaipoke-api.net/api/v1/sms/status/poll/run >> /home/carelink-cron/sms_status_poll.log 2>&1
```

> send-due は 1 回で最大 30 通を毎秒 1 通ずつ送るので、`-m 300` で curl の待ち時間に余裕を持たせる。
> 途中で切れても、送り終えていない行は次の回に送る (同じ人へ二重には送らない)。

### 初回の手動確認

```bash
sudo -u carelink-cron bash -c 'set -a; . /home/carelink-cron/.secrets/carelink.env; set +a; curl -sS -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" https://carelink.kaipoke-api.net/api/v1/sms/changes/detect/run'
```

旗が OFF なら `{"skipped":"disabled"}` が返る (正常)。

## 使い始める (PO の了承を段ごとに取る)

値は本番 VPS の秘密のファイル (`/opt/carelink/.env`) にだけ置く。ここ・コード・引き継ぎ書に値を書かない。

1. `SMS_PROVIDER=mock` のまま旗 `sms_notify` を ON にし、画面と cron の動きを確かめる
   (mock は外へ送らない・照会は「届いた」を返し、`status_raw` は `mock`。お知らせの記録では
   `provider=mock` の行に「模擬（実際には送っていません）」と出る)。mock で試すときも
   `SMS_SEND_MODE=test` と `SMS_TEST_ALLOWLIST` を使う (下の 5 を参照)。
2. KDDI の接続の値 (`SMS_KDDI_HOST`・`SMS_KDDI_AGG_ID`・`SMS_KDDI_BOT_ID`・`SMS_KDDI_SMS_NUMBER`・
   `SMS_KDDI_BASIC_ID`・`SMS_KDDI_BASIC_PASSWORD`) を PO が置く。最初はデモアカウントの値。
3. `SMS_SEND_MODE=test`(既定) のまま、`SMS_TEST_ALLOWLIST` に試験の番号 (PO と事務所の方の携帯・
   数字だけ・カンマ区切り) を入れ、`SMS_PROVIDER=kddi` にする。一覧に無い番号へは送らず
   `skipped`(試験の状態のため) として記録だけ残る。**一覧が空なら 1 件も送らない。**
4. 必要なら `SMS_STATION_SHORT_NAME` (変更のお知らせの頭の【 】) を入れる。
5. 本番のアカウントの値に替え、数名の患者様 → 全員と広げる段で `SMS_SEND_MODE=live` にする。
   **`live` は `SMS_PROVIDER=kddi` のときだけ送る。** `live` なのに窓口が `kddi` でない
   (`mock`) とき、または `SMS_PROVIDER` が知らない値 (打ち間違い・`test` でも) のときは
   1 件も送らず `failed` (`error_code=config`) にし、ベル「SMS の設定に誤りがあるため、
   送れていません」(`type='sms_config_error'`・24 時間に 1 回まで) を出す
   (届かなかったベルにはしない)。`/sms/status` の `provider_ok` も `false` になる。
   - `SMS_PROVIDER=kddi` なのに接続の値が足りないときは、送らずに待ちのまま、ベル
     「SMS の設定が足りないため、送れていません」(`type='sms_config_missing'`) を出す
     (24 時間に 1 回まで)。待ちの行は期限 (60 分) で `expired` になる。
   - `GET /api/v1/sms/status` の `provider_ok` で、設定がそろっているかを確かめられる。
6. 設定を変えたら backend を作り直す (`docker compose ... up -d --force-recreate backend`)。
7. mock で「お伝えした」方 (`patient_week_notices.told_via='mock'`) は、実際には届いていない。
   `SMS_PROVIDER` を mock 以外にすると、その方は週の一覧で「まだお伝えしていない」(`new` など)
   に戻り、変更のお知らせ待ちにも出ない (mock のあいだは今のまま)。kddi にしたら、
   週のお知らせを送り直す。

## 止める

- すぐ止める: 旗 `sms_notify` を OFF にする (画面の口は 404・cron は `skipped`)。
  送る順番を待っている行 (`queued`) はそのまま残る。**ON に戻しても古い行は送られない**:
  期限 (60 分) を過ぎた・日が替わった行は、次の send-due で `expired` になり、
  「送れなかったお知らせ」のベルが出る。お伝えした内容は替わっていないので、週の一覧で
  「届いていない」と出る方を確かめて、送り直す。
- 外へ送るのだけ止める: `SMS_SEND_MODE=test` かつ `SMS_TEST_ALLOWLIST=` (空) にして作り直す。
  以後 `queued` の行は送らず `skipped` になる。
- cron を止める: `sudo -u carelink-cron crontab -e` で 3 行の頭に `#` を付ける。

## モニタリング

- `/home/carelink-cron/sms_detect.log`・`sms_send_due.log`・`sms_status_poll.log` に応答が残る
  (`logrotate` weekly 推奨)。
- ベルは `notifications.type IN ('sms_change_pending','sms_delivery_failed','sms_unsent',
  'sms_config_missing','sms_config_error')` で追える。
- 週の画面の「届いていないお知らせ N 件」(`/sms/status` の `undelivered_count`) は
  **直近 7 日**の、届かなかった (`failed`)・分からない (`unknown`)・送れなかった
  (`skipped` のうち `expired`・`superseded`・`contact_changed`・`patient_inactive`。試験の状態の
  ためは除く) 件数。リンク先の一覧 (`/sms/messages?undelivered=true`) は同じ決まりで、
  日の範囲は付けない (7 日より前の分も出る)。
- 1 通の経過は `sms_status_events`。本文と番号はログに書かない。

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `{"skipped":"disabled"}` が続く | 旗 `sms_notify` が OFF | 使い始めるときに ON にする。 |
| `{"skipped":"locked"}` | 前の回が動いている | 待つ (transaction が終われば外れる)。 |
| `sent` が 0 のまま | 送れる時間の外 / `queued` が無い / 次に送ってよい時刻の前 / KDDI の設定が足りない | 時間帯と `sms_messages.not_before`・`/sms/status` の `provider_ok` を確かめる。 |
| `skipped` (expired) が出る | 時間の外・設定の不足・旗 OFF のあいだに期限を過ぎた | 週の一覧で「届いていない」方を確かめて送り直す。 |
| `failed` (config) が出る | `SMS_SEND_MODE=live` なのに `SMS_PROVIDER` が `kddi` でない・`SMS_PROVIDER` が知らない値 | 設定を合わせて作り直し、送り直す。 |
| `skipped` (test_mode) ばかり | `SMS_SEND_MODE=test` で一覧に無い番号 | 試験の段なら正常。広げる段で PO の了承のうえ `live` に。 |
| `unknown` が増える | 送った後に応答が途切れた・72 時間たっても決まらない | 自動では送り直さない。KDDI に `transaction_id` で問い合わせる。 |
| 401 Unauthorized | ADMIN_TOKEN の期限切れ | `checkin_jobs_cron.md` の手順で再発行する。 |
