# カイポケ取込の不具合 6 件の根治（2026-10-01）

**出典**: `session-2026-09-24-HANDOFF.md` §4-2（1〜6）・`session-2026-09-30-HANDOFF.md` §5。期限 = 次の祝日 10/12 より前。
**状態**: 作業ブランチで実装・テスト済み。**本番未反映・migration なし・本番データ未修正**。

| # | 内容 | コミット | 状態 |
|---|---|---|---|
| 1 | 取込が打刻済みの訪問を取り消す | `c30b8a6` | 済 |
| 2 | 跨ぎ date_change と移動先 delete の同居 | `25286e5` | 済 |
| 3 | 担当2 の解除が同行を外さない | `c1933e9` | 済（範囲は PO 確認、§3） |
| 4 | 日付変更でコースが元の曜日のまま | `43d5bf3` | 済 |
| 5 | 予定外打刻の昇格 | `98285f3` | 済 |
| 6 | 藤原様 9/23 の打刻付け替え | `ff779f5` | スクリプトのみ・**本番未実行** |
| – | プレビューの表示（件数チップ・注記） | `f159975` | 済 |
| – | 月跨ぎ週のプレビューが約 100 秒 | — | 調査のみ（§7）・後続タスクでジョブ化（PO 決定） |
| – | 独立レビュー（REQUEST CHANGES）への対応 | §9 | 済 |

テスト: `backend/tests/test_kaipoke_inbound_fixes_20261001.py`（30 件）・`backend/tests/scripts/test_reattach_cancelled_checkins.py`（12 件）・FE `InboundControlsEvents.test.tsx` ⑦/⑦b。

**PO 決定（2026-10-01）**: #3 = 取込が張った同行だけを外す（実装どおり）／#5 = 近い時刻の窓は 60 分／#6 = デプロイ後にバックアップ → dry-run → 実行／月跨ぎ週の約 100 秒 = 後続タスクでバックグラウンドジョブ化（本作業ではしない）。

---

## 1. 取込が打刻済みの訪問を取り消す

**再現**: 打刻（visit_checkins）が付いた訪問が、カイポケ側に無い（または別の行と結ばれた）ため差分に `delete` が出る → 適用で `status='cancelled'`。9/23 藤原様は 小西さんの打刻（11:55〜12:29）が付いた 13:00 の訪問が取り消され、実績がモニターから消えた。

**原因**: `inbound.py` の delete → cancel に打刻のガードが無かった。置換パートは「打刻のある日は置換しない」実績ガードがあるので対象外。

**修正**:
- `apply_inbound_items` の delete 経路で、対象（2 名体制の相方を含む）に打刻があれば取り消さず `failed`・`reason="checked_in"`・「打刻済みのため取り消していません。カイポケの予定と実際の訪問をご確認ください」。
- **date_change も同じ**（レビュー HIGH）: 対象（相方を含む）に打刻があれば別の日へ動かさず `failed`・`reason="checked_in"`・「打刻済みのため別の日へ移していません…」。予定外訪問も同じ。プレビューは該当行に注記・`diffSummary.checked_in_move`。移動グラフ（玉突き/循環の並べ替え）にも入れない。
- プレビュー（`_build_inbound_sheet`）で該当 delete 行に同じ文言の `comment` を付け、`diffSummary.checked_in_delete` に件数。選択は外さない（適用のたびに結果と通知に「要確認」として残すため）。
- 通知タイトルは「失敗」と分けて「打刻済みのため見送り N」と数える（smart-apply と手動の apply-inbound の両方）。
- FE: 実績日の差分サマリに「打刻済みのため取り消しません（要確認）」「打刻済みのため日付を動かしません（要確認）」チップ、プレビューのカードに注記。
- **挙動の変更**: 既存テスト `test_smart_case_b_with_checkin_on_moved_visit_is_blocked`（打刻付きの訪問が置換日へ動く → 置換の実績ガードで 422・全体ロールバック）は、差分パートで date_change が failed（要確認）になり取込の他の部分は通る形へ書き換えた。打刻の付いた訪問は元の日に残り、置換日はカイポケの行で書き直される。

**テスト**: `test_delete_keeps_checked_in_visit`（dry-run/実適用）・`test_delete_without_checkin_still_cancels`（従来挙動）・`test_smart_preview_and_apply_report_checked_in_delete`・`test_date_change_keeps_checked_in_planned_visit`（dry-run/実適用）・`test_date_change_keeps_checked_in_unplanned_visit`・`test_preview_marks_checked_in_date_change`。

## 2. 跨ぎ date_change と移動先 delete の同居

**再現（9/24 の形）**: らく助 = 21 日 10:00（A）と 22 日 10:00（B・別の担当）、カイポケ = 22 日 10:00（A の担当）だけ。B の担当が准看護師だと、らく助 CSV のサービス内容が「…・准看」、カイポケは「…・正看」で、差分エンジンの Pass 1/2（同日・サービス一致）に乗らない。Pass 3 が A を 22 日へ `date_change`、B を `delete` に割る。適用は delete → B 取消、date_change → 「移動先に別の予定があります」で failed（取消行も UNIQUE 枠を占有）→ smart は移動元 21 日の置換を見送り、21 日に古い予定が残った。テスト `test_smart_holiday_shift_shape_keeps_target_visit` は修正前のコードで失敗することを確認済み。

**原因**: (a) inbound でもサービス内容の一致を突合の条件にしていた（らく助のサービス内容は担当の資格から作った値で、訪問の同一性とは関係ない）。(b) 適用側が同じ実行で取り消す枠を「生きた予定」と見ていた。

**修正**:
- 差分エンジン `_compare_entries(prefer_same_slot=True)`（inbound だけ）: 日ごとの Pass 1（サービス一致・同時刻）の直後、**Pass 2（サービス一致・時刻違い）と Pass 3（日付変更）より前に**、同じ日・同じ開始時刻の残りをサービス内容に関わらず `edit` に結ぶ（Pass 1.5）。当初は Pass 2 の後に置いていたが、レビュー指摘で前へ移した（後ろだと、区分違いの同時刻の組より先に、同じ区分の別の時刻の行が相手を取ってしまう）。outbound は従来どおり（区分変更を delete+add / grade_change で送る必要があるため）。
- 適用: delete を変更系より先に処理する（並べ替え）。移動先が**同じ実行の delete で取り消す訪問**なら、その行を論理削除（`deleted_at`）して枠を空け、先に flush してから移動する。以前からの取消（今週だけ取消・以前の取込の取消）・打刻済み・2 名体制の行は従来どおり退かさない（既存テスト `test_edit_into_occupied_slot_fails_without_500[cancelled]` の挙動を維持）。

**テスト**: `test_engine_inbound_pairs_same_slot_before_date_change`・`test_engine_inbound_same_slot_before_service_match`（A 9:00 正看 + B 14:00 准看 vs A′ 9:00 准看 + B′ 15:00 准看 → A↔A′・B↔B′。どちらも outbound の挙動不変を同じテストで確認）・`test_date_change_into_slot_cancelled_in_same_run`・`test_date_change_into_locally_cancelled_slot_still_fails`・`test_smart_holiday_shift_shape_keeps_target_visit`（smart の通し・held_days が空）。

**handoff との差**: 再現データ（9/24 の correction sheet `465c763d…`）は本番にしか無いため使っていない。同じ形をテストで組んだ。

## 3. 担当2 の解除が同行を外さない

**再現**: 取込で新人の担当2 を同行（`accompaniments`・target_type=visit・source=import）として取り込んだ後、カイポケで担当2 を外す → 取込は「担当2を解除」と記録するが、`secondary_staff_id` と `visit_staff_assignments` しか直さず、同行が残る（9/24 に 4 件を手で削除）。

**修正**: edit 経路で担当2 が外れた/別人に替わったとき、元の担当2 が**取込で張った visit 直リンク**なら同行を削除する（「同行「…」を外しました」）。同一実行の後続 item 用に突合集合も更新。担当（コース `assigned_staff_id` / 訪問の primary・secondary / `visit_staff_assignments`）の 3 か所のうち担当2 に関わるのは後ろの 2 つで、これは従来どおり直る。

**PO 決定（2026-10-01）**: 取込が張った同行だけを外す（実装どおり）。画面で人が張った同行（`manual` / `default`）とコース単位の同行は**外さない**。「同行「…」はらく助で設定された同行のため残しています（要確認）」と注記するだけ。

**テスト**: `test_staff2_clear_removes_import_accompaniment`（dry-run/実適用）・`test_staff2_clear_keeps_manual_accompaniment_with_note`。

## 4. 日付変更でコースが元の曜日のまま

**再現**: 担当そのままの `date_change` で訪問の日付だけが動き、`course_id` は元の曜日のコース（例: 月曜の稲毛C）のまま → 移動先の日のモニターに元曜日のコース行が出る（9/22 海老澤様・帆足様、9 月で 4 件）。

**修正**: 担当そのままの date_change では、移動先の日にその担当（primary、無ければ元のコースの担当）が持つコースへ付け替え、無ければ臨時コース（臨〜臨9）を作る。add と同じ規則。担当変更を伴う場合は従来どおり移動先の曜日で解決。2 名体制の相方は相方の担当で移動先のコースを引く（無ければ元のまま）。

**handoff / 指示との差**: 指示は「移動先日のコース、無ければなし」、handoff は「無ければ臨」。add の規則（無ければ臨時）と handoff に合わせた。

**データ修復**: 9 月の 4 件は修正していない（コードは今後の取込だけに効く）。

**テスト**: `test_date_change_moves_course_to_target_day`・`test_date_change_without_target_course_creates_temp_course`。

## 5. 予定外打刻の昇格

**再現**: QR の予定外打刻で生まれた訪問（`is_unplanned=true`・`course_id NULL`）が、カイポケに実時刻どおり登録された後も予定外のまま（9/23 川名さんの 6 件）。らく助 CSV の行とカイポケの行が完全一致するので差分が 1 件も出ず、何も起きない。時刻が少し違うと edit になるが、昇格はしない。サービス内容の違いで delete+add に割れると、予定外訪問は（#1 で）残り、カイポケ行が別の訪問として増えて二重になる。

**修正**（3 つの入り口）:
共通の条件（レビュー MEDIUM・PO 決定の 60 分）: **開始の差が 60 分以内**（`UNPLANNED_MATCH_MINUTES`）かつ **カイポケの担当1 が予定外訪問の担当（primary）か打刻した職員**。コースを決められない（患者の主担当拠点が無い等）ときは昇格させない（`is_unplanned` を外して `course_id` が NULL のまま、を作らない）。昇格した行の `source` はどの経路でも `manual_week`（`VISIT_SOURCE_PROMOTED`）。理由: 昇格は取込が新しく作った行ではなく、打刻の付いた既存の訪問をその週だけカイポケに合わせる変更で、edit 経路の他の変更と同じ意味。`manual_week` も `import` も週再生成からは保護される。

1. edit 経路: 予定外訪問に同じ日のカイポケ行が結ばれたら `is_unplanned=false` にし、担当のその日のコース（無ければ臨時）へ載せる。訪問の状態（訪問中/完了）と打刻はそのまま。別の日への移動では昇格させない（打刻済みなら #1 で動かさない）。条件を満たさないときは**時刻も動かさず昇格もさせず** `failed`・`reason="unplanned_mismatch"`（「…60分を超えて離れているため、合わせていません」／「…担当が違うため、合わせていません」）で人に渡す。
2. 完全一致: smart プレビューがカイポケ現況から「同じ患者・同じ日・同じ開始 HH:MM で、担当の条件を満たす行がある予定外訪問」を探し、昇格用の `edit` 行（`comment` 付き）を足す。`diffSummary.unplanned_promote`。
3. delete+add に割れた場合: 適用の事前パスで、条件を満たす (add, 予定外訪問) の候補を**全部**集めて差の小さい順に 1 対 1 で割り当てる（add の並び順で先着させない）。組になった add は新しい訪問を作らず予定外訪問をカイポケの予定（時刻・担当・コース）で昇格させる（`reason="unplanned_promoted"`）。組になった delete の結果は **add の成否が出てから確定する**: 昇格できたら「取り消さず、カイポケの予定に合わせました」、できなければ「打刻済みのため取り消していません」（failed・checked_in）。担当が違う add は昇格させず通常どおり追加し、「近い時刻に担当の違う予定外の訪問があります（別の訪問として追加・要確認）」と注記する（delete は打刻済みのため残る）。カイポケ行の枠に別の訪問が居るときは組にしない。

**制約**: 手動の「差分取込」（`/integrations/diff-inbound`）はカイポケ CSV を受け取らないため、2 の完全一致は smart（通常の取込ボタン）だけ。

**データ修復**: 川名さんの 6 件は修正していない。9/21 週を smart で取り込み直せば 2 で昇格する（その週の他の差分も同時に入るので、プレビューを確認してから）。

**テスト**: `test_smart_exact_match_promotes_unplanned_visit`（秒付きの開始時刻・状態不変・二重にならない）・`test_delete_add_pair_promotes_unplanned_visit`（dry-run/実適用）・`test_add_far_from_unplanned_visit_is_not_merged`・`test_promotion_pairs_nearest_add_globally`（9:00 と 10:00 の add・9:40 の打刻 → 10:00）・`test_promotion_requires_same_staff`・`test_paired_delete_reports_kept_when_add_fails`・`test_edit_path_promotes_unplanned_visit`（5a・source=manual_week）・`test_edit_path_mismatch_is_left_for_a_person`（60 分超・担当違い）・`test_edit_path_promotion_skipped_when_no_course`。

## 6. 藤原様 9/23 の打刻付け替え

一般の規則は #1（今後は取り消さない）と #5。既に起きた分は一回限りのスクリプトで直す。

`backend/scripts/reattach_cancelled_checkins.py`:
- 探し方: `status='cancelled'`（論理削除なし）で打刻のある訪問のうち、**取込の delete で取り消されたもの**（note に `IMPORT_CANCEL_NOTE`「カイポケ側で削除されたためキャンセル」があり、source が今週だけ取消・ステータス連動の取消ではない）× 同じ患者・同じ日の生きている訪問で打刻が 0 件のもの。
- 自動で組むのは、候補がちょうど 1 件・候補の担当が取消の担当か打刻した職員と同じ **または** 候補の開始が取消の開始・最初の打刻から ±90 分以内・その候補を自動で選ぶ取消が 1 件だけ、のすべてを満たすとき。それ以外は「要確認」（理由を表示）で、`--pair 取消ID=移動先ID` で指定したときだけ移す（同じ移動先へ 2 件を当てる `--pair` はエラー）。
- `--apply` には `--from` と `--to` が必須。
- 移すもの: 打刻・写真・時刻の調整・録音の `visit_id`、レビューは移動先に無いときだけ。移動先の状態は退出の打刻があれば `completed`、到着だけなら `in_progress`（planned/in_progress のときだけ）。両方の note に記録。移動先の「未訪問」通知を解消。
- **既定は dry-run**（一覧のみ）。`--apply` で 1 トランザクション commit。

**実行の条件（PO 決定）**: デプロイ後に、バックアップ → dry-run → `--apply`。さらに **対象日の遅れて届く打刻の受付（72 時間）が終わってから**流す。develop にマージ済みの変更（`services/checkin/actuals.py`）は、遅れて届いた打刻（最大 72 時間）を「読み取った日と同じ日の訪問」に載せ、取り消された訪問には載せない。受付中に流すと、付け替えの後に届いた打刻が別の訪問に載るなど組が崩れうる。9/23 分は既に 72 時間を過ぎている。

**本番では実行していない**。本番で該当するのは 1 件（2026-09-23 藤原様・取消済み `00bce4ed-…`（高岡 13:00）の打刻 11:55/12:29 → 熊澤 11:30 `78960321-…`）の見込み。流す手順:

```bash
# 1) バックアップ (runbook どおり)
# 2) dry-run で 1 件だけ・移動先が 78960321-… であることを確認
ssh root@<VPS> 'docker exec -i -w /app carelink-backend python scripts/reattach_cancelled_checkins.py --from 2026-09-23 --to 2026-09-23'
# 3) 確認後に --apply（--from/--to は必須）
ssh root@<VPS> 'docker exec -i -w /app carelink-backend python scripts/reattach_cancelled_checkins.py --apply --from 2026-09-23 --to 2026-09-23'
```

テスト: `test_find_and_reattach_single_candidate`（付け替え後は対象に出ない＝冪等）・`test_multiple_candidates_need_explicit_pair`・`test_date_filter_excludes_other_days`・(a) `test_target_claimed_by_two_cancelled_visits_is_ambiguous`・(b) `test_far_candidate_with_other_staff_is_ambiguous` / `test_far_candidate_with_same_staff_is_paired`・(c) `test_non_import_cancellations_are_ignored`（manual_cancel / status_cancel / 刻印なし）・(d) `test_apply_requires_date_range`（3 通り）。

## 7. 月跨ぎ週のプレビューが約 100 秒（調査のみ）

**計測**: ローカルで 9/28 週（9 月と 10 月にまたがる）・訪問 1,590 件（週 約 180 件、本番より多い）・export をスタブで即時返しにして smart-preview を cProfile。**らく助側の処理は合計 1.1 秒**（差分エンジン 0.5 秒・うち氏名の正規化 0.45 秒、CSV 生成 0.13 秒）。

**結論**: 100 秒のほぼ全部は RPA の同期 export（1 回 約 50 秒）× **2 回直列**（月跨ぎ週は両月を取る `export_current_week_csv`）。非跨ぎ週の実測 45〜73 秒と整合する。らく助側を速くしても効かない。

**検討した案**:
| 案 | 効果 | リスク | 判断 |
|---|---|---|---|
| 2 本の export を並列 | 半減 | RPA は単一スロット（`KaipokeBusyError`） | 不可 |
| smart-preview をジョブ化（202 + ポーリング、イベント取込 524 根治と同じ型） | 時間制限から外れる | BE+FE の改修・状態管理 | 本命。10/12 前に入れるには大きい |
| 直前の週 snapshot（`kaipoke_csv_snapshots`）を再利用 | export 0〜1 本 | 古いカイポケ現況で差分を作る | 取込の正しさに関わるので見送り |
| 日曜だけが翌月の週（10/26〜11/1）は翌月の export を省く | 10/26 週は 1 本 | 週 snapshot に日曜行が入らず ●未送信 の日曜判定に影響 | 見送り（smart は日曜を適用しないが、snapshot 共用の副作用を詰めていない） |

**10/26 週の当面の運用**: 100 秒を超えて画面がエラー（524）になった場合に、サーバ側の処理が最後まで走るかは未検証。取り直す前に `kaipoke_jobs`（`params->>'op'='smart-preview'`）の状態を確認する。ジョブ化は別タスクとして残す。

## 8. 変更していないこと・残り

- migration なし。お客様固有の値の決め打ちなし。
- 本番データは未修正: #4 の 9 月 4 件、#5 の川名さん 6 件、#6 の藤原様 1 件。
- PO 判断は 2026-10-01 に決定済み（冒頭）。
- 既存テストの失敗 8 件（`test_integration_kaipoke.py` の権限まわり。403 を期待して 200 など）は作業前から失敗しており、本作業とは無関係。

## 9. 独立レビュー（REQUEST CHANGES）への対応（2026-10-01）

| 重さ | 指摘 | 対応 |
|---|---|---|
| HIGH | 打刻済みの訪問（予定外を含む・相方を含む）が date_change で別の日へ動く | §1。failed・checked_in・プレビュー注記と `checked_in_move`・通知で数える |
| MEDIUM | delete+add の昇格の組が add の並び順で先着 | §5-3。候補を全部集めて差の小さい順に 1 対 1。担当の条件 |
| MEDIUM | edit 経路の昇格に窓と担当の条件が無い | §5-1。60 分・担当。外れたら時刻も動かさず failed（unplanned_mismatch） |
| MEDIUM | 差分エンジンの同時刻の組が Pass 2 の後 | §2。Pass 1.5 へ移動。outbound 不変をテストで確認 |
| MEDIUM | スクリプトの安全装置 | §6。(a) 取り合いは要確認 (b) 担当 or ±90 分 (c) 取込の取消だけ (d) `--apply` は `--from/--to` 必須・72 時間の注意 |
| LOW | 昇格の `source` が経路で違う | どちらも `manual_week`（§5 冒頭に理由） |
| LOW | コースを決められないのに昇格 | 昇格させない（§5 冒頭） |
| LOW | 組になった delete が add の前に「昇格」と報告 | add の成否が出てから確定（§5-3） |
| LOW | チップの文言 | 「打刻済みのため取り消しません（要確認）」 |
| LOW | 手動の apply-inbound の通知 | 打刻済みの見送りを「失敗」と分けて数える（§1） |
