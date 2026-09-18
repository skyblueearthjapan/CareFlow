# セッション引き継ぎ 2026-09-16〜18（スマホ盤不一致の根治 → W38〜W40 カイポケ正 → 音声記録 Phase 1〜3 → QR カード → 実績時刻）

**次のエージェントへ**: まずこのファイル。詳細な経緯は `session-2026-09-16-HANDOFF.md`（§1〜§11・時系列の追補）。カイポケ連携の本線は `session-2026-09-11-HANDOFF.md`。本ファイルは 3 日分の総括と、次に着手すべきことの一覧。

## ★ 最初の 3 分

| 項目 | 値 |
|---|---|
| 本番 らく助 HEAD | `40a1070`（docs は `d9b57d3`）・2026-09-18 10:50 JST |
| alembic | `0087_visit_recordings_summary_edit`（単一 head） |
| RPA PlaywrightTest1 | `0e00107`（本セッション変更なし） |
| 最新バックアップ | `/opt/carelink/backups/pre-deploy-actualtime-20260918-0145.sql.gz` |
| 作業ツリー | クリーン（未追跡: `docs/HANDOFF.md`・`docs/manuals/`・`docs/mockups/renkei-layout-wireframe.html` は前セッション以前のもの・未確認） |
| **本番に残っているテストデータ** | S009（稼働中・稲毛・看護師）＋架空患者 8 名（P120〜P127・【検証】付き）＋ 9/18 の S009 訪問 6 件 → **音声テスト終了後に後片付け必須**（§5-1） |

本番稼働した機能（すべて origin/develop に push 済み・レビュー APPROVE・テストはベースライン同等）:
1. **スマホ盤の職員スケジュール不一致の根治**（9/16 `58ca484`）: 取込 422 の置換ガード限定・可視性のコース担当フォールバック・スマホにイベント/休み合成。
2. **W38・W39・W40 をカイポケ正で取込**（9/16〜17）: 取込後 137/137・145/145・138/138 一致。ダメ出し是正 `36b2b44`（同行 source=import・イベント absorb・mig 0084/0085）。
3. **訪問の音声記録 Phase 1〜3**（9/18 `c7bec58` → `640003f` → `818a8bd`）: Vertex AI 東京 gemini-2.5-flash・録音→文字起こし→要約・QR なし導線・PC `/records`・A4 出力・利用状況カード・purge cron。
4. **患者 QR 印刷を A5 カード化**（9/18 `05a5250`）: よりより の連絡先＋公式 SVG ロゴ。
5. **QR 打刻の実時刻を予定と並記**（9/18 `40a1070`）: モバイル一覧/詳細・PC モニター。

## 1. 正典（設計・調査・運用）
- スマホ盤不一致: `docs/plans/mobile-staff-schedule-mismatch-investigation-2026-09-16.md`・`mobile-staff-schedule-design-2026-09-16.md`
- 音声記録: `docs/plans/visit-voice-record-design-2026-09-17.md`（§8 PO 確認事項 1〜11・§10 Phase 1 契約・§11 Phase 2/3 契約・§11-5 Phase 3 決定）・モック `docs/mockups/visit-voice-record-mock.html`・runbook `docs/runbook/voice_recording_cron.md`
- QR カード: モック `docs/mockups/qr-checkin/qr-print.html`（ピクセル正典・SVG 同梱）・プレビュー `docs/reports/2026-09-18-qr-card-preview.{pdf,png}`（gitignore）
- カイポケ突合レポート（gitignore・ローカルのみ）: `docs/reports/2026-09-16-w38-*.html`・`2026-09-17-w39-*`・`2026-09-17-w40-*`
- 音声テストデータの道具: `docs/tools/voice-test/{setup_voice_test.py, cleanup_voice_test.py, voice_test_manifest.json}`
- memory: `careflow-mobile-staff-schedule-mismatch`・`careflow-visit-voice-record`・`careflow-qr-card-actual-time`

## 2. 本セッションのコミット（時系列・本番反映順）
| 日時 | コミット | 内容 |
|---|---|---|
| 9/16 | `18fa5f8` `da68223` `043fb0e` `58ca484` | スマホ盤不一致の根治 3 本＋docs |
| 9/17 | `35301cb` `a7de187` `9d91c85` `36b2b44` | 同行 source=import（mig 0084）・イベント absorb・.gitignore・mig 0085（旧 CHECK 除去） |
| 9/18 | `c2dae44` `6c322ac` `e064d95` `c7bec58` | 音声 Phase 1（mig 0086）＋ requests 依存修正 |
| 9/18 | `09eceea` `351486e` `640003f` | 音声 Phase 2（mig 0087） |
| 9/18 | `5c3a510` `1eaa411` `818a8bd` `22506ae` `73147e2` `abae0b4` | 音声 Phase 3（A4・usage・no-store）＋docs |
| 9/18 | `05a5250` | QR 印刷 A5 カード化 |
| 9/18 | `e140160` `40a1070` `d9b57d3` | 実績時刻の並記（BE/FE）＋docs |

## 3. 残タスク（優先順）

### 3-1. すぐ（今日〜今週）
1. **音声テストの後片付け**（今泉さんのテスト完了後）: `docs/tools/voice-test/cleanup_voice_test.py` を本番コンテナで `DRY=1` → 本実行（録音＋音声ファイル → 訪問 6 件 → 架空患者 8 名 → S009 を retired に戻す）。手順は §5-1。**テスト中はカイポケ送信と週生成を押さない**（架空患者が未送信として出る）。
2. **音声記録の実機確認**（累積・未実施）: iPhone/Android で録音→停止→要約／録音中に QR 読取画面を開いても止まらない／画面ロック復帰／圏外→復帰の自動再送／`/m/record/new` → 患者選択 → 訪問詳細へ／PC `/records` の再生（1.5×・位置保持）／要約編集→モバイル追随／「A4 で出力」が 1 枚に収まる／利用状況カードに当月の件数と費用。
3. **QR カードの実機確認**: 実プリンタで A4 → 切り取り線で A5 2 枚（余白 9mm が印字可能領域に収まるか）／パウチ越しに iPhone で読める／一括で末尾白紙なし。稼働中タブに【検証】患者が混ざっている間はチェックを外す。
4. **実績時刻の現場案内**: スマホは PWA を開き直し、PC は Ctrl+Shift+R。打刻の無い訪問は従来どおり予定のみ。

### 3-2. PO / お客様の判断待ち（データ変更なし・実装は判断後）
5. **実績時刻の次段**（川名様・松岡様「請求時に実時間で加算算定」）: ②日別スタッフ別の実績リスト（画面＋A4）→ ③カイポケ実績への自動反映（RPA は予定画面しか触っていない・請求直結のため検証を厚く）。確認事項: 加算に使う時刻は QR 打刻で確定してよいか／打刻忘れ・遅れの扱いと修正権限。
6. **dedupe `--apply`**（手入力朝会×取込朝会の二重 41 件・`docs/tools/kaipoke-ops/dedupe_manual_events.py`）: お客様 OK 後に実行（JSON 退避付き）。
7. **資格不整合**: W38 13 件＋W39 25 件（高岡=准看護師 なのにカイポケ「正看」）。らく助から grade_change を送れば直るが **grade_change の初回実機送信が未実施**（9/11 総括 §4-1）。9 月レセプト前に要対処。
8. **非稼働 7 名のカイポケ週間パターン停止**（石塚・藤田守・渡辺・朝倉・小川・瀧本・清水政憲）: 事務がカイポケ側で止めないと毎週「カイポケのみ」に残る。
9. **都賀のコース枠**（A 1 枠＋M）: 2〜4 人動く日は必ず「臨」。枠数＝その日その拠点の稼働看護師数、容量＝1 人が回れる患者数、という設計案を PO 相談中。
10. **9/21 新人 小西さんが職員1・高岡さんが職員2 の 4 件**: 取込は職員1=主担当として忠実に反映。意図どおりか確認。
11. **音声記録の運用（設計 §8）**: 録音同意の運用・保持期間（現 90 日）・閲覧範囲（拠点異動時の office_id 第 2 レグ §8-11）・要約項目の増減（テンプレ v2）・月額予算（現 5,000 円）・FE の文書 CSP。
12. **主担当 NULL 22 件の修復 SQL**（9/16 設計書 §6）: フォールバックで表示・送信は直っているため急がない。
13. **音声 Phase 1 の検証 staff S009** は後片付けで retired に戻す。削除するかは PO 判断（今泉 admin に紐付いたまま）。

### 3-3. 技術的な残件（判断不要・時間があれば）
14. **ログイン後の飛び先を端末で分ける**: ログイン画面を直接開くと callbackUrl 既定が `/dashboard`（PC）。`/` だけ UA で `/m/home`。`app/(auth)/login/page.tsx:14` の既定を UA または画面幅で分ける（松岡様の iPhone が PC UI に入った事象）。
15. **月跨ぎ週の突合 100 秒制限**（9/11 総括 §3-A3）: 画面からの突合は Cloudflare 100 秒で切れ得る。admin_call（ASGI 直叩き）では無関係。ジョブ化は未着手。次の月跨ぎ週は 10/26 週。
16. **`_restrict_to_qr_capability` が denylist**: VisitRead にフィールドを足すたびに担当外 QR 所持者への開示を判断する必要がある。allowlist への反転を検討。
17. **音声 Phase 3 の記録のみ LOW**: usage の 3 クエリがスナップショット非一致になり得る／`visit_recordings.created_at` に index なし／preflight の「無い」と「読めない」が同じ表示。
18. **実績時刻の LOW**: 圏外で打刻した直後は一覧に実績が出ない（サーバ値のみ・詳細の経過時間カードが受け皿）。
19. **backend テストの既知失敗**（§6 参照）を整理して README 化。

## 4. 気になる点（リスク・次のエージェントが踏みそうな所）
- **本番の `git pull` が黙って失敗した前例**（9/18）: `preflight-check.sh` の chmod で ff 拒否 → `git pull | tail` でエラーが握られ、旧コードでビルド・再起動した。`core.fileMode false` を本番に設定済み。**以後は `git pull --ff-only origin develop && git rev-parse --short HEAD` で HEAD を必ず目視**してからビルド。
- **admin_call.py は容器の再作成で消える**: ホスト `/tmp/admin_call.py` を `docker cp` して `/app/admin_call.py` に置き、`docker exec -w /app` で実行（`/tmp` に置くと `app` パッケージが解決できない）。終わったら `docker exec -u root ... rm`。
- **Vertex AI の 3.5/3.8 Flash は東京リージョンに無い**（global のみ）。国内処理を優先して 2.5 Flash 東京で稼働。`.env` の `VERTEX_MODEL_*` を変えるときはリージョンの対応表を再確認。
- **SA 鍵は VPS `/opt/carelink/secrets/rakusuke-voice-sa.json` の 1 つだけ**（ローカルコピーは削除済み）。漏洩時は GCP で鍵をローテーション。
- **音声ファイルはバックアップ対象外**（保持 90 日のパージを生かすため）。DB の transcript/summary だけが耐久。
- **手入力イベントの absorb は題名を上書き**する（収束のため・意図的）。
- **frontend の e2e 9 ファイルは vitest で常に FAIL 表示**（playwright spec）。数に入れない。
- **backend 全体テストは 2 分割で回す**（一気に回すと 25〜30 分で打ち切られる・別 pytest 併走で顕著）。スクリプト例: `tests/test_*.py` をソートして前半/後半。
- **PowerShell の Get-Content/Set-Content で日本語ファイルを触らない**（3 回事故）。Python の heredoc も `\g` 等のエスケープ事故があった（`C:\tmp\gsa` → `C:	mp`）。パスは `/` で書く。
- **並行エージェントに git stash を使わせない**（stack 共有で他レーンを破壊した前例）。今セッションは 0 件。

## 5. 手順メモ

### 5-1. 音声テストデータの後片付け（本番）
```bash
scp docs/tools/voice-test/cleanup_voice_test.py docs/tools/voice-test/voice_test_manifest.json root@72.60.211.213:/tmp/
ssh root@72.60.211.213 'cd /opt/carelink && docker cp /tmp/cleanup_voice_test.py carelink-backend:/app/ && docker cp /tmp/voice_test_manifest.json carelink-backend:/app/ \
  && docker exec -w /app -e DRY=1 carelink-backend python /app/cleanup_voice_test.py /app/voice_test_manifest.json \
  && docker exec -w /app carelink-backend python /app/cleanup_voice_test.py /app/voice_test_manifest.json \
  && docker exec -u root carelink-backend rm -f /app/cleanup_voice_test.py /app/voice_test_manifest.json'
```
確認: `visit_recordings`（deleted_at）・`visits`（6 件 deleted）・`patients`（【検証】8 名 deleted）・`staff` S009 が retired。音声ファイルは削除 API で unlink 済み、残骸は 03:30 の purge が拾う。

### 5-2. 標準デプロイ（本セッションで 6 回実施・すべて成功）
pg_dump → `git pull --ff-only origin develop` → **HEAD 目視** → `GIT_SHA=$(git rev-parse HEAD) docker compose --env-file /opt/carelink/.env -f docs/deployment/docker-compose.production.yml build backend frontend` → `up -d --force-recreate backend frontend` → 30 秒待ち → `run --rm backend alembic upgrade head` → `alembic heads` 単一 → healthz（127.0.0.1:18001・公開ドメイン）→ 新ルートの未認証応答（401/404/422）。

### 5-3. テスト
- backend: `python -m pytest`（uv run ではない）。全体は 2 分割。既知失敗は §6。
- frontend: `pnpm vitest run` → 既知 2（`middleware.test.ts` manager・`PatientFixedVisitsPanel` E-3）＋断続 1（`BulkPoolInsertDialog`）。`pnpm tsc --noEmit`・`pnpm next lint`。

## 6. 既知失敗ベースライン（backend・2026-09-18 時点 32〜33 件）
- RBAC の manager 系（`test_rbac`・`test_patients_v2`・`test_staff_v2`・`test_patients_excel_replace_all`・`test_staff_excel_replace_all`・`test_password_change`）= manager 廃止（2026-08-09）後の旧テスト。
- `test_patient_status_sync` の `ck_svm_weekday` 3 件 = 日付依存。
- `test_schedule_v2_api` の reset_to_fixed 2 件・`test_visit_v2::test_visit_v2_two_staff_pattern`・`test_pending_requests::test_db_row_persisted`・`test_auth::test_login_locks_after_5_failed_attempts` = 既存。
- `test_visits::test_visits_delete_manager_returns_204` = aiosqlite「SQL statements in progress」・HEAD 単体でも落ちる（9/18 確認）。
- 新規の失敗が出たら「HEAD の別 worktree で同じテストを回す」で退行かどうかを切り分ける（`git worktree add --detach C:/tmp/cf_head HEAD`）。

## 7. 教訓（本セッション）
- 「反映されない」は audit_logs の apply の HTTP ステータスと `visits.source` で即答できる。部分失敗を成功トーストで隠す UI は事故になる。
- 「表示の正典＝コース担当・visits はミラー」は可視性・CSV・実現性チェックの 3 箇所で同じ条件に揃える。ミラー修正を出したら修正前に作られた週の修復まで見る。
- AI 呼び出しのテストは認証をモックするため、依存パッケージ漏れ（`requests`）を検出できない。本番初回は必ず合成データ 1 件で実行して費用と状態遷移を確認する。
- 医療記録は「消したはずの内容が配布物に出る」が最悪。要約の出し分け規則（`summary_edited_at` あり→`summary_text` が正・空なら削除表示）は画面と印刷で同一にし、テストで固定した。
- PHI を含む HTML/音声のレスポンスには `Cache-Control: no-store`。
- 打刻の実時刻は秒単位で残っていた。お客様の「取れていない」は表示の問題だった。予定を書き換えず並べる。到着が無ければ退出だけは出さない。no_show では出さない。
- お客様のロゴは公式サイト（STUDIO 製）の `studio-design-asset-files` にベクター SVG があった。スクリーンショットより先に公式サイトを探す。
- レビューは「執筆と承認を別レーン」で 1〜3 ラウンド。今セッションの是正は HIGH 4・MEDIUM 20 超・LOW 40 超で、本番不具合は 0。
