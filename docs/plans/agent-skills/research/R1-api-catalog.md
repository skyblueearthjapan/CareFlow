# R1 らく助 HTTP API カタログ（エージェント用）

調査日: 2026-10-12 / 対象: `backend/app/api/v1`（develop・c17aafe 時点）/ 読むだけの調査（サーバー未接続・本番未確認）。

作り方: `backend/app/api/v1/__init__.py` の include_router と各ファイルの `@router.*` を AST で全件抽出（grep の件数 311 と一致）→ フロントエンド（`frontend/app`・`components`・`lib`、テスト除く）でパス文字列（定数・テンプレート展開込み）を照合 → 危険・罠は手で注記。

## 0. まとめ

- **総数 311 本**。すべて `/api/v1` 配下（「v2」は別プレフィックスではなく `/api/v1/schedule/v2/*` というパス名）。モバイル専用・カイポケ専用のルーターは無く、モバイル画面も同じ API、カイポケ連携は `/api/v1/integrations/*`。
- メソッド: GET 108 / POST 140 / PUT 14 / PATCH 23 / DELETE 26。
- 権限: admin のみ 236 / admin・staff 21 / ログインのみ（ロール不問）50 / 認証なし 4（login・refresh・healthz・readyz）。
- 書き込み系（GET と読むだけの POST を除く）: 186 本。そのうちカイポケへ外部書込するもの 3 本（`integrations/apply`・`expand`・`events-outbound-apply/start`）。
- 画面から呼ばれていない（UI 未使用・hook のみを含む）: 57 本 → §17。大半は旧 API・cron 用・内部用。
- 【危険】【罠】の印つき: 32 本 → 下の一覧。

### 0-1. エージェントが API だけでできること（＝管理者の画面入力はほぼ全部）
- フロントエンド（Next.js）は自前の DB を持たず、**画面の入力はすべてこの API 経由**（Next.js 側にあるのは NextAuth の `/api/auth/*` だけ）。したがって管理者が画面でする入力は原則すべて API で再現できる。PO の約束「API で代行するときも画面と同じ API」を守るため、各表の「UI」列で画面が使っている API を選ぶこと（旧 API を使わない）。
- 主な業務と入口: 患者・職員マスター（1 件ずつ/Excel 取込）→§4・§6、固定訪問の枠と希望→§5（希望は患者の weekly_pattern）、週を作る（コピー/生成/自動割当）と今週だけの操作→§7、休み・イベント・申請承認・月確定→§8、特別訪問週間→§9、同行→§10、提案系→§11、カイポケの差分→送信→取込→§12、実績時刻を合わせる・確認済み→§13、設定・ユーザー→§15。

### 0-2. API が無い／API だけでは足りないこと
- **帳票の PDF 化・印刷**: A4 帳票（実現性チェック・連携結果・突合・予実・打刻履歴・音声記録）は JSON/HTML を返すだけ。PDF 化はエージェント側（headless Chrome 等。日本語ファイル名だと失敗する罠あり）。QR カードも画面側で描画（データは `GET /patients/{id}/qr`＋`/business-profile`）。
- **カイポケのロボット（kaipoke-api :5000）の直接操作**: 進み具合 `GET :5000/api/apply/result`・出し直し `POST :5000/api/export` は別サービスで本 API の外（`GET /integrations/live` で一部は見える）。
- **DB バックアップ（pg_dump）・cron・デプロイ・alembic**: API なし（PO の約束「本番に書く前に了承と pg_dump」）。
- **週のならし案**（`docs/tools/week-leveling`・skill week-leveling）はオフラインツール。反映は既存 API（visit-assign-staff-week 等）を順に叩く。
- **取消/変更の記録**（10-01 保留）・**SMS 自動通知**・**患者の連絡先**: 機能自体が未実装。
- **職員本人の操作**（打刻・サイン退出・不在・録音・自分の休み申請・自分のパスワード変更）は API はあるが、管理者が代行すると実績や本人意思の偽装になるため対象外とすべき。
- **一括更新 API は少ない**（患者・職員の一括更新は Excel 取込のみ）。エージェントは 1 件ずつ叩くか、Excel を作って `import`（dry_run→本番）する。
- **検索 API が弱い**: `GET /patients`・`/staff` は limit/offset のみ（名前検索はクライアント側）。全件取得してから絞る。

## 1. 認証の仕組み（スクリプトから admin で叩く方法）

- `POST /api/v1/auth/login` body `{"identifier": "<職員コード or email>", "password": "..."}`（旧キー `email` も可）→ `{"user": {...}, "tokens": {"access_token", "refresh_token", ...}}`。以後 `Authorization: Bearer <access_token>`。
- JWT（HS256）: **access 3600 秒（1 時間）・refresh 30 日**（`backend/app/core/config.py` `jwt_access_ttl_seconds`/`jwt_refresh_ttl_seconds`）。切れたら `POST /auth/refresh {"refresh_token"}`。logout はサーバー側では何もしない。
- ロック/制限: 同一アカウント 5 回失敗で 15 分ロック（423）。レート制限は識別子ごと 5/15 分・IP ごと 100/15 分（429）。`X-Login-Identifier` ヘッダーを付けるなら body と一致必須。**ログインを繰り返すスクリプトは書かない**（refresh を使う）。2026-09-14 の全員ログイン不可事故（メモリ careflow-login-ratelimit-incident）。
- ロールは 2 値（admin/staff・旧 manager は admin 扱い・`backend/app/core/deps.py:116`）。`must_change_password` は API 側では他エンドポイントを塞がない。
- **本番の入口**: `carelink.kaipoke-api.net` 全体が **Cloudflare Access（人間用ログイン）** の後ろ（`frontend/lib/cfAccess.ts`）。外からスクリプトで叩くには Access のサービストークンが要る（あるかは未確認）。アプリ変更なしで今できる方法:
  1. **VPS 内から実 HTTP**: `http://127.0.0.1:18001/api/v1/auth/login` で通常ログイン → Bearer。送信（apply）など裏で処理が続くものはこの方法でないと止まる（10-09 HANDOFF §6）。エージェント専用の admin ユーザーを作ると監査ログで区別できる。
  2. **コンテナ内で鍵を発行して ASGI 直叩き**: `docs/tools/kaipoke-ops/admin_call.py`（`create_access_token()` で今泉アカウントの鍵・パスワード不要）。読み取りや同期処理は可。**裏で続く処理（カイポケ送信など）は ASGI 直叩きだと止まるので不可**。
- 監査: HTTP 操作は audit_logs に残る（`GET /api/v1/audit-logs`）。操作者はトークンのユーザー。

## 2. 表の見方

- **権限**: admin / admin/staff / ログイン（ロール不問）/ なし。**種別**: 読・書・RPA読（カイポケから export して読む・1〜2 分）・書＋RPA読・書（カイポケ）＝外部へ書込。
- **安全装置**: dry_run（試し）・confirm（`confirm:true` 必須）・state_token（シミュレーション後に盤面が変わっていたら拒否）・expected_counts・戻る(op-log)＝`POST /schedule/v2/op-log/undo` で戻せる・スナップショット＝`POST /integrations/inbound-snapshots/{id}/restore` で戻せる・制約警告の承認（`acknowledge_constraint_warnings`）・通知（職員に通知が飛ぶ）・監査ログ。自動検出なので漏れはありうる。
- **印**: 【危険】大量削除・置換・外部書込・不可逆。【罠】既知の落とし穴。【注意】マスター変更など影響が広い。
- **UI**: 呼んでいる画面/部品（`CourseDayTablePanel`=週の予定の盤面、`cockpit/*`=今週の運転席、`field/*`=現場ボード、`m/*`=モバイル、`integrations/kaipoke`=カイポケ連携画面）。
- **主な入力**: body のフィールド（`*`=必須）と `?クエリ`。**カイポケ連携の body は camelCase**（`sheetId`, `dryRun`, `weekStart`, `month`）。それ以外は snake_case。週は多くが `iso_year`+`iso_week`、カイポケ系は `weekStart`（月曜の日付）。
- **実装**: `backend/app/api/v1/<file>.py:<デコレータの行>`。

### 危険・罠の一覧（先に読む）

| 印 | メソッド パス | 内容 |
|---|---|---|
| 【危険】 | `DELETE /accompaniments/future` | 今週以降の同行リンクを物理削除＋既定を全削除（新人卒業） |
| 【危険】 | `POST /admin/checkin/purge-gps` | 2 年超の GPS を NULL 化（cron 用・不可逆） |
| 【危険】 | `DELETE /admin/users/{user_id}` | ユーザーを論理削除（ログイン不可になる） |
| 【危険】 | `POST /admin/users/{user_id}/reset-password` | 一時パスワードを発行し `must_change_password=true`・ロック解除。応答に `temp_password`。統一パスワードが崩れる（メモリ careflow-unified-password） |
| 【危険】 | `POST /admin/visit-recordings/purge-audio` | 保持期限超の音声削除（cron 用・不可逆） |
| 【危険】 | `POST /admin/visit-signatures/purge-images` | サイン画像のパージ（cron 用・不可逆） |
| 【危険】 | `POST /integrations/apply` | カイポケへ書き込む（RPA）。body `{sheetId, dryRun, itemIds}`・`dryRun` 既定 true。1 件 30〜60 秒。17〜18 時台は失敗しやすい。担当なし(-)行は export に出ない。お客様がカイポケで直した分を上書きする恐れ→必ず差分を見る。ASGI 直叩き不可（裏の処理が止まる・127.0.0.1:18001 経由） |
| 【危険】 | `PUT /integrations/credentials` | カイポケのログイン情報を保存（暗号化） |
| 【危険】 | `POST /integrations/events-outbound-apply/start` | 職員イベントをカイポケへ送る（RPA・非同期 202）→ status をポーリング |
| 【危険】 | `POST /integrations/expand` | カイポケの月展開（RPA・外部書込）。`dryRun` 既定 false に注意。8/31 誤展開事故 |
| 【危険】 | `POST /integrations/inbound-snapshots/{snapshot_id}/restore` | 「取り込み前/コピー前に戻す」: 週を白紙化してスナップショットを書き戻す。打刻のある週は 422 |
| 【危険】 | `POST /integrations/replace-inbound` | 置換取込: 週のらく助予定を白紙化しカイポケ現況で全置換。`dryRun` 既定 true・打刻 1 件でもあれば 422・スナップショットあり。青ピンを守らない（B-3）。画面からの直接呼び出しは未検出 |
| 【危険】 | `POST /integrations/smart-inbound-apply` | 取込の本体: 打刻あり日=差分適用・なし日=置換を 1 TX。`dryRun` 既定 true。実行時に再取得。スナップショット（5 世代）。置換部分は青ピンを守らない（B-3） |
| 【危険】 | `DELETE /offices/{office_id}` | 拠点削除 |
| 【危険】 | `POST /patients/fixed-visits/from-week-bulk` | 全 active 患者の固定訪問の枠をその週の予定で置換（赤ピン行は残す）。dry_run なし |
| 【危険】 | `POST /patients/import-export/replace-all` | Excel で患者マスターを完全置換（バックアップ復元用）。multipart・`dry_run` |
| 【危険】 | `DELETE /patients/{patient_id}` | 患者を論理削除（予定・枠の連動処理はしない） |
| 【罠】 | `PATCH /patients/{patient_id}` | 患者マスター更新（希望=weekly_pattern・住所・性別制限・2名体制など）。status を PATCH で変えても予定・枠は消えない（終了時の連動は status-change） |
| 【危険】 | `DELETE /patients/{patient_id}/fixed-visits` | その mode の固定訪問の枠を全削除（`?mode=`） |
| 【危険】 | `POST /patients/{patient_id}/qr/regenerate` | QR トークン再発行。旧カードは無効（掲示カードの刷り直しが必要） |
| 【危険】 | `POST /patients/{patient_id}/status-change` | 状態変更＋予定連動。`remove_schedule=true` で当日以降の予定と固定訪問の枠も消す（終了=内部 `cancelled`）。消した週はカイポケへ送ると削除が出る |
| 【危険】 | `POST /schedule/generate-and-assign` | 旧「生成＋割当」1 TX。UI 未使用 |
| 【危険】 | `POST /schedule/generate-week` | 旧 Layer1（週生成）。UI 未使用。generate-week-only を使う |
| 【危険】 | `POST /schedule/generate-week-only` | 「週を生成」。その週の `source=auto` の予定を全部消して固定訪問の枠から作り直す（担当は割り当てない）。罠 B-2: 「今週全件解除」の後に押すと手で動かした訪問（manual_week→auto に戻っている）も消える |
| 【危険】 | `POST /schedule/v2/reset-to-fixed` | 週の予定を論理削除→固定訪問の枠から再生成。`confirm:true` 必須・`dry_run` |
| 【危険】 | `POST /schedule/v2/sync-fixed-to-week` | 1 名の週を固定訪問の枠から作り直す。manual_week を消さず、よその担当・同行の既定・固定イベントまで書き換える → 使わない（10-09 §7）。UI 未使用 |
| 【危険】 | `POST /schedule/v2/unassign-all-staff` | 週の全コース担当と visit_staff_assignments を一括解除（予定は残る）。戻る対象外（監査ログのみ） |
| 【罠】 | `POST /schedule/v2/visit-cancel-week` | 今週だけ取消（status=cancelled・行は残る・送信すると削除）。保留プールには出ない。当日以前/打刻あり/青ピンは 422。`cancel=false` で戻す。戻る対象 |
| 【罠】 | `POST /schedule/v2/visits/week-pin/bulk` | 青ピン一括。`pinned=false`（今週全件解除）は manual_week を auto に戻す＝次の週生成で消える（B-2）。`dry_run` で件数 |
| 【危険】 | `POST /staff/import-export/replace-all` | Excel で職員マスターを完全置換。multipart・`dry_run` |
| 【危険】 | `DELETE /staff/{staff_id}` | 職員を論理削除 |
| 【危険】 | `DELETE /visit-recordings/{recording_id}` | 音声記録を削除 |

その他の既知の罠（10-09 HANDOFF §7・10-12 HANDOFF §5）:
- 「今週全件解除」（`week-pin/bulk pinned=false`）→「週を生成」（`generate-week-only`）で週 42〜44 が消えて作り直される（B-2）。直るまでこの順で叩かない。
- 「今週だけ取消」（visit-cancel-week）では保留プールに出ない（取消も件数に数える）。プールへ移すのは `DELETE /visits/{id}`。
- `sync-fixed-to-week` は使わない（manual_week を消さず、よその担当・同行の既定・固定イベントまで書き換える）。
- カイポケ取込の置換（replace-inbound・smart-inbound-apply の打刻なし日）は青ピンを守らない（B-3）。
- 患者の状態「終了」= 内部 `cancelled`。予定・枠を消すのは `status-change` の `remove_schedule` だけ（PATCH・申請の承認・取込では消えない）。
- お客様がカイポケ側で直接直す運用がある → らく助から送る前に必ず `diff-local` で差分を見る（週42 のずれ約 45 件）。
- カイポケの export は担当なし（-）の行を出さない → 送る前に担当なしが 0 件か確認。RPA は 17〜18 時台に失敗しやすい（原則日中）。准看の追加も送れる（本番 `.env` `KAIPOKE_RPA_SERVICE_BRANCH_ENABLED=True`）。
- `_serialize_visit`（`visits.py:396`）は手書き dict。新しい列は API に出ないことがある（青ピン非表示バグの原因）。
- 本番 VPS は UTC。日付境界の判定は API 側で JST。

## 3. 認証（5 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `POST /auth/change-password` | auth.py:195 | ログイン | 書 | 【注意】自分のパスワード変更。統一パスワード運用を崩すので代行しない | current_password*, new_password* |  | 監査ログ | settings/password |
| 2 | `POST /auth/login` | auth.py:54 | なし | 書 | body `{identifier\|email, password}` → `{user, tokens:{access_token, refresh_token}}`。5 回失敗で 15 分ロック(423)・レート制限 5/15分(識別子)・100/15分(IP) |  | LoginResponse |  | NextAuth（lib/auth.ts） |
| 3 | `POST /auth/logout` | auth.py:184 | ログイン | 書 | サーバー側は何もしない（JWT はステートレス） |  |  |  | **UI 未使用** |
| 4 | `GET /auth/me` | auth.py:141 | ログイン | 読 |  |  | UserOut |  | NextAuth（lib/auth.ts） |
| 5 | `POST /auth/refresh` | auth.py:146 | なし | 書 | body `{refresh_token}` → 新しい TokenPair（refresh は 30 日） |  | TokenPair |  | NextAuth（lib/auth.ts） |

## 4. 患者（23 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 6 | `GET /patient-same-address-links` | patient_same_address_links.py:235 | ログイン | 読 | 同住所リンク一覧を取得 (read-only; audit_log は記録しない). | ?patient_id | list[PatientSameAddressLinkRead] |  | patients/SameAddressLinksSection |
| 7 | `POST /patient-same-address-links` | patient_same_address_links.py:82 | admin | 書 | 同住所リンクを UPSERT. | patient_a_id*, patient_b_id* | PatientSameAddressLinkRead \| None | 監査ログ | patients/SameAddressLinksSection |
| 8 | `DELETE /patient-same-address-links/{patient_a_id}/{patient_b_id}` | patient_same_address_links.py:191 | admin | 書 | 同住所リンクを削除する. 行が無い場合は 204 で冪等 (= 既に preferred). |  |  |  | patients/SameAddressLinksSection |
| 9 | `GET /patients` | patients.py:167 | ログイン | 読 |  | ?limit&offset | list[PatientRead] |  | BulkPoolInsertDialog、CourseDayTablePanel、CreatePatientDialog、PendingRequestPanel 他11 |
| 10 | `POST /patients` | patients.py:233 | admin | 書 | 患者マスター新規。`weekly_pattern`（曜日ごとの希望: time_type/preferred_start/end 等）もここ |  | PatientRead |  | BulkPoolInsertDialog、CourseDayTablePanel、CreatePatientDialog、PendingRequestPanel 他11 |
| 11 | `GET /patients/import-export/export` | patients_excel.py:109 | admin | 読 | alive な患者全件 + 関連 PFV をワークブックに書いて返す. |  |  |  | patients/PatientsExcelButtons |
| 12 | `POST /patients/import-export/import` | patients_excel.py:171 | admin | 書 | Excel 取込（追加/更新）。multipart・`dry_run` でプレビュー | ?file&dry_run | PatientExcelImportResponse | dry_run | patients/PatientsExcelButtons |
| 13 | `GET /patients/import-export/karte-export` | patients_excel.py:409 | admin | 読 | 指定患者のカルテ Excel を返す. Content-Disposition は 患者コード_氏名.xlsx. |  |  |  | BulkPoolInsertDialog、CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList 他9 |
| 14 | `POST /patients/import-export/karte-import` | patients_excel.py:450 | admin | 書 | カルテ 1 枚取込。multipart・`dry_run` | ?file&dry_run | PatientExcelImportResponse | dry_run | patients/PatientKarteButtons、patients/[id] |
| 15 | `GET /patients/import-export/karte-template` | patients_excel.py:383 | admin | 読 | dropdown と体裁のみの空白カルテを返す (新規患者ヒアリング記入用). |  |  |  | patients/PatientKarteButtons |
| 16 | `POST /patients/import-export/replace-all` | patients_excel.py:260 | admin | 書 | 【危険】Excel で患者マスターを完全置換（バックアップ復元用）。multipart・`dry_run` | ?file&dry_run | PatientExcelReplaceAllResponse | dry_run | patients/PatientsExcelButtons、patients/PatientsReplaceAllButton |
| 17 | `GET /patients/import-export/template` | patients_excel.py:74 | admin | 読 | ヘッダーと DataValidation のみ書かれた空ワークブックを返す. |  |  |  | patients/PatientsExcelButtons |
| 18 | `DELETE /patients/{patient_id}` | patients.py:406 | admin | 書 | 【危険】患者を論理削除（予定・枠の連動処理はしない） |  |  |  | CourseDayTablePanel、PatientEditDialog、PatientNotActiveGate、PatientScheduleDetailDialog 他5 |
| 19 | `GET /patients/{patient_id}` | patients.py:200 | ログイン | 読 |  |  | PatientRead |  | CourseDayTablePanel、PatientEditDialog、PatientNotActiveGate、PatientScheduleDetailDialog 他5 |
| 20 | `PATCH /patients/{patient_id}` | patients.py:278 | admin | 書 | 【罠】患者マスター更新（希望=weekly_pattern・住所・性別制限・2名体制など）。status を PATCH で変えても予定・枠は消えない（終了時の連動は status-change） |  | PatientRead |  | CourseDayTablePanel、PatientEditDialog、PatientNotActiveGate、PatientScheduleDetailDialog 他5 |
| 21 | `GET /patients/{patient_id}/ng-staff` | patient_ng_staff.py:73 | admin/staff | 読 | 当該患者の NG スタッフを名前解決付きで返す. |  | list[PatientNgStaffRead] |  | PatientScheduleDetailDialog、field/FieldSheets、patients/PatientNgStaffSection、patients/[id] |
| 22 | `DELETE /patients/{patient_id}/ng-staff/{staff_id}` | patient_ng_staff.py:175 | admin | 書 | NG 指定を解除する. 行が無ければ 404. |  |  |  | patients/PatientNgStaffSection |
| 23 | `PUT /patients/{patient_id}/ng-staff/{staff_id}` | patient_ng_staff.py:112 | admin | 書 | NG スタッフを登録 / 理由メモを更新する (冪等). | note | PatientNgStaffRead |  | patients/PatientNgStaffSection |
| 24 | `GET /patients/{patient_id}/qr` | patient_qr.py:67 | admin | 読（初回書） | QR トークン取得（無ければ生成して保存＝初回だけ書く） |  | PatientQrRead |  | patients/qr-print |
| 25 | `POST /patients/{patient_id}/qr/regenerate` | patient_qr.py:106 | admin | 書 | 【危険】QR トークン再発行。旧カードは無効（掲示カードの刷り直しが必要） |  | PatientQrRead | 監査ログ | patients/[id] |
| 26 | `GET /patients/{patient_id}/same-address-candidates` | patients.py:459 | ログイン | 読 | 同住所候補 (= ``_address_bucket`` 同一かつ ``address`` 文字列一致の他患者) を返す. |  | list[SameAddressCandidate] |  | patients/SameAddressLinksSection、patients/[id] |
| 27 | `POST /patients/{patient_id}/status-change` | patients.py:367 | admin | 書 | 【危険】状態変更＋予定連動。`remove_schedule=true` で当日以降の予定と固定訪問の枠も消す（終了=内部 `cancelled`）。消した週はカイポケへ送ると削除が出る | status*, from_date, special_period_action, regenerate, remove_schedule, note | StatusChangeResult |  | patients/PatientStatusChangeDialog |
| 28 | `GET /patients/{patient_id}/status-impact` | patients.py:316 | admin | 読 | 状態変更の影響件数（確認ダイアログ用・読むだけ）。`?to=` | ?to | StatusImpact |  | patients/PatientStatusChangeDialog |

## 5. 固定訪問(PFV)・希望・週⇔マスター書き戻し（13 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 29 | `POST /patients/bulk-apply-week-only-visit-changes` | patient_sync.py:787 | admin | 書 | 【注意】(患者,曜日) 単位で週の予定を一括更新。`dry_run`。UI 未使用 | patient_visit_changes*, iso_year*, iso_week*, dry_run | BulkApplyWeekOnlyVisitChangesResponse | dry_run | **UI 未使用** |
| 30 | `POST /patients/bulk-sync-week-to-fixed` | patient_sync.py:615 | admin | 書 | 【注意】週→固定訪問の枠（複数名・1 TX）。`dry_run` | patient_ids*, iso_year*, iso_week*, dry_run | BulkSyncWeekToFixedResponse | dry_run | CourseDayTablePanel、PatientScheduleDetailDialog |
| 31 | `POST /patients/fixed-visits/from-week-bulk` | patient_fixed_visits.py:806 | admin | 書 | 【危険】全 active 患者の固定訪問の枠をその週の予定で置換（赤ピン行は残す）。dry_run なし | ?iso_year&iso_week&mode |  |  | BulkFixToPatternButton |
| 32 | `POST /patients/fixed-visits/pin/bulk` | patient_fixed_visits.py:986 | admin | 書 | 赤ピン一括切替 |  | list[PatientFixedVisitV2Read] | 監査ログ | CourseDayTablePanel |
| 33 | `PATCH /patients/fixed-visits/{pfv_id}/pin` | patient_fixed_visits.py:940 | admin | 書 | 赤ピン（固定訪問の枠を動かさない）切替 | is_pinned* | PatientFixedVisitV2Read | 監査ログ | CourseDayTablePanel |
| 34 | `DELETE /patients/{patient_id}/fixed-visits` | patient_fixed_visits.py:779 | admin | 書 | 【危険】その mode の固定訪問の枠を全削除（`?mode=`） | ?mode |  |  | CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList、SpecialVisitPlaceLauncher 他1 |
| 35 | `GET /patients/{patient_id}/fixed-visits` | patient_fixed_visits.py:411 | ログイン | 読 |  | ?mode | list[PatientFixedVisitV2Read] |  | CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList、SpecialVisitPlaceLauncher 他1 |
| 36 | `PUT /patients/{patient_id}/fixed-visits` | patient_fixed_visits.py:532 | admin | 書 | 【注意】固定訪問の枠を mode（normal/special）単位で一括上書き。`change_scope=pattern_and_week` でその週の予定も作り直す。制約警告は `acknowledge_constraint_warnings` | mode*, items, change_scope, iso_year, iso_week, capacity_override_reason, acknowledge_constraint_warnings | PatientFixedVisitsBulkPutResponse | dry_run・制約警告の承認・通知 | CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList、SpecialVisitPlaceLauncher 他1 |
| 37 | `GET /patients/{patient_id}/fixed-visits/course-load` | patient_fixed_visits.py:474 | admin | 読 | 案Z: コースセレクトの空き情報 (○△×・残容量) の材料。 | ?mode | PfvCourseLoadResponse |  | patients |
| 38 | `POST /patients/{patient_id}/fixed-visits/from-week` | patient_fixed_visits.py:1065 | admin | 書 | 【注意】その週の予定を 1 名の固定訪問の枠へ書き戻す（マスター変更） | ?iso_year&iso_week&mode | list[PatientFixedVisitV2Read] |  | patients |
| 39 | `POST /patients/{patient_id}/fixed-visits/validate` | patient_fixed_visits.py:443 | admin | 読 | 枠編集のライブ検査（書かない） | mode*, items | PfvValidateResponse |  | patients |
| 40 | `POST /patients/{patient_id}/sync-week-visits-to-fixed` | patient_sync.py:344 | admin | 書 | 【注意】週→固定訪問の枠 upsert（1 名）。`dry_run`。画面からの直接呼び出しは未検出 | iso_year*, iso_week*, dry_run | SyncWeekToFixedResponse | dry_run | **UI 未使用** |
| 41 | `POST /schedule/v2/update-fixed-time-master` | schedule_v2.py:2868 | admin | 書 | 【注意】同住所集約の「マスター更新」= 固定訪問の時刻を変更 | patient_id*, weekday*, new_start*, new_end, new_time_type | UpdateFixedTimeMasterResponse |  | FixedTimeEditModal |

## 6. 職員・コース・コーステンプレート・受け入れ枠（28 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 42 | `GET /acceptance-calendar` | acceptance_calendar.py:56 | admin/staff | 読 | 指定拠点の全エントリを weekday + time_slot 昇順で返す. | ?office_id | list[AcceptanceCalendarRead] |  | **UI 未使用** |
| 43 | `PUT /acceptance-calendar` | acceptance_calendar.py:80 | admin | 書 | 拠点の受入カレンダーを全置換。UI 未使用（受け入れ枠は acceptance-matrix 側） | office_id*, entries | list[AcceptanceCalendarRead] |  | **UI 未使用** |
| 44 | `GET /acceptance-matrix` | acceptance_matrix.py:45 | admin/staff | 読 | 指定週・拠点の受け入れ枠マトリックスを返す (DB 書込なし). | ?iso_year&iso_week&office_id | AcceptanceMatrixResponse |  | acceptance、field/AcceptanceFieldView |
| 45 | `DELETE /acceptance-matrix/standing-override` | acceptance_matrix.py:235 | admin | 書 | 常設上書きを解除する (週別 or 自動算出に戻す). 冪等 (無くても 204). | ?weekday&time_slot&office_id |  |  | acceptance |
| 46 | `PUT /acceptance-matrix/standing-override` | acceptance_matrix.py:179 | admin | 書 | (office_id?, weekday, time_slot) の常設上書きを upsert する (毎週共通). | office_id, weekday*, time_slot*, status*, notes |  |  | acceptance |
| 47 | `DELETE /acceptance-matrix/week-override` | acceptance_matrix.py:135 | admin | 書 | 週別上書きを解除する (常設 or 自動算出に戻す). 冪等 (無くても 204). | ?office_id&iso_year&iso_week&weekday&time_slot |  |  | acceptance、field/AcceptanceFieldView |
| 48 | `PUT /acceptance-matrix/week-override` | acceptance_matrix.py:71 | admin | 書 | (office_id, iso_year, iso_week, weekday, time_slot) の週別上書きを upsert する. | office_id*, iso_year*, iso_week*, weekday*, time_slot*, status*, notes | WeekOverrideRead |  | acceptance、field/AcceptanceFieldView |
| 49 | `GET /course-templates` | course_templates.py:93 | admin/staff | 読 | 指定拠点の全テンプレート (deleted_at IS NULL) を label 昇順で返す. | ?office_id | list[CourseTemplateRead] |  | CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList、SpecialVisitPlaceLauncher 他1 |
| 50 | `POST /course-templates` | course_templates.py:117 | admin | 書 | 新規テンプレート作成. UNIQUE (office_id, label) 違反は 409. | office_id* | CourseTemplateRead |  | CourseDayTablePanel、ImprovementSuggestionsSection、PoolCandidateList、SpecialVisitPlaceLauncher 他1 |
| 51 | `DELETE /course-templates/{template_id}` | course_templates.py:173 | admin | 書 | 【注意】コーステンプレート論理削除 |  |  |  | **画面から未使用**（hook `useDeleteCourseTemplate` のみ） |
| 52 | `PATCH /course-templates/{template_id}` | course_templates.py:150 | admin | 書 | 部分更新. office_id は変更不可. | label, capacity_mon, capacity_tue, capacity_wed, capacity_thu, capacity_fri, capacity_sat, capacity_sun, notes | CourseTemplateRead |  | **画面から未使用**（hook `useDeleteCourseTemplate` のみ） |
| 53 | `GET /courses` | courses.py:90 | admin | 読 |  | ?iso_year&iso_week&weekday&course_status&limit&offset | list[CourseRead] |  | CourseDayTablePanel |
| 54 | `POST /courses` | courses.py:285 | admin | 書 |  |  | CourseRead |  | CourseDayTablePanel |
| 55 | `DELETE /courses/{course_id}` | courses.py:485 | admin | 書 | 【注意】週のコースを削除 |  |  |  | CourseDayTablePanel |
| 56 | `GET /courses/{course_id}` | courses.py:271 | admin | 読 |  |  | CourseRead |  | CourseDayTablePanel |
| 57 | `PATCH /courses/{course_id}` | courses.py:317 | admin | 書 | コースの担当(assigned_staff_id)等を変更。戻る(op-log)対象。表示の正典は courses.assigned_staff_id |  | CourseRead | 戻る(op-log)・通知 | CourseDayTablePanel |
| 58 | `GET /staff` | staff.py:87 | ログイン | 読 |  | ?limit&offset | list[StaffRead] |  | BulkPoolInsertDialog、CourseDayTablePanel、PendingRequestPanel、SpecialVisitPlaceLauncher 他9 |
| 59 | `POST /staff` | staff.py:132 | admin | 書 |  |  | StaffRead |  | BulkPoolInsertDialog、CourseDayTablePanel、PendingRequestPanel、SpecialVisitPlaceLauncher 他9 |
| 60 | `GET /staff/import-export/export` | staff_excel.py:87 | admin | 読 | alive なスタッフ全件 + 関連 shift をワークブックに書いて返す. |  |  |  | staff/StaffExcelButtons |
| 61 | `POST /staff/import-export/import` | staff_excel.py:160 | admin | 書 | multipart/form-data でアップロード. ``dry_run`` が True ならプレビューのみ. | ?file&dry_run | StaffExcelImportResponse | dry_run | staff/StaffExcelButtons |
| 62 | `POST /staff/import-export/replace-all` | staff_excel.py:262 | admin | 書 | 【危険】Excel で職員マスターを完全置換。multipart・`dry_run` | ?file&dry_run | StaffExcelReplaceAllResponse | dry_run | staff/StaffExcelButtons、staff/StaffReplaceAllButton |
| 63 | `GET /staff/import-export/template` | staff_excel.py:58 | admin | 読 | ヘッダーと DataValidation のみ書かれた空ワークブックを返す. |  |  |  | staff/StaffExcelButtons |
| 64 | `DELETE /staff/{staff_id}` | staff.py:222 | admin | 書 | 【危険】職員を論理削除 |  |  |  | CourseDayTablePanel、admin/pending-requests、cockpit/SyncBar、m/home 他5 |
| 65 | `GET /staff/{staff_id}` | staff.py:113 | ログイン | 読 |  |  | StaffRead |  | CourseDayTablePanel、admin/pending-requests、cockpit/SyncBar、m/home 他5 |
| 66 | `PATCH /staff/{staff_id}` | staff.py:162 | admin | 書 | 職員マスター更新（is_trainee・qualification 等） | code, name, kana, sex, role, status, primary_office_id, note, is_trainee… | StaffRead |  | CourseDayTablePanel、admin/pending-requests、cockpit/SyncBar、m/home 他5 |
| 67 | `GET /staff/{staff_id}/ng-patients` | patient_ng_staff.py:208 | admin/staff | 読 | スタッフ詳細の閲覧サマリ用の逆引き. |  | list[StaffNgPatientRead] |  | CourseDayTablePanel、staff/[id] |
| 68 | `GET /staff/{staff_id}/shifts` | staff_shifts.py:60 | ログイン | 読 |  |  | ShiftsResponse |  | admin/pending-requests、m/shifts、staff、staff/[id] 他1 |
| 69 | `PUT /staff/{staff_id}/shifts` | staff_shifts.py:84 | admin | 書 | 勤務時間（曜日別）を全置換 | shifts* | ShiftsResponse |  | admin/pending-requests、m/shifts、staff、staff/[id] 他1 |

## 7. 週の予定（週を作る・コピー・生成・割当・今週だけ移動/取消/担当替え/休み・ピン・戻る）（34 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 70 | `POST /schedule/apply-staff-review` | schedule.py:2261 | admin | 書 | 割当レビューで承認したコースを反映 | iso_year*, iso_week*, items*, op_group_id | ApplyStaffReviewResponse | 戻る(op-log)・通知 | AssignWarningDialog、CourseDayTablePanel |
| 71 | `POST /schedule/assign-staff-only` | schedule.py:1841 | admin | 書 | 自動スタッフ割当（Layer3）。週単位ロック（二度押し 409） | iso_year*, iso_week*, office_id | AssignStaffOnlyResponse |  | CourseDayTablePanel |
| 72 | `POST /schedule/generate-week-only` | schedule.py:1703 | admin | 書 | 【危険】「週を生成」。その週の `source=auto` の予定を全部消して固定訪問の枠から作り直す（担当は割り当てない）。罠 B-2: 「今週全件解除」の後に押すと手で動かした訪問（manual_week→auto に戻っている）も消える | iso_year*, iso_week*, office_id | GenerateWeekOnlyResponse |  | CourseDayTablePanel |
| 73 | `POST /schedule/place-and-fix` | schedule.py:840 | admin | 書 | 空き枠へ 1 件配置（予定作成）。`fix_pattern=true` で固定訪問の枠にも登録。戻る(op-log)対象 | patient_id*, course_template_id, course_template_ids, iso_year*, iso_week*, weekday*, start_time*, duration_min*, staff_count… | PlaceAndFixResponse | 戻る(op-log)・制約警告の承認・通知 | CourseDayTablePanel、PoolCandidateList、SpecialVisitPlaceLauncher |
| 74 | `POST /schedule/v2/assign-candidates` | substitute_candidates.py:70 | admin | 読 | 指定した訪問 (コース丸ごと / 複数コース / 訪問 ID 指定) を誰に入れられるかを返す. | date*, course_id, course_ids, visit_ids | SubstituteCandidatesResponse |  | CourseDayTablePanel、cockpit/AssignSuggestionPopover、cockpit/VisitActionMenu |
| 75 | `GET /schedule/v2/board` | schedule_v2.py:4496 | admin/staff | 読 | 対象週 × 拠点の実 Visit を office × weekday × course 構造で返す read-only API. | ?iso_year&iso_week&office_id | BoardResponse |  | field/FieldBoard |
| 76 | `POST /schedule/v2/copy-week` | copy_week.py:261 | admin | 書 | 【注意】前の週をコピーして週を作る（body は preview と同じ source_week_start/target_week_start/exclude_visit_ids/fill_from_fixed に assign_staff・confirm・expected_counts を足す）。`confirm:true` 必須・`expected_counts` で取り違え防止。実行前に写す先をスナップショット（inbound-snapshots/restore で戻せる）。カイポケへは送らない | assign_staff, confirm, expected_counts | CopyWeekResponse | confirm・expected_counts | MakeWeekDialog |
| 77 | `POST /schedule/v2/copy-week/preview` | copy_week.py:247 | admin | 読 | コピー内容の確認（DB 不変）。`fill_from_fixed`・`exclude_visit_ids` | source_week_start*, target_week_start*, exclude_visit_ids, fill_from_fixed | CopyWeekPreviewResponse |  | MakeWeekDialog |
| 78 | `GET /schedule/v2/copy-week/sources` | copy_week.py:218 | admin | 読 |  | ?target_week_start | CopyWeekSourcesResponse |  | MakeWeekDialog |
| 79 | `POST /schedule/v2/course-move-weekday-week-only` | schedule_v2.py:2241 | admin | 書 | コース帯を別曜日へ（今週だけ）。戻る対象 | course_id*, to_weekday*, op_group_id | CourseMoveWeekdayWeekOnlyResponse | 戻る(op-log) | CourseDayTablePanel |
| 80 | `POST /schedule/v2/op-log/redo` | op_log.py:106 | admin | 書 | やり直し | iso_year*, iso_week* | UndoRedoResponse |  | CourseDayTablePanel |
| 81 | `GET /schedule/v2/op-log/state` | op_log.py:31 | admin | 読 | 自分のその週の操作スタック状態を返す（D-4: 自分の操作のみ）。 | ?iso_year&iso_week | OpLogStateResponse |  | CourseDayTablePanel |
| 82 | `POST /schedule/v2/op-log/undo` | op_log.py:53 | admin | 書 | 自分の直近操作グループを取り消す（週単位・自分の操作のみ） | iso_year*, iso_week* | UndoRedoResponse |  | CourseDayTablePanel |
| 83 | `GET /schedule/v2/pfv-course-presence` | schedule_v2.py:3294 | admin | 読 | PO 決定 (2026-07-09): 固定訪問スケジュール (PFV) に含まれるコースを「正」とし、 |  | PfvCoursePresenceResponse |  | **画面から未使用**（hook `usePfvCoursePresence` のみ） |
| 84 | `POST /schedule/v2/reset-to-fixed` | schedule_v2.py:1240 | admin | 書 | 【危険】週の予定を論理削除→固定訪問の枠から再生成。`confirm:true` 必須・`dry_run` | iso_year*, iso_week*, office_ids, confirm, mode, dry_run | AutoScheduleV2ResetToFixedResponse | dry_run・confirm | ResetToFixedButton |
| 85 | `POST /schedule/v2/staff-off-week` | schedule_v2.py:1819 | admin | 書 | 「○○さんを M/D 休みに」= 休み登録＋担当付け替えを 1 TX。`to_staff_id` 省略で担当なし。戻る 1 回で全部戻る | staff_id*, date*, to_staff_id, acknowledge_constraint_warnings, op_group_id | StaffOffWeekResponse | 戻る(op-log)・制約警告の承認・通知 | CourseDayTablePanel |
| 86 | `POST /schedule/v2/substitute-candidates` | substitute_candidates.py:39 | admin | 読 | 指定スタッフが指定日に抜けたときの代替候補をコース単位で返す. | staff_id*, date*, course_id | SubstituteCandidatesResponse |  | cockpit/SubstitutePanel |
| 87 | `POST /schedule/v2/sync-fixed-to-week` | schedule_v2.py:1362 | admin | 書 | 【危険】1 名の週を固定訪問の枠から作り直す。manual_week を消さず、よその担当・同行の既定・固定イベントまで書き換える → 使わない（10-09 §7）。UI 未使用 | iso_year*, iso_week*, patient_id* | AutoScheduleV2SyncFixedToWeekResponse | dry_run | **UI 未使用** |
| 88 | `POST /schedule/v2/unassign-all-staff` | schedule_v2.py:3158 | admin | 書 | 【危険】週の全コース担当と visit_staff_assignments を一括解除（予定は残る）。戻る対象外（監査ログのみ） | iso_year*, iso_week*, office_ids | AutoScheduleV2UnassignAllResponse | 監査ログ | UnassignAllStaffButton |
| 89 | `POST /schedule/v2/update-fixed-time-week-only` | schedule_v2.py:3039 | admin | 書 | 予定 1 件の時刻だけ上書き（今週だけ） | visit_id*, new_start*, new_end | UpdateFixedTimeWeekOnlyResponse |  | **画面から未使用**（hook `useUpdateFixedTimeWeekOnlyMutation` のみ） |
| 90 | `POST /schedule/v2/visit-assign-staff-week` | schedule_v2.py:1673 | admin | 書 | 今週だけ担当を付け替え（盤面ドラッグ）。`staff_id=null` で担当なし。戻る対象 | visit_id*, staff_id, acknowledge_constraint_warnings, op_group_id | VisitAssignStaffWeekResponse | 戻る(op-log)・制約警告の承認・通知 | CourseDayTablePanel |
| 91 | `POST /schedule/v2/visit-cancel-week` | schedule_v2.py:6619 | admin | 書 | 【罠】今週だけ取消（status=cancelled・行は残る・送信すると削除）。保留プールには出ない。当日以前/打刻あり/青ピンは 422。`cancel=false` で戻す。戻る対象 | visit_id*, cancel*, reason, op_group_id | VisitRead | 戻る(op-log) | CourseDayTablePanel |
| 92 | `POST /schedule/v2/visit-move-week-only` | schedule_v2.py:1471 | admin | 書 | 今週だけ別の曜日/時刻/コースへ移す。戻る対象 | iso_year*, iso_week*, patient_id*, old_weekday*, old_start_time*, new_weekday*, new_start_time*, new_course_template_id, op_group_id… | VisitMoveWeekOnlyResponse | 戻る(op-log)・制約警告の承認・通知 | CourseDayTablePanel、ImprovementSuggestionsSection |
| 93 | `POST /schedule/v2/visit-service-override` | schedule_v2.py:6860 | admin | 書 | 訪問 1 件の請求区分（kaipoke_service_override）を設定/解除。戻る対象 | visit_id, item_id, service_content, op_group_id | VisitRead | 戻る(op-log) | CourseDayTablePanel、cockpit/SyncBar |
| 94 | `POST /schedule/v2/visits/week-pin/bulk` | schedule_v2.py:2502 | admin | 書 | 【罠】青ピン一括。`pinned=false`（今週全件解除）は manual_week を auto に戻す＝次の週生成で消える（B-2）。`dry_run` で件数 | iso_year*, iso_week*, pinned*, dry_run | VisitWeekPinBulkResponse | dry_run・監査ログ | BulkWeekPinAllButton |
| 95 | `PATCH /schedule/v2/visits/{visit_id}/week-pin` | schedule_v2.py:2424 | admin | 書 | 青ピン 1 件切替 | pinned* | VisitWeekPinResponse |  | CourseDayTablePanel |
| 96 | `GET /schedule/v2/weekday-staff-capacity` | schedule_v2.py:3217 | admin | 読 | 週ビュー (CourseWeekOverview / CourseDayTablePanel) の A-E コース「休」/ | ?iso_year&iso_week&office_id | WeekdayStaffCapacityResponse |  | **画面から未使用**（hook `useWeekdayStaffCapacity` のみ） |
| 97 | `GET /visits` | visits.py:579 | ログイン | 読 |  | ?limit&offset&week_end | list[VisitRead] |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、m/home、m/record/new 他2 |
| 98 | `POST /visits` | visits.py:910 | admin | 書 | 予定を直接作成（通常は place-and-fix を使う） |  | VisitRead |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、m/home、m/record/new 他2 |
| 99 | `DELETE /visits/{visit_id}` | visits.py:1066 | admin | 書 | 【注意】予定を論理削除→保留プールに出る。2 名体制はペアも消す（`cascade_partner` 既定 true）。`cascade_fixed_visit=true` は 422。戻る対象 |  |  | 戻る(op-log) | CourseDayTablePanel、SpecialVisitPlaceLauncher、m/today/[visitId] |
| 100 | `GET /visits/{visit_id}` | visits.py:784 | ログイン | 読 | 訪問詳細を返す。 |  | VisitRead |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、m/today/[visitId] |
| 101 | `PATCH /visits/{visit_id}` | visits.py:962 | admin | 書 | 予定を直接更新（担当・日時・status など） | patient_id, primary_staff_id, secondary_staff_id, mentor_staff_id, visit_date, start_time, end_time, type, status… | VisitRead |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、m/today/[visitId] |
| 102 | `POST /visits/{visit_id}/staff` | visits.py:1207 | admin | 書 | 2 人目以降の担当を追加。UI 未使用 | staff_id* | VisitRead |  | **UI 未使用** |
| 103 | `DELETE /visits/{visit_id}/staff/{staff_id}` | visits.py:1259 | admin | 書 | 担当を外す。UI 未使用 |  |  |  | **UI 未使用** |

## 8. 休み・職員イベント・申請/承認・月確定（34 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 104 | `GET /event-templates` | event_templates.py:97 | ログイン | 読 |  | ?staff_id&include_inactive | list[EventTemplateRead] |  | TimelineEventAddDialog、staff、staff/[id] |
| 105 | `POST /event-templates` | event_templates.py:205 | admin | 書 |  | staff_id, title*, event_type, start_time, end_time, blocking, note, sort_order, is_active | EventTemplateRead |  | TimelineEventAddDialog、staff、staff/[id] |
| 106 | `GET /event-templates/history-suggestions` | event_templates.py:120 | ログイン | 読 |  | ?staff_id&months | list[HistorySuggestionItem] |  | staff |
| 107 | `PUT /event-templates/reorder` | event_templates.py:243 | admin | 書 |  | staff_id, ordered_ids* | list[EventTemplateRead] |  | staff |
| 108 | `DELETE /event-templates/{template_id}` | event_templates.py:324 | admin | 書 |  |  |  |  | staff |
| 109 | `PATCH /event-templates/{template_id}` | event_templates.py:286 | admin | 書 |  | title, event_type, start_time, end_time, blocking, note, sort_order, is_active | EventTemplateRead |  | staff |
| 110 | `GET /pending-requests` | pending_requests.py:552 | ログイン | 読 |  | ?request_type&target_staff_id&target_patient_id&target_date_from&target_date_to&limit&offset | Paginated[PendingRequestV2Read] |  | field/ApprovePanel、field/FieldSheets、m/leave |
| 111 | `POST /pending-requests` | pending_requests.py:408 | admin/staff | 書 | 申請作成（`request_type` 12 種＋`payload`・target_staff_id/target_patient_id/target_date/scope） |  | PendingRequestV2Read |  | field/ApprovePanel、field/FieldSheets、m/leave |
| 112 | `POST /pending-requests/create-and-apply` | pending_requests.py:477 | admin | 書 | 【注意】申請作成＋即反映を 1 TX。UI 未使用 |  | PendingRequestV2Read |  | **UI 未使用** |
| 113 | `DELETE /pending-requests/{request_id}` | pending_requests.py:435 | ログイン | 書 | 未処理 (pending) の申請を取り下げる (mobile-leave-request-design.md §1-c). |  |  |  | m/leave |
| 114 | `GET /pending-requests/{request_id}` | pending_requests.py:611 | ログイン | 読 |  |  | PendingRequestV2Read |  | m/leave |
| 115 | `PATCH /pending-requests/{request_id}/approve` | pending_requests.py:761 | admin | 書 | 【注意】申請を承認し applier が業務反映（休み申請→休み登録 等） | edited_payload | PendingRequestV2Read |  | PendingRequestPanel、admin/pending-requests、field/ApprovePanel |
| 116 | `PATCH /pending-requests/{request_id}/approve-with-edit` | pending_requests.py:775 | admin | 書 | 内容を直して承認。画面からは未使用 | edited_payload | PendingRequestV2Read |  | **画面から未使用**（hook `useApproveWithEdit` のみ） |
| 117 | `PATCH /pending-requests/{request_id}/reject` | pending_requests.py:794 | admin | 書 | 却下＋通知 | rejection_reason* | PendingRequestV2Read | 通知 | admin/pending-requests、field/ApprovePanel |
| 118 | `PATCH /shift-requests/{request_id}/status` | shift_requests.py:105 | admin | 書 |  | status* | ShiftRequestRead |  | **画面から未使用**（hook `useUpdateShiftRequestStatus` のみ） |
| 119 | `POST /staff-event-defaults/bulk` | staff_event_defaults.py:79 | admin | 書 | 固定イベント（毎週）を staff×曜日で一括作成 | staff_ids*, weekdays*, start_time*, end_time*, title*, blocking, note | EventDefaultBulkResult |  | TimelineEventAddDialog、staff、staff/[id] |
| 120 | `GET /staff/overrides-week` | staff_overrides.py:32 | admin | 読 | 職員スケジュール盤面 (staff×weekday) にセル単位で引ける形で返す. | ?iso_year&iso_week | list[WeekOverrideRead] |  | CourseDayTablePanel、admin/pending-requests、m/leave、m/shifts 他2 |
| 121 | `GET /staff/{staff_id}/event-defaults` | staff_event_defaults.py:180 | ログイン | 読 |  |  | list[EventDefaultRead] |  | staff/[id] |
| 122 | `POST /staff/{staff_id}/event-defaults` | staff_event_defaults.py:202 | admin | 書 |  | weekday*, start_time*, end_time*, title*, blocking, note | EventDefaultRead |  | staff/[id] |
| 123 | `DELETE /staff/{staff_id}/event-defaults/{default_id}` | staff_event_defaults.py:281 | admin | 書 |  |  |  |  | staff/[id] |
| 124 | `PATCH /staff/{staff_id}/event-defaults/{default_id}` | staff_event_defaults.py:238 | admin | 書 |  | weekday, start_time, end_time, title, blocking, note | EventDefaultRead |  | staff/[id] |
| 125 | `GET /staff/{staff_id}/events` | staff_events.py:62 | ログイン | 読 | イベント一覧 (staff-event-history-design.md §2 Phase 1). | ?date_from&date_to&limit&offset&q&source&event_type | list[EventRead] |  | 職員詳細 EventsCard |
| 126 | `POST /staff/{staff_id}/events` | staff_events.py:135 | admin | 書 |  | date*, title*, start_time*, end_time*, type*, note, blocking | EventRead |  | 職員詳細 EventAddDialog、TimelineEventAddDialog |
| 127 | `DELETE /staff/{staff_id}/events/{event_id}` | staff_events.py:282 | admin | 書 |  |  |  |  | 職員詳細 EventsCard（helper でパス組立） |
| 128 | `PATCH /staff/{staff_id}/events/{event_id}` | staff_events.py:164 | admin | 書 | Update event (Wave 39: ``new_staff_id`` で別 staff への付け替えに対応). | date, title, start_time, end_time, type, note, new_staff_id, blocking | EventRead |  | 職員詳細 EventsCard（helper でパス組立） |
| 129 | `POST /staff/{staff_id}/events/{event_id}/cancel-week` | staff_events.py:238 | admin | 書 | 固定イベントを今週だけ取消/戻す | cancel* | EventRead |  | CourseDayTablePanel |
| 130 | `GET /staff/{staff_id}/overrides` | staff_overrides.py:131 | ログイン | 読 |  | ?date_from&date_to&limit | list[OverrideRead] |  | CourseDayTablePanel、admin/pending-requests、m/this-week、m/today 他1 |
| 131 | `POST /staff/{staff_id}/overrides` | staff_overrides.py:181 | admin | 書 | 日単位の休み/時間変更（type）を登録 | date*, type*, start_time, end_time, note | OverrideRead |  | CourseDayTablePanel、admin/pending-requests、m/this-week、m/today 他1 |
| 132 | `DELETE /staff/{staff_id}/overrides/{override_id}` | staff_overrides.py:265 | admin | 書 | 休みの取消（本人に通知あり） |  |  | 通知 | admin/pending-requests、staff/[id] |
| 133 | `PATCH /staff/{staff_id}/overrides/{override_id}` | staff_overrides.py:212 | admin | 書 |  | date, type, start_time, end_time, note | OverrideRead |  | admin/pending-requests、staff/[id] |
| 134 | `GET /staff/{staff_id}/shift-confirmations` | staff_shift_confirmations.py:48 | ログイン | 読 |  | ?from_&to | list[ShiftConfirmationRead] |  | admin/pending-requests |
| 135 | `POST /staff/{staff_id}/shift-confirmations` | staff_shift_confirmations.py:73 | admin | 書 | 月の休みを確定＋本人へ通知（再確定=更新+再通知） | month* | ShiftConfirmationRead | 通知 | admin/pending-requests |
| 136 | `GET /staff/{staff_id}/shift-requests` | shift_requests.py:53 | ログイン | 読 |  | ?limit&offset | list[ShiftRequestRead] |  | m/mypage |
| 137 | `POST /staff/{staff_id}/shift-requests` | shift_requests.py:78 | ログイン | 書 |  | target_month*, content* | ShiftRequestRead |  | m/mypage |

## 9. 特別訪問週間（10 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 138 | `GET /special-visit-marks/pool` | special_visits.py:944 | admin | 読 |  | ?iso_year&iso_week&office_id | list[PoolTicketRead] |  | CourseDayTablePanel、SpecialTicketPlacePanel |
| 139 | `DELETE /special-visit-marks/{mark_id}` | special_visits.py:682 | admin | 書 | ○の取消（配置済みは `?force`） | ?force |  |  | CourseDayTablePanel、SpecialTicketPlacePanel、SpecialVisitPlaceLauncher、SpecialVisitWeekDialog |
| 140 | `POST /special-visit-marks/{mark_id}/place` | special_visits.py:1031 | admin | 書 | 追加枠チケットをその週のコースへ配置（course_id/course_template_id, start_time, weekday…） |  | PlaceResponse | 通知 | CourseDayTablePanel、PoolCandidateList、SpecialVisitPlaceLauncher |
| 141 | `POST /special-visit-marks/{mark_id}/restore` | special_visits.py:853 | admin | 書 | 退避を解除（`?force`） | ?force | MarkRead |  | SpecialVisitWeekDialog |
| 142 | `GET /special-visit-periods` | special_visits.py:334 | admin | 読 |  | ?patient_id&include_inactive | list[PeriodRead] |  | SpecialVisitWeekDialog |
| 143 | `POST /special-visit-periods` | special_visits.py:285 | admin | 書 | 特別訪問週間の期間を作成（patient_id, start_date, end_date, weekly_target。active 重複は 422） |  | PeriodRead |  | SpecialVisitWeekDialog |
| 144 | `PATCH /special-visit-periods/{period_id}` | special_visits.py:355 | admin | 書 | 目標変更・延長・終了（end_date, weekly_target, note, status） |  | PeriodRead |  | SpecialVisitWeekDialog |
| 145 | `GET /special-visit-periods/{period_id}/calendar` | special_visits.py:405 | admin | 読 |  |  | CalendarRead |  | SpecialVisitWeekDialog |
| 146 | `POST /special-visit-periods/{period_id}/displace` | special_visits.py:729 | admin | 書 | 【注意】その日の固定訪問をプールへ退避（iso_year, iso_week, weekday・スナップショットを残す） |  | MarkRead |  | SpecialVisitWeekDialog |
| 147 | `POST /special-visit-periods/{period_id}/marks` | special_visits.py:627 | admin | 書 | ○（追加枠）を 1 セル追加（iso_year, iso_week, weekday） |  | MarkRead |  | SpecialVisitPlaceLauncher、SpecialVisitWeekDialog |

## 10. 同行（新人）（6 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 148 | `GET /accompaniment-defaults` | accompaniments.py:581 | ログイン | 読 |  | ?staff_id&trainee_staff_id | list[AccompanimentDefaultRead] |  | staff/[id] |
| 149 | `PUT /accompaniment-defaults` | accompaniments.py:608 | admin | 書 | 毎週の同行既定を全置換 | staff_id, trainee_staff_id, items | list[AccompanimentDefaultRead] |  | staff/[id] |
| 150 | `GET /accompaniments` | accompaniments.py:339 | ログイン | 読 |  | ?iso_year&iso_week&staff_id&trainee_staff_id | AccompanimentsListResponse |  | CourseDayTablePanel、staff/[id]、useAccompanimentController |
| 151 | `PUT /accompaniments` | accompaniments.py:368 | admin | 書 | 【注意】その職員×その週の同行リンクを全置換（course_ids/visit_ids/defaults） | staff_id, trainee_staff_id, iso_year*, iso_week*, course_ids, visit_ids, defaults, acknowledge_constraint_warnings | AccompanimentsListResponse | 制約警告の承認・通知 | CourseDayTablePanel、staff/[id]、useAccompanimentController |
| 152 | `GET /accompaniments/course-guard` | accompaniments.py:703 | ログイン | 読 | 当該スタッフが今週以降のコース担当 (courses.assigned_staff_id) に残っているか. | ?staff_id&trainee_staff_id | CourseGuardResponse |  | staff/[id]/edit |
| 153 | `DELETE /accompaniments/future` | accompaniments.py:766 | admin | 書 | 【危険】今週以降の同行リンクを物理削除＋既定を全削除（新人卒業） | ?staff_id&trainee_staff_id | AccompanimentFutureDeleteResponse |  | staff/[id]/edit |

## 11. 提案系（改善提案・範囲最適化・詰まり解消・プール投入・実現性チェック・健康診断・旧自動配置）（31 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 154 | `POST /allocate/run` | allocate.py:256 | admin | 書 | 旧配置エンジン。画面からは未使用 | week_start*, options | AllocateResponse |  | **画面から未使用**（hook `useRunAllocate` のみ） |
| 155 | `POST /courses/assign-staff` | courses.py:226 | admin | 書 | 【注意】旧 Layer3 割当。画面からは未使用 | iso_year*, iso_week* | CourseAssignStaffResponse |  | **画面から未使用**（hook `useAssignStaff` のみ） |
| 156 | `POST /courses/generate` | courses.py:157 | admin | 書 | 旧 Layer2 コース分け案。画面からは未使用 | iso_year*, iso_week*, weekday*, staff_count, random_state | CourseGenerateResponse |  | **画面から未使用**（hook `useGenerateCourseProposals` のみ） |
| 157 | `POST /diff/compute` | diff.py:25 | admin | 読 | CSV 2 本の差分計算（旧）。UI 未使用 | current_csv*, optimized_csv*, target_week_start*, target_week_end*, target_users | DiffResponse |  | **UI 未使用** |
| 158 | `POST /schedule/auto-allocate` | schedule.py:2968 | admin | 書 | 【注意】旧自動配置（proposed を永続化）。UI 未使用 | iso_year*, iso_week*, office_ids, mode | AutoAllocateResponse |  | **UI 未使用** |
| 159 | `POST /schedule/fix` | schedule.py:207 | admin | 書 | 【注意】旧: 週レイアウト→patients.weekly_pattern。UI 未使用 | iso_year*, iso_week*, layout | ScheduleFixResponse |  | **UI 未使用** |
| 160 | `POST /schedule/fix-or-pattern` | schedule.py:327 | admin | 書 | 【注意】旧: 既存 visit の時刻変更（mode で型にも）。UI 未使用 | mode*, visit_id*, new_weekday*, new_start_time*, new_duration_min*, iso_year*, iso_week* |  |  | **UI 未使用** |
| 161 | `POST /schedule/generate-and-assign` | schedule.py:1209 | admin | 書 | 【危険】旧「生成＋割当」1 TX。UI 未使用 | iso_year*, iso_week*, office_id | GenerateAndAssignResponse |  | **UI 未使用** |
| 162 | `POST /schedule/generate-week` | schedule.py:245 | admin | 書 | 【危険】旧 Layer1（週生成）。UI 未使用。generate-week-only を使う | iso_year*, iso_week* | GenerateWeekResponse |  | **UI 未使用** |
| 163 | `POST /schedule/proposal/{proposal_batch_id}/apply` | schedule.py:2997 | admin | 書 | 旧提案バッチ採用。UI 未使用 |  | ProposalApplyResponse |  | **UI 未使用** |
| 164 | `POST /schedule/proposal/{proposal_batch_id}/discard` | schedule.py:3020 | admin | 書 | 【注意】旧提案バッチの proposed を物理削除。UI 未使用 |  | ProposalDiscardResponse |  | **UI 未使用** |
| 165 | `POST /schedule/v2/apply-individual` | schedule_v2.py:955 | admin | 書 | 提案で 1 患者の固定訪問の枠を更新（`confirm`）。画面からの直接呼び出しは未検出 | proposal_id, proposal_batch_id, patient_id, confirm, iso_year, iso_week, force_lunch, visit_plans, change_scope… | AutoScheduleV2ApplyIndividualResponse | dry_run・confirm・制約警告の承認・通知 | **画面から未使用**（hook `useApplyIndividualMutation` のみ） |
| 166 | `POST /schedule/v2/apply-week-only` | schedule_v2.py:2589 | admin | 書 | 【注意】全面最適化の提案を予定のみへ反映。UI 未使用 | iso_year*, iso_week*, office_ids, visit_plans_per_patient, pending_edits, confirm, force_lunch | AutoScheduleV2ApplyWeekOnlyResponse | confirm | **UI 未使用** |
| 167 | `POST /schedule/v2/diff-add` | schedule_v2.py:756 | admin | 読 | 機能 A: プール患者 (固定枠未登録 active) を抽出し、各 1 件ごとの提案を返す. | iso_year*, iso_week*, office_ids | AutoScheduleV2DiffAddResponse |  | **画面から未使用**（hook `useDiffAddProposalsMutation` のみ） |
| 168 | `GET /schedule/v2/feasibility-report` | feasibility.py:25 | admin | 読 |  | ?iso_year&iso_week&office_id&days&fmt&include_html | FeasibilityReportRead |  | 実現性チェック A4（feasibilityReportPath） |
| 169 | `POST /schedule/v2/full-optimize` | schedule_v2.py:871 | admin | 読 | 全患者で再構築した提案を返す（重い）。UI 未使用 | iso_year*, iso_week*, office_ids, pending_edits | AutoScheduleV2FullOptimizeResponse |  | **UI 未使用** |
| 170 | `GET /schedule/v2/improvement-suggestions` | schedule_v2.py:4698 | admin | 読 | 対象患者の各固定枠について、移動を縮められる入れ替え候補を返す read-only API. | ?patient_id&iso_year&iso_week | ImprovementSuggestionsResponse |  | ImprovementSuggestionsSection |
| 171 | `POST /schedule/v2/improvement-suggestions/apply-swap` | schedule_v2.py:4963 | admin | 書 | 2 名の枠を入れ替え（`change_scope`） | patient_a_id*, patient_b_id*, a_new*, b_new*, iso_year*, iso_week*, change_scope, acknowledge_constraint_warnings | ApplySwapResponse | dry_run・制約警告の承認・通知 | ImprovementSuggestionsSection |
| 172 | `POST /schedule/v2/improvement-suggestions/dismiss` | schedule_v2.py:4762 | admin | 書 | 改善提案の却下を記録する. | patient_id*, kind*, target_weekday*, reason*, reason_note, promote_movability | ImprovementDismissResponse |  | DismissReasonDialog |
| 173 | `POST /schedule/v2/pool-bulk-apply` | schedule_v2.py:4115 | admin | 書 | プール一括投入を固定訪問の枠へ登録（1 TX）。simulate の `state_token` 必須（盤面が変わっていたら拒否）。戻る対象 | iso_year*, iso_week*, office_id*, placements, state_token*, acknowledge_constraint_warnings | PoolBulkApplyResponse | dry_run・state_token・制約警告の承認・通知 | BulkPoolInsertDialog |
| 174 | `POST /schedule/v2/pool-bulk-simulate` | schedule_v2.py:3996 | admin | 読 | プール一括投入の read-only プレビュー (設計書 §3-4). | iso_year*, iso_week*, office_id*, patient_ids, ordering | PoolBulkSimulateResponse |  | BulkPoolInsertDialog |
| 175 | `POST /schedule/v2/pool-overview` | schedule_v2.py:3864 | admin | 読 | 保留プールの患者それぞれの「最良候補 1 件」を軽量計算して返す read-only API. | iso_year*, iso_week*, office_id, patient_ids | PoolOverviewResponse |  | PoolOverviewPane |
| 176 | `POST /schedule/v2/propose-slots` | schedule_v2.py:3558 | admin | 読 | 候補患者の希望から、対象週 × 拠点の実スケジュールの実現可能な空き枠を返す. | address, lat, lng, service_minutes*, time_type, preferred_start, preferred_end, preferred_weekdays, visit_frequency… | ProposeSlotsResponse |  | CourseDayTablePanel、PoolCandidateList、SpecialVisitPlaceLauncher、field/FieldSheets |
| 177 | `POST /schedule/v2/propose-unblock` | schedule_v2.py:6144 | admin | 読 | 対象患者 (候補 0 件) を入れるための開通手順を、乱れの小さい順に提案する. | limit | ProposeUnblockResponse |  | PoolCandidateList |
| 178 | `POST /schedule/v2/propose-unblock/apply` | schedule_v2.py:6351 | admin | 書 | 【注意】詰まり解消プランを適用（他患者の退避→対象配置→今週再生成）。`state_token` 必須 | office_id, iso_year*, iso_week*, plan*, state_token*, target_patient_id*, acknowledge_constraint_warnings | ProposeUnblockApplyResponse | dry_run・state_token・制約警告の承認・通知 | PoolCandidateList |
| 179 | `GET /schedule/v2/schedule-health` | schedule_v2.py:3354 | admin | 読 | schedule-advisor §3 Phase 1「診る」: 固定スケジュールの無駄を数字で返す. | ?iso_year&iso_week&office_id | ScheduleHealthResponse |  | ScheduleHealthDialog |
| 180 | `GET /schedule/v2/schedule-health/course-detail` | schedule_v2.py:3430 | admin | 読 | 健康診断→処方箋 (H1): 「なぜこのコースが重いのか」の内訳を返す. | ?iso_year&iso_week&office_id&course_code | ScheduleHealthCourseDetailResponse |  | ScheduleHealthDialog |
| 181 | `GET /schedule/v2/schedule-health/trend` | schedule_v2.py:3391 | admin | 読 | schedule-advisor §3 Phase 3「見直しどき通知」: 週次トレンドの素データを返す. | ?iso_year&iso_week&weeks&office_id | ScheduleHealthTrendResponse |  | ScheduleHealthDialog、ScheduleReviewBanner |
| 182 | `POST /schedule/v2/scope-optimization/apply` | schedule_v2.py:5765 | admin | 書 | 範囲最適化の先頭 N 手を固定訪問の枠へ適用。`state_token` 必須 | iso_year*, iso_week*, scope*, state_token*, steps*, search_scope, change_scope, acknowledge_constraint_warnings | ScopeOptimizationApplyResponse | dry_run・state_token・制約警告の承認・通知 | ScopeOptimizeDialog |
| 183 | `POST /schedule/v2/scope-optimization/simulate` | schedule_v2.py:5370 | admin | 読 | 選択範囲の中で move / swap を貪欲反復で積み上げ、手順列と前後メトリクスを返す. | iso_year*, iso_week*, scope*, search_scope | ScopeOptimizationSimulateResponse |  | ScopeOptimizeDialog |
| 184 | `POST /schedule/v2/travel-estimate` | schedule_v2.py:4358 | admin | 読 | 空き枠直接配置 (案2) 用に from→to の移動時間を推定する read-only API. | from_patient_id, from_lat, from_lng, to_address, to_lat, to_lng | TravelEstimateResponse |  | field/FieldSheets |

## 12. カイポケ連携（差分・送信・取込・イベント・ジョブ・スナップショット・突合）（52 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 185 | `POST /integrations/apply` | integrations.py:1804 | admin | 書（カイポケ） | 【危険】カイポケへ書き込む（RPA）。body `{sheetId, dryRun, itemIds}`・`dryRun` 既定 true。1 件 30〜60 秒。17〜18 時台は失敗しやすい。担当なし(-)行は export に出ない。お客様がカイポケで直した分を上書きする恐れ→必ず差分を見る。ASGI 直叩き不可（裏の処理が止まる・127.0.0.1:18001 経由） | sheet_id*, dry_run, item_ids | JobAccepted | dry_run・スナップショット | cockpit/SyncBar、cockpit/useKaipokeReconcile、integrations/kaipoke |
| 186 | `POST /integrations/apply-inbound` | integrations.py:2687 | admin | 書 | 【注意】差分シートをらく助へ書き写す（RPA 不使用）。`dryRun` 既定 true・`days` | sheet_id*, dry_run, days | InboundApplyResult | dry_run・スナップショット・通知 | cockpit/useKaipokeReconcile |
| 187 | `PATCH /integrations/correction-items/{item_id}` | integrations.py:3074 | admin | 書 |  | include, comment | CorrectionItemRead |  | cockpit/useKaipokeReconcile |
| 188 | `GET /integrations/correction-sheets/latest` | integrations.py:3011 | admin | 読 |  | ?month | CorrectionSheetRead |  | **UI 未使用** |
| 189 | `GET /integrations/correction-sheets/{sheet_id}/items` | integrations.py:3034 | admin | 読 |  | ?type&include&limit&offset | Paginated[CorrectionItemRead] |  | cockpit/useKaipokeReconcile、integrations/kaipoke |
| 190 | `POST /integrations/correction-sheets/{sheet_id}/items/bulk` | integrations.py:3099 | admin | 書 |  | ids*, patch* | dict |  | cockpit/useKaipokeReconcile |
| 191 | `POST /integrations/correction-sheets/{sheet_id}/reverse` | integrations.py:3546 | admin | 書 | 「らく助が正」でカイポケを上書きするシートを作る（送信は apply） |  | ReverseSheetResult |  | cockpit/useKaipokeReconcile |
| 192 | `GET /integrations/credentials` | integrations.py:2132 | admin | 読 |  |  | KaipokeCredentialsRead |  | integrations/kaipoke |
| 193 | `PUT /integrations/credentials` | integrations.py:2154 | admin | 書 | 【危険】カイポケのログイン情報を保存（暗号化） | corp_id*, user_id*, password* | KaipokeCredentialsRead |  | integrations/kaipoke |
| 194 | `POST /integrations/credentials/test` | integrations.py:2185 | admin | RPA読 | RPA で実ログインを 1 回試す（スロット占有） |  | KaipokeLoginTestResult |  | integrations/kaipoke |
| 195 | `POST /integrations/diff` | integrations.py:1249 | admin | RPA読 |  | month*, office_id, week_start, week_end | DiffAccepted |  | **画面から未使用**（hook `useStartDiff` のみ） |
| 196 | `POST /integrations/diff-inbound` | integrations.py:2561 | admin | RPA読 | 提供中の週にカイポケ側で入った直し込みを差分として可視化する (書込なし)。 | month*, office_id, week_start, week_end | DiffAccepted |  | cockpit/useKaipokeReconcile |
| 197 | `POST /integrations/diff-local` | integrations.py:1480 | admin | RPA読 | 送る前の差分: カイポケから export（RPA・約 1〜2 分・月またぎは両月）→ らく助と比べ correction sheet を作る。body `{month, weekStart, weekEnd}`（camelCase） | month*, office_id, week_start, week_end | DiffAccepted |  | integrations/ReconcileReportButton、integrations/kaipoke |
| 198 | `POST /integrations/events-inbound-apply` | integrations.py:4051 | admin | 書 | カイポケのイベント差分を staff_events へ適用。`dryRun` 既定 true | week_start*, dry_run, changes* | EventsInboundApplyResult | dry_run・通知 | ImportEventsFromKaipokeDialog、cockpit/useKaipokeReconcile、integrations/kaipoke |
| 199 | `POST /integrations/events-inbound-preview` | integrations.py:3777 | admin | RPA読 | カイポケ個別業務(イベント)を取得して staff_events との差分計画を返す。 | week_start* | EventsInboundPreviewRead |  | **UI 未使用** |
| 200 | `POST /integrations/events-inbound-preview/start` | integrations.py:3861 | admin | RPA読 | イベント差分プレビューの RPA 取得を非同期で起動する (202 即返し)。 | week_start* | EventsInboundStartRead |  | ImportEventsFromKaipokeDialog、cockpit/useKaipokeReconcile、integrations/kaipoke |
| 201 | `GET /integrations/events-inbound-preview/status/{job_id}` | integrations.py:3928 | admin | 読 | 非同期プレビューの進行確認 (軽量 <1s・Cloudflare 制限とは無縁)。 |  | EventsInboundStatusRead |  | ImportEventsFromKaipokeDialog、cockpit/useKaipokeReconcile、integrations/kaipoke |
| 202 | `POST /integrations/events-outbound-apply/start` | integrations.py:4267 | admin | 書（カイポケ） | 【危険】職員イベントをカイポケへ送る（RPA・非同期 202）→ status をポーリング | week_start*, event_ids | EventsOutboundStartRead |  | SendEventsToKaipokeDialog、cockpit/SyncBar |
| 203 | `GET /integrations/events-outbound-apply/status/{job_id}` | integrations.py:4366 | admin | 読 | 送信の進行確認。完了を最初に観測したポーリングで昇格処理（DB 書込）も行う |  | EventsOutboundStatusRead |  | SendEventsToKaipokeDialog、cockpit/SyncBar |
| 204 | `POST /integrations/events-outbound-preview` | integrations.py:4227 | admin | 読 |  | week_start* | EventsOutboundPreviewRead |  | SendEventsToKaipokeDialog |
| 205 | `POST /integrations/expand` | integrations.py:1147 | admin | 書（カイポケ） | 【危険】カイポケの月展開（RPA・外部書込）。`dryRun` 既定 false に注意。8/31 誤展開事故 | month*, dry_run | JobAccepted | dry_run | integrations/kaipoke |
| 206 | `GET /integrations/expand-status` | integrations.py:1112 | admin | 読 | CareFlow の実行履歴 (KaipokeJob) から、その月が展開済みかを判定する。 | ?month | ExpandStatusRead |  | integrations/kaipoke |
| 207 | `POST /integrations/export` | integrations.py:1202 | admin | RPA読 |  | month*, format | JobAccepted |  | **画面から未使用**（hook `useStartExport` のみ） |
| 208 | `GET /integrations/generated-csv` | integrations.py:932 | admin | 読 | 対象月の確定 visits を18列CSV へ生成して返す (差分適用の最適化CSV側)。 | ?month&office_id | GeneratedCsvRead |  | **UI 未使用** |
| 209 | `GET /integrations/inbound-eligibility` | integrations.py:2455 | admin | 読 | 過去週・今週は無条件で開放 / 未来週は実apply記録が必要 (2026-07-26 PO確定)。 | ?week_start | InboundEligibilityRead |  | integrations/kaipoke |
| 210 | `GET /integrations/inbound-snapshots` | integrations.py:2476 | admin | 読 | 対象週の「取り込み前に戻す」候補 (直近 5 世代・新しい順)。 | ?week_start | InboundSnapshotListRead |  | CourseDayTablePanel、integrations/kaipoke |
| 211 | `POST /integrations/inbound-snapshots/{snapshot_id}/restore` | integrations.py:2521 | admin | 書 | 【危険】「取り込み前/コピー前に戻す」: 週を白紙化してスナップショットを書き戻す。打刻のある週は 422 |  | SnapshotRestoreResultRead | スナップショット | CourseDayTablePanel、integrations/kaipoke |
| 212 | `PATCH /integrations/job-items/{item_id}` | integrations.py:2929 | admin | 書 | ジョブ明細に「手で対応済み」等。UI 未使用 | manually_handled, comment | dict |  | **UI 未使用** |
| 213 | `GET /integrations/jobs` | integrations.py:2900 | admin | 読 |  | ?limit&offset | Paginated[KaipokeJobRead] |  | **画面から未使用**（hook `useIntegrationJobs` のみ） |
| 214 | `GET /integrations/jobs/{job_id}` | integrations.py:2844 | admin | 読 |  |  | KaipokeJobRead |  | **UI 未使用** |
| 215 | `POST /integrations/jobs/{job_id}/stop` | integrations.py:2862 | admin | 書 | RPA ジョブ停止 |  | KaipokeJobRead |  | integrations/kaipoke |
| 216 | `GET /integrations/kaipoke/jobs` | integrations.py:151 | admin | 読 |  | ?week_start&job_status&job_type&limit&offset | Paginated[KaipokeJobRead] |  | integrations、integrations/PlanActualReportCard、integrations/kaipoke |
| 217 | `POST /integrations/kaipoke/jobs` | integrations.py:214 | admin | 書 | ジョブ行を作る（汎用） |  | KaipokeJobRead |  | integrations、integrations/PlanActualReportCard、integrations/kaipoke |
| 218 | `GET /integrations/kaipoke/jobs/{job_id}` | integrations.py:196 | admin | 読 |  |  | KaipokeJobRead |  | integrations/kaipoke/[id] |
| 219 | `POST /integrations/kaipoke/jobs/{job_id}/cancel` | integrations.py:244 | admin | 書 |  |  | KaipokeJobRead |  | integrations/kaipoke/[id] |
| 220 | `GET /integrations/kaipoke/jobs/{job_id}/report` | sync_report.py:34 | admin | 読 | 完了済みジョブ 1 件の結果を A4 縦の報告書にして返す (両方向)。 | ?fmt&include_html&verify | SyncReportRead |  | integrations/SyncReportButton |
| 221 | `GET /integrations/live` | integrations.py:637 | admin | 読 |  | ?tail | LiveSnapshotRead |  | cockpit/useKaipokeReconcile、integrations/PlanActualReportCard、integrations/kaipoke |
| 222 | `POST /integrations/master-reconcile` | integrations.py:1690 | admin | RPA読 | カイポケ現況 CSV（RPA export）で名簿突合 | month* | MasterReconcileRead | スナップショット | cockpit/useKaipokeReconcile |
| 223 | `GET /integrations/monitor-url` | integrations.py:726 | admin | 読 |  |  | dict |  | **UI 未使用** |
| 224 | `POST /integrations/plan-actual-compare` | integrations.py:1599 | admin | RPA読 | 予定・実績を RPA export して保存（非同期） | month* | JobAccepted | スナップショット | integrations/PlanActualReportCard |
| 225 | `GET /integrations/plan-actual-report` | integrations.py:871 | admin | 読 | 保存済みの 予定CSV / 実績CSV スナップショット 2 本から突合レポートを組む。 | ?month&fmt |  |  | **UI 未使用** |
| 226 | `POST /integrations/reconcile-jobs` | integrations.py:737 | admin | 書 | 未決着ジョブを completed/failed に確定（5 分おき cron が叩く）。UI 未使用 |  | dict |  | **UI 未使用** |
| 227 | `GET /integrations/reconcile-report` | integrations.py:778 | admin | 読 | 差分確認/突き合わせ用の見やすい HTML (PO 要望 2026-09-01)。 | ?week_start&days&fmt&include_html | ReconcileReportRead |  | integrations/ReconcileReportButton |
| 228 | `POST /integrations/replace-inbound` | integrations.py:4441 | admin | 書＋RPA読 | 【危険】置換取込: 週のらく助予定を白紙化しカイポケ現況で全置換。`dryRun` 既定 true・打刻 1 件でもあれば 422・スナップショットあり。青ピンを守らない（B-3）。画面からの直接呼び出しは未検出 | week_start*, dry_run | ReplaceInboundResult | dry_run・スナップショット・通知 | **画面から未使用**（hook `useReplaceInbound` のみ） |
| 229 | `POST /integrations/smart-inbound-apply` | integrations.py:5424 | admin | 書＋RPA読 | 【危険】取込の本体: 打刻あり日=差分適用・なし日=置換を 1 TX。`dryRun` 既定 true。実行時に再取得。スナップショット（5 世代）。置換部分は青ピンを守らない（B-3） | week_start*, sheet_id, dry_run | SmartInboundApplyResult | dry_run・スナップショット・通知 | integrations/kaipoke |
| 230 | `POST /integrations/smart-inbound-preview` | integrations.py:4921 | admin | RPA読 | 打刻あり日=差分シート作成・なし日=置換dry-run、を1回のexportで実行する。 | week_start* | SmartInboundPreviewRead |  | **UI 未使用** |
| 231 | `GET /integrations/smart-inbound-preview/active` | integrations.py:5340 | admin | 読 | 画面を離れて戻ってきたときに使う。 | ?week_start | SmartInboundPreviewStatusRead \| None |  | integrations/kaipoke |
| 232 | `POST /integrations/smart-inbound-preview/start` | integrations.py:5122 | admin | RPA読 | 統合プレビューのジョブを立ててすぐ 202 を返し、実体はバックグラウンドで走らせる。 | week_start* | JobAccepted |  | cockpit/useKaipokeReconcile、integrations/kaipoke |
| 233 | `GET /integrations/smart-inbound-preview/status/{job_id}` | integrations.py:5274 | admin | 読 | ポーリング用 (RPA には触らない・軽量)。completed なら preview を返す。 |  | SmartInboundPreviewStatusRead |  | cockpit/useKaipokeReconcile、integrations/kaipoke |
| 234 | `GET /integrations/status` | integrations.py:379 | admin | 読 |  |  | KaipokeStatusRead |  | **画面から未使用**（hook `useKaipokeStatus` のみ） |
| 235 | `POST /integrations/unsent-summary` | integrations.py:3217 | admin | 読 | 未送信の変更を即時に返す（RPA 不使用） | week_start* | UnsentSummaryRead | スナップショット・通知 | cockpit/SyncBar |
| 236 | `GET /integrations/week-schedule` | integrations.py:959 | admin | 読 | 対象週の確定 visits を週ビュー用の構造化データで返す (read-only)。 | ?week_start&week_end&office_id | WeekScheduleRead |  | integrations/kaipoke |

## 13. 打刻・QR・訪問モニター・打刻履歴・音声記録・サイン・実績時刻（31 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 237 | `GET /checkin-settings` | checkin_settings.py:66 | admin/staff | 読 |  |  | CheckinSettingsRead |  | settings/checkin |
| 238 | `PUT /checkin-settings` | checkin_settings.py:106 | admin | 書 | 【注意】打刻しきい値（全社一律） | match_m, review_m, accuracy_m, no_show_grace_min, late_min, max_inprogress_min, arrival_max_back_min, departure_max_ahead_min, staff_adjust_window_days… | CheckinSettingsRead | 監査ログ | settings/checkin |
| 239 | `GET /checkin-settings/public` | checkin_settings.py:80 | ログイン | 読 | staff を含む全ログインユーザが読める距離系しきい値 (match/review/accuracy). |  | CheckinSettingsPublic |  | app/q/[token]、m/today/[visitId]、records |
| 240 | `GET /monitor` | visit_monitor.py:30 | admin/staff | 読 | 指定日の訪問モニター集計を返す (DB 書込なし). | ?date&office_id | MonitorResponse |  | monitor |
| 241 | `GET /monitor/nearby` | visit_monitor.py:51 | admin/staff | 読 | 指定座標付近の active 患者 (lat/lng 有) を距離順に返す. | ?lat&lng&radius_m&limit | NearbyResponse |  | monitor |
| 242 | `GET /visit-history` | visit_history.py:215 | admin/staff | 読 |  | ?query&limit&offset | VisitHistoryList |  | records |
| 243 | `GET /visit-history/export` | visit_history.py:236 | admin/staff | 読 |  |  |  |  | records |
| 244 | `GET /visit-history/report` | visit_history.py:255 | admin/staff | 読 |  | ?query&group&include_none&page_break |  |  | records |
| 245 | `GET /visit-recordings` | visit_recordings.py:584 | ログイン | 読 |  | ?patient_id&staff_id&visit_id&office_id&from_&to&status_filter | VisitRecordingList |  | m/this-week、m/today、m/today/[visitId]、mobile/VoiceRecorderPanel 他3 |
| 246 | `POST /visit-recordings` | visit_recordings.py:366 | ログイン | 書 |  | ?visit_id&patient_id&device_mime&client_id | VisitRecordingRead |  | m/this-week、m/today、m/today/[visitId]、mobile/VoiceRecorderPanel 他3 |
| 247 | `DELETE /visit-recordings/{recording_id}` | visit_recordings.py:1078 | admin | 書 | 【危険】音声記録を削除 |  |  |  | m/record/new、m/today、mobile/VisitRecordCard、records/RecordDetailDialog |
| 248 | `GET /visit-recordings/{recording_id}` | visit_recordings.py:709 | ログイン | 読 |  |  | VisitRecordingRead |  | m/record/new、m/today、mobile/VisitRecordCard、records/RecordDetailDialog |
| 249 | `PATCH /visit-recordings/{recording_id}` | visit_recordings.py:913 | ログイン | 書 |  | patient_id, visit_id, reviewed, note_append, summary_text | VisitRecordingRead |  | m/record/new、m/today、mobile/VisitRecordCard、records/RecordDetailDialog |
| 250 | `GET /visit-recordings/{recording_id}/audio` | visit_recordings.py:766 | ログイン | 読 |  |  |  |  | mobile/AuthedAudio、records/AuthedAudioPlayer |
| 251 | `GET /visit-recordings/{recording_id}/report` | visit_recordings.py:804 | ログイン | 読 | 音声記録 1 件を A4 縦の訪問記録にして返す (read-only・設計 §11-3)。 | ?fmt | VisitRecordingReportRead |  | records/RecordReportButton |
| 252 | `POST /visit-recordings/{recording_id}/retry` | visit_recordings.py:1042 | admin | 書 |  |  | VisitRecordingRead |  | records/RecordDetailDialog |
| 253 | `GET /visit-signatures/{signature_id}/image` | visit_signatures.py:80 | ログイン | 読 |  |  |  |  | records/SignatureViewer |
| 254 | `POST /visits/adhoc-checkin` | visits.py:1796 | ログイン | 書 | 【注意】予定外 QR 打刻（予定作成＋到着＋通知） | qr_token, lat, lng, accuracy, device_time, reason, is_override, adjusted_time, adjust_reason_code | VisitRead | 通知・監査ログ | モバイル打刻キュー（lib/checkin-flush.ts） |
| 255 | `GET /visits/resolve-qr/{token}` | visits.py:691 | ログイン | 読 | `/q/{token}` ランディングページの遷移先解決 (v2 = 凍結コントラクト 2026-08-16)。 |  | QrResolveRead |  | app/q/[token] |
| 256 | `DELETE /visits/{visit_id}/actual-time` | visits.py:2121 | admin/staff | 書 | 合わせた時刻を取り消して読取時刻に戻す（冪等） | ?kind&x_client_surface | VisitRead | 監査ログ | m/today/[visitId]、records、records/ActualTimeAdjustBox |
| 257 | `PUT /visits/{visit_id}/actual-time` | visits.py:2076 | admin/staff | 書 | 実績時刻を「合わせる」（追記型・監査）。`kind`/`time`/`reason_code` | kind, time, reason_code, reason_text | VisitRead | 監査ログ | m/today/[visitId]、records、records/ActualTimeAdjustBox |
| 258 | `POST /visits/{visit_id}/checkin` | visits.py:1440 | ログイン | 書 | 【注意】到着打刻（職員本人用・QR/GPS）。管理者の代行は実績の偽装になるので対象外 | qr_token, lat, lng, accuracy, device_time, reason, is_override, adjusted_time, adjust_reason_code | VisitRead | 通知・監査ログ | m/today/[visitId] |
| 259 | `POST /visits/{visit_id}/checkout` | visits.py:1488 | ログイン | 書 | 【注意】退出打刻（同上） | qr_token, lat, lng, accuracy, device_time, reason, is_override, adjusted_time, adjust_reason_code | VisitRead | 通知・監査ログ | m/today/[visitId] |
| 260 | `POST /visits/{visit_id}/checkout-signature` | visits.py:1563 | ログイン | 書 | 利用者さんのサインをもらって退出を記録する (QR の退出と同じ扱い)。 | ?lat&lng&accuracy&at&reason&is_override&client_id | VisitRead | 通知 | モバイル サイン退出（lib/signature-checkout.ts） |
| 261 | `POST /visits/{visit_id}/no-show` | visits.py:1936 | ログイン | 書 | 【注意】不在記録（同上） | qr_token, lat, lng, accuracy, device_time, reason, is_override, adjusted_time, adjust_reason_code | VisitRead |  | m/today/[visitId] |
| 262 | `GET /visits/{visit_id}/photos` | visit_photos.py:101 | ログイン | 読 |  |  | list[VisitPhotoRead] |  | m/today/[visitId] |
| 263 | `POST /visits/{visit_id}/photos` | visit_photos.py:124 | ログイン | 書 |  | ?caption | VisitPhotoRead |  | m/today/[visitId] |
| 264 | `DELETE /visits/{visit_id}/photos/{photo_id}` | visit_photos.py:213 | admin | 書 |  |  |  |  | **画面から未使用**（hook `useDeletePhoto` のみ） |
| 265 | `GET /visits/{visit_id}/photos/{photo_id}/download` | visit_photos.py:241 | ログイン | 読 |  |  |  |  | AuthedPhoto（応答の URL を使用） |
| 266 | `DELETE /visits/{visit_id}/review` | visit_review.py:112 | admin | 書 | 確認済みマークを削除する (要対応トレイに復活). |  |  |  | monitor |
| 267 | `POST /visits/{visit_id}/review` | visit_review.py:54 | admin | 書 | 確認済みマーク（モニターの要対応から外す） | comment | VisitReviewResponse |  | monitor |

## 14. ダッシュボード（3 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 268 | `GET /dashboard/kpi` | dashboard.py:123 | ログイン | 読 |  |  | DashboardKpiResponse |  | dashboard |
| 269 | `GET /dashboard/staff-performance` | dashboard.py:256 | admin | 読 | 期間 (既定 = 今週) のスタッフ別の実績 (設計 dashboard-staff-performance §3 / §7)。 | ?date_from&date_to&office_id | StaffPerformanceResponse |  | dashboard/performance/StaffPerformanceSection |
| 270 | `GET /dashboard/trend` | dashboard.py:198 | ログイン | 読 |  | ?days | DashboardTrendResponse |  | dashboard、dashboard/performance/StaffPerformanceSection |

## 15. 管理（ユーザー・設定・事業所・市区町村・機能フラグ・通知・監査・地図）（39 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 271 | `POST /admin/checkin/check-missing` | admin_checkin.py:49 | admin | 書 | 未訪問を通知（cron 用）。UI 未使用 |  | CheckMissingResponse |  | **UI 未使用** |
| 272 | `POST /admin/checkin/purge-gps` | admin_checkin.py:76 | admin | 書 | 【危険】2 年超の GPS を NULL 化（cron 用・不可逆） |  | PurgeGpsResponse |  | **UI 未使用** |
| 273 | `POST /admin/geocoding/audit` | admin_geocoding.py:66 | admin | 書 | 【注意】全患者の座標を再ジオコーディングし 100m 超を補正。`dry_run` | dry_run, limit_patients | GeocodingAuditResponse | dry_run・監査ログ | **UI 未使用** |
| 274 | `GET /admin/users` | admin.py:107 | admin | 読 |  | ?role&q&include_deleted&limit&offset | Paginated[AdminUserRead] |  | admin/users |
| 275 | `POST /admin/users` | admin.py:155 | admin | 書 | アカウント作成（応答に初期パスワード） | email, username, role, staff_id | AdminUserCreateResponse |  | admin/users |
| 276 | `DELETE /admin/users/{user_id}` | admin.py:287 | admin | 書 | 【危険】ユーザーを論理削除（ログイン不可になる） |  |  |  | admin/users |
| 277 | `PATCH /admin/users/{user_id}` | admin.py:207 | admin | 書 | 【注意】email/username/role/staff_id/must_change_password を変更。role を変えると権限が変わる | email, username, role, staff_id, must_change_password | AdminUserRead |  | admin/users |
| 278 | `POST /admin/users/{user_id}/reset-password` | admin.py:265 | admin | 書 | 【危険】一時パスワードを発行し `must_change_password=true`・ロック解除。応答に `temp_password`。統一パスワードが崩れる（メモリ careflow-unified-password） |  | AdminPasswordResetResponse |  | admin/users |
| 279 | `POST /admin/visit-recordings/purge-audio` | admin_visit_recordings.py:128 | admin | 書 | 【危険】保持期限超の音声削除（cron 用・不可逆） |  | PurgeAudioResponse |  | **UI 未使用** |
| 280 | `GET /admin/visit-recordings/usage` | admin_visit_recordings.py:205 | admin | 読 | 1 か月ぶんの利用状況を集計して返す (設計 §11-3 費用ダッシュボード). | ?month | VisitRecordingUsage |  | records/RecordReportButton、records/VoiceUsageCard |
| 281 | `POST /admin/visit-signatures/purge-images` | admin_visit_signatures.py:96 | admin | 書 | 【危険】サイン画像のパージ（cron 用・不可逆） |  | PurgeSignatureImagesResponse |  | **UI 未使用** |
| 282 | `GET /audit-logs` | audit_logs.py:28 | admin | 読 |  | ?since&until&user_id&role&path&method&status_code | Paginated[AuditLogRead] |  | admin/audit-logs |
| 283 | `GET /business-profile` | business_profile.py:42 | ログイン | 読 |  |  | BusinessProfileRead |  | patients/qr-print、settings/business |
| 284 | `PUT /business-profile` | business_profile.py:47 | admin | 書 | 事業所情報（QR カードの名称・電話・ロゴ） | station_name, contact_tel, contact_hours, contact_days, logo_url | BusinessProfileRead |  | patients/qr-print、settings/business |
| 285 | `GET /cities` | cities.py:42 | admin/staff | 読 |  | ?q&prefecture&limit&offset | list[CityRead] |  | offices、offices/[id] |
| 286 | `POST /cities` | cities.py:79 | admin | 書 |  |  | CityRead |  | offices、offices/[id] |
| 287 | `DELETE /cities/{city_id}` | cities.py:115 | admin | 書 | 【注意】市区町村削除。UI 未使用 |  |  |  | **UI 未使用** |
| 288 | `GET /cities/{city_id}` | cities.py:67 | admin/staff | 読 |  |  | CityRead |  | **UI 未使用** |
| 289 | `PATCH /cities/{city_id}` | cities.py:97 | admin | 書 |  | prefecture, name, jis_code | CityRead |  | **UI 未使用** |
| 290 | `POST /geocode` | geocoding.py:49 | admin/staff | 読 | Cache-first geocode lookup. | address*, force_refresh | GeocodeResponse |  | field/FieldSheets |
| 291 | `GET /geocoding/cache` | geocoding.py:160 | admin | 読 |  | ?q&limit&offset | Paginated[GeocodingCacheRead] |  | **UI 未使用** |
| 292 | `GET /integrations/geocoding/cache` | integrations.py:294 | admin | 読 |  | ?q&limit&offset | Paginated[GeocodingCacheRead] |  | integrations |
| 293 | `GET /notifications` | notifications.py:36 | ログイン | 読 |  | ?unread_only&limit&offset | Paginated[NotificationRead] | 通知 | Header、m |
| 294 | `POST /notifications/admin-create` | notifications.py:120 | admin | 書 | 任意ユーザーへ通知を作る。UI 未使用 | user_id*, type*, title*, body | NotificationRead | 通知 | **UI 未使用** |
| 295 | `POST /notifications/mark-all-read` | notifications.py:96 | ログイン | 書 |  |  | NotificationMarkAllReadResponse | 通知 | Header、m |
| 296 | `PATCH /notifications/{notification_id}/read` | notifications.py:71 | ログイン | 書 |  |  | NotificationRead | 通知 | Header、m |
| 297 | `GET /office-feature-flags` | office_feature_flags.py:32 | admin | 読 |  | ?feature_key | list[OfficeFeatureFlagRead] |  | admin/feature-flags |
| 298 | `POST /office-feature-flags` | office_feature_flags.py:51 | admin | 書 | 【注意】拠点の機能フラグ upsert | office_id*, feature_key*, enabled*, note | OfficeFeatureFlagRead | 監査ログ | admin/feature-flags |
| 299 | `DELETE /office-feature-flags/{office_id}/{feature_key}` | office_feature_flags.py:136 | admin | 書 | flag 行を物理 DELETE する. 行が無くても 204 (冪等). |  |  |  | admin/feature-flags |
| 300 | `GET /offices` | offices.py:275 | admin/staff | 読 |  | ?q&limit&offset | list[OfficeRead] |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、acceptance、admin/feature-flags 他13 |
| 301 | `POST /offices` | offices.py:322 | admin | 書 |  | allowed_cities | OfficeRead |  | CourseDayTablePanel、SpecialVisitPlaceLauncher、acceptance、admin/feature-flags 他13 |
| 302 | `POST /offices/area-prompt-dismissals` | offices.py:162 | admin | 書 | 未カバー地域の担当エリア登録呼びかけを組織として却下し、記憶する (W-7)。 | city_id* |  |  | patients |
| 303 | `POST /offices/resolve` | offices.py:81 | admin | 読 | 住所文字列から該当する拠点を自動判定する。 | address* | OfficeResolveResponse |  | field/FieldSheets、patients |
| 304 | `DELETE /offices/{office_id}` | offices.py:388 | admin | 書 | 【危険】拠点削除 |  |  |  | field/FieldSheets、offices/[id]、offices/[id]/edit、patients |
| 305 | `GET /offices/{office_id}` | offices.py:299 | admin/staff | 読 |  |  | OfficeRead |  | field/FieldSheets、offices/[id]、offices/[id]/edit、patients |
| 306 | `PATCH /offices/{office_id}` | offices.py:350 | admin | 書 |  | name, code, prefecture, address, lat, lng, note, sort_order, short_label… | OfficeRead |  | field/FieldSheets、offices/[id]、offices/[id]/edit、patients |
| 307 | `POST /offices/{office_id}/area-cities` | offices.py:193 | admin | 書 | 拠点の担当エリアに City を 1 件追加する (additive / 冪等) (W-7)。 | city_id* | AreaCityAddResponse |  | patients |
| 308 | `GET /scheduling-settings` | scheduling_settings.py:74 | admin/staff | 読 |  |  | SchedulingSettingsRead |  | CourseDayTablePanel、WeekdayScheduleCard、field/FieldBoard、settings/scheduling 他1 |
| 309 | `PUT /scheduling-settings` | scheduling_settings.py:89 | admin | 書 | 【注意】配置の全体設定（バッファ・速度・昼休み・営業時間・コース上限）。全提案に効く | visit_buffer_min, travel_speed_kmh, lunch_duration_min, lunch_window_start, lunch_window_end, business_start, business_end, max_patients_per_course | SchedulingSettingsRead | 監査ログ | CourseDayTablePanel、WeekdayScheduleCard、field/FieldBoard、settings/scheduling 他1 |

## 16. その他（ヘルスチェック）（2 本）

| # | メソッド パス | 実装 | 権限 | 種別 | 何をする／何が変わる | 主な入力 | 応答 | 安全装置 | UI |
|---|---|---|---|---|---|---|---|---|---|
| 310 | `GET /healthz` | health.py:17 | なし | 読 | Always return 200 if the process is running. |  |  |  | Cloudflare Access 切れ検知（lib/cfAccess.ts） |
| 311 | `GET /readyz` | health.py:23 | なし | 読 | Return 200 + status=ready when DB is reachable, else 503. |  |  |  | **UI 未使用** |

## 17. 画面から呼ばれていない API（57 本）

旧 API（第 1 世代の自動配置・Layer1〜3 直叩き）、cron 用（admin/checkin・purge・reconcile-jobs）、裏側/内部用（replace-inbound・smart-inbound-preview 同期版・diff/export 単体）、未接続の管理系（cities 個別・acceptance-calendar・notifications/admin-create）。**エージェントは原則使わない**（PO の約束「画面と同じ API」）。

- `GET /acceptance-calendar` — acceptance_calendar.py:56（UI 未使用）
- `PUT /acceptance-calendar` — acceptance_calendar.py:80（UI 未使用）
- `POST /admin/checkin/check-missing` — admin_checkin.py:49（UI 未使用）
- `POST /admin/checkin/purge-gps` — admin_checkin.py:76（UI 未使用）
- `POST /admin/geocoding/audit` — admin_geocoding.py:66（UI 未使用）
- `POST /admin/visit-recordings/purge-audio` — admin_visit_recordings.py:128（UI 未使用）
- `POST /admin/visit-signatures/purge-images` — admin_visit_signatures.py:96（UI 未使用）
- `POST /allocate/run` — allocate.py:256（画面から未使用（hook `useRunAllocate` のみ））
- `POST /auth/logout` — auth.py:184（UI 未使用）
- `DELETE /cities/{city_id}` — cities.py:115（UI 未使用）
- `GET /cities/{city_id}` — cities.py:67（UI 未使用）
- `PATCH /cities/{city_id}` — cities.py:97（UI 未使用）
- `DELETE /course-templates/{template_id}` — course_templates.py:173（画面から未使用（hook `useDeleteCourseTemplate` のみ））
- `PATCH /course-templates/{template_id}` — course_templates.py:150（画面から未使用（hook `useDeleteCourseTemplate` のみ））
- `POST /courses/assign-staff` — courses.py:226（画面から未使用（hook `useAssignStaff` のみ））
- `POST /courses/generate` — courses.py:157（画面から未使用（hook `useGenerateCourseProposals` のみ））
- `POST /diff/compute` — diff.py:25（UI 未使用）
- `GET /geocoding/cache` — geocoding.py:160（UI 未使用）
- `GET /integrations/correction-sheets/latest` — integrations.py:3011（UI 未使用）
- `POST /integrations/diff` — integrations.py:1249（画面から未使用（hook `useStartDiff` のみ））
- `POST /integrations/events-inbound-preview` — integrations.py:3777（UI 未使用）
- `POST /integrations/export` — integrations.py:1202（画面から未使用（hook `useStartExport` のみ））
- `GET /integrations/generated-csv` — integrations.py:932（UI 未使用）
- `PATCH /integrations/job-items/{item_id}` — integrations.py:2929（UI 未使用）
- `GET /integrations/jobs` — integrations.py:2900（画面から未使用（hook `useIntegrationJobs` のみ））
- `GET /integrations/jobs/{job_id}` — integrations.py:2844（UI 未使用）
- `GET /integrations/monitor-url` — integrations.py:726（UI 未使用）
- `GET /integrations/plan-actual-report` — integrations.py:871（UI 未使用）
- `POST /integrations/reconcile-jobs` — integrations.py:737（UI 未使用）
- `POST /integrations/replace-inbound` — integrations.py:4441（画面から未使用（hook `useReplaceInbound` のみ））
- `POST /integrations/smart-inbound-preview` — integrations.py:4921（UI 未使用）
- `GET /integrations/status` — integrations.py:379（画面から未使用（hook `useKaipokeStatus` のみ））
- `POST /notifications/admin-create` — notifications.py:120（UI 未使用）
- `POST /patients/bulk-apply-week-only-visit-changes` — patient_sync.py:787（UI 未使用）
- `POST /patients/{patient_id}/sync-week-visits-to-fixed` — patient_sync.py:344（UI 未使用）
- `POST /pending-requests/create-and-apply` — pending_requests.py:477（UI 未使用）
- `PATCH /pending-requests/{request_id}/approve-with-edit` — pending_requests.py:775（画面から未使用（hook `useApproveWithEdit` のみ））
- `GET /readyz` — health.py:23（UI 未使用）
- `POST /schedule/auto-allocate` — schedule.py:2968（UI 未使用）
- `POST /schedule/fix` — schedule.py:207（UI 未使用）
- `POST /schedule/fix-or-pattern` — schedule.py:327（UI 未使用）
- `POST /schedule/generate-and-assign` — schedule.py:1209（UI 未使用）
- `POST /schedule/generate-week` — schedule.py:245（UI 未使用）
- `POST /schedule/proposal/{proposal_batch_id}/apply` — schedule.py:2997（UI 未使用）
- `POST /schedule/proposal/{proposal_batch_id}/discard` — schedule.py:3020（UI 未使用）
- `POST /schedule/v2/apply-individual` — schedule_v2.py:955（画面から未使用（hook `useApplyIndividualMutation` のみ））
- `POST /schedule/v2/apply-week-only` — schedule_v2.py:2589（UI 未使用）
- `POST /schedule/v2/diff-add` — schedule_v2.py:756（画面から未使用（hook `useDiffAddProposalsMutation` のみ））
- `POST /schedule/v2/full-optimize` — schedule_v2.py:871（UI 未使用）
- `GET /schedule/v2/pfv-course-presence` — schedule_v2.py:3294（画面から未使用（hook `usePfvCoursePresence` のみ））
- `POST /schedule/v2/sync-fixed-to-week` — schedule_v2.py:1362（UI 未使用）
- `POST /schedule/v2/update-fixed-time-week-only` — schedule_v2.py:3039（画面から未使用（hook `useUpdateFixedTimeWeekOnlyMutation` のみ））
- `GET /schedule/v2/weekday-staff-capacity` — schedule_v2.py:3217（画面から未使用（hook `useWeekdayStaffCapacity` のみ））
- `PATCH /shift-requests/{request_id}/status` — shift_requests.py:105（画面から未使用（hook `useUpdateShiftRequestStatus` のみ））
- `DELETE /visits/{visit_id}/photos/{photo_id}` — visit_photos.py:213（画面から未使用（hook `useDeletePhoto` のみ））
- `POST /visits/{visit_id}/staff` — visits.py:1207（UI 未使用）
- `DELETE /visits/{visit_id}/staff/{staff_id}` — visits.py:1259（UI 未使用）

## 付録. 照合の限界

- UI 照合はパス文字列の静的照合。ヘルパー関数でパスを組み立てる箇所（`staff-events.ts` の `base(staffId)`、`integrations.ts` の `useRelayMutation(name)`）は手で補正したが、取りこぼしはありうる。「hook のみ」は lib に hook はあるが画面の import を検出できなかったもの（間接利用の可能性あり）。
- 「何が変わる」はドキュメント文字列・本文のキーワード（commit/notify/snapshot/op_log 等）と主要エンドポイントの読解による。副作用の網羅は保証しない。書込前は必ず dry_run/preview を使う。
- 権限は `require_role(...)` と `CurrentActiveUser` の静的検出。「ログイン」の書込系（`POST /visit-recordings`・`PATCH /visit-recordings/{id}`・`POST /staff/{id}/shift-requests`・`DELETE /pending-requests/{id}`・打刻系）は本文で本人/担当チェックをしている可能性がある（未精査）。
- 応答型は `response_model` の名前のみ。中身は `backend/app/schemas/` 参照。
