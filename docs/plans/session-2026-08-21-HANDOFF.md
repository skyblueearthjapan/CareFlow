# セッション引き継ぎ 2026-08-17〜21（524根治・休み申請一式・イベント双方向連携）

**次のエージェントへ: まずこのファイルを読むこと。**
本番 HEAD = `960232f`（全コミットデプロイ済み・healthz 健全確認済み）。
DB migration head = `0074_staff_event_defaults`（本セッションで 0073・0074 の 2 本を新設・適用済み）。
RPA リポ（VPS `/root/PlaywrightTest1`・コンテナ `kaipoke-api`）HEAD = `4155fd3`。
バックアップ = `/opt/carelink/backups/pre-deploy-202608{17,18,20,21}-*.sql.gz` 多数。

migration 込みデプロイ手順は従来どおり: pg_dump → pull → `build --no-cache` →
`compose run --rm backend alembic upgrade head` + `alembic heads` 1本確認 → BE/FE 再作成。
FE のみの変更は通常 build で可。

---

## 0. どのようなアプリケーションか（30秒版）

**らく助**（旧称 CareFlow・**「楽スケ」と書かない** — 2026-08-21 ユーザー訂正）=
訪問介護スケジューリングアプリ。本番稼働中（VPS root@72.60.211.213 /
https://carelink.kaipoke-api.net / コード `/opt/carelink`・develop ブランチ）。

- 構成: `backend/`(FastAPI+SQLAlchemy+Alembic) / `frontend/`(Next.js 15+pnpm) /
  RPA別リポ(PlaywrightTest1・カイポケ操作・単一スロット)
- 3UI: PC版 `(app)` / 現場ボード `(field)` / モバイル `(mobile)`（下部タブ4つ）
- 中核: 週生成(Layer1)→自動スタッフ割当(Layer3)→カイポケ連携(取込/送信/CSV)。
  コース目線の盤面 + 「職員スケジュール」タブ(スタッフ目線・今セッションで新設)
- 小規模運用の実態: スタッフ6名(S001-S008)・全員アカウントあり・統一パスワード
  (`staff-login-cards.html` 参照)・admin 5名
- テスト: BE `python -m pytest`（`uv run` 不可）/ FE `pnpm vitest run`+`pnpm tsc --noEmit`。
  日本語ファイル置換に PowerShell Get/Set-Content 禁止（Edit ツール必須）

## 1. このセッションでやったこと（時系列サマリ）

### 8/17: イベント取込 524 の根治（お客様報告起点）
- 真因2層: ①RPA `individual_tasks.py` の裸 networkidle 30s タイムアウト（ガード化で根治・
  RPA `70d42b4`）②同期60〜140sがCloudflare約100s制限を跨ぐ構造問題 →
  events-preview を「start(202)→/status/{job_id} ポーリング」型へ（`d694f43`）。
  この「job_id エコー型」が以後の新規カイポケ通信の標準型。
  正典 = `kaipoke-events-async-preview-design.md`

### 8/18: 申請機能の再構築（休み申請一式）
- **モバイル休み申請 `/m/leave`**（`5db2a00`）: カレンダーぽちぽち→ 1日=1件の
  pending_requests(staff_off)。payload.override_type は **DB正典 'off'**（日本語ラベル不可）。
  作成時重複ガード409 + 取り下げDELETE新設。承認→`staff_weekly_overrides` 反映は
  既存 applier 経路。正典 = `mobile-leave-request-design.md`
- **PC 休み・月確定**（`e25c741`/mig 0073 → パネル移設 `a409db1`）:
  `staff_shift_confirmations`(staff×month UNIQUE・再確定=同一行更新+再通知)。
  UI は申請履歴ページ右カラムの `StaffLeavePanel`（400px・スタッフ選択で左リスト連動・
  「―全員」で解除・枠下端揃え）。**本人通知3種** = `leave_rejected`(却下・冪等)/
  `leave_cancelled`(override削除・毎回)/`shift_confirmed`(月確定・毎回) —
  `services/leave_notify.py`・宛先=`User.staff_id`(未紐付けno-op)・FE変更不要。
  申請リスト可読化（実名/日時整形/種別色分け/内容要約・scope&payload列廃止）・
  「編集承認」(生JSON)撤去。正典 = `staff-shift-confirmation-design.md`
- **モバイル出勤カレンダー `/m/shifts`**: 既存GET2本(shifts+overrides)を
  `lib/shift-calendar.ts`(純関数)で月へ畳み込み・確定バッジ表示。BE追加ゼロ
- テストデータで却下/承認の実機挙動確認→SQLで痕跡ゼロ削除済み

### 8/20: アカウント運用
- 小西さん(S008)ログイン不可の真因 = **アカウント未作成** → `s008` 作成
  （統一パスワードhashコピー）。小林さんも統一化。**新規スタッフの職員登録では
  アカウントは自動作成されない**（運用注意・仕組み化は未着手）
- ログイン案内 Artifact 作成（ID/共通パスワード `yoriyori0401` 記載・社内限定）:
  https://claude.ai/code/artifact/6482582e-2b1e-4a40-a42f-32dbba511470
- **スタッフコード自動採番**（`fb51592`）: 空欄登録で S### 自動（soft-delete跨ぎ・
  patient_code.py と同型・API/applier両経路）

### 8/20〜21: イベント双方向連携（今セッションの本丸・全Phase本番稼働）
正典 = **`kaipoke-event-two-way-design.md`**（調査結果・設計・Phase構成すべて）
- **Phase 1** イベントのみ取込（`653f61b`）: 連携ページ「取り込む対象」2択
- **Phase 1.5** 職員スケジュールタブ（`dfdc1fe`）: 週ビュー内「スタッフ別」を
  トップレベルタブへ昇格（スタッフ×曜日・在籍全員行・セル＋で追加・帯クリックで編集・
  投影(訪問=読むだけ)/正典(イベント=ここが家)の二層）
- **Phase 0** プローブ: カイポケ登録ポップアップ構造を実機採取
  （方式2択=蓄積マスタ1,597件から選択 or 新規名称/時刻select/予定01実績02/
  保存=ajaxDoRegisterDone）
- **Phase 3** らく助→カイポケ書込（`daf5ee7`/RPA `4155fd3`）:
  RPA `individual_tasks_apply.py`（showIndividualAdd→入力→保存→再パースで採番ID回収・
  重複skip・同名マスタ優先）+ BE outbound(preview/start/status) + FE 送信ダイアログ。
  **昇格** = 送信成功で external_key を刻み source='kaipoke' 化（二重化根絶）。
  **本番実機検証済み**: 2026-11-18 テスト書込→ID回収→削除→痕跡ゼロ（ユーザー承認手順）
- **Phase 2** 固定イベント（`67dc967`/mig 0074）: `staff_event_defaults`
  (staff×weekday0-5×時刻×名称×blocking) → 週生成系3地点から冪等展開
  (source='fixed'・**冪等2段=fixedキー+内容一致**)。fixed も送信対象。
  スタッフ詳細に「毎週の固定イベント」カード。**朝会の手入力が不要になる定常運用が完成**
- **スケジュール側取込**（`345e064`）+ **重なり警告 案A**（`46b5369`）:
  取込イベント×担当訪問の時間交差を preview/apply で検出し親切に警告
  （取込は行う=カイポケが正・解消は人の判断）。
  UI磨き: モーダル幅2xl（`960232f`）・ボタン「⇩カイポケ取込/⇧カイポケ送信」・
  矢印統一（`1142b89`）・「カイポケ反映外」文言全撤去

## 2. 気になる点・残タスク（優先度順）

1. **【ユーザー判断待ち】重なり訪問のワンクリック組み直し** — 提案済み未回答:
   重なり警告から該当訪問だけ未割当化→自動割当が休みを避けて代役を探す導線。
   背景 = 自動スタッフ割当は**既割当コースを保護**する仕様
   （layer3_assignment.py W25 Bug#2・~:992）のため、休み取込後の再実行では
   既存割当が動かない。現運用 = 手動担当変更 or 一斉未割当→再割当
2. **イベント連携の実運用立ち上げ**: 初回の実送信はプレビュー確認しながら少数で。
   PO確認事項 = two-way設計書§5（朝会の現運用/メモ系も送るか/実績02は対象外で
   よいか/カイポケ由来をらく助で編集時の原則）
3. **Phase 4（未着手）**: 送信の定期実行化・カイポケ側の更新/削除の自動化・
   登録ダイアログ「毎週繰り返す」チェック（→固定イベント化）・
   Staff.kaipoke_id 列追加（現状の職員内部ID供給=取込実績の逆引きのみ）
4. **休み申請系の持ち越し**（mobile-leave-request-design §6）:
   applier upsert化（同日override既存で承認422）/ **am_off・pm_off がエンジン全箇所で
   無視される既知ギャップ**（'off'しか見ない）/ 管理者への新着申請ベル通知 /
   **request_type CHECK制約不一致の別件バグ疑い**（mig0013のCHECKが旧9種のまま・
   staff_status_update 等は本番PostgresでINSERT失敗するはず・SQLiteテストでは未検出）/
   shift_requests(月次フリーテキスト)との統合判断 / 承認後の既生成週への再割付導線
5. **既知制約（仕様として明記済み）**: 固定イベントは展開行を手で消しても既定が
   残れば週生成で復活（止めるには既定削除）/ RPA単一スロット（取込と送信は同時不可）/
   イベント送信は1件約40秒
6. **前セッスからの持ち越し**: 都賀A誤紐付け24行（お客様確認待ち）/
   加藤様・並木様5枠（お客様回答待ち）/ QR実機確認3点・同行実機確認15項目 /
   新規スタッフ登録時のアカウント自動作成の仕組み化
7. **テスト既知ベースライン**（触らない・変更前比較で実証済み）: 8/17一覧に加え
   `test_pending_requests.py::test_db_row_persisted`（str vs UUID・ローカルPy3.14系）

## 3. 教訓（今セッション発生分）

1. **Cloudflare 100s**: 同期で60秒を超えるAPIは成功しても524に見える。
   長時間処理は必ず「start(202)+job_idエコー→statusポーリング」型で作る
2. **networkidle は使わない**: 常駐通信で永遠に待つ。家風は
   「try networkidle 15s → domcontentloaded で続行」+ 後続の値検証
3. **既存資産の確認を先に**: 「スタッフ週ビュー新設」を提案したら既存の
   「スタッフ別」がまさにそれだった（ユーザー指摘で発覚）。設計提案の前に
   類似機能の grep を
4. **外部システムへの書込テストは「遠い未来日+即削除」**をユーザー承認の上で
   （今回: 3ヶ月先・採番ID回収→削除→盤面消失確認まで）
5. **本番データ修正は dry-run→COMMIT の二段** + テストデータは目印
   (【テスト】note) を付けて後で一括削除できる形に
6. **名称は「らく助」**。「楽スケ」は誤記（メモリ `careflow-rakusuke-naming` 参照）
7. Windows 環境の Python heredoc で日本語 print は cp932 で落ちる —
   `io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')` を仕込むか print しない

## 4. 動作確認の手引き（お客様・PO向け要点）

ハードリロード（PWA は再起動）必須。
- **休み申請**: スマホ ホーム→「休みを申請する」→カレンダーで日を選んで申請。
  管理者は 申請履歴ページ右パネルで承認/却下/日クリック増減/「この月を確定して通知」
- **出勤カレンダー**: スマホ ホーム→「出勤カレンダー」（確定バッジ表示）
- **イベント連携**: スケジュール→「職員スケジュール」タブ →
  「⇩ カイポケ取込」（イベントのみ・重なり警告つき）/「⇧ カイポケ送信」/
  セルの＋でイベント追加 / スタッフ詳細「毎週の固定イベント」で朝会等を定義
