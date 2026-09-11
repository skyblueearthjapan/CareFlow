# セッション引き継ぎ 2026-09-10（患者ステータス×予定の連動: Phase 1 デプロイ → Phase 2 入口ガード → Phase 3 表示の保険・突合）

**次のエージェントへ: まずこのファイル → `patient-status-schedule-design-2026-09-09.md` §6-C（PO 確定事項）・§7（実装計画・「実装済み」の囲み）を読むこと。** 前セッション総括 = `session-2026-09-09-HANDOFF.md`（Phase 0 一掃・設計確定・Phase 1 実装）。

## ★ 最初の 5 分
0. **本番稼働 HEAD `5c10906`（2026-09-10 朝・migration 0082 適用済み）**。Phase 1〜3 + PO フィードバック是正（バッジはステータス変更日以降の予定だけ）が全部本番に入っている。作業ツリーはクリーン（未追跡は従来の docs/reports 等のみ）。
1. **コミット**: `462a8d8`（Phase 1・前セッション）→ `4f7082e`（Phase 2 入口ガード）→ `14d53d5`（Phase 3 表示・突合）→ `5c10906`（PO フィードバック: 「入院中」バッジは `status_changed_at` の JST 日付以降の planned だけ・未記録は今日以降・DTO に `patient_status_since`・現場ボード/モニターは表示日で判定）。各 Phase は BE/FE 2 レーン並行 → code-reviewer（Opus）2 本 → 是正 → 独立確認（pytest 対象スイート緑・tsc 緑・vitest 2335/2336 = 失敗 1 は既知 middleware）→ コミット。
2. **前セッションの「落ちた」の正体**: Claude Code のセッション自体が 9/10 00:02 JST に終了。最終報告まで書き終えた区切りで止まっており作業損失なし。本番サーバーは無事（再起動・OOM なし）。
3. **デプロイの教訓（新規）**: この手順では **migration が自動適用されない**。`up -d` 後に `docker compose ... run --rm backend alembic upgrade head` → `alembic heads` 単一確認が必須（9/10 に 0082 が未適用のまま healthz 200 だった）。memory `careflow-deploy` に追記済み。
4. **残 = 実機確認（§4）と PO 判断（§5）**。共通パスワードはリポジトリに無いので認証付きの実機確認は PO 側で。
5. PO が「見えない」と言ったら PWA キャッシュ（Ctrl+Shift+R）。

## 1. 各 Phase でできたもの（正典 = 設計書 §7 の囲み + 各コミットメッセージ）

### Phase 2 入口ガード（4f7082e）
- BE: `ensure_patient_schedulable`（422 `patient_not_active` + `can_override:true`）を place-and-fix / fix-or-pattern / POST visits / apply-individual / visit-move-week-only / update-fixed-time-master / apply-swap / propose-slots(existing_patient_id) / sync-fixed-to-week / ⭐ period・place・restore・marks・displace に配線。**PUT fixed-visits と apply-individual は型のみ許可**（pattern_and_week は 422 + `allowed_scope:"pattern_only"`）。一括系（pool-overview / bulk-simulate / bulk-apply）は除外して `excluded_patients[]`。⭐ プール/カレンダーは除外せず `patient_status` を載せる（Q⭐「残す」）。取込 preview は非稼働 add 行を `inactive_patient` で自動選択外 + `summary.inactive_patient`、apply/replace は `reason/code="inactive_patient"` で skip。PATCH /visits は非稼働への付替え・`status_cancel` の planned 復活を 422（manual_cancel は既存の戻し経路を維持）。correction-items PATCH の MissingGreenlet 500 を修正。
- FE: `PatientNotActiveGateProvider`（app/providers.tsx）+ `useGuardedMutation` を全配置経路に配線。422 → 「予定に入れられません」→「稼働中にして続ける」→ `PatientStatusChangeDialog(to='active')` → 復帰トースト → 元操作を 1 回だけ再実行。同一患者の並行呼び出しは相乗り、別患者は後着を元エラーで拒否。⭐チケット / PatientCard / 期間ダイアログの非稼働バッジ。ピッカー除外（SuggestSheet / PlacementSheet / PatientCombobox / ＋訪問）。固定訪問パネルは「型のみ編集」バナー + scope 固定（三重ロック）。取込 preview チップ「非稼働患者 N」+ 行バッジ。pool-overview の除外トースト。

### Phase 3 表示の保険・突合（14d53d5）
- BE: `VisitRead.patient_status`、`BoardVisit` / `MonitorVisit` に `source` + `patient_status`。突合レポートに「非稼働患者」カテゴリ = **今日以降・カイポケのみ・患者非稼働** の行だけ（削除候補）。4 構造キーは不変で内数、専用セクション。`_read_correction_items` は delete 行にも `inactive_patient`（inbound=らく助側の残骸取消 / outbound=削除候補）。未送信サマリに `inactive_groups[{patient_id,patient_name,status,status_label,count,sendable_count}]` + `inactive_residue`（今日以降 planned・⭐配置除外・拠点スコープ・突合不能時は 0）。pool_bulk_inserter は `BulkUnplaced(reason="patient_not_active")`。
- **設計判断（レビューで確定・設計書 §3-4/§5 より優先）**: csv_builder と 提案/実現性/health/代替 の占有計算は **非稼働患者の planned を除外しない**。理由 = ⭐「残す」配置と過去の実績を守る・二重予約を防ぐ・残骸は隠さず `inactive_residue` で報告する。除外するのは「候補としての非稼働患者」（Phase 2 ガード）だけ。
- FE: `lib/schedule/visitVisibility.ts`（`classifyVisitDisplay` 単一ソース）。トグル「非稼働を表示」（`useUIStore.showInactiveVisits` 永続化・盤面ツールバー右端）。status_cancel は既定非表示、表示時は打ち消し + 「取消（連動）」でメニュー/DnD 不可。非稼働 planned は「入院中」バッジ + 薄表示（完了/不在/取消には付けない）。型へ反映ダイアログと件数集計はトグル非依存。現場ボード / モニター / モバイルは常時非表示 + バッジ。SyncBar は行種別タグを残して非稼働患者バッジを追加（add=取り込まない / delete=削除候補）。未送信「◯◯様 入院中の取消 N 件（うち過去 M 件は送信対象外）」+ 残骸アラート。突合レポートボタンに非稼働患者件数。

## 2. 進め方（このセッションで機能した型）
- コーディネータ（私）が契約（422 の形・追加フィールド名）を先に固定 → BE/FE executor（Opus）を並行投入 → 完了ごとに code-reviewer（Opus）で **契約突合** → 是正指示 → 独立確認 → コミット。
- レビューは実際に BLOCKER を捕まえた（Phase 3 BE: csv_builder が過去実績まで落として削除候補に載せる）。**レビュー無しでは本番に出ていた**。
- **禁止事項の再確認**: 並行レーンで `git stash` 禁止（2 レーンが「stash で確認」と報告 → 実害なしを確認したが指示に明記が必要）。`cp` での退避も共有ファイルでは危険。

## 3. 教訓
0. 訪問の「完了」は QR 退室打刻でしか付かない（日付経過では変わらない・no_show も planned 据置）。「planned のまま残った過去日」は実績である可能性が高く、現在ステータスで塗ってはいけない。
1. migration は自動適用されない（§0-3）。
2. 「非稼働患者の予定を除外」は文脈で意味が逆転する: **候補**からは除外、**占有**からは除外しない。設計書の一文（§3-4「提案系は対象外に」）はこの区別を欠いていた。
3. `inactive_patient` のような真偽フラグは方向（add/delete）で意味が反転する。FE は必ず action で分岐する。
4. モニターの DTO は `status` を持たない（`phase`）。共有ヘルパを使い回すときは DTO の語彙を確認する。

## 4. 実機確認（本番・admin・テスト患者で・設計書 §7-7 相当）
(a) テスト患者に型を作り週生成 → 入院中へ → ダイアログ件数 = 盤面の件数 → 確定 → 盤面/タイムライン/職員スケジュール/現場ボード/モニター/モバイルから消える → トグル「非稼働を表示」で打ち消し表示 → 「稼働中」に戻す → 週別件数 → 予定が戻る。
(b) 特別訪問週間ありのテスト患者で keep/end 両方。keep 時: ⭐ プールに「入院中」バッジ・配置は 422 → 「稼働中にして続ける」で復帰 → 再実行される。
(c) 入院中の患者を ＋訪問 / 空き枠登録 / プールから配置しようとする → 案内 → 「稼働中にして続ける」→ 復帰トースト → 元操作完了。「やめる」→ 従来のエラートースト 1 回だけ。
(d) 入院中の患者の固定訪問パネル → バナー「型のみ編集できます」・週反映の選択が固定。
(e) 現場シート（/m）からのステータス変更でもダイアログ。
(f) 突合（🔄）→ 非稼働患者の未来のカイポケ行が「非稼働患者（削除候補）」。過去の実績行は一致のまま。未送信サマリに「◯◯様 入院中の取消 N 件」。残骸 0 なら残骸アラートは出ない。
(g) 取込 preview → 非稼働患者の add 行は未選択 + 「非稼働患者 N」チップ。
(h) 通知（admin 全員・申請者）。

## 5. PO 判断・バックログ
- `PATCH /special-visit-periods` は未ガード（非稼働患者の期間を延長できる）。Q⭐「残す」に隣接するので PO 判断。
- 復帰後の再実行が別エラー（409 など）で落ちるケース: 復帰トースト + 元経路のエラートースト 1 回。文言改善は将来。
- カイポケ週間パターン停止は人手（通知文に記載）。RPA 化は将来。
- 効果日（入院予定日）の事前指定（設計 §6-3）は将来。
- 前々セッション残（`session-2026-09-08-HANDOFF.md` §6）: 実機確認 (a)〜(h)・PO 判断 5 件・BE 開講日バリデーション・入替 1 TX。
- 既知失敗: backend 29 件（RBAC drift 13・audit 3・patients_v2 3・reset ペア整列 2・diff engine float.replace 2・misc）+ フレーク 3（test_visits delete 204 / qr_open_checkin / admin_users）。frontend 1（middleware manager）。

## 6. 参照
- 設計: `docs/plans/patient-status-schedule-design-2026-09-09.md`
- 前セッション: `docs/plans/session-2026-09-09-HANDOFF.md`
- デプロイ: memory `careflow-deploy`（migration 手動適用を追記済み）/ `docs/deployment/runbook.md`

## 7. 9/10 午後の不具合連絡 3 件（松岡さん・Slack）
1. **藤原朋夏様を稼働中に戻せない** → BE は 15:14:32 の 1 回目で完了（status=active・W37〜W42 を型から再生成・⭐配置 5 件維持・以後 4 回の再押下は direction=none の no-op）。原因は FE の `statusChangeResultSchema.patient` がフォーム用 `patientReadSchema`（sex_restriction/note の null 不可）だったこと。**是正 32c9b09（v2 read スキーマ + 「すでに稼働中です」表示）・本番デプロイ済み**。
2. **患者マスタ API 404（篠原千晶様 P104）** → 不具合ではない。15:18:44 に松岡さん自身の admin アカウントから `DELETE /patients/{id}`（論理削除）。復元は PO 指示待ち（`deleted_at=NULL` に戻すだけ・予定/型なし）。
3. **8 月レセプトで本名さんの実績が重複** → らく助起因ではないと判断（証拠: RPA は予定側 '01' のみ操作・実績側は読まない/書かない=`auto_apply.py:141,531`。8/31 22:30 の実績 CSV に重複 0。「未・予定外」行の時刻＝当時の予定（週間パターン or 8/24 時点の予定）の時刻・職員＝本名）。仕組み: 予定→実績反映で作られた未確定行が残ったまま、本名さんの実際の実績（記録Ⅱ付き・時刻が 5〜15 分ずれる）が別行で登録され、9/1 の実績合わせで予定を実績側に寄せた結果、未確定行が「予定外」に変わって目立つようになった。本名さんの 8 月実績 96 行中 60 行はパターン時刻と不一致＝重複候補。対処＝カイポケ 職員別›本名›実績の「未・予定外」行を削除（純正）。将来は RPA の実績 export（`planAchievementsDivision02`）で予実比較レポートを出せば機械的に検出できる（残タスク⑩）。

## 8. 予定×実績 突合レポート（段階①）本番稼働（9/10 夕方）
- **本番**: らく助 `e371043`（mig 0083 適用）+ RPA `362150f`（`docker compose up -d --force-recreate kaipoke-api` で反映＝単一ファイル bind mount の罠、memory `careflow-rpa-deploy`）。設計・比較ルール・限界・リリース手順 = `docs/plans/plan-actual-compare-design-2026-09-10.md`。
- **初回実行 2026-08（17:54〜17:55 JST・64 秒）**: 予定 524 / 実績 524（別 CSV・md5 相違・実績は記録Ⅱ列付き 21 列）→ 一致 493・時刻ズレ 29・担当違い 2・予定のみ 0・実績のみ 0・重複 0。時刻ズレの大半は 8/31（9/1 の実績合わせで対象外だった日）、担当違い 2 件は 8/31 熊澤→髙梨。**未確定（未）行は CSV に出ない**ため本名さんの重複は本レポートでは 0（既知の限界・段階②で対応）。レポート = `docs/reports/2026-09-10-plan-actual-2026-08.html/.pdf`（未追跡）。
- 使い方: 連携（カイポケ）コンソール › 「予実比較レポート（月）」カード › 月を選び「実績を取得して比較」（約 2 分・RPA 実行中は不可）→「最新のレポートを開く」。ジョブ履歴の 📄 予実レポート。運用ツール: `docs/tools/kaipoke-ops/admin_call.py`（timeout を 900 に伸ばした版を `/tmp/admin_call_long.py` で使用）。
- レビューで捕まえた重大事項: 旧 RPA は `division` を無視して予定を返す → BE は echo 検証で fail-closed / 週単位スナップショットが月次レポートに混入 → 月全体のみ＋ジョブ記録の snapshot 優先 / 同日 2 回訪問の誤重複 → (担当,時刻) キー / export 失敗時に空 CSV を「カイポケ空」と誤解 → `ensure_export_ok` を diff-local/週 export/master-reconcile に配線。
- 次 = 段階②（月間スケジュール画面の実績側を読み取り「未・重複・予定外」の印を拾う）。
- **9/10 21:03 PO 実行の 2026-07 が失敗**（「実績 CSV を取得できませんでした（RPA: CSV出力に失敗しました）」）→ 正体 = RPA 既知の不安定「スケジュール表のクリック 30 秒タイムアウト」。2 回連続 export でカイポケが直接「出力条件 設定」画面を返し、bare text ロケータが見出しに当たっていた（失敗時スクショ `artifacts/export_schedule_error_screenshot_20260910_210319.png` で確認）。**RPA `ad780b1` で根治**（設定画面検出→クリック不要・リンク限定ロケータ 10 秒・到達検証・goto から 1 回再試行・タイトル記録・tests 10 件）→ 再作成デプロイ → **2026-07 再実行成功（94 秒・予定 515/実績 516・一致 184・時刻ズレ 330・相違 1・実績のみ 1）**。7 月は実績合わせ未実施のため時刻ズレが多いのは想定どおり。
- **訂正（9/10 夜）**: 「未確定（未）行は実績 CSV に出ない」は誤り。「未」= **職種未設定** の職種バッジ（正=正看/准=准看）。実績 CSV には職種１が空の行として **出る**。本番データで確認: 6/9 兼行様・本名（未 17:00-17:35 + 正 17:05-17:45）、7/29 小俣様・本名（正 16:20-16:55 + 未 16:25-17:00）。8 月は事務が未行を削除済みのため 0、9 月は進行中で 0。**すべて本名さん**。7/29 の未行はらく助の予定（高岡 15:30）と一致せず当週の送信ジョブも無い → らく助送信起因ではなさそう（作成元はカイポケ側履歴が削除済みで未確定）。
- **`4cd08d1`**: レポートに「職種未設定」（職種１空）と「同日複数」（同一担当・時刻違いの複数行）の内数タグを追加・注意書きを訂正・FE 注記も更新。これで本名さん型の二重登録を自動一覧化できる（段階②の画面読み取りは必須ではなくなった＝優先度を下げる）。
- 設計書 `plan-actual-compare-design-2026-09-10.md` §3 の「未確定行は CSV に含まれない」も同様に読み替えること（次回更新時に修正）。
- **本番 `4cd08d1` でタグ付き再生成（22:0x）**: 6 月 職種未設定 1・同日複数 2（6/9 兼行様・本名）／7 月 同（7/29 小俣様・本名）／8 月 0（削除済）／9 月 職種未設定 1（9/8 峯﨑様・髙梨: **予定側**の職種が空・RPA の操作記録なし）。レポート = `docs/reports/2026-09-10-plan-actual-2026-0{6,7,8,9}.html/.pdf`（未追跡）。
- **RPA 起因の仮説（未証明・要注意）**: `auto_apply.py:2860` 時間変更は **削除→再追加** で処理し、9/1 の修正前は追加行の職種が未設定になっていた（`auto_apply.py:1120` 経緯コメント）。→ 9/1 以前に RPA が時刻を変えた予定行は「未」の新規行になり、予定→実績反映でその「未」が実績側に残り得る。本名さんは熊澤さんの担当を引き継いで時刻がずれる訪問が多く、対象になりやすい。ただし 7/29 小俣様はらく助の送信ジョブが無い（GAS 時代の送信の可能性）。証明にはカイポケ側の作成履歴が要る。

## 9. 9/11 朝: W37（9/7 週）カイポケ正→らく助 取込の検証と是正
- PO 依頼「9/8〜13 をカイポケへ反映したい／7 日を書き換えたくない」→ 調査（書き込みなし）: 週送信には過去日ガード（当日以前は送らない `integrations.py:1816`）があり 9/7〜9/10 は送られない。一方 9/3 以降らく助の W37 は未更新でカイポケ側だけ手直しされており、送ると 9/11・9/12 の担当付け替え・同行者設定を戻してしまう → PO 判断「カイポケ正でらく助へ取込」。
- 再調査（9/11 午前）: **松岡さんが 08:47〜08:52 に連携画面から取込適用済み**（smart-apply: 9/7 打刻あり=差分 更新16/追加2/取消7・9/8〜12 置換 白紙115/挿入116・イベント29・失敗0）。取込後の残差（机上 outbound diff・snapshot daa27773）= 41 → 日時/担当の食い違いは **久須見様 9/7 11:00 髙梨 1 件**（カイポケの日付変更 9/8 15:00 高岡→9/7 11:00 髙梨 が、置換日(9/8)→差分日(9/7) をまたぐため取り込み側で行が消えた=**smart-inbound の不具合・バックログ**）。残り 40 = 同じ訪問でサービス内容（正看/准看）だけ違う 20 対（csv_builder が職員1資格で判定＝残タスク⑦・送信方向でのみ問題）。
- 是正（PO 承認・04:33 バックアップ `pre-kusumi-0907-add-20260911-0433.sql.gz`）: `POST /visits` + `POST /visits/{id}/staff` で 久須見様 9/7 11:00-11:35 髙梨・コース D・source=import を追加（visit 3d2b70ae）。
- 道具: 机上 diff = `/tmp/dry_diff_w37b.py`（build_local_diff に current_csv 注入・rollback・RPA 不使用）。
