# セッション引き継ぎ 2026-09-07〜09（伊藤様 不規則日程 → 「＋訪問」→ 特別訪問週間の整理 → プール/⭐ の DnD 全ビュー化）

**次のエージェントへ: まずこのファイルを読むこと。** 前セッション総括 = `session-2026-09-03-HANDOFF.md`。本セッションの時系列メモ = `session-2026-09-07-HANDOFF.md`（§1〜§8・追記型で読みにくいので本書を正とする）。

## ★ 最初の 5 分
1. **状態（2026-09-09 18:15 JST 確認）**: 本番 HEAD **`203a5c1`**（develop = origin = 本番）。backend/frontend とも healthy・healthz 200/200・最終デプロイ 9/8 07:40 JST（frontend）/ 06:54 JST（backend）。migration 無し（DB 0081 のまま）。作業ツリーはクリーン（未追跡は従来どおり docs/reports 等）。
2. **お客様案件 2 件は決着済み**: ①松岡様「伊藤様 9/14・17・19・22・25・28 12:00」→ らく助のみ展開済み（担当なし=M・35 分・固定訪問不変・カイポケ未送信・報告書 `docs/reports/2026-09-07-ito-irregular-schedule-report.html`） ②松岡様 9/4「特別訪問週間の ○ が押せない／プールで移動できない」→ 原因特定＋改修＋本番反映（§2・§3）。
3. **本セッションで入れた機能（全部本番稼働）**: 「＋訪問（任意日付の訪問追加）」／固定訪問保存の反映先確認（E）／モーダル可読性基準／プールの M 出口（F-1/F-2）／特別訪問週間ダイアログの全面整理（セル＝メニュー・2 段セル・4 語・配置をダイアログ内で完結）／⭐・プールカードをどこでも掴んで 4 ビュー（日タイムライン・週タイムライン・週リスト・職員スケジュール）へドロップ→「配置の確認」モーダル／BE `place` 拡張（course_template_id・visit_id・weekday）。
4. **PO が「見えない」と言ったら**: PWA キャッシュ。Ctrl+Shift+R。
5. **残タスクは §6**。最優先は実機確認（§6-1）と PO 判断 5 件（§6-2）。

## 1. 進め方（本セッションの方式・次も踏襲推奨）
- **ディレクター方式**: 私（コーディネータ）が設計書を書き契約（API/props）を先に固定 → Opus executor を **ファイル所有を分けて並行投入** → 完了ごとに **別レーンの code-reviewer（Opus）** で BE 契約まで突合 → 是正 → 私がコミット（git 操作はコーディネータのみ・stash 禁止）→ push → デプロイ。
- レビューは毎回 REQUEST CHANGES が出た（FE の推測分類 vs BE の許可リスト／候補順位の再ソート／savepoint で更新が消える／曜日ガード等）。**「BE の実装を読んでから FE を判断」がレビューで最も効いた**。
- 共有ファイル（zod スキーマ等）は私が先に編集して両エージェントに「触るな」と通知（衝突回避）。

## 2. 何を作ったか（機能別・正典設計書）
| 機能 | コミット | 正典 |
|---|---|---|
| **＋訪問**（患者全員・カレンダー複数日・時刻/所要(基本時間)・`propose-slots` を時刻固定で週ごとに呼び候補提案・0 件は主担当拠点→サブ拠点(要確認)→M・反映先 3 択(型/その週/新規)・結果ダイアログ・週ごとの元に戻す） | 5874e7e(E) / bb48c27(Phase0) / cf2b7d5 / ed9738f / f5cbae9(可読性) | `add-visit-anywhere-design.md`（PO 決定 12 点・§3-5 サイズ基準・§10 追跡） |
| **E: 固定訪問保存の反映先確認**（型だけ／型＋対象週。消える/保護/作られる件数は BE `reset_visits_to_fixed` の許可リストを鏡写し） | 5874e7e | 同上 §6 |
| **プールの M 出口 F-1/F-2**（候補 0 件で「担当なし（M）へ入れる」・列外ドロップ案内） | 1ca0dec | `pool-placement-blockers-investigation-2026-08-31.md` §4 |
| **特別訪問週間ダイアログ**（セル＝メニュー・○/● の取消は確認・配置先を決める…＝＋訪問モーダル流用→`place{visit_id}`・期間終了は「…」＋確認・2 段セル・4 語・同日 2 回目確認・週合計内訳） | 5109978 / 464bcfb / 2ea7d39 | `special-visit-week-ux-investigation-2026-09-07.md`（§1 状態モデル・§6 実装記録） |
| **⭐/プールカードの DnD 全ビュー化**（どこでも掴める・置く瞬間に「配置の確認」・曜日違いは警告→○ を移して配置・休は拒否） | 34794eb / e0de2b4 / 203a5c1 | `special-ticket-dnd-design-2026-09-08.md` → `dnd-all-views-design-2026-09-08.md` |
| **BE `POST /special-visit-marks/{id}/place` 拡張**（`course_template_id`＝Course 未生成なら生成・主担当拠点のみ／`visit_id`＝既存訪問の紐付け（patient 一致・planned・同日・source manual_week/manual・二重 409・NG 検査・2 名体制グループ対応）／`weekday`＝同一 TX で ○ を移してから配置・409 `special_mark_cell_conflict`+`existing_mark_id`・IntegrityError も 409・早期 flush） | 0337525 / cfaa42e | 同上 §2-3 |

## 3. 調査で確定した事実（次に効くもの）
- **○ は人が付ける追加枠。システムは ○ を作らない。** 「プール待ち（時間未定）」が実体。9/7 21:09 の PO 自身の操作で「○ クリック＝即取消」を再現（唐鎌様データは元通り）。
- **固定訪問の保存＝型更新＋「今日の ISO 週」再生成**（週文脈なし）。松岡様の 9/7 11:31 保存で今週 W37 だけ変わった。E で既定を「型だけ」に。
- **`patient_allowed_offices` は本番 0 件・API 未公開**。サブ拠点の実体は固定訪問行の `sub_office_id`（51 行）。
- **BE 契約の罠**: `visit-move-week-only` は該当なしでも 200 `visits_moved:0`／`place-and-fix` は 2 名体制に staff_count=2＋異なる 2 テンプレート必須・主担当拠点以外 422・**過去日も閉じた曜日も通す**（Course を作る）／`place` は op-log を書かない（undo 不可）／op-log undo は週単位／PUT fixed-visits は op-log 対象外／session は autoflush=False（早期 flush 必要）。
- **M テンプレート**は稲毛・都賀とも有効（`label='M'`）。
- **バックアップのファイル名は UTC 日付**（`pre-deploy-20260907-2237` = 9/8 07:37 JST）。`docker ps` の Up 時間と突き合わせるときに惑わされない。
- 既存 pytest 失敗 33 件・FE の e2e spec 9 本＋middleware 1 件は既知。`BulkPoolInsertDialog.test` は単独/並列負荷で落ちる既存フレーク（`pnpm vitest run` で他ファイルと一緒なら通る）。

## 4. 教訓
1. FE の分類・順位・ガードは必ず BE 実装と突合する（許可リスト／限界コスト順位／曜日解決／閉講日）。
2. 「掴めない」より「置く瞬間に確認」（PO 明示）。確認モーダルが唯一の砦になるので「閉じたら API が飛ばない」をテストで固定する。
3. 破壊を伴う入替は「新を作ってから旧を消す」順（同時刻のままの入替は一意制約で不可→案内）。
4. モーダルの基準（`add-visit-anywhere-design.md` §3-5）: 幅 max-w-5xl・本文 14px・入力 h-9〜10・行全体クリック。11px 以下は使わない。
5. 並行エージェントには「触ってよいファイル」を明示し、共有ファイルはコーディネータが先に編集する。

## 5. 実機確認の手口（本番・admin）
- API 直叩き: `docs/tools/kaipoke-ops/admin_call.py`（`docker cp` → `docker exec -w /app -e PYTHONPATH=/app carelink-backend python /tmp/admin_call.py METHOD PATH [JSON]`）。読み取り専用の `propose-slots` で候補 0 件の再現ができる。
- DB: `docker exec -i carelink-postgres psql -U carelink -d carelink`。監査ログ `audit_logs`（actor→users.email・request_body）で画面操作を再現できる（GET は記録されない・DELETE は記録される）。

## 6. 残タスク
### 6-1. 実機確認（未・すべて本番で 1 度ずつ）
(a) 患者画面の固定訪問保存 → 確認モーダル既定「型だけ」／盤面の患者詳細からは表示週 (b) ＋訪問: 未来週・テスト患者で 2 日付 12:00 → 提案 → 登録 → 元に戻す (c) 📅 曜日を移動… → 火曜へ → 火曜タブ (d) 担当なし行の＋訪問 → M 列 (e) 特別訪問週間ダイアログ: ○ クリックでメニュー（消えない）→「配置先を決める…」→ ● になる・入れ子 Popover のクリック/フォーカス (f) 2 段セル・週合計内訳・同日 2 回目の確認 (g) プール候補 0 件 → 「担当なし（M）へ入れる」→ M 列 (h) ⭐/プールカードを日/週タイムライン・週リスト・職員スケジュールへドロップ → 確認 → 配置・曜日違いは警告→○ が移る・休の列は拒否・コース帯/訪問帯の標準 DnD が従来どおり。
### 6-2. PO 判断（5 件）
1. 唐鎌様の火曜 ○ 4 件（9/8・9/15・9/22・9/29。型 月火水 16:00/金 16:30 の火曜に重なる）: 川名様の意図（火曜 2 回目）か誤りか。誤りなら取消（`DELETE /special-visit-marks/{id}`）。
2. 過去日ガードを既存のプール ⭐ 経路（course_code）と place-and-fix にも適用するか（現状は新モードのみ）。
3. 固定退避（displaced）チケットもドラッグで置いてよいか（現状は可）。
4. 2 名体制患者を M へ入れる／他拠点候補を「新規追加」で使う（BE 緩和が要る・現状は画面でブロック）。
5. 伊藤様: 今週 9/7・9/10 12:00 の扱い／月木 12:00 の固定訪問を残すか／9/14 の担当（髙梨）。
### 6-3. 改修バックログ
1. **BE 開講日バリデーション**: `place-and-fix` / `place`(course_template_id) が閉じた曜日に Course を作る。今は FE ガードのみ。
2. **入替の 1 TX 化**: `place` に「既配置マークへ visit_id 指定＝旧訪問を削除して差し替え」。同時刻のままの入替が可能になる。
3. `place` の op-log 対応（undo）／op-log undo を op_group_id 指定で。
4. 特別訪問週間カレンダー API に退避元スナップショット（時刻/コース）を載せ、退避日に打ち消し線カードを出す。
5. ＋訪問モーダルの時刻 select で所要時間に収まらない選択肢を無効化（現状は登録時に警告）。
6. 前セッション残（`session-2026-09-03-HANDOFF.md` §6）: running 残骸ジョブの決着／都賀A 9/10 担当 4 件／レポート Phase 3／ミラー未対応 3 箇所／患者ステータス非表示 ほか。

## 7. 参照
- 設計: `add-visit-anywhere-design.md`・`special-visit-week-ux-investigation-2026-09-07.md`・`special-ticket-dnd-design-2026-09-08.md`・`dnd-all-views-design-2026-09-08.md`
- 報告書（未追跡・患者名あり）: `docs/reports/2026-09-07-ito-irregular-schedule-report.html`（§5 は「＋訪問」手順に更新済み）
- 主要コード: FE `components/schedule/v2/{CourseDayTablePanel,PlacementConfirmDialog,SpecialVisitWeekDialog,SpecialVisitPlaceLauncher,SpecialTicketPlacePanel,PoolCandidateList,StaffWeekBoard,CourseWeekOverview,courseDnd}.tsx|ts`・`components/schedule/v2/cockpit/{AddVisitAnywhereDialog,AddVisitAnywhereRows,AddVisitResultDialog}.tsx`・`components/schedule/timeline/WeekTimelineBoard.tsx`・`lib/scheduling/{addVisitPlan,addVisitExecutor,courseTemplateMatch}.ts`・`app/(app)/patients/_components/FixedVisitScopeConfirmDialog.tsx`／BE `app/api/v1/special_visits.py`・`app/schemas/special_visit.py`
- メモリ: `careflow-ito-yumeka-irregular-request`・`careflow-ui-adhoc-visit-gaps`・`careflow-ui-modal-readability`・`careflow-special-visit-week`・`careflow-pool-force-placement`
