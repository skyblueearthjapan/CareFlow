# 2026-11-01 セッション引き継ぎ（10 月の実績をカイポケへ反映・訪問週の片付け・事務所からのやること）

## 1. 10 月の実績をカイポケへ反映（済み・本番）

PO の決定（11/1）：**らく助の QR 実績（後から合わせた時刻を含む）が正**。30 分未満と退出の QR が無い訪問は**到着＋35 分**（松岡さんの前からの決まり）。患者ごとの訪問数が違えば、らく助を正としてカイポケに足す。看護記録書Ⅱとずれても QR を正とする。メモ `careflow-actual-to-kaipoke-rules`。

- 突き合わせ：らく助（取消を除く 645 件）×カイポケの実績 CSV（`kaipoke_csv_snapshots` division=actual・連携の「予実比較（月）」の「実績を取得して比較」で取得）。名前の字体（齋/齊・髙/高 など）をそろえて（日・利用者）で組にした。
- カイポケに無かった 3 件を画面で足した：P102 10/23・P115 10/3・P130 10/29（15:32〜16:07＝到着＋35 分）。
- RPA（PlaywrightTest1 `feat/actual-time-edit` → main `d3d2591`・VPS 反映済み）で実績側の時刻をその場で書き換えた：試し 1＋257＋追加 3＋ご姉妹 1 = **262 件・失敗 0**（約 46 分）。
- 反映後に実績を読み取り直して確認：645 件中 638 件が QR と同じ。残り 7 件は決めたとおり（到着＋35 分 5 件・入力しない 2 件）。
- 入力しない（松岡さんに確認）：川名さん 10/15 木村様（植田様と時間が重なる）・熊澤さん 10/28 伊藤様（次の訪問と重なる）。
- ご姉妹 10/1（増島 梨紗様・由里彩様）：時刻はらく助、担当はカイポケのとおり 2 名のまま（1 名にすると正看→准看になるため PO「2 名にする」）。人数は松岡さんに確認。
- 渡すもの（`docs/reports/agent/`・お名前入り・外へ出さない）：`2026-10 訪問の実績（反映後）.xlsx/.html/.pdf`、`2026-10-実績の確認のお願い（松岡様）.html/.pdf`。精査の材料 `oct-actuals-raw.json`・`oct-actuals-after.json`・`2026-10-actual-review*.json`。
- 気づき：試しの dry-run で、同じ日・同じ時刻のご姉妹の行を 2 件が指す一覧の誤りが見つかった（鍵に利用者名が無かった）。**送る一覧は利用者名を鍵に含め、同じカイポケ行を 2 件が指さないことを確かめる**。

### RPA の実績書き換え（新しい口）
- `POST /api/actual-time/apply`（dry_run 既定 true・items：user_name・date・old_start・old_end・staff1・new_start・new_end）→ `GET /api/actual-time/result`。説明は PlaywrightTest1 `ACTUAL_TIME_EDIT_SPEC.md`。
- 手順：実績側（onclick 3 番目 '02'）の行を日付・旧時刻・職員1 で特定 → 時刻 → 「職員情報入力」（これで「登録する」が押せる）→ 担当が元のままか → 登録 → 一覧で確認。削除→追加はしない。
- 呼び出しは本番 backend の中から（`KaipokeClient`＋`get_credentials_payload`）。今回の道具は scratchpad の `rpa_run.py`（`PYTHONPATH=/app`、入力は UTF-8）。
- 予実管理（請求側）にも自動で反映される（医療保険）。

## 2. 訪問週の片付け（develop 済み・本番まだ）
`fix/visit-weeks-cleanup` → develop `b4d36557`（PO 了承）。保存のたびに頻度に合わない `visit_weeks`／`biweekly_anchor` を消す。設計 `biweekly-monthly-design-2026-10-30.md` §10。本番反映は別に了承を取る（migration なし）。本番後に P128・P131 を患者マスタで保存し直すと "1,3" が消える。
隔週の 3 名（P083・P128・P131）の起点の週は本番で 2026-10-19 に設定済み（実績から）。

## 3. 事務所からのやること（作業中・ブランチ feat/patient-tasks）
- 設計 `docs/plans/patient-tasks-design-2026-11-01.md`、モック `docs/mockups/patient-tasks-mock.html`（PO と方向を決めた：患者＋日付「この日から出す」・持ち越し・完了は担当と事務所・PC は訪問モニターの詳細＋左メニューの新しいページ「やること」）。
- backend（mig 0103）→ レビュー（軽微で可）→ 指摘を直す `1a3d37d2`（先の訪問へ重ねて出さない・mentor を担当に・同時押し 409）。画面（PC 訪問モニターの印と詳細の枠・左メニューの「やること」ページ・スマホの帯／カードの印／訪問詳細）`29795095`・`dfbb2d9c`・`62ac8420` → レビュー（軽微で可）→ 指摘を直す `7f5957fd`（二度押し・done_from・/tasks を管理者だけ）。
- **develop へ push・本番反映済み（PO 了承・11/1）**：本番 7f5957fd・alembic 0102→0103。pg_dump `/opt/carelink/backups/pre-deploy-patient-tasks-20261101-0143.sql.gz`（UTC）。build → `run --rm --no-deps backend alembic upgrade head` → `up -d --no-deps --force-recreate backend frontend`。確認：公開の API 200・/api/v1/task-item-types 401（ログインが要る）・両コンテナ healthy・入れ替え後の 5xx/Traceback 0・PO の PC で「やること」ページと訪問モニターの凡例を確認。patient_tasks・task_item_types は 0 件（項目は事務所が画面で足す）。訪問週の片付けも同じ反映で本番に入った。
- 残り：項目の初期登録（事務所）・実際に 1 件付けてスマホで見る確認（PO と）・現場は Ctrl+Shift+R。

## 4. 月の実績タブ（設計の案）
`docs/plans/kaipoke-actual-write-design-2026-11-01.md`（11/1 の画面確認・1 件の試し・夜に流す運用）、モック `docs/mockups/monthly-actual-sync-mock.html`。今日の精査の決まりを画面にする。

## 5. 残り
- 松岡さんの回答（重なり 2 件・ご姉妹の人数）を受けてカイポケを合わせる。
- 事務所からのやること：レビュー → 画面（PC・スマホ）→ レビュー → develop・本番（それぞれ了承）。
- 訪問週の片付けの本番反映（了承待ち）。
- 月の実績タブの実装。
- 前からの残り：session-2026-10-30-HANDOFF.md §5。
