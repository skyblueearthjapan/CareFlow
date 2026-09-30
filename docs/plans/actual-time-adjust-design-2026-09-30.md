# 実績の時刻を合わせる — 設計と契約（打刻履歴 Phase 2）

作成: 2026-09-30 ／ ステータス: **実装中** ／ 前段: `visit-history-design-2026-09-30.md`（打刻履歴 Phase 1）
見た目の正典: `docs/mockups/mobile-actual-time-fix-mock.html`（スマホ・PO 承認 2026-09-30）、`docs/mockups/visit-history-tab-mock.html`（PC 打刻履歴）

## 0. 何を解決するか

現場では、時間どおりに着いてもインターホンを押してから家に入るまで待つことがある。QR は家に入ってから読むので、記録上の到着が実際より遅くなり、退出は予定どおりなので滞在が短く見える（例: 予定 35 分・13:00 に着いて 13:10 に入室 → 記録上は 25 分）。

**スタッフ自身が、その場でも後からでも、実績の時刻を実際に合わせられるようにする。予定は一切動かさない。**

## 1. PO 決定（2026-09-30）

| # | 決定 |
|---|---|
| 1 | 予定（`visits.start_time` / `end_time`）は動かさない。合わせるのは実績だけ |
| 2 | 到着の記録は **QR を読んだ瞬間**に近づける。15 秒程度の遅れは可、数分の遅れは不可（圏外の後送りで何時間も後になるのは不可） |
| 3 | その場で（到着直後に 1 回押すだけ）も、後から（訪問中・完了後・過去の訪問）も合わせられる |
| 4 | 操作は**ホイール式**（時刻の列を回す。10 分の目盛りが基本、最終的に 1 分単位） |
| 5 | 実績時刻の算出を **1 箇所にまとめる**（今は `visits.py` / `monitor.py` / `notify.py` に別々にある） |
| 6 | 管理者は訪問モニターで見られれば十分。スタッフはスマホで自分の実績を把握できること |
| 7 | **文言は「合わせる」「調整」。「直す」「修正」「補正」は使わない**（遅れて記録されるのは看護師の誤りではない）。理由の選択肢も「読み忘れ」「遅れた」ではなく「読み取りなし」「読み取りが後になった」 |

モックで仮置きし、PO がそのまま承認したもの: 本人が合わせられるのは自分の訪問で直近のもの／到着は読取時刻より後にできない・さかのぼれるのは最大 90 分／理由は任意（初期値「インターホン待ち」）。

## 2. 用語

| 語 | 意味 |
|---|---|
| 読取時刻 | QR を読んだ時刻。`visit_checkins` の 1 行から決まる（§3） |
| 調整 | スタッフまたは管理者が実績の時刻を合わせた記録。`visit_time_adjustments` の 1 行 |
| 実績時刻 | 画面・集計・レポートが使う時刻。調整があれば調整後、無ければ読取時刻 |

## 3. 読取時刻の決め方（決定 #2）

- スマホは打刻リクエストの `at`（= `device_time`）に **QR を読み取った瞬間**（カメラが読んだ時点／「QRなしで記録」を押した時点／ディープリンクで開いた時点）の端末時刻を入れる。今は「記録する」を押した時点なので、位置の取得と確認の時間だけ前へ寄る。「位置を再取得」しても読取の瞬間は変えない。
- サーバの読取時刻 = **`device_time` が妥当ならそれ、そうでなければ `scanned_at`（サーバ受信時刻）**。
  妥当の条件: `device_time <= scanned_at + 120 秒`（端末の時計が進んでいる場合は採らない）かつ `device_time >= scanned_at - 18 時間` かつ JST の日付が `scanned_at` と同じ。
- `scanned_at` は今までどおりサーバ受信時刻のまま保存する（監査用・並び順・GPS 保持期限の基準）。**`visit_checkins` は追記専用のまま**で、既存の行は書き換えない。
- 圏外で退避した打刻は、再送時も退避時点の `at` をそのまま送る（今もそう）。これで後送りでも読取時刻がずれない。
- 既存データは `device_time` と `scanned_at` の差が最大 5 秒（2026-09 実測・圏外の後送り 0 件）なので、切り替えで見える時刻はほぼ変わらない。

## 4. データモデル（migration `0088`・`down_revision = 0087`）

新テーブル `visit_time_adjustments`（追記専用）:

| 列 | 型 | 内容 |
|---|---|---|
| `id` | UUID PK | |
| `visit_id` | FK visits RESTRICT, NOT NULL | |
| `kind` | String(12), NOT NULL | `arrival` / `departure`（CHECK） |
| `adjusted_at` | timestamptz, NULL 可 | 合わせた時刻（分単位・秒 0）。**NULL = 読取時刻に戻す** |
| `base_checkin_id` | FK visit_checkins SET NULL, NULL 可 | 調整の元になった打刻。読み取りの無い退出を手で入れた場合は NULL |
| `reason_code` | String(24), NULL 可 | `intercom_wait` / `read_later` / `no_read` / `other` |
| `reason_text` | Text, NULL 可 | 自由記述（200 字まで） |
| `source` | String(12), NOT NULL | `mobile` / `pc` / `checkin`（打刻リクエストに同梱） |
| `created_by_user_id` | FK users SET NULL | |
| `created_by_staff_id` | FK staff SET NULL | |
| `created_at` / `updated_at` | TimestampMixin | |

インデックス `(visit_id, kind, created_at DESC)`。

**どの調整が効くか**: (visit, kind) ごとに `created_at DESC, id DESC` の先頭 1 行。その行の `adjusted_at` が NULL でなく、かつ同じ kind の最新の打刻より後に作られていれば（`adjustment.created_at >= checkin.created_at`、打刻が無ければ無条件）有効。打刻し直すと、それより前の調整は効かなくなる。

理由コードの表示名: `intercom_wait` = インターホン待ち／`read_later` = 読み取りが後になった／`no_read` = 読み取りなし／`other` = その他。

## 5. 実績時刻の単一ソース（決定 #5）

`backend/app/services/checkin/actuals.py`（Phase 1 で新設）を拡張し、**ここだけが実績時刻を決める**。

```
load_actuals(db, visit_ids) -> dict[visit_id, VisitActuals]

VisitActuals:
  arrival / departure: KindActual | None
  no_show: VisitCheckin | None            # 最新の no_show
  checkin_staff_ids: list[UUID]           # 到着・退出の全打刻者（新しい順・重複なし）= 代行判定用
  latest_checkin: VisitCheckin | None     # kind を問わない最新 1 件

KindActual:
  at: datetime            # 実績時刻（調整後。無ければ読取時刻）
  read_at: datetime | None  # 読取時刻（読み取りの無い手入力は None）
  adjusted: bool
  manual: bool            # 読み取りが無く、調整だけで成り立っている
  checkin: VisitCheckin | None   # 最新の打刻行（位置判定・打刻者など）
  adjustment: VisitTimeAdjustment | None  # 効いている調整
```

これに寄せる既存箇所（**独自に `scanned_at` を読むのをやめる**）:

| 箇所 | 変えること |
|---|---|
| `api/v1/visits.py` `_project_checkins` / `_load_latest_checkin` / `_load_latest_checkins_bulk` / `_serialize_visit` | `actual_arrival_at` / `actual_departure_at` を実績時刻に。§6 の新項目を足す。`latest_checkin` は生の打刻のまま |
| `services/checkin/monitor.py` `build_monitor` | 最新打刻の取得・`arrival_at` / `departure_at` マップ・`arr_scanned` / `dep_scanned`・滞在分（`_stay_minutes`）を実績時刻で。phase・遅延・`arrival_delay_min`・退出忘れ・ペア補正がすべて実績時刻基準になる。読み取りの無い退出（手入力）があれば phase は done |
| `services/checkin/notify.py` `run_check_missing` | 到着の有無とペア補正の時刻を `load_actuals` から |
| `services/checkin/history.py` | Phase 1 から `actuals.py` を使っている。§6 の項目を足す |
| `api/v1/visits.py` checkout の「予定外訪問の `end_time` 更新」、`_adhoc_visit_moment` | 読取時刻（§3）を使う |

**予定外訪問（`is_unplanned`）だけの例外**: 予定欄に実績が写してあるので、到着を調整したら `visits.start_time`、退出を調整（手入力を含む）したら `visits.end_time` も同じ時刻に更新する（`start < end` が崩れる場合は更新しない）。通常の訪問の予定には触れない。

**読み取りの無い退出を入れたとき**: `visits.status` が `in_progress` なら `completed` にする。それを読取時刻に戻した（= 退出が無くなった）ら、退出の打刻が無い限り `in_progress` に戻す。

変わらないもの（打刻の**有無**か `visits.status` だけを見ている箇所）: カイポケ取込の日別判別と置換ガード、取り込み前に戻す、今週だけ取消、患者ステータス連動の取消、undo/redo、ダッシュボードの完了数、カイポケ送信 CSV、GPS パージ。発行済みの通知本文の時刻は書き換えない（その時点の事実）。

## 6. API

### 6-1. 調整する・戻す

`PUT /api/v1/visits/{visit_id}/actual-time`
```json
{ "kind": "arrival", "time": "12:56", "reason_code": "intercom_wait", "reason_text": null }
```
- `time` は JST の `HH:MM`。サーバが `visit_date` と組み合わせる（端末側でタイムゾーン計算をさせない）。
- 応答は `VisitRead`（打刻 API と同じ形・§6-3 の項目つき）。
- `source` はサーバが決める: リクエストヘッダ `X-Client-Surface: mobile | pc`（無ければ `pc`）。

`DELETE /api/v1/visits/{visit_id}/actual-time?kind=arrival` — 読取時刻に戻す（`adjusted_at = NULL` の行を追記）。応答は `VisitRead`。

権限（`require_role("admin","staff")`）:
- **admin**: すべての訪問。
- **staff**: `visit_date` が今日から 7 日前まで（JST）で、かつ自分がその訪問の担当集合（primary / secondary / mentor / `visit_staff_assignments` / 同行 / コース担当フォールバック = 打刻と同じ可視性）に入るか、**自分がその訪問の到着または退出を打刻した**（代行・予定外）場合。
- 見えない訪問は 404、見えるが期間外などで合わせられない場合は 403。

検証（違反は 422、`detail` はそのまま画面に出せる日本語）:

| 対象 | 規則 |
|---|---|
| 共通 | 削除済みの訪問は 409。取消済みでも打刻があれば可。分単位 |
| 到着 | 到着の読み取りが必要（無ければ 409「到着の記録がありません」）。`読取時刻 - 90 分 <= time <= 読取時刻（分に切り捨て）`。退出の実績があればそれより前。admin は 90 分の下限なし（同じ日の 00:00 以降） |
| 退出（読み取りあり） | `到着の実績 < time <= 読取時刻 + 30 分`。今日の訪問は現在時刻まで |
| 退出（読み取りなし = 手入力） | 到着の読み取りが必要。`到着の実績 < time`。今日の訪問は現在時刻まで、過去の訪問は 23:59 まで |

監査: `audit_logs` に before / after つきで明示記録する（`visit_actual_time_adjust` / `visit_actual_time_reset`。`visit_review` の記録と同じ流儀）。

### 6-2. 打刻リクエストへの同梱（圏外でその場で合わせた場合）

`CheckinCreate`（checkin / checkout / adhoc-checkin 共通）に任意項目を足す:
- `adjusted_time`: `HH:MM`（JST）
- `adjust_reason_code`

打刻を記録したあと、§6-1 と同じ検証で調整を 1 行作る（`source = 'checkin'`）。**検証に通らない `adjusted_time` は黙って無視し、打刻そのものは必ず記録する**（退避キューの再送は 4xx で破棄されるため、調整の不備で訪問の記録を失わない）。

### 6-3. 読み取り側の項目追加

`VisitRead`（一覧・詳細・打刻応答・QR capability の絞り込み後も残す）:

| 項目 | 内容 |
|---|---|
| `actual_arrival_at` / `actual_departure_at` | **意味を実績時刻に変更**（調整後。無ければ読取時刻） |
| `actual_arrival_read_at` / `actual_departure_read_at` | 読取時刻。読み取りが無ければ null |
| `actual_arrival_adjusted` / `actual_departure_adjusted` | bool |
| `actual_departure_manual` | bool。読み取りの無い退出 |
| `actual_adjust_allowed` | bool。今のユーザーがこの訪問の実績を合わせられるか（§6-1 の権限をサーバで判定。画面はこれでボタンを出し分ける） |

`MonitorVisit`（`GET /monitor`）: `arrival_at` / `departure_at`（実績時刻）、`arrival_read_at` / `departure_read_at`、`arrival_adjusted` / `departure_adjusted`、`departure_manual`、`adjustments`（効いている調整の配列: `kind` / `reason_label` / `reason_text` / `by_name` / `created_at`）。`arrival` / `departure`（`MonitorCheckin`）は生の打刻のまま残す。`stay_minutes` / `arrival_delay_min` / `phase` / `alert_level` は実績時刻基準。

打刻履歴（`GET /visit-history` の行）: `arrival_at` / `departure_at` を実績時刻に。`arrival_read_at` / `departure_read_at` / `arrival_adjusted` / `departure_adjusted` / `departure_manual` / `adjustments`（上と同じ形）/ `adjust_allowed` を足す。`state` の絞り込みに `adjusted` を足し、`summary.adjusted` を足す。
あわせて Phase 1 の取りこぼしを埋める: 応答に `groups`（`sort=staff` または `patient` のとき、**ページングする前の絞り込み結果全体**でのグループ別件数。`[{ "label": "看護師名または患者名", "count": 120, "with_arrival": 32 }]`。`sort=date` は空配列）を足し、画面の見出し行の「N 件・打刻あり M 件」はこれを使う（Phase 1 は表示中の 50 件から数えていた）。備考に `時刻調整` を足す（「取消済みの予定に記録」の前）。Excel は `読取時刻（到着）` `読取時刻（退出）` の 2 列を足し、備考に「時刻調整（理由）」。A4 は備考に「調整（読取 13:06・インターホン待ち）」。

## 7. スマホ（モックどおり）

対象: `frontend/app/(mobile)/`、`frontend/app/q/[token]/`、`frontend/components/mobile/`、`frontend/lib/queries/me.ts`、`frontend/lib/checkin-queue.ts` ほか。

1. **読取の瞬間を控える**（§3）。訪問詳細の `handleScanned` / `handleManual` / ディープリンクの `startScan`、`/q/[token]` の予定外打刻。確認画面に「読み取った時刻 HH:MM で記録します」と出す。
2. **到着した直後のカード**（打刻成功のトーストに加えて、訪問中の表示の先頭に出す）:
   「到着 13:06 を記録しました」「お宅に着いてから読み取るまでに時間があったら、着いた時刻に合わせられます。」
   ［5分前］［10分前］［15分前］（それぞれ合わせた後の時刻を小さく併記）／［細かく合わせる］［このままでOK］
   押したら即保存し、「到着を 12:56 に合わせました（読取 13:06）」［元に戻す］［OK］に変わる。
   圏外で打刻が退避された場合もカードを出し、**退避キューの payload に `adjusted_time` を書き込む**（送信前なので API は呼ばない）。
3. **実績の行に「時刻を合わせる」ボタン**（訪問詳細。訪問中も完了後も。`actual_adjust_allowed` が true のときだけ）。その下に「滞在 35 分 ・ 到着を 10 分 調整（読取 13:06）」。
4. **実績の時刻を合わせるシート**（下から出るシート。既存の `NotificationListSheet` と同じく Dialog を下寄せ）:
   到着／退出の切り替え、大きな時刻と「読取 13:06 ・ 10 分前」、ひと押しチップ（到着: 読取どおり・5・10・15・20・30 分前／退出: 滞在 予定どおり ほか）、**ホイール**（1 分刻みの時刻の列・10 分ごとが太字・中央の帯が選択中・scroll-snap）、「1 分 まえ」「1 分 あと」、滞在の表示（「滞在 35 分（予定 35 分）予定どおり」）、理由チップ、「12:56 に合わせる」。
   ホイールは共通部品 `frontend/components/mobile/TimeWheel.tsx` として作る。
5. **退出の読み取りが無いとき**: 訪問中の表示に「退出の QR を読んでいないときは、退出時刻を入れる」。シートの退出側で入れる（理由の初期値「読み取りなし」）。
6. **経過時間**は実績の到着（`actual_arrival_at`）を起点にする。
7. **今日の一覧カード**: 実績の行に「調整」の印。
8. **今週の予定**: 実績のある訪問と今日の訪問のチップを押せるようにし（高さ 44px 以上）、実績の時刻を右に出す（「✓ 09:33–10:08」「到着 11:08・退出なし」）。押すと訪問詳細へ（戻るリンクは「今週の予定に戻る」）。
   過去の日の訪問詳細では QR の打刻ボタンと「訪問できなかった」を出さない（サーバが当日以外を拒否するため）。
9. 成功後は `['me']` を無効化する（既存の打刻と同じ）。

## 8. PC

1. **訪問モニター**: 実績バーの位置と幅、`+N分`、「✓12:56–13:31」の併記、ツールチップ、詳細パネルの到着・退出・滞在・遅延を実績時刻で。調整のある訪問には「調整」の印を付け、ツールチップと詳細パネルに読取時刻・理由・誰がいつ合わせたかを出す。モニターは閲覧のみ（ここから調整はしない）。
2. **打刻履歴**（Phase 1 のタブに足す）: 「調整」バッジ、到着・退出欄の下に「読取 13:06」、集計帯「時刻の調整」、絞り込み「時刻の調整あり」、詳細ダイアログに「実績の時刻を合わせる」枠（PC は `<Input type="time">`。理由の選択、保存、「読取時刻に戻す」、調整の履歴）。`adjust_allowed` のときだけ操作できる。
3. 保存後は `['visit-history']` `['monitor']` `['visits']` `['me']` を無効化する。

## 9. テスト

BE（`tests/test_actual_time_adjust.py` 新設 ＋ 既存の更新）:
- 読取時刻: `device_time` が妥当なら採用／未来・別日・古すぎは `scanned_at`。
- 調整: 到着の範囲（90 分・読取より後は不可・退出より後は不可）、退出の範囲、手入力の退出で completed になる・戻すと in_progress、打刻し直すと古い調整が効かなくなる、戻す操作、分単位。
- 権限: 本人・担当集合・代行で打刻した本人は可／他人は 404／8 日前は 403／admin は可。
- 反映: `VisitRead` の各項目、モニターの phase・遅延・滞在・`arrival_delay_min`、ペア補正が実績時刻基準、`run_check_missing` が変わらず動く、打刻履歴の行と備考と `state=adjusted`、Excel の列。
- 予定外訪問の `start_time` / `end_time` の追随。通常の訪問の予定が変わらないこと。
- 同梱: `adjusted_time` つきの checkin で調整が 1 行できる／不正な `adjusted_time` でも打刻は記録される。
- 監査ログに before / after が残る。migration 0088 の往復。
- 既存の `test_visit_checkin.py` `test_qr_open_checkin.py` `test_visit_monitor.py` `test_checkin_notify.py` が通る（`scanned_at` を実績として固定していたテストは、`device_time` 無しなら同じ値になるので原則そのまま）。

FE: ホイール（値の選択・範囲の端・チップで飛ぶ）、到着直後のカード（1 回押しで保存・元に戻す・圏外時はキューに書く）、シート（到着／退出・範囲外で保存不可・理由）、読取の瞬間を `at` に載せること、今週のチップから詳細へ、`actual_adjust_allowed=false` でボタンが出ないこと、モニターの実績バーが実績時刻を使うこと、打刻履歴の調整表示と詳細からの調整。

## 10. デプロイ時の注意

- migration 0088 を含む。**migration は自動では当たらない**（`up -d` の後に `alembic upgrade head` を実行し、`alembic heads` が単一であることを確認）。
- スマホは PWA を開き直すまで古い画面が残る。古い画面は「記録する」を押した時点を `at` に送るが、サーバの規則（§3）はそのまま成り立つ。

## 11. 今回やらないこと

- 到着の読み取りが無い訪問に、到着を後から手で入れること（打刻漏れの補完）。11 月の QR 完全移行までに別途決める。
- 圏外で退避した打刻が日をまたいで再送された場合の扱い（今はサーバが当日以外を拒否する）。
- さかのぼれる上限（90 分）や本人が合わせられる期間（7 日）を設定画面で変えること。
- 実績時刻のカイポケへの反映。

## 12. 実装とレビューで確定した点（2026-09-30 追記・上の本文より優先）

レビュー（バックエンド・フロントエンド、実装とは別の担当）の指摘を反映した結果。本文と食い違う箇所は、ここが正。

**データ（§4）**
- `visit_time_adjustments` に `prev_visit_status` / `prev_visit_end_time` を追加。読み取りの無い退出を入れる前の `visits.status`（と予定外訪問の `end_time`）を覚え、戻すときに使う（元から completed の訪問を in_progress に落とさない）。
- **どの調整が効くか**に例外を追加: 最新の打刻の `device_time` が、調整の `base_checkin_id` の打刻の `device_time` と一致する（どちらも非 NULL）なら、同じ読み取りの**再送**とみなして調整を有効のままにする（スマホは成功した打刻を再送し得るため）。読み直した場合は従来どおり、古い調整は効かなくなる。
- `MonitorAdjustment`（モニター・打刻履歴の `adjustments[]`）に `reason_code` を追加。

**API（§6）**
- `actual_adjust_allowed` / `adjust_allowed` は、権限に加えて**到着の読み取りがある訪問だけ** true。
- 調整 API の応答は、担当集合の外で「自分が打刻しただけ」で通ったスタッフには QR capability と同じ絞り込みを掛ける（`note`・`kaipoke_id`・担当一覧・同行を返さない）。
- 追加のエラー: 403「まだ訪問日になっていない訪問の時刻は合わせられません」／409「読み取った日（9/28）と訪問日（9/29）が違うため、時刻を合わせられません。管理者に連絡してください」（合わせる操作のみ。読取時刻に戻す操作は通す）／到着を戻すときの 422 は、退出が調整されている場合だけ。
- 調整が 1 件も無い状態の DELETE は 200 で現状を返す（行を足さない）。スマホ・PC は、読取時刻と同じ時刻で保存したら PUT ではなく DELETE を呼ぶ。
- staff ロールへの応答では、スタッフ名の無い調整者（スタッフ未紐付けの管理者）を「管理者」と出す。
- 監査ログの調整行に role / method / path / IP / user_agent を入れる。

**計算（§5）**
- **滞在分は `actuals.stay_minutes` の 1 か所**: 到着・退出をそれぞれ JST の分に切り捨ててからの差（負は 0。訪問中は現在時刻を分に切り捨て）。画面の HH:MM の引き算と一致する。モニターは従来の四捨五入から最大 1 分変わる。
- 到着ズレ（`arrival_delay_min`）は秒込みの四捨五入のまま（未統一）。
- 打刻履歴では、主担当が空（手動の付け替えでない）の訪問はコース担当を「予定の担当」と代行判定の担当集合に使う。モニターと通知の代行判定は従来のまま（主担当が空だとコース担当本人の打刻も代行扱い）。

**スマホ（§7）**
- 退出の `at` は、ディープリンクで開いた場合も「QRで退出を記録」を押した時点（ディープリンクの読取時刻は到着にだけ使う）。圏外で退避した場合もディープリンクのトークンは消費する。
- `/q/{token}` から訪問詳細へは読取時刻を `read_at` で引き継ぐ（未来・10 分超・形が違う値は無視）。
- 退出の読み取りが無い訪問の保存ボタンは「退出を 13:41 で記録する」（記録すると訪問は完了になる旨を添える）。記録後は「元に戻す」つきのトースト、シートに「入れた退出時刻を取り消す」。
- 退避中の打刻の再送と重なった場合は、再送の完了を待ってから合わせる（調整を黙って失わない）。圏外で退避中も「到着の時刻を合わせる」入口を出す。
- 今週の予定: 今日の訪問中は「訪問中」、過去の日だけ「退出なし」。過去の日で記録が無い訪問には案内を出す。
- スマホのシートは管理者でも 90 分まで（バックエンドは管理者に下限なし。仕様）。

**理由の撤去（PO 決定 2026-10-01）**
- 時刻を合わせるときの**理由は画面から外す**（スマホのシート・到着直後のカード・PC の打刻履歴の詳細）。保存は「時刻を選んで保存」だけ。
- 根拠: 読取時刻・合わせた時刻・誰がいつ合わせたかは記録と監査ログに残る。理由を毎回尋ねるのは看護師に説明を求める形になる。水増しの歯止めは理由ではなく、時刻の範囲の制限と PC の「調整」の印・読取時刻の表示。
- `visit_time_adjustments.reason_code` / `reason_text` の列と API の受け口は残す（将来戻せるように）。過去データに理由があれば表示する。
- 備考は理由なしで「時刻調整」、A4 は「調整（読取 13:06）」。

**残っている小さな点（本番反映後でよい）**
- 打刻 API（checkin / checkout）の応答は、QR capability で通った担当外スタッフにも全量の `VisitRead`（今回より前からの挙動）。
- モニターの確認者名（`reviewed_by_name`）は、スタッフ未紐付けの確認者だと staff にも email が出る。
- 読取日と訪問日が違う訪問でも「合わせる」ボタンは出る（押すと 409 の文言が出る）。
- 読み取りのある退出の理由に「読み取りなし」が選べる（PO に確認中。外すならスマホと PC を同時に）。
- 月次レポートの道具（`docs/tools/visit-history/extract.sql`）は `scanned_at` を直接読むので、画面機能とは秒単位で一致しないことがある。
