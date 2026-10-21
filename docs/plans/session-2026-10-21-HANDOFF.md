# 2026-10-21 セッション引き継ぎ（休み申請・勤務表・受け入れ枠・スケジュールの切り替えを本番へ）

## 1. 本番に出したもの（d9cb766a・alembic 0098・0099）
- 休み申請の本格化
  - スマホ「休み」（終日・午前休・午後休・有給）→ PC「休み・勤務表」で承認・追加 → 担当なしへ移す＋通知。
  - カイポケと比べる → 合わせる → 月の確定（送信と勤務表の予定版）→ 実績を記録する（確定版）。
  - 設計の正典: `docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`、`shared-foundation-leave-acceptance-2026-10-20.md`。
- 勤務表（従業者の勤務の体制及び勤務形態一覧表）を Google ドライブへ（OAuth `drive.file`・事業所ごとにサインイン）。
  - **未設定**: Google Cloud の設定と VPS の `GOOGLE_DRIVE_TOKEN_SECRET`。手順は `docs/deployment/google-drive-setup.md`。
  - つなぐまでは勤務表は「未作成」として記録され、つないだあと「作る」で作れる。
- 受け入れ枠の厳密な計算（直近 4/6/8 週・○△・マネージャーと新人を除く）。正典: `acceptance-strict-design-2026-10-20.md`。
  - **初回は人の操作が要る**: 新しい表 `kaipoke_source_snapshots` は空で出したため、画面の「8 週ぶんをすべて取り直す」を 1 回押すまでは全部「－」（判定できません）。17〜18 時台は避ける。
- スケジュールの［コース｜職員］の切り替え（案B・コンパクト表示対応）と、職員ごとの「N件（M名）・満杯・超過」。正典: `schedule-unit-switch-design-2026-10-21.md`。
  - 見本: https://claude.ai/artifact/HQAttXMjxTRN9YWJTQ16kE

## 2. 本番の作業の記録（UTC）
- 読むだけの確認: 申請中の休みの重複は 0 件（0099 の部分 UNIQUE で止まらない）。
- pg_dump: `/opt/carelink/backups/pre-deploy-leave-schedule-20261021-0104.sql.gz`。
- develop を d9cb766a まで fast-forward → backend・frontend を build・`--no-deps --force-recreate` → `alembic upgrade head`（0097→0099）→ agent-relay・agent-sandbox を build・up。
- コンテナはすべて healthy、healthz は内側と公開の両方で ok、新しい API は 401（未ログイン）で道があることを確かめた。ログは `/opt/carelink/backups/deploy-d9cb.log`。

## 3. 試験の基準
- backend 全体: 34 件落ち。作業前（7b1f4a3a）と流し比べ、31 件は前から、3 件は順番の揺れ。新しく壊れたものなし。
- frontend vitest: 3663 件中 7 件落ち（m/today/[visitId] 5・middleware の manager 1・BulkPoolInsertDialog の揺れ 1）。e2e の 9 ファイルは vitest では読めない前からのもの。
- PG16 で 0098・0099 の up → down → up と、申請中の重複を rejected にそろえる処理を確かめた。

## 4. お客様に確かめること（PO 経由）
- 勤務表の仮決め:
  - 休憩は 6 時間を超える日だけ 60 分。
  - 半休の日の管理時間は 0。
  - 有給は「休」。
  - 祝日の番号は日付順。
  - 休職者は全日「休」。
  - 非常勤は「対象外」。
- 午後休は、見本の「PM休」（文字）ではなく時間で入れる。
- 見本の誤り: 予定の合計の式から 1 名抜けている。常勤換算が切り捨てでない。
- 本番データの食い違い:
  - 小西さん 10/13〜16・20: カイポケだけ休み。
  - 髙梨さん 10/21: カイポケは午前休、勤務表は出勤。
- カイポケの実績に退職した職員の名前があると、その月は「実績を記録する」が押せない。

## 5. PO に画面で確かめてもらうこと（スケジュール）
- 職員の表示で曜日を押すと、職員のままその曜日を見る（前はコースの曜日へ移った）。
- 2 名体制の 2 人目は、カードは出るが件数に入らず「＋同行」。
- 半休の行は休みの時間だけを塗る。
- 現場ではハードリロード（Ctrl+Shift+R）が要る。

## 6. 次の作業（PO 了承済み・本番のあとに続ける）
- 訪問モニターで、午前休・午後休・有給の札が出ない（`MonitorTimeline.tsx:436-441`・`MonitorRowPanel.tsx:66-70`）。
- m/today/[visitId] の vitest 5 件が単独でも落ちる。
- 既存の試験に残る実名らしい名前を仮の名前へ。
- 照合の失敗で、英語の例外の型の名前が画面に出る。
- 持ち越し:
  - 「この休みを送る」（1 件だけ急いで送る・設計 #6）
  - 受け入れ枠で covered_dates が NULL のときの扱い

## 6-2. 同じ日の 2 回目の本番（c23fdf31・migration なし・13:13 JST）
- 受け入れ枠: 初回の取り直し（8 週・14 本すべて ok）を済ませた／「全て・稲毛・都賀」（全ては上書き操作不可）／縦長の表（行＝時刻・列＝曜日・［縦｜横］）。
- 職員スケジュール: 曜日リストの見た目（氏名 200px・件数は氏名の下・コースのカードを横に並べる）／職員の週タイムライン（ドラッグ・曜日またぎはコースを付け替え）。
- 訪問モニターの午前休・午後休・有給の札／照合の失敗の文を日本語だけに／試験の名前を仮の名前へ（808＋α か所）。
- pg_dump: `/opt/carelink/backups/pre-deploy-schedule2-20261021-0413.sql.gz`（UTC）。ログ `/opt/carelink/backups/deploy-1021b.log`。
- GitHub CI を直して backend の全試験が動くようにした（alembic_version を広く作る・requirements.txt を入れる・往復は直近 3 本・上限 45 分）。残り: 整形の検査（black/ruff format）は前から赤い／SQLite の「SQL statements in progress」で揺れる試験がある。
- PO に画面で見てもらうこと: 2 名体制の訪問を曜日またぎで動かしたときの相方の担当／1024〜1280px での受け入れ枠の縦の表。

## 6-3. ★次のセッションの最初の仕事: Google ドライブの接続の準備の続き（PO と一緒に・jev-hands で操作）
PO が「続きお願いします」と言ったら、ここから始める。手順の正典は `docs/deployment/google-drive-setup.md`。

**進め方**
- PO の PC の Chrome を jev-hands（スキル `jev-pc-use`）で操作する。
- パスワード・規約への同意・秘密の値の貼り付けは、PO 本人にしてもらう。

**決まっていること（10-21 PO）**
- Google アカウントは `yuji.imaizumi@thousands.jp`（Workspace の組織 thousands.jp）。
- 同意画面の対象は「内部」（今は thousands.jp のアカウントだけ）。別の事業所に出すときは「外部」に切り替えて「本番にする」。
- Google Cloud のプロジェクトは `rakusuke-kinmu`（プロジェクト ID も同じ）。

**済んだこと（10-21）**
1. プロジェクト `rakusuke-kinmu` を作った。
2. API を有効にした: Google Drive API・Google Sheets API・Google Picker API。
3. 同意画面（Google Auth Platform → ブランディング）を作った。
   - アプリ名「らく助」、サポートのメールと連絡先は yuji.imaizumi@thousands.jp、対象は「内部」。
   - ポリシーへの同意と「作成」は PO が押した。

**途中で止まったこと（確かめてから続ける）**
4. データアクセス（`https://console.cloud.google.com/auth/scopes?project=rakusuke-kinmu`）に `https://www.googleapis.com/auth/drive.file` を足す。
   - 手動の欄に入れて「テーブルに追加」まで進めた。
   - 右のパネルの「更新」と、ページの「Save」は PO が押す途中で、ほかの仕事で中断した。
   - **保存されていない見込み。** 「非機密のスコープ」に drive.file が出ているかをまず確かめ、無ければやり直す。
   - jev-hands の注意: 右のパネルの「更新」は画面の下でタスクバーに隠れ、押せない（"covered by another window"）。PO に押してもらうか、Chrome を縮めてから押す。
   - 「スコープを追加または削除」は jev の安全確認に引っかかるので、`allow_sensitive=true` が要る。開くだけのボタン。

**残り**
5. 認証情報 → OAuth クライアント ID を作る。
   - 種類は「ウェブ アプリケーション」。
   - 承認済みのリダイレクト URI は `https://carelink.kaipoke-api.net/api/v1/google-drive/callback` を 1 つだけ。
   - クライアント ID とシークレットは、PO が控える（チャット・git に書かない）。
6. 認証情報 → API キーを作る。
   - アプリケーションの制限は HTTP リファラーで `https://carelink.kaipoke-api.net/*`。
   - API の制限は Google Picker API だけ。
7. 「IAM と管理 → 設定」でプロジェクト番号を控える（`GOOGLE_PICKER_APP_ID`）。
8. VPS の `/opt/carelink/.env` に値を入れる。
   - 入れる名前: `GOOGLE_DRIVE_CLIENT_ID`・`GOOGLE_DRIVE_CLIENT_SECRET`・`GOOGLE_DRIVE_REDIRECT_URI`・`GOOGLE_PICKER_API_KEY`・`GOOGLE_PICKER_APP_ID`・`GOOGLE_DRIVE_TOKEN_SECRET`。
   - 秘密の値は PO が VPS で直接貼る。`GOOGLE_DRIVE_TOKEN_SECRET` は VPS で `openssl rand -hex 32` で作り、画面に出さずにファイルへ書く。
   - そのあと backend を `--no-deps --force-recreate` で作り直す。
9. 戻り先の確認: ログイン中のブラウザーで `https://carelink.kaipoke-api.net/api/v1/google-drive/callback?error=access_denied` を開き、「許可されませんでした」と出ること（手順書 §6）。
10. 管理者で「設定 → Google ドライブ」から接続し、共有ドライブのフォルダを選ぶ。置き場（共有ドライブとフォルダ）は PO に確認する。
11. その月の勤務表を「作る」（休み・勤務表の画面の rebuild の plan）で 1 本作り、ドライブにできるか確かめる。

**注意**
Google Cloud の画面に「2 段階認証プロセス（2SV）を有効にする（2026-10-20 から義務化）」と出ていた。yuji.imaizumi@thousands.jp で 2 段階認証が無効だと、Cloud コンソールに入れなくなるおそれがある。PO に有効にしてもらう。

## 7. 台帳
- `.omc/plans/leave-acceptance-impl-ledger.md`（レーンごとの経過・持ち越し）。
