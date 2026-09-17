# 訪問の音声記録（録音・文字起こし・要約）— 調査と設計案（2026-09-17）

**0. 一言で**: スマホの訪問詳細（または QR 導線・患者選択導線）から会話を録音し、サーバーで文字起こし＋要約して「訪問記録」として患者・スタッフ単位に残す。画面は要約を主役に、音声と文字起こしは開くと見える。PC には新ページ「訪問記録」を足し、患者詳細・スタッフ詳細にもカードで出す。

**PO 確認事項（§8）に回答が要る前提**: 2026-07-05 に PO 決定で AI（Gemini）を全撤去した経緯があり、本機能は「AI の再導入」になる。加えて録音同意・保持期間・外部送信先（医療情報）の判断が要る。

---

## 1. 現状調査（要点）

### 1-a. モバイルの土台（そのまま使えるもの）
| 既存 | 使い方 | 出典 |
|---|---|---|
| 訪問詳細 `/m/today/[visitId]`（到着→訪問中パネル→退出・写真 UP） | 録音ボタンは「訪問中パネル」に置く。写真 UP と同じ列 | `app/(mobile)/m/today/[visitId]/page.tsx:832-903` |
| QR 導線 `/q/[token]`（担当直行／代行／予定外＝adhoc-checkin） | QR あり の予定外訪問は既存のまま visit が作られるので、その visit に録音を紐付ける | `app/q/[token]/page.tsx:132-193` |
| 写真アップロードの型（multipart は素の fetch・`VISIT_PHOTOS_DIR/{visit_id}/{uuid}.ext`・Bearer 配信・`AuthedPhoto`） | 音声も同型。ただし **10 MiB 全量読みは不可**（§3-3） | `lib/queries/visit-photos.ts:104-118`, `backend/app/api/v1/visit_photos.py` |
| オフライン退避（`checkin-queue` = localStorage、online で再送） | 音声 Blob は localStorage に入らない → **IndexedDB** の別キューを新設（同じ「黙って消さない」方針） | `lib/checkin-queue.ts`, `lib/checkin-flush.ts` |
| Service Worker は `/api/*` を network-only | 録音データはアプリ側キューで扱う（SW に任せない） | `public/sw.js:62-71` |
| 患者検索 UI（現場ボードの `PatientLinkPanel`／`usePatients` の 500 件クライアント検索・かな順） | 「QR なし」導線の患者選択に流用 | `components/field/FieldSheets.tsx:2094-2230`, `lib/kana-sort.ts` |
| マイク: HTTPS 必須・iOS はユーザー操作起点 | 旧 AI 入力の設計メモがそのまま効く | `docs/design/10-mobile.md:295-305` |

### 1-b. 保存・ジョブ・制約
- **バイナリ保存はファイルシステム bind-mount**（DB はメタのみ）。マウント漏れで 500 になった前例（要 `chown 999:999`）。
- **監査ミドルウェアが request body を全量メモリに乗せる**（16 KiB 超は記録しないが読み切る）。VPS は swap 0・OOM killer あり。音声 POST は監査のバイパス経路＋ストリーミング書き込みが必須。
- **Cloudflare 経由は約 100 秒で切断**（524）。文字起こし・要約は必ず非同期ジョブ（`plan-actual-compare` の BackgroundTasks 型＋stale 掃除、または start→polling 型）。
- **cron はアプリ内に無く VPS の cron から admin API を叩く方式**（保持期間パージは `purge-gps` と同型で足す）。
- ディスク: 46 GB 空き（不要イメージ 24 GB 回収可）。音声は 30 分 ≈ 7〜14 MB。月 400 件×平均 10 分 ≈ 2 GB/月。**保持期間を最初から設計**（既定 90 日で音声のみ削除・文字起こし/要約は残す）。
- AI 設定は settings に一切残っていない（新規フィールド＋`.env.example`＋`env-template.md` の 3 箇所）。旧 GEMINI キーは Google 側で失効未確認 → **新規発行**。
- 認可: staff 本人スコープは `visits.py` の `_staff_visibility_filter` ＋ `_course_fallback_staff_ids`（`visit_photos._check_visit_access` は同行・コース担当を見ない狭い版なので使わない）。

### 1-c. PC 画面・デザイン基盤
- **訪問履歴の画面は存在しない**。患者詳細はタブ無しのカード縦積み（訪問記録カードの追加先として最有力）。スタッフ詳細の `EventsCard`（期間タブ・検索・種類チップ・BE 絞り込み）が一覧の手本。
- 訪問モニターは当日 1 日のみ表示（毎日流れる）。詳細パネルに「🎙 記録あり」リンクを足す程度が適切。
- A4 出力は `REPORT_CSS` ＋ BE 純関数レンダラ ＋ `SyncReportButton` の型で完全に流用可。
- デザイン規約: 本文 14px・11px 以下禁止・モーダル `max-w-5xl`/`max-h-[90vh]`・マスコットは「状態の瞬間」だけ・lucide アイコン・ダークモード無し。トークンは `styles/tokens.css`。

### 1-d. ブラウザ録音の技術制約（外部調査）
- iOS Safari PWA: MediaRecorder は使える（`audio/mp4`）が **画面ロック／バックグラウンドで録音が止まる**。Wake Lock で消灯を防ぎ「録音中は画面を点けたまま」の UX にする。着信でも止まる。
- Android Chrome: `audio/webm;codecs=opus`、バックグラウンド継続可。
- 10 秒 timeslice で分割→IndexedDB 退避→停止時に結合（Safari の分割片は単体再生不可だが結合すれば有効）。`visibilitychange` で `stop()` を呼び直前まで保全。
- **ブラウザ内音声認識（Web Speech API）は iOS PWA で動かない** → サーバー側文字起こし一択。
- 代替: 端末のボイスメモで録音して `<input type=file accept="audio/*">` で上げる導線（画面ロック中も録れる保険。UX は劣る）。

### 1-e. 文字起こし・要約 API（外部調査・2026-09 時点の公式料金）
| 構成 | 30 分 1 件 | 月 400 件 | 備考 |
|---|---|---|---|
| **Gemini 2.5 Flash（Vertex AI）音声→文字起こし＋要約を 1 コール** | ≈ $0.08 | **≈ $32** | 9.5 時間まで・m4a/webm 可・日本リージョン可・GCP の DPA。**AI Studio 版は規約で臨床利用禁止 → 必ず Vertex AI** |
| Gemini 2.5 Flash-Lite（同上） | ≈ $0.02 | ≈ $8 | 最安。雑音下の精度は Flash に劣る可能性 |
| OpenAI gpt-4o-mini-transcribe ＋ gpt-4o-mini 要約 | ≈ $0.09 | ≈ $37 | 25 MB 上限（AAC 30 分 ≈ 14 MB で可）。話者分離は diarize モデル。BAA は Enterprise |
| OpenAI gpt-4o-mini-transcribe ＋ **GPT-5.6 Luna** 要約（$0.20/$1.20 per 1M・effort=low・構造化出力） | ≈ $0.09 | ≈ $37 | Luna は音声入力・Transcription 非対応（要約専用）。要約 1 件 ≈ $0.0036。Bedrock 提供あり（東京は global 横断のみ・国内 In-Region 無し）。Intelligence Index 38 |
| AWS Transcribe（ja-JP・話者分離・医療語彙）＋ gpt-4o-mini | ≈ $0.18 | ≈ $73 | HIPAA 適格・東京リージョン・S3 保持制御が明確 |
| Google Cloud STT / Azure | ≈ $0.5 | ≈ $200 | 高い |
- 学習利用: いずれも API 有料版は学習に使わない。3 省 2 ガイドラインへの「認定」は存在せず、委託先管理（DPA）と院内規程で担保する。
- **推奨**: 第一候補 = Vertex AI 経由 Gemini 2.5 Flash（1 コール・安価・日本リージョン・用語辞書をプロンプトで渡せる）。第二候補 = OpenAI（API キーだけで始められる・PoC 向き）。要約だけ別モデルにする 2 段構成は、文字起こしを監査用に独立保存したい場合に採る。

### 1-f. 提供経路の比較対照（2026-09-17・PO 判断用）
| 観点 | Gemini API（AI Studio） | Gemini（Vertex AI） | OpenAI 直（API） | OpenAI（AWS Bedrock・GPT-5.6 Luna 等） | AWS Transcribe ＋ 要約モデル |
|---|---|---|---|---|---|
| 音声→文字起こし | ○ 2.5 Flash/Flash-Lite が音声を直接入力（9.5 h・m4a/webm 可） | ○ 同左 | ○ gpt-4o(-mini)-transcribe / whisper-1（25 MB 上限） | × Luna/Sol/Terra は音声非対応（Bedrock の OpenAI モデルは文字起こし不可） | ○ ja-JP・話者分離・医療語彙（Custom Vocabulary） |
| 要約 | ○ 同じモデルで 1 コール | ○ 同左 | ○ gpt-4o-mini / GPT-5.6 Luna（$0.20/$1.20） | ○ Luna（In-Region $0.22/$1.32・Global $0.20/$1.20） | 別途（gpt-4o-mini / Luna / Gemini / Claude） |
| 月 400 件（30 分）目安 | Flash ≈ $32 / Flash-Lite ≈ $8 | 同左（Vertex は同価格・課金は GCP） | mini-transcribe ＋ Luna ≈ $37 | 文字起こしを別途要するため Luna 単独では成立せず | ≈ $73 |
| 日本語精度・話者分離 | 高／プロンプト任せ（保証なし） | 同左 | 高／diarize モデルあり | — | 良／話者分離 ○（保証あり） |
| 医療用途の規約 | **臨床利用を規約で禁止（不可）** | 可（GCP CDPA・HIPAA 対象サービス） | 可（学習不使用。BAA は Enterprise） | 可（AWS HIPAA 適格・第三者モデル規約） | 可（HIPAA 適格） |
| データの所在（推論処理） | 米国等（指定不可） | **東京 asia-northeast1 で処理可**（機能により許可リスト） | 推論は米国。**保存データのみ日本レジデンシー可**（2025-05〜・対象顧客が API Project で国選択） | 東京は Global 横断のみ（米国で処理）。国内 In-Region 無し | **東京 ap-northeast-1 で処理・保存** |
| 学習への利用 | 有料版は不使用 | 不使用 | 不使用（オプトイン制） | 不使用 | 不使用 |
| 保持・ゼロ保持 | 明記なし | GCP DPA・設定で最小化 | 30 日（ZDR は審査/Enterprise） | S3 等は自社管理 | S3 ライフサイクルで自社管理 |
| 導入の手軽さ | API キーのみ（最も簡単・**ただし医療不可**） | GCP プロジェクト・請求・IAM・サービスアカウント | API キーのみ（簡単） | AWS アカウント・Bedrock API キー | AWS アカウント・S3・IAM |
| 100 秒制限との相性 | 非同期ジョブ前提（同じ） | 同左 | 同左 | 同左 | バッチ API があり相性良 |
| 当社設計での役割 | PoC の精度確認にも使わない（規約） | **本命**（1 コール・国内処理・安価） | **PoC 最短ルート**／国内処理不要なら本番も可 | 要約のみ。既に AWS を使う場合の選択肢 | 話者分離・語彙登録を厳密にしたい場合の上位案 |
- 結論: 国内処理を要件にするなら Vertex AI（Gemini 2.5 Flash）一択。要件にしないなら OpenAI 直（gpt-4o-mini-transcribe ＋ Luna）が最短で費用も同水準。AI Studio 版の Gemini は無料枠があっても医療用途では使わない。
- 出典: OpenAI モデル/料金ページ、Gemini API 料金・規約、Vertex AI データレジデンシー、AWS Bedrock モデルカード（GPT-5.6 Luna）、AWS Transcribe 料金/HIPAA、OpenAI「Introducing data residency in Asia」。

---

## 2. 設計案

### 2-1. 録音の導線（3 本）と患者紐付け
| 導線 | 患者の決まり方 | 実装 |
|---|---|---|
| A. 予定のある訪問 | 訪問詳細（`/m/today/[visitId]`）の訪問中パネルに「🎙 録音」 | visit_id 確定。到着打刻の有無に関係なく録音可（打刻前でも可） |
| B. QR を読んだ予定外／代行 | 既存 `/q/[token]` → adhoc visit or 代行 visit → その詳細画面で A と同じ | 既存フローをそのまま利用。QR 所持＝現地証明 |
| C. QR なしの予定外 | `/m/today` の「予定に無い訪問を記録」→ **先に録音を開始できる**（患者未確定）→ 停止後に患者を選ぶ（今日/今週の担当患者 → 拠点の稼働患者 あいうえお順 → 検索） | `visit_recordings.patient_id` を **nullable** にし「紐付け待ち」を許す。紐付け時に **予定外訪問（is_unplanned）を生成**して visit_id も付ける（打刻は付けない＝`checkin` は無し）。24 時間紐付け無しは admin 画面に「要紐付け」として出す |
- C を「先に録音」にする理由: 現場で患者を探す操作が録音開始を遅らせる。紐付けは後からで良い（要約が出れば患者名の手掛かりにもなる）。
- 誤紐付けの訂正: admin は患者・訪問の付け替え可（監査ログ）。staff 本人は紐付け後 24 時間以内なら変更可。

### 2-2. 録音 UI（モバイル）
- 訪問中パネル内: `🎙 録音を始める`（`CheckInButton` と同意匠・高さ 48px）→ 録音中は **赤い経過タイマー＋波形風のインジケータ＋「一時停止／停止」**。画面上部に「録音中は画面を点けたままにしてください」を常時表示（iOS 制約）。Wake Lock 取得。
- 停止 → 「保存して文字起こしへ」確認 → アップロード（進捗バー）→ 完了トースト「らく助が文字起こし中です（数分）」。失敗時は IndexedDB に残し「電波が戻ると自動送信」バナー（打刻の未送信バナーと同じ場所・同じ文言体系）。
- 録音の同意: 開始ボタン押下前に 1 行「患者様に録音の了承を得ています」チェック（初回のみ説明ダイアログ・以後は 1 タップ）。運用ルールは PO 決定（§8）。
- 端末制限の保険: 「ボイスメモから取り込む」リンク（`<input type=file accept="audio/*">`）を録音パネルの下に小さく。
- 分割: `MediaRecorder.start(10000)`、`audioBitsPerSecond: 32000`（Safari は指定無効・AAC 既定）。`isTypeSupported` で `audio/webm;codecs=opus` → `audio/mp4` の順。
- 上限: 60 分で自動停止（警告 55 分）。

### 2-3. 表示（要約が主役）
- **モバイル訪問詳細**: 「訪問記録」カード＝要約（見出し付き箇条書き）＋ 状態バッジ（文字起こし中／要約済み／失敗）＋「音声を聞く」「全文を見る」（折りたたみ）。要約はスタッフが 1 タップで「確認済み」にできる（誤りがあれば追記欄）。
- **モバイル今日/今週**: 訪問カードに 🎙 マーク（記録あり）。
- **PC 新ページ `/records`（訪問記録）**: 左にフィルタ（期間タブ 今週/今月/過去/すべて・患者・スタッフ・拠点・状態・検索）、右に一覧（日付・時刻・患者・スタッフ・要約の 1 行目・状態・🎙）。行クリックで **詳細ダイアログ**（`max-w-5xl`・2 列: 左=要約（14px・見出し）＋メタ（訪問・打刻・写真へのリンク）、右=音声プレーヤー（`AuthedAudio`）＋文字起こし全文（14px・話者ラベル・タイムスタンプ）。admin は要約編集・紐付け変更・削除。
- **患者詳細**: 「訪問記録」カード（直近 5 件＋「すべて見る」→ `/records?patient=`）。**スタッフ詳細**: 同じカード（`EventsCard` の意匠）。
- **訪問モニター**: 詳細パネルに「🎙 記録を見る」リンク（モニターは当日限りなので置き場は `/records`）。
- **A4 出力**（Phase 3）: 訪問記録 1 件＝1 枚（要約＋文字起こし）を `REPORT_CSS` で。

### 2-4. データモデル（新規 migration 0086）
`visit_recordings`
| 列 | 型 | 備考 |
|---|---|---|
| id | uuid | |
| visit_id | uuid null (FK visits, SET NULL) | 紐付け待ちは null |
| patient_id | uuid null (FK patients) | 同上 |
| staff_id | uuid (FK staff) | 録音者 |
| office_id | uuid | 表示のスコープ |
| recorded_at / ended_at | timestamptz | 端末時刻（device_time）とサーバー受領時刻を分ける |
| duration_sec | int | |
| audio_path / audio_mime / audio_bytes | text/varchar/int | `${VISIT_AUDIO_DIR}/{yyyy}/{mm}/{id}.{ext}`。音声削除後は null |
| audio_deleted_at | timestamptz null | 保持期間パージ済み |
| status | varchar | `uploaded` → `transcribing` → `summarized` / `failed` / `unlinked`（患者未紐付け） |
| transcript | text null | 全文（話者ラベル付きプレーンテキスト） |
| transcript_json | jsonb null | セグメント（開始秒・話者・本文） |
| summary | jsonb null | 構造化: `{ "主訴・様子": [], "バイタル": {...}, "処置・ケア": [], "申し送り": [], "次回": [] , "free": "" }` |
| summary_text | text null | 画面用の整形済み |
| provider / model / prompt_version | varchar | 監査・再実行用 |
| tokens_in / tokens_out / cost_usd | int/int/numeric | 月次の費用可視化 |
| error_message | text null | |
| consent_confirmed | bool | 録音同意チェック |
| reviewed_by / reviewed_at | uuid/timestamptz null | 「確認済み」 |
| created_by_user_id, created_at, updated_at, deleted_at | | soft delete |
- `visit_recording_jobs` は作らず `status` ＋ `updated_at` で stale 判定（10 分超 `transcribing` は failed 化→再試行ボタン）。

### 2-5. API（`/api/v1/visit-recordings`）
| メソッド | 用途 | 認可 |
|---|---|---|
| `POST /visit-recordings` (multipart: audio, visit_id?, patient_id?, recorded_at, duration_sec, consent) | 受領（**ストリーミング書き込み・50 MB 上限・監査 body バイパス**）→ 202 ＋ id。受領後 BackgroundTasks で処理開始 | staff（自分の visit か未紐付け）/admin |
| `GET /visit-recordings?patient_id&staff_id&office_id&from&to&status&q&limit&offset` | 一覧（BE 絞り込み） | staff は自分の録音のみ・admin 全件 |
| `GET /visit-recordings/{id}` | 詳細（要約・文字起こし・メタ） | 同上 |
| `GET /visit-recordings/{id}/audio` | 音声（Bearer・Range 対応・`FileResponse`） | 同上 |
| `PATCH /visit-recordings/{id}` | 紐付け（patient_id/visit_id）・確認済み・要約の追記 | staff 本人 24h 以内 or admin |
| `POST /visit-recordings/{id}/retry` | 失敗の再処理 | admin |
| `DELETE /visit-recordings/{id}` | soft delete＋音声削除 | admin |
| `POST /admin/visit-recordings/purge-audio` | 保持期間超の音声削除（cron・advisory lock・冪等） | admin token |
- 処理ジョブ: `transcribe_and_summarize(recording_id)`＝ 音声を AI クライアントへ → 文字起こし・要約を保存 → 通知（本人へ「要約ができました」）。AI クライアントは `KaipokeClient` の作法（専用ラッパ・カスタム例外・`set_test_client` シーム・タイムアウト・1 回リトライ）。
- 要約プロンプト: 看護記録の定型（主訴/観察/バイタル/処置/申し送り/次回）＋ 用語辞書（褥瘡・包交・バイタル…）＋「推測しない・不明は不明と書く」。`prompt_version` を保存し、テンプレ変更時に再生成可能に。

### 2-6. セキュリティ・個人情報
- 送信先: Vertex AI（asia-northeast1）or OpenAI。**AI Studio 版 Gemini は規約上不可**。契約（DPA）と院内規程を PO 側で整備。
- 保持: 音声は既定 90 日で削除（設定可・下限 30 日）、文字起こし・要約は訪問と同じ寿命。cron の `purge-gps` と同型。
- 閲覧: staff 本人の録音のみ（同行者も可とするかは PO 決定）、admin 全件。担当外は 404 秘匿。
- 監査: 一覧/詳細/音声 GET は監査対象外（既存規則）だが、**音声 GET と紐付け変更は明示的に audit_logs へ記録**（読み取り監査の例外）。
- 通信: 既存 HTTPS/Cloudflare。CSP に `media-src 'self' blob:` を追加（blob 再生用）。
- 誤送信対策: 録音開始前に患者名を画面上部に固定表示（A/B）、C は「患者未確定」を赤で表示。

### 2-7. 障害と運用
- アップロード失敗 → IndexedDB に残し自動再送（打刻キューと同じ UX）。端末ストレージ逼迫時は警告。
- 処理失敗 → 状態バッジ「失敗」＋ admin の再試行。3 回失敗で通知。
- 費用: `cost_usd` を集計する管理カード（連携コンソールの隣）。月上限に近づいたら通知（任意）。
- デプロイ: `VISIT_AUDIO_DIR` bind-mount ＋ `chown 999:999` を runbook に追加（写真の事故の再発防止）。バックアップは音声を除外（サイズ）し、DB と文字起こしのみ日次。音声は保持期間内のみ rsync 世代なしミラー（任意）。

---

## 3. 実装フェーズ（目安）
| Phase | 内容 | 規模 |
|---|---|---|
| 0 PoC（1〜2 日） | 端末 2 機種（iPhone/Android）で MediaRecorder→アップロード→Vertex/OpenAI 1 コール→要約表示を試す。日本語精度・雑音・費用を実測。**PO の AI 再導入判断の材料** | 小 |
| 1（1.5〜2 週） | 導線 A/B、録音 UI、IndexedDB キュー、`visit_recordings`＋API、非同期処理、モバイル要約表示、保持期間パージ、費用記録 | 中〜大 |
| 2（1 週） | 導線 C（先に録音→患者選択）、PC `/records`、患者詳細・スタッフ詳細カード、モニターのリンク、admin の紐付け/再試行 | 中 |
| 3（0.5 週） | A4 出力、要約テンプレの調整、費用ダッシュボード、ボイスメモ取り込み | 小 |

## 4. 再利用する既存部品（実装時に必ず使う）
`CheckInButton` / `MobileSection` / `RakusukeNote`（空=think・完了=clap・失敗=puzzled）/ `AuthedPhoto` → `AuthedAudio` / `checkin-flush` の UX 文言 / `EventsFilterBar` / `Card`・`Dialog`・`Badge` / `REPORT_CSS` / `require_role`・`_staff_visibility_filter`・`_course_fallback_staff_ids` / `plan-actual` の BackgroundTasks＋stale 掃除 / `purge-gps` の cron 型。

## 5. モック
`docs/mockups/visit-voice-record-mock.html`（モバイル: 訪問中パネル＋録音中＋要約カード＋患者選択／PC: 訪問記録一覧＋詳細ダイアログ）。トークンは実アプリと同値。

## 6. テスト観点
- MediaRecorder の MIME 判定・分割結合（jsdom ではモック、実機 2 機種で手動）。
- アップロード: 50 MB 超 413・空 422・MIME 不一致 415・staff 担当外 404・未紐付け許可。
- ジョブ: 成功/失敗/stale/再試行・費用記録・通知。
- 一覧の絞り込みは BE（窓を FE で削らない）。
- 保持期間パージの冪等・下限ガード。
- 監査: 音声 GET と紐付け変更が記録される。

## 7. 非対象（今回やらない）
リアルタイム文字起こし（iOS PWA 不可）／音声のクライアント側暗号化／話者自動識別の厳密化（プロンプト任せ）／カイポケへの記録送信。

## 8. PO 確認事項
1. **AI 再導入の可否**（2026-07-05 の全撤去決定との整合）。
2. **送信先と契約**: Vertex AI（GCP 契約・日本リージョン・DPA）か OpenAI（手軽・米国）か。PoC はどちらで行うか。
3. **録音同意の運用**（患者/家族への説明文・同意の記録方法・拒否時の扱い）。
4. **保持期間**（音声 90 日案・文字起こし/要約の寿命）。
5. **閲覧範囲**（同行者・拠点管理者・他拠点）。
6. **要約テンプレの項目**（主訴/観察/バイタル/処置/申し送り/次回 で良いか）。
7. **予算**: 月 $10〜40（400 件）＋ストレージ。
8. QR なし導線 C で「先に録音・後で患者選択」を許すか（誤紐付けリスクとの兼ね合い）。

## 9. 参照
- 調査（本セッション・エージェント報告）: モバイル構造／保存基盤／PC 画面・デザイン／ブラウザ録音（WebKit・MDN・W3C・firt.dev）／API 比較（OpenAI・Google・AWS 公式料金・Gemini API 利用規約）。
- 関連: `docs/design/09-global-ai-input.md`（旧 AI 入力 UI・参考）、`docs/design/10-mobile.md §マイク`、`docs/plans/ai-removal-mobile-hardening-HANDOFF.md`、`docs/plans/sync-result-report-design.md`（A4）、`docs/plans/add-visit-anywhere-design.md §3-5`（可読性基準）。
