# R4 既存の道具・スキルの形式・試す場所・読み取り専用の DB（調査 2026-10-12）

対象: らく助の専用エージェントを **Claude Code と Codex CLI の両方で使えるスキル一式**として作る。最初は PC、のちに VPS（アプリの隣）。アプリのコードは変えない（既存の HTTP API を管理者で使う＋必要なら読み取り専用の SQL＋既存の道具）。

本書は **【事実】（ファイル・コマンドの出力を根拠）** と **【推奨】** を分けて書く。【推測】【未確認】は明記する。

---

## 1. 包む候補の既存の道具

### 1-1. 一覧（【事実】）

| 道具 | 入力 | 出力 | データの読み方 | 書き方 | 依存 |
|---|---|---|---|---|---|
| `docs/tools/week-leveling/level.py`（入口） | `--week` `--day` `--off` `--allow-over` `--over-before-manager` `--balance` `--drop` `--add-special` `--jev` `--no-app-check` `--from-dir` `--seconds` `--out`（L82-92）＋ `config.local.json` | `docs/reports/<日時>-week<NN>-leveling*/`（`leveling.xlsx`・`leveling-a4.html/.pdf`・`summary.json`・`week.json`・`history.json`）。利用者名入り | `extract.py` 経由（下） | **書かない** | ortools・openpyxl・httpx（`uv run --python 3.12 --with ...`） |
| `extract.py` | 月曜日・`server`・`container`（config） | `week.json`・`history.json` | `ssh -o BatchMode=yes <server> "docker exec -i carelink-postgres psql -U carelink -d carelink -v ON_ERROR_STOP=1 -At"` に `begin transaction read only; … commit;` を流す（L86-104） | 書かない（DB が拒否） | 標準ライブラリ＋ ssh |
| `solver.py` | `week.json`/`history.json` | 案（メモリ） | ファイル | — | ortools（`pywrapcp`） |
| `report.py` | 案 | Excel・A4 HTML、決まりの数え直し `check()` | — | ローカルファイル | openpyxl |
| `diagnose.py` / `app_yardstick.py` | 案・ルート | アプリの物差しの数値 | `backend/.venv/{Scripts/python.exe,bin/python}` を探し、`app_yardstick.py` を **backend の環境で子プロセス実行**（`PYTHONPATH=backend`、L40-66）。アプリの `schedule_health._compute_course_metrics` を import（DB には繋がない） | — | backend の venv（無ければ診断だけ飛ばす） |
| `jev_check.py` | 変更ごとの記号と数字 | 「要確認」の目安 | `httpx` で `TYPESAFE_BASE_URL`（既定 `https://api.typesafe.ai`）へ送信（外部送信） | — | httpx・`TYPESAFE_API_KEY` |
| `apply/generic/build_ops.py` | level の出力フォルダ（Excel） | `ops.json`・`expect.txt` | ローカルファイルのみ | — | openpyxl |
| `apply/generic/apply.py` | `W_TOKEN`（環境変数）・`ops.json`・`--from N` | `log.jsonl` | **サーバー上で**実行前提: `BASE="http://127.0.0.1:18001/api/v1"`（L6）、コース一覧は `docker exec -i carelink-postgres psql`（L29-31） | アプリの API（`urllib`）。最初の想定外の応答で止まる | 標準ライブラリのみ（VPS ホストの `python3`） |
| `apply/generic/check.py`・`make_restore_TEMPLATE.sql`・`verify_TEMPLATE.sql` | psql 出力 | 照合・戻し SQL | サーバーで psql | 戻し SQL は手で流す | 標準 |
| `apply/*_w42*`・`set_departure_plus35.py` | 週42 決め打ち | 同上 | 同上（`127.0.0.1:18001`） | API | 標準（再利用しない） |
| `docs/tools/kaipoke-ops/admin_call.py` 等 | 引数 | 標準出力 | **backend コンテナ内**で `create_access_token` で JWT を内部発行し ASGITransport で API を 1 回呼ぶ。`docker cp` → `docker exec -w /app -e PYTHONPATH=/app carelink-backend python /tmp/…`（README） | API（POST も可） | アプリのコード（コンテナ内） |
| `kaipoke-ops/three_way_check.py`・`dedupe_manual_events.py`・`build_payload.py` | 引数 | 標準出力/JSON | コンテナ内で `app.db.session.get_session_factory` を直接使う（ORM） | `dedupe_manual_events.py --apply` は **DB に直接書く**（API を通らない） | アプリのコード |
| `kaipoke-ops/*.sql`・`classify.py`・`csvrows.py`・`watch_rpa.py` | — | — | psql・RPA ログ・RPA の `127.0.0.1:5000/api/apply/result`（トークンは `docker exec carelink-backend printenv KAIPOKE_API_TOKEN`） | 読むだけ | VPS 上 |
| `docs/tools/visit-history/extract.sql`＋`build_report.py` | `ssh … psql < extract.sql > rows.json` | 月次 A4 HTML＋Excel | ssh+psql | — | openpyxl |
| `docs/tools/voice-test/*` | — | — | コンテナ内 ASGITransport＋内部 JWT | API で試験データを作る/消す | アプリのコード |
| `backend/scripts/*`（約 60 本） | 多くは Excel・`--dry-run/--apply` | DB | `app.db.session` で DB 直結（例 `create_admin.py`・`seed_pool_test_patients.py`・`import_*.py`） | **DB 直書き**（API を通らない） | backend の環境 |

補足（【事実】）:
- `config.local.json` の `server` は `root@<VPS の IP>`、`container` は `carelink-postgres`。`config.example.json` に職員コード → 拠点・マネージャーの対応。
- 本番 compose（`docs/deployment/docker-compose.production.yml`）: postgres は**ホストのポートを公開しない**（L42-44）。backend は `127.0.0.1:18001:8000`（L70-71）。backend のコードはイメージに焼き込み（`/app` の bind mount なし。mount は写真・音声・鍵だけ）→ 道具をコンテナで動かすには `docker cp` が要る。
- ログイン `POST /api/v1/auth/login` は識別子ごと `5/15minutes`・IP ごと `100/15minutes`（`backend/app/core/rate_limit.py` L41-45）。
- 既存の「API で書く」道具は 2 系統: (a) VPS ホストから `127.0.0.1:18001` に Bearer（鍵はコンテナ内で `create_access_token` で発行、`apply/generic/README.md`）、(b) コンテナ内 ASGITransport（`admin_call.py`）。どちらも **ssh で VPS に入ってから**動かす前提。

### 1-2. 動かす場所ごとに変えるもの

| 道具 | (a) PC で動かす | (b) VPS 上（ssh なし・アプリの隣） |
|---|---|---|
| `level.py`/`extract.py` | 今のまま動く（ssh 鍵・uv・backend/.venv が要る） | `extract._psql` の `ssh <server>` を外し `docker exec -i carelink-postgres psql` を直接（`server` が空ならローカル実行、の分岐 1 か所）。ホストに `uv`＋ortools が要る【未確認: VPS に uv があるか】。CPU を 1 日 30 秒×日数使うのでアプリと同居時は `nice`／時間帯を選ぶ |
| `diagnose.py` | `backend/.venv` があれば動く | ホストに backend の venv は無い【推測】→ `docker exec -i -w /app -e PYTHONPATH=/app carelink-backend python - < app_yardstick.py`（標準入力で JSON を渡す形はそのまま使える）に切り替える分岐が要る |
| `apply.py` 系 | `127.0.0.1:18001` 決め打ち・`docker exec psql` → そのままでは PC で動かない。`BASE` を環境変数にし、コース一覧の psql を `GET` API か ssh 経由に替える必要 | 今のまま動く（今の正規の場所） |
| `kaipoke-ops/*`・`voice-test/*` | 動かない（コンテナ内前提） | `docker cp`＋`docker exec` で今のまま |
| `jev_check.py` | 動く（外部送信・鍵） | 動く。外部送信を VPS から行う点を PO に確認 |
| `backend/scripts/*` | ローカル DB に向ければ動く | `docker exec carelink-backend python scripts/…`。DB 直書きなので**エージェントからは呼ばせない**（下の推奨） |

### 1-3. 既存のスキル（【事実】）
- プロジェクト: `CareFlow02/.claude/skills/week-leveling/SKILL.md`（frontmatter は `name`・`description` のみ、本文に手順と「守ること」。スクリプトは同梱せず `docs/tools/week-leveling/` を参照）。
- 利用者: `~/.claude/skills/jev-pc-use/SKILL.md`、`~/.claude/skills/synced/…`（claude.ai 同期）。
- Codex: `~/.codex/skills/{bambu-a1-mini,hatch-pet,jev-pc-use}`。`bambu-a1-mini` は `SKILL.md`＋`agents/openai.yaml`（`interface.display_name/short_description/default_prompt`）＋`references/`＋`scripts/`。`jev-pc-use` は Claude 版と**別内容**（`diff -q` で差あり）＝今は二重管理。
- リポジトリ直下に `AGENTS.md`・`CLAUDE.md`・`.agents/` は**無い**。

---

## 2. スキルの形式（Claude Code と Codex CLI）

### 2-1. 【事実】Claude Code
- スキル = `<dir>/<name>/SKILL.md`（YAML `name`・`description`）＋任意の scripts/references。プロジェクト `.claude/skills/`、利用者 `~/.claude/skills/`。
- `CLAUDE.md` は `@AGENTS.md` で取り込める。「CLAUDE.md がある時 Claude Code は CLAUDE.md だけ読み、`@AGENTS.md` の取り込みは 2 回読まない」（`PC-Assistant/docs/REPORT_GR1.md` L7）。組み込みプラグイン `agents-md` が存在（`PC-Assistant/DESIGN.md` L60・L125）。
- MCP: `.mcp.json`（プロジェクト）または `--mcp-config`。HTTP MCP のツールのアイドル 5 分の既定（`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`）に注意（`DESIGN.md` L56）。

### 2-2. 【事実】Codex CLI（この PC の版）
- `codex --version` → `codex-cli 0.153.4`。サブコマンドに `exec`・`mcp`（list/get/add/remove/login/logout）・`mcp-server`・`plugin`・`features`。
- `codex features list` 抜粋: `skill_search stable true`・`skill_mcp_dependency_install stable true`・`plugins stable true`・`hooks stable true`・`skip_host_skill_discovery under development false`。
- `~/.codex/config.toml`: `[[skills.config]] path=…\hatch-pet\SKILL.md`、`[mcp_servers.node_repl]`・`[mcp_servers.jev-hands]`、`[projects.'c:\…\careflow02'] trust_level = "trusted"`。
- PC-Assistant の調べ（`PC-Assistant-work/providers-research/CODEX_COMPAT.md`）:
  - L39: スキルの探し場所は **リポジトリの `.agents/skills`（作業フォルダー〜親〜ルート）**、`$HOME/.agents/skills`、`/etc/codex/skills`、同梱。YAML は `name`・`description` 必須、追加は `agents/openai.yaml`。最初は一覧（文脈の 2%／8,000 字まで）、選んだ時に全文。明示は `$名前`。シンボリックリンク可。
  - L40: 加えて `$CODEX_HOME/skills`（Deprecated だが互換で読む）、プロジェクト設定層の `.codex/skills`。
  - L43: Windows の sandbox は作業フォルダーの `.agents` を書き込みから守る（エージェントが自分でスキルを書き換えられない見込み）。
  - L78: AGENTS.md は `CODEX_HOME` の `AGENTS.override.md`→`AGENTS.md`、次に Git ルート→作業フォルダーの各階層を連結、上限 32 KiB。L82: `CLAUDE.md` は読まない（`project_doc_fallback_filenames=["CLAUDE.md"]` で読ませられる）。
  - L128: プロジェクトの MCP は `.codex/config.toml`（**信頼したプロジェクトだけ**）。
  - `PC-Assistant/docs/REPORT_AI1.md` L74「ChatGPT は CLAUDE.md をまだ読まず、AGENTS.md は読む」。`docs/MA1_PLAN.md` L98・L336「決まりは AGENTS.md の 1 か所、CLAUDE.md からは 1 行で取り込む（Claude も ChatGPT も同じものを読む）」。
  - 版の注意: `DESIGN.md` L146「0.159.2 には `mcp-server` が無い」。CODEX_COMPAT の出典は 0.159.2 のソース。**この PC は 0.153.4 で、リポジトリの `.agents/skills` が読まれるかは未実測**【未確認】。
- 【事実】`git config core.symlinks` → `false`。Windows でシンボリックリンクをリポジトリに入れると、チェックアウト時に**中身がパスのテキストファイル**になる。

### 2-3. 【推奨】1 つの元から両方が読む配置

```
CareFlow02/
  AGENTS.md                         ← 決まりの正典（両 CLI 共通。短く、32 KiB 未満）
  CLAUDE.md                         ← 1 行「@AGENTS.md」だけ（Claude 用の取り込み）
  .agents/skills/                   ← スキルの正典（Codex がそのまま読む）
    rakusuke-week-leveling/
      SKILL.md                      ← name/description＋手順（コマンドは `rk …` で書く）
      agents/openai.yaml            ← 任意（Codex の表示名）
      references/*.md               ← 決まり・API 早見表など
    rakusuke-ask/  rakusuke-apply/  …
  .claude/skills/<同名>/SKILL.md     ← 生成物（同期スクリプトで .agents から写す）
  agent/rk/                         ← 共通の Python CLI（下 2-4）。pyproject で `rk` を定義
  agent/sync_skills.py              ← .agents/skills → .claude/skills を写し、差があれば失敗（テストで確認）
```

- シンボリックリンクは使わない（`core.symlinks=false`・Windows の開発者モード依存）。**写し＋同期確認テスト**で二重管理を防ぐ。代替として `.claude/skills/<name>/SKILL.md` を「同じ frontmatter＋『全文は `.agents/skills/<name>/SKILL.md` を読む』の 1 行」にする薄い形も可（同期は frontmatter だけで済む）。
- 既存の `.claude/skills/week-leveling` は `.agents/skills/rakusuke-week-leveling` に移し、上の同期で戻す。
- 最初に **0.153.4 でリポジトリの `.agents/skills` が一覧に出るか**を実測する（`codex exec` は model を呼ぶので PO の了承の後。出なければ `[[skills.config]] path=` を `~/.codex/config.toml` に足す、または `codex update`）。
- VPS でも同じリポジトリ（`/opt/carelink`）を使えば同じ配置がそのまま効く。ただし VPS の本番チェックアウトに未リリースのスキルを置かない → エージェント用は別チェックアウト（例 `/opt/rakusuke-agent`）を推奨。

### 2-4. 【推奨】共通 Python CLI（`rk`）か MCP サーバーか → **`rk` CLI を先に、MCP は後で同じ関数を包むだけ**

| 観点 | 共通 CLI `rk`（スキルから Bash で呼ぶ） | MCP サーバー |
|---|---|---|
| 両 CLI での登録 | 不要（Bash があれば両方動く） | Claude は `.mcp.json`（承認）、Codex は `.codex/config.toml`（trusted のみ）か `codex mcp add`。2 か所の設定 |
| 長い処理（ソルバー 30 秒×日数） | `run_in_background`／普通の shell で問題なし | Claude の HTTP MCP アイドル 5 分、Codex の `tool_timeout_sec` 調整が要る（`CODEX_COMPAT.md` の PV3 メモ: `tool_timeout_sec=10800`） |
| 書き込みの守り | `rk` 側で `--dry-run` 既定・`--apply` に確認トークン、を 1 か所で | 同等だが、承認 UI は CLI ごとに違う |
| 人が手で使う・テスト | そのまま pytest・手で実行できる | クライアントが要る |
| VPS 常駐・アプリからの呼び出し | cron/systemd から呼べる | 将来アプリやチャットから呼ぶならこちらが向く |

構成案: `rk` のサブコマンド（例）
- `rk login`（管理者でログインし、トークンを `~/.config/rakusuke/token`〔0600〕に。レート制限 5 回/15 分に注意して再利用）
- `rk api GET|POST|PATCH|DELETE <path> [json]`（`RK_BASE_URL`: PC=本番の公開 URL or 試験環境、VPS=`http://127.0.0.1:18001/api/v1`。書く動詞は `--apply` 無しでは送らない）
- `rk sql <名前>`（下 4 の読み取り専用 SQL を名前付きで。任意 SQL は不可、または `begin transaction read only` 付きでのみ）
- `rk level …`（`level.py` を包む）・`rk apply …`（`apply/generic` を包む。戻し SQL の空打ちを必須手順に）
- `rk report …`（visit-history 等）
- 接続先は 1 つの設定（`RK_MODE=pc|vps`）で切り替える: pc = `ssh <server> docker exec …`、vps = `docker exec …` 直接。**`extract.py`・`apply.py` の分岐をここに寄せる**（道具の変更でアプリの変更ではない）。

`backend/scripts/*`・`kaipoke-ops/dedupe_manual_events.py` のような **DB 直書き**の道具はエージェントに渡さない（API の検査・監査ログを通らない）。

---

## 3. 本番に触れずに試す場所

### 3-1. 【事実】
- `backend/tests/conftest.py`: `DATABASE_URL="sqlite+aiosqlite:///:memory:"` を強制（L14-17）、`APP_ENV!="test"` なら create_all を拒否（L44-50）、`Base.metadata.create_all`（L62）、`client` フィクスチャは `create_app()`＋`ASGITransport`（L80-92）。テストファイル約 240。`tests/fixtures/` は `layer2_24patients.json`・`layer3_4weeks.json` のみ。
- アプリの既定 `database_url = postgresql+asyncpg://carelink:carelink@localhost:5432/carelink`（`backend/app/core/config.py` L33-35）。モデルは `sqlalchemy.dialects.postgresql` の `UUID`・`JSONB` を使う。API の一部（`visits.py`・`schedule.py`・`schedule_v2.py`・`integrations.py` など 10 ファイル）が `postgresql insert`/`on_conflict`/`with_for_update` 等を使う → **SQLite で uvicorn を立てても一部の書き込み経路は本番と違う動き（または失敗）になりうる**【推測：テストが通る範囲は動く】。
- `backend/docker-compose.yml`: ローカル用の `postgres:16-alpine`（5432 公開）＋ backend（8000）。
- `docker --version` → `Docker version 29.1.3`。ただし `docker info` → `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`（**Docker Desktop は入っているが停止中**）。`psql`・PostgreSQL のインストールは無し。
- メモリ: 総 16.4 GB、空き約 5.5 GB（調査時）。
- `backend/.venv/Scripts/python.exe` あり。
- 本番のバックアップ: 日次 `pg_dump --format=plain` の gzip（`daily-20261006-2034.sql.gz` 3.8 MB、`backup-restore-runbook.md` L6・L22・L51）。runbook は「月 1 回ステージングに流し込んでリハーサル」と書く（L170、`runbook.md` L708）が、**ステージング用 compose・ステージングの実体は見当たらない**（`docs/deployment` に staging の定義なし）。
- 試験データの種: `backend/scripts/seed_offices_v2.py`・`seed_cities.py`・`seed_pool_test_patients.py`・`create_admin.py`・`import_*.py`（Excel から）。

### 3-2. 【推奨】安い順
1. **読むだけの道具・判断の試験**: `level.py --from-dir <取り出し済み>` と backend の pytest（SQLite）で十分。本番には繋がない。
2. **書く経路（rk api / rk apply）の試験 = VPS 上の使い捨て Postgres＋使い捨て backend**:
   `docker run` で `postgres:16-alpine`（別名・別ネットワーク・ポート非公開）に最新の日次 dump を流し込み、同じ backend イメージを `DATABASE_URL` だけ替えて `127.0.0.1:18101` で起動 → `rk` を `RK_BASE_URL=http://127.0.0.1:18101/api/v1` で流し、照合 → 終わったら両方 `docker rm -v`。
   - 利点: PC のメモリを使わない、本番と同じ Postgres・同じイメージ・同じデータ、個人情報が VPS の外に出ない、runbook の「月 1 回のリストアリハーサル」も兼ねる。
   - 注意: 使い捨て backend は **kaipoke ネットワークに繋がない**（RPA へ送らない）・`KAIPOKE_*`・SMS・Vertex の鍵を渡さない・JWT の秘密は別値。VPS のメモリ・CPU の空きを先に確認【未確認】。**インフラの作業なので PO の了承が要る**。
3. PC で行う場合: Docker Desktop を起動して `backend/docker-compose.yml` の postgres だけ（数百 MB〜1 GB＋WSL2 の分）。本番 dump を PC に持ち出すことになる（利用者名・住所入り）ので、**PO の了承と、終わったら消す手順**が要る。SQLite で uvicorn を立てる案は、Postgres 専用の文があるので書く経路の試験には使わない。

---

## 4. 質問に答えるための読み取り専用の DB

### 4-1. 【事実】
- `extract.py` は `begin transaction read only;` を先頭に付けて ssh→`docker exec psql -U carelink` に流す（L86-89 のコメント「誤って書く文が混ざっても DB が拒否する」）。接続ユーザーは **carelink（所有者・全権）**。守りは「読み取り専用トランザクション」だけで、`commit;` の前に `set transaction read write` 等を混ぜれば破れる（SQL を人やモデルが自由に書くと守りにならない）。
- 本番の postgres はホストのポートを公開しない（compose L42-44）。
- `staff_events` の時刻は `AT TIME ZONE 'UTC'` で見る注意（SKILL.md「守ること」）。VPS は UTC（MEMORY）。

### 4-2. 選択肢
| 案 | 変更 | 守りの強さ | 備考 |
|---|---|---|---|
| A. 今のまま（ssh＋psql＋read only トランザクション） | なし | 中（所有者ロール。SQL を自由にさせると破れうる） | `rk sql` で **名前付きの SQL だけ**（リポジトリ内の `.sql`）を流す形にすれば実用上十分 |
| B. `PGOPTIONS='-c default_transaction_read_only=on'` を付けて同じ接続 | なし（クライアント側） | A と同程度（セッションで `set` すれば外せる） | A と併用 |
| C. 読み取り専用ロール（`CREATE ROLE rakusuke_ro LOGIN; GRANT SELECT …; ALTER ROLE … SET default_transaction_read_only=on`、必要なら個人情報列を除いたビュー） | **本番 DB のインフラ変更（アプリのコードは不変）→ PO の了承が要る** | 強（DB が権限で拒否） | VPS 上ではソケット/コンテナ内 `psql -U rakusuke_ro` で。パスワードは VPS の秘密置き場に |
| D. API だけで答える | なし | 強（アプリの権限・監査） | 答えられない問い（横断集計）がある |

【推奨】 最初は **D を優先、足りない所は A＋B を `rk sql <名前付き>` に限定**。VPS で常駐させる段階（モデルが任意の質問を受ける）になったら **C を PO に提案**（ロール作成は数行の SQL で、アプリのコードも compose も変えない。戻すのは `DROP ROLE`）。

---

## 5. まとめ（推奨の要点）
1. 道具は `rk`（共通 Python CLI）に集め、`pc`/`vps` の違い（ssh の有無・`docker exec` の場所・BASE URL）を 1 か所に寄せる。既存の `level.py`・`apply/generic` は中身を変えずに包む（必要な改修は `extract._psql` と `apply.py` の BASE/psql の 2 か所の分岐だけ・道具側）。
2. 配置は `AGENTS.md`（正典）＋`CLAUDE.md`（`@AGENTS.md` 1 行）＋`.agents/skills/`（正典）→`.claude/skills/`（同期の写し＋同期テスト）。シンボリックリンクは使わない。MCP は後で `rk` を包む形で足す。
3. 書く経路の試験は VPS の使い捨て Postgres＋使い捨て backend（最新 dump・ポート非公開・RPA/外部鍵なし）。PC は読むだけの試験と pytest に限る。
4. 読み取りは API 優先＋名前付き SQL（read only トランザクション）。常駐時に読み取り専用ロールを PO に提案。
5. 未確認: Codex 0.153.4 がリポジトリの `.agents/skills` を読むか／VPS に uv・ortools を入れられるか・メモリの空き／本番 dump を使い捨て環境へ入れることへの PO の了承。
