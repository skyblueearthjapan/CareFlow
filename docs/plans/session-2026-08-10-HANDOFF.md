# セッション引き継ぎ 2026-08-08〜10（ピン統合・取込全週開放・賢いマスタ・ロール二軸・QR印刷）

**次のエージェントへ: まずこのファイルを読むこと。**
本番 HEAD = `91278f7`（全コミットデプロイ済み・健全確認済み）。
DB migration head = `0069_retire_manager_role`（0066〜0069 をこのセッションで適用）。
バックアップ = `/opt/carelink/backups/backup_pre0067_*` / `backup_pre0068_*` / `backup_pre0069_*`。

デプロイ手順は `docs/deployment/runbook.md`（migration 含むときは build `--no-cache` +
`compose run --rm backend alembic upgrade head` 手動実行 + `alembic heads` 1 行確認）。
compose は `/opt/carelink` で `-f docs/deployment/docker-compose.production.yml --env-file .env`。

---

## 1. ピンモデル統合（赤=完全固定・青=蓋）— 最重要の概念変更

正典 = `docs/plans/pin-and-movability-spec.md`（統合後の正仕様へ全面改訂済み）。

- **赤ピン = 完全固定 = `PFV.movability='locked'` の 1 概念**（mig 0067 で is_pinned をミラー同期。
  is_pinned は非推奨ミラーで全書込経路が同期を維持）。
- **意味 =「エンジンは動かさない・人手はいつでも編集できる」**。旧 pfv_validator V2
  （pinned 行の PUT 422）は全廃。人手が動かす時は確認 UI に「⚠ これは完全固定です」。
- **青ピン = `visits.week_pinned` フラグ（mig 0066）= 蓋**。刺さっている間は人手操作も
  ブロック（BE 422 + FE ドラッグ/削除不可）。解除してもその場では動かず次の週生成で型復帰。
- **エンジン経路の完全固定ガードは明示実装**: apply-swap / 手順移動 / プール投入上書き
  （`schedule_v2.py`）。⚠ V2 撤廃時に swap の保護が道連れで消えていた事故を
  2026-08-09 に発見・復旧済み（教訓 §9）。
- `_serialize_visit`（visits.py）は手書き dict。**新カラムは明示追加しないと API に出ない**。

## 2. 基本の訪問時間（希望=ベース・35 分）

正典 = `docs/plans/base-visit-minutes-design.md`。

- 希望訪問パターンの service_minutes = 患者の基本時間。型（PFV）はそれをデフォルト継承、
  変更はイレギュラー（「基本N分と異なる」琥珀バッジ）。
- 所要時間セレクトは希望側と同一ソース `SERVICE_MINUTES_OPTIONS`（15〜180・5 分刻み）
  + 基本 + 現在値。デフォルト 35 に統一（FE zod / BE Pydantic / 空き枠登録）。
- ドリフト 3 名（P062/P069/P091 希望35 vs 型30）は PO 確認のうえ本番 SQL で 35 に修正済み。

## 3. カイポケ取込: 全週開放 + 取り込み前に戻す

正典 = `docs/plans/kaipoke-reverse-sync-design.md` 冒頭の 2026-08-09 改訂ノート。

- **時間ゲート撤廃**: `inbound_week_eligible` は常に True。未来週も無制限（客先要望・
  「カイポケで先に計画」運用）。安全は内容ベース: 空CSV拒否 / dry-run / 大量キャンセル警告 /
  打刻ガード / FE 未来週警告帯。RPA の未来月 export は PO が実機確認済み（動く）。
- **取り込み前スナップショット（mig 0068 `inbound_snapshots`）**: 実適用直前に週の盤面
  （visits+割当+同行+コース）を自動保存（diff/replace/smart の 3 経路・同一 TX・週 5 世代）。
  取り込みカード「取り込み前に戻す」でワンクリック復元。打刻の付いた週は復元不可 422。
- 取込は週生成に依存しない（置換/smart は白紙週でもコース自前構築）。
  「先に取込→後から週生成」も安全（import 日はスキップ保護）。
- 連携画面 UI: 稼働状況カードは flex-1 で下辺揃え + 直近ジョブ履歴 5 件。
  取り込みカード最下部に「直近の取り込み」履歴（実適用のみ・週/種別/成否）。

## 4. 賢いマスタ（案Z）+ 二段検査 + 空き提案導線

正典メモ = memory `careflow-smart-master`。

- 患者マスタの固定訪問スケジュール編集に:
  ① 入力停止 700ms でライブ検査（常設表示・`POST /fixed-visits/validate` dry-run）
  ② コースセレクトに「A ○ 残85分」「B × 満杯」（`GET /fixed-visits/course-load`）
  ③ 警告あり保存は確認ダイアログ必須 ④ 正規ルート案内。
- **二段検査**: V3=型vs型（毎週）と V8=`week_conflict`（型vs今週の実配置・【今週のみ】表示）
  を分けて表示。移動 20km/h + バッファ 8 分 + 動的昼休み判定は全経路で同一部品。
- 「この患者様の空き提案を見る」→ `/schedule?proposePatient={id}` deep link で
  既存提案ダイアログが直接開く（CourseDayTablePanel が 1 回だけ発火・URL から消す）。
- 未実装の合意事項: 盤面オーバーレイ「ここに入れられます」は**時間軸の理由であえて見送り**
  （型を書く画面に特定週の空きを見せない）。やるならスケジュール画面側の提案モードとして。

## 5. 患者マスタ ステータスタブ + QR 印刷一式

- 一覧にステータスタブ（稼働中69/開始前6/一時休止6/入院中6/解約済み11/すべて・件数バッジ・
  URL ?status= 同期・既定=稼働中）。旧「有効のみ」チェックは死んでいたため撤去。
- QR 印刷: ① AppShell ごと印刷される問題を `body:has(#qr-print-area)` 方式で修正
  （受入枠 #acceptance-print の実運用方式を移植）② 末尾白紙ページ解消（最終可視シートは
  改ページしない + 高さ 296.5mm）③ ステータス絞り込みチップ（既定=稼働中・マスタから
  ?status= 引き継ぎ）④ 戻るボタン（一括=マスタへ+status 引継/個別=患者詳細へ）
  ⑤ ツールバー明示 2 段構造（1段目=戻る+見出し/2段目=左絞り込み・右操作）。

## 6. ロール二軸分離（mig 0069）

正典 = `docs/plans/role-two-axis-design.md`。

- **アカウント権限 = admin(管理者)/staff(一般) の 2 値。manager 廃止**
  （川名様 s001 → admin 昇格・PO 承認済み。本番 admin4/staff6）。
- `normalize_user_role('manager')=='admin'` の恒久別名（models/user.py）。
  require_role/管理API保存/直接比較すべて正規化済み。**新しい権限チェックは
  `require_role("admin")` で書く**（"manager" リテラルを増やさない）。
- FE = `lib/rbac.ts` の `isAdminRole()`/`userRoleLabel()`（20 ファイル置換済み）。
- **業務ロール（Staff.role: staff/manager）は不変・エンジン専用**（manager=自動割当対象外・
  救済のみ・人数カウント外）。UI は「業務ロール」表示 + 説明文。

## 7. 受け入れ枠マトリックス: 設定漏れ診断

- per-office `setup_state`: `not_generated`（週を生成の案内）/ `assignment_pending`
  （自動スタッフ割当の案内 — この工程が proposed→course_fixed 昇格を兼ねる）/ null。
- ⚠ **「マネージャー不在」診断は入れて即撤去した**（訂正済み 0622652）:
  マネージャー在籍が条件なのは M 系予備枠だけで、M 系は母集合から除外済み。
  都賀はマネージャー 0 名でも staff_assigned コースで ○× 算出可（実証済み）。
- 構造的限界（未対応・要件化されず）: 患者ゼロの新拠点は週生成してもコースが出来ず
  ○× を出せない（コースは訪問展開から生まれるため）。

## 8. 残タスク・保留

- ピン第 3 期候補（未着手・PO 未発注）: 最適化 Before/After のピン表示 /
  モバイル・現場ボードのピン表示 / ズレ枠の赤ピン表示設計。
- 賢いマスタ: ○△× 閾値（△=残60分未満）は PO フィードバック待ち。
  「型ベースの空きヒント」（マスタ内に恒久軸の空き時間帯表示・案2）は提案のみ。
- 提案モード（スケジュール画面の盤面ゴースト表示）は構想のみ。
- 業務ロール Excel 取込（staff_excel/schema.py ROLE_VALUES に 'admin' が残る）は互換のため
  据え置き。整理するなら業務ロール 2 値へ。
- 受入枠の新拠点（患者ゼロ）問題（§7）。

## 9. ハマり所・教訓（今セッション発生分）

1. **保護機構を共有部品ごと撤廃すると、別経路の保護が道連れになる**: V2 撤廃で
   swap 適用の完全固定保護が消えた（テスト全体を回して発見）。エンジン経路の不可侵は
   validator 依存にせず**経路ごとに明示ガード**すること。
2. **Bash ツールの heredoc は `\\n` が 1 段階アンエスケープされる**ことがある。日本語や
   backslash を含む置換は python スクリプトを Write してから実行するか Edit ツールを使う。
3. **python 置換スクリプトは全 assert 通過後に書き込む**構造にする（途中失敗で
   部分適用されない）。ただし逆に「成功したと思ったら書き込まれていない」もあるので、
   置換後に grep で確認。
4. aiosqlite の既知フレーク「cannot commit - SQL statements in progress」:
   バッチ実行時のみ発生。頻発するテストは先頭に `await db.rollback()` を入れて安定化
   （test_visits / test_admin_users で実施済み）。
5. **既知のベースライン失敗**（変更前のクリーンツリーで再現確認済み・触らない）:
   `test_visit_v2.py::test_visit_v2_two_staff_pattern`（'str' has no attribute 'hex'）、
   旧メモの test_schedule_v2_api reset_to_fixed×2 / audit middleware / auth lockout /
   kaipoke integration / patients_v2 系。FE は BulkPoolInsertDialog の並列実行フレーク。
6. ruff format / prettier は**必ず対象ファイルを列挙して実行**（リポジトリ全体に掛けない）。
7. 印刷ページは AppShell（h-screen + overflow-hidden）に包まれるため、
   `body:has(#print-id)` の visibility 方式でスコープするのが本リポジトリの正攻法。

## 10. 主要コミット（時系列・全て本番反映済み）

7d982c0 ピン統合(0067) / 6112548+a0f3563 基本の訪問時間 / 5c0093b 取込全週開放 /
bcac229 スナップショット(0068) / abdc630+1ac071a 連携UI履歴 / f92f77d+6109825 賢いマスタ+二段検査 /
d698b12 ステータスタブ / 884fd34 ロール二軸(0069)+swapガード復旧 / 6fcba90+0622652 受入枠診断 /
d3a5a12〜91278f7 QR印刷一式
