# セッション引き継ぎ 2026-09-03（9/7 週 らく助→カイポケ反映の失敗解析と根治）

**次のエージェントへ: まずこのファイルを読むこと。** 前セッション総括は `session-2026-08-31-HANDOFF.md`。

| 項目 | 値 |
|---|---|
| 本番 HEAD | `f90ee71`（2ae1809 送信ガード + f90ee71 ミラー是正・migration 無し・20:10 JST デプロイ・healthz 200/200） |
| RPA (PlaywrightTest1) | `06fdcae`（auto_apply 耐性強化・VPS 反映済み・`docker restart kaipoke-api` 済み・旧版 `commands/auto_apply.py.bak-20260903`） |
| 本番バックアップ | `/opt/carelink/backups/pre-deploy-20260903-2005.sql.gz` |
| 修復 SQL | `repair-2026-09-03-w37-primary-mirror.sql` を **実行済み**（W37 稲毛A 9/9 の 5 訪問 primary→宇田川・残 0） |
| 残差シート | `ffb03017`（diff-local 20:58 JST・**5 件 = 都賀A 9/10 担当なし 4 + 麻生**） |

## 0. 何が起きたか（確定した事実）
9/7〜9/13 週の送信（ジョブ `57a70c9c`・17:05〜18:25・178 件）が **成功 155 / 失敗 22 / スキップ 1**。差分最新化で残差 29。カイポケ側の被害:

| 種別 | 件数 | 対象 |
|---|---|---|
| 行が消えた（削除成功→追加失敗） | 5 | 並木 9/9、安永 9/9、前川七海 9/10、前川心愛 9/10、一ノ瀬 9/11 |
| 担当なし(-)で登録され export に映らない「隠れ行」 | 8 | 山岡・清水・井川・菅原 9/9、林・森田・松戸・石川 9/10（**カイポケ上に実在**・dry-run で確認） |
| 旧内容のまま | 11 | 加藤 9/7・9/10、園田 9/8、三浦 9/9、吉川 9/9、唐鎌 9/9・9/11、安永 9/11、並木 9/11、井川 9/11、久須見 9/10 |
| 二重（旧行削除失敗＋新行追加成功） | 1 | 木村 9/7 16:45（熊澤 旧 + 高岡 新） |
| 未登録 | 5 | 久須見 9/8・9/12、唐鎌 9/8、井川 9/10、麻生 9/9（カイポケに利用者未登録） |

### 根因 3 系統
1. **らく助が担当なし(-)を送った（9 件）**: `csv_builder` は `visits.primary_staff_id` しか見ない。稲毛A 9/9 の 5 件は 15:57 のプール一括投入（`reset_visits_to_fixed`）がコース担当を宇田川に書いたのに既存 auto 訪問の primary を NULL のまま放置（ミラー欠落）。都賀A 9/10 の 4 件は本当に担当未設定。**カイポケの職員別 export は担当なし行を出さない**ため、送った後は「無い」ように見え、次の diff で add が再出現 → 再送すると二重登録になる地雷。
2. **RPA の待機不足（カイポケが重い 17〜18 時台に連鎖）**: 利用者切替直後の行検出失敗 3、削除ボタン 3 秒待ちで諦め 5、削除反映の検証が早すぎ 2、職員欄 2 秒固定待ち 9。1 時間後の dry-run では全て通る＝時刻依存。
3. **RPA の「削除→追加」順序**: 追加が落ちると行が消える。同時刻 2 行あるとき職員名で区別せず先頭を消す危険。

W37 の週生成が 15:43 に 2 回走っているが、2 回目が 1 回目を正しく論理削除しておりらく助側に重複は無い（`deleted_at` を見ずに数えると 110 組の偽重複に見えるので注意）。

## 1. 入れた改修（全てレビュー承認・本番稼働）
### らく助 2ae1809（送信ガード）
- `services/kaipoke/rpa_capability.py`: `UNASSIGNED_REASON` / `is_unassigned_item` / `unassigned_item_ids`（add/edit/date_change で `after.staff1` が空か `-`。**pair_key で相方 delete も道連れ**＝孤立 delete による訪問消失防止）。
- `POST /integrations/apply`: 送信前に除外・`skipped_unassigned(+reason)` を job.params / result_summary に記録・全件対象外なら 422。
- `POST /integrations/unsent-summary`: `items[].unassigned` / `unassigned_count` / `sendable_count` から差引・管理者通知 `unassigned_unsent`（`rpa_unsupported_notify._upsert_for_admins` に一般化・週 1 回・件数変化で更新）。
- FE `SyncBar`: 「担当なし」タグ＋注記・送る無効・全件送るから除外。zod は default で後方互換。
### らく助 f90ee71（ミラー欠落是正）
- `services/scheduling/course_staff_mirror.py::mirror_course_staff_to_visits`: `reset_visits_to_fixed` がコース担当を書いた直後に既存訪問へ伝播（planned/proposed のみ・青ピン除外・manual_staff_override 尊重・primary NULL または旧担当のみ・VSA は書かない=courses.py と同じ判断）。
- `csv_builder.resolve_month_rows`: primary NULL かつ非上書きならコース担当（active のみ）へフォールバック。**全月に効く**ので、過去月の突合で `'-'→担当` の差分が新たに出得る（実績付き行は RPA で直せない）→ 次回送信前にレポートで件数を目視。
- `docs/plans/backlog-2026-09-03-course-staff-mirror-gaps.md`: 未ミラーの書き手 3 箇所（op_log undo / 急休代替 takeover / inbound course_takeover）。
### RPA 06fdcae（auto_apply 耐性強化）
- 行検出: テーブル描画待ち＋3 回再走査・予定側(`'01'`)リンクのみ・同時刻複数行は **職員1 名で特定**（不一致なら触らない）。
- 削除: ボタン/モーダル閉鎖/検証をポーリング化（10s/15s/8s）・同時刻行の件数減少も要求・実績側を無視。
- 追加: 職員欄を最大 12 秒ポーリング・未表示時はフォームエラー記録。
- 順序: (日,開始) が変わる編集/日付変更は **追加→削除**、同キーは削除→追加＋失敗時ロールバック（既に登録済みなら再追加しない）。リカバリ後は利用者を再選択してから書く。
- 失敗理由 `reason` を結果 details に付与（`GAS_APPLY_COMPLETION_SPEC.md` に一覧）。要注目 = `old_row_remains_duplicate`（二重・手動削除）/ `add_may_have_registered` / `add_failed_row_lost`（手動復元）。
- dry-run 実績: 残差 20 件 → 19 成功・失敗 0（麻生のみ利用者未検出）。木村 9/7 は「熊澤妙子(重複)」の行を職員名で特定、並木 9/11 は追加→削除順で動作確認済み。

## 2. 復旧 実施済み（PO 承認 20:15 JST → 21:00 完了）
1. **隠れ行 8 件を RPA で削除**（20:17〜20:20・8/8 削除検証 OK・ジョブ直叩き）。
2. **らく助から送信**: diff-local（シート `d9f0f31c`・29 件）→ `POST /integrations/apply dryRun:false`（ジョブ `47423f5f`・20:24〜20:39）= **成功 24 / 失敗 0 / スキップ 1（麻生・利用者未登録）・`skipped_unassigned` 4** = 新ガードが都賀A 9/10 の 4 件を自動除外。
3. **export で確定**: 直後の export 2 回は「スケジュール表のクリック失敗 Timeout」で古い CSV（既知の罠・md5 不変）→ 3 回目で `CSV出力完了`（md5 変化）。**残差 5 = 都賀A 9/10 担当なし 4（林・森田・松戸・石川）+ 麻生 9/9**。突合レポート（20:58 snapshot）= **一致 131 / 相違 0 / らく助のみ 1（麻生）/ カイポケのみ 0**。消えていた 5 行は復元、木村 9/7 の二重も解消（高岡のみ）。
4. **PO 判断が残るもの**: 都賀A 9/10 の担当 4 件（らく助で付ければ次の 🔄→⇧ で add される・ガードで送信前に「担当なし」表示）／麻生様のカイポケ利用者登録。

### 2-b. 手順の記録（次回同種の事故で使う）
- 隠れ行 payload: VPS `/tmp/hidden_delete_live.json`（`staff1_from` 空 = 時刻のみで特定・dry-run で「1 件のエントリを発見」を全件確認してから実行）。
- 成否は必ず export の `CSV出力完了` ログ + md5 変化で確認。連続失敗時は 20 秒待って再試行（3 回目で通った）。

## 3. 教訓（本日）
1. カイポケ export（スケジュール表）は **担当なし行を含まない**。隠れ行の実在は RPA dry-run の delete（登録しない・クリックで発見）で確かめる。
2. RPA の失敗は時刻依存が大半。失敗 22 件の当日 dry-run 再実行は 19/20 成功。夜 17〜18 時台は避ける。
3. 「削除→追加」の順序は消失事故の設計欠陥。順序を変えられないケースはロールバックを持つ。
4. 同時刻 2 行のときは職員名で行を特定しないと別行を消す。RPA 結果の details に `reason` が入ったので、以後は原因分類をログではなく結果から読める。
5. 週の重複調査では `deleted_at IS NULL` を忘れない（週生成の再実行は旧行を論理削除して残す）。
6. 既存 pytest 失敗は 33 件（manager ロール廃止追随漏れ・audit middleware・Python 3.14×SQLAlchemy UUID 等）で全て改修前 HEAD と同一。`test_pfv_sub_office` は順序依存で単体では通る。FE は e2e spec 8 本＋middleware/KaipokeConsole の 4 件が既存失敗。

## 4. 参照
- ジョブ結果: `kaipoke_jobs` `57a70c9c`（失敗 22 の details）・RPA ログ `/var/log/supervisor/api.log`（17:05〜18:25）・失敗時 PNG `artifacts/debug_add_failed_20260903_17*.png` / `delete_verify_ng_*`。
- 直叩き: `tools/admin_call.py`（`docker exec -w /app -e PYTHONPATH=/app carelink-backend python /tmp/admin_call.py METHOD PATH JSON`）。RPA は `curl -H "Authorization: Bearer $KAIPOKE_API_TOKEN" http://127.0.0.1:5000/api/apply`。
