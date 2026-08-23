# セッション引き継ぎ 2026-08-22〜23（運転席 Phase E 本番化・同期ストリップ刷新・カイポケ サービス内容・休みの1操作化）

**次のエージェントへ: まずこのファイルを読むこと。**
本番 HEAD = `e473b09`（全コミットデプロイ済み・healthz 健全・DB = **mig 0078**）。RPA リポ（VPS `/root/PlaywrightTest1`・コンテナ `kaipoke-api`）HEAD = `4c5303c`。
バックアップ = `/opt/carelink/backups/pre-deploy-20260823-*.sql.gz` ほか（UTC 表示注意）。デプロイ手順は従来どおり（migration 含む時は `build --no-cache` + `alembic upgrade head`）。

---

## 0. 進行中の作業（5h制限で中断され得る）

### 「休みにする」の1操作化（Phase 1）— executor(opus) が実装中・**未コミット**
作業ツリーに BE 変更あり: `schedule_v2.py` / `visits.py` / `schemas/v2/auto_schedule_v2.py` / `op_log_service.py` / 新規 `tests/test_staff_off_week.py`。FE はまだ。
仕様（PO 決定 2026-08-23）:
1. 新API `POST /schedule/v2/staff-off-week {staff_id, date, to_staff_id|null, op_group_id?, reason?}` = 休み登録(override upsert) + その日の担当訪問を全件付替(既定は担当なし) + コース担当 を **1トランザクション・同一 op_group_id**（新 op `set_staff_off` + set_visit_staff + set_course_staff）→「戻る」1回で休みごと戻る。青ピン/過去日/新人/同一人物は 422。
2. FE `SubstitutePanel` を **モーダル**に: 「○○さんを M/D 休みにします。予定 N件（コース…）は担当なしに戻します」。**コース丸ごと引き受けられる ok スタッフがいる時だけ**「△△さんに割り当てる」(最大3名)。1人ずつの提案はしない。[担当なしに戻す](既定)/[やめる]。
3. 「戻る」で戻すものが無い(400)→ toast.info「これ以上戻せません」。
4. 削除済み患者（近藤 菜穂・8/21 削除）の fixed-visits 404 参照を掃除。
**再開手順**: `git status` で BE 差分を確認 → 途中なら executor(opus) に上記仕様を渡して続行（「git 操作禁止・stash 禁止」を明記）→ レビュー → `python -m pytest tests/test_staff_off_week.py tests/test_op_log_u3.py tests/test_visit_cancel_week.py -q` / tsc / `pnpm vitest run components/schedule/v2` → コミット → デプロイ（migration 無しの見込み）。

### Phase 2（未着手・設計から）
「担当なし」行の患者/コースからの **投入提案**（保留プールと同じ「ここに空きがあります/この方はここに入れそう」）。担当なしのコースを上に上げる or 患者単位提案。既存 propose-slots / pool 提案エンジンの流用を前提に設計。

---

## 1. 本セッションで本番化したもの（コミット順・全て develop）

| コミット | 内容 |
|---|---|
| `1441545` | **運転席 Phase E**（急休代替・今週だけ取消・固定イベント週内除外・●未送信+🔄突合の同期バー・横バータイムライン・mig 0075/0076）。設計 `week-cockpit-design.md`・調査 `week-cockpit-investigation.md`・進捗 `week-cockpit-progress.md` |
| `8573a39` | 横スクロールバーを上にも表示（SyncedHScroll） |
| `b582d72` | 週間シフトの既定 09:00〜18:00 + 月〜金/月〜土 一括 |
| `238fc20` `390dab5` `e89877c` | タイムライン拡大（2400px・行54px・文字13px）・氏名列 sticky |
| `5d6e886` | **スタッフ入れ替えDnD + 行アクション**。急休付替も PATCH /courses 経路を廃止し訪問単位(runAssignQueue)に統一。422 確認フロー(useConstraintConfirmRetry)の連続422/中止対応 |
| `be6da50` | 突合: 担当者名の正規化・異体字 槇↔槙（8/17週 偽差分46件の根治）。マスタ修正: 髙梨　桂子/看護師、槙　恵 |
| `42bf03e` | **サービス内容 S1+S2**: patients.visit_category(mig 0077)・スタッフ資格UI・csv 分岐(区分×職員1資格)・diff 前方一致・S3 完了までの送信ガード(`KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=False`) |
| `745deb8` | 訪問単位「カイポケに合わせる」(mig 0078)・資格突合・RPA未対応通知・**ペア保護**(add がスキップなら対の delete も止める) |
| `48bb5ee` `1387270` `e473b09` | 上部ツールバーの折りたたみ（コンパクト表示）・診断/最適化を1段目右端に常設 |
| `4a6c479` `5b57f82` `ed7b182` | **同期ストリップ（方向性A）**: 1行+3ボタン(取り込む/送る/同期確認)・押した時だけカード行パネル・行内「何から何へ」・同期確認はトグル(再実行は「再確認」)・作業中演出 |
| `90c1ee3` | 横スクロール 案A: 上の1本に統一・空き部分ドラッグでパン・初回ヒント |
| `46a56d8` | スタッフ並びをコード順（S001〜S008） |

RPA: `d2f5cbd`（異体字 槇）・`4c5303c`（**S3**: service_type から 区分/基本療養費Ⅰ/職員資格 を value で選択。`probe_service_options.py` / `dryrun_service_branch.py` で実画面確認済み・登録なし）。

## 2. カイポケ同期の現状（8/17週）
- 差分 **0 件**（例外2件＝8/21 唐鎌様/熊澤=准看、峯﨑様/高岡=正看 は訪問単位でカイポケに合わせ済み）。
- 残: **准看1件の本番テスト → `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True`** で送信ガード解除（通知「カイポケへ自動送信できない予定が N 件」が出たら）。小西さん(S008)の資格は運用開始時に設定。8/24週は取込→突合後、8/26 植田様のズレを「この訪問だけカイポケに合わせる」で解消予定。
- 遠隔オペ用スクリプト（VPS `/tmp/unsent_check.sh` / `apply_override.sh` / `live_check.sh`）: admin トークンは `create_access_token(subject=<admin id>, role='admin')`。

## 3. お客様向け資料・モック
- `docs/mockups/kaipoke-service-type-proposal.html`（A4 1枚・印刷対応）
- `docs/mockups/sync-strip-mock.html` / `staff-schedule-week-cockpit-mock.html` / `staff-schedule-reconcile-mock.html`

## 4. 教訓（今セッション）
1. 並行 executor に **git stash 禁止**を明記しても2回起きた → 指示文に「stash も禁止」を毎回書く。復元は pop で可能だったが要確認。
2. レビューで繰り返し出た型: (a) コース単位付替は undo が訪問を戻さない/manual_staff_override を動かせない → **訪問単位に統一** (b) 差分の add をスキップするなら **対の delete も止める** (c) 「確認中」は単に 2〜3 分かかる。サーバ側ジョブ(kaipoke_jobs/ログ)で完了を確認してから FE を疑う。
3. FE テストの jsdom: PointerEvent 無し → 素の Event に座標を載せる / ResizeObserver スタブ / scrollWidth はプロトタイプを defineProperty。
4. 既知ベースライン fail: BE 32 件（HEAD でも同一・manager ロール廃止/RBAC/audit/sqlite フレーク）、FE 1 件(middleware manager)+e2e 収集。増減のみ見る。
