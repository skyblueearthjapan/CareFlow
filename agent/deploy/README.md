# らく助エージェント 段 3 — 本番・staging への置き方（中継・箱・出口）

設計: `docs/plans/agent-ui/phase3-implementation-design-2026-10-12.md`（PO 決定 D1〜D14・§1-1/1-2/1-8・§2-1〜2-8・§5）。
**このフォルダのものは、まだ何も VPS で実行していない。** 本番への変更は 1 つずつ PO の了承を取ってから（§2 のチェック表）。

## 0. 中身

| ファイル | 役割 |
|---|---|
| `docker-compose.agent.yml` | 3 コンテナ（中継 `carelink-agent`・箱 `carelink-agent-sandbox`・出口 `carelink-agent-egress`）。本番と staging で共用（差し込み値だけ違う） |
| `agent.prod.env` / `agent.stg.env` | compose の差し込み値（秘密なし）。ポート・網・名前の頭・CLI の版 |
| `Dockerfile.relay` | 中継（Python 3.12・rk・`psql`/`pg_dump` 16）。`agent/relay/` を `python -m relay` で起こす（手元で build・healthz 200 を確かめ済み 2026-10-12） |
| `Dockerfile.sandbox` | 箱（Python 3.12＋ortools・Node 22・`@anthropic-ai/claude-code@2.1.296`・`@openai/codex@0.162.1`・runner・`agent/` の写し） |
| `egress/` | 出口プロキシ（tinyproxy）。`allowlist` のホストへの 443 だけ通す |
| `runner/agent_runner.py` | 箱の中の小さな常駐。unix ソケットで中継からだけ（SO_PEERCRED で uid 10001 を確認）`spawn`・`stdin`・`close_stdin`・`kill`・`status` を受け、決まった引数でだけ CLI を起こす |
| `runner/isolation.json`・`runner/empty-mcp.json` | claude に毎回渡す決まりの設定（`--settings`・`--mcp-config`）。runner が会話ごとに写す（中継からは中身を受けない） |
| `sql/roles.sql` / `sql/roles-rollback.sql` / `sql/verify-roles.sql` | DB の専用ロール 3 つ（作る・戻す・確かめる）。冪等 |
| `set-role-passwords.sh` | ロールのパスワードを作り、中継の秘密ファイル（0400）を書く。既定は表示だけ・`--apply` で実行 |
| `preflight.sh` | 立てる前の確認（読むだけ）: メモリ・ディスク・ポート・網・秘密ファイル・ロール・もう一方の箱 |
| `../.dockerignore` | image に `runs/`（利用者名入りの実行記録）・`staging/`・`tests/`・秘密を入れない |

## 1. VPS の事実（2026-10-12 に読むだけで確認）

| 項目 | 事実 |
|---|---|
| Docker / Compose | 29.2.0 / v5.0.2・x86_64 |
| メモリ | 7,936MB・**swap なし**・available 約 2,550MB |
| らく助の網 | `deployment_default`（172.24.0.0/16）: `carelink-postgres`・`carelink-backend`（＋`playwrighttest1_default`）・`carelink-frontend`。compose project = `deployment` |
| Postgres | 16.13・ロールは `carelink`（superuser）だけ・スキーマ `public` だけ・56 表・RLS 0・ラージオブジェクト 0・alembic 0090。pg_hba は `host all all all scram-sha-256`（網の中からパスワードで入れる） |
| 秘密の列 | `users.password_hash`・`kaipoke_credentials.password_encrypted`・`patients.qr_token`（掲示 QR の合言葉）・`revoked_qr_tokens.token`（取り消し済み・害なし） |
| 使っている網の範囲 | 172.17〜172.29/16・10.78.0/24・10.79.0/24。箱の網に **172.31.250.0/24（本番）・172.31.251.0/24（staging）** を使う（空き）。出口の網は **172.31.240.0/24（本番）・172.31.241.0/24（staging）** に固定（自動に任せると 172.31.0.0/16 を取って箱の網と重なる） |
| ポート | 18201・18211 は空き（18000/18001 = らく助、18101 = staging backend） |
| cloudflared | **systemd（ホスト上）**・`/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run`・版 2026.1.2（古いと警告あり）・tunnel `ff143e2a-…` |
| tunnel の設定の正 | **Cloudflare ダッシュボード（リモート管理）**。根拠: ログの `ingressRule=2 → 127.0.0.1:18789`・`ingressRule=3 → localhost:18001`・`ingressRule=4 → localhost:18000` が、ローカルの `/etc/cloudflared/config.yml`（linebot が 1 番目・`127.0.0.1` 表記・2026-05-05 から変わっていない）と合わない。runbook Phase G の「2026-05-05 実測: ダッシュボード管理」と一致。**ローカルの config.yml を編集しても効かない** |

## 2. 本番に入れる前のチェック表（各行 PO の了承が要る）

| # | 変更 | 了承 | 戻し方 |
|---|---|---|---|
| P1 | 本番 DB の pg_dump（毎回・変更の直前） | 不要（いつもの手順） | — |
| P2 | `sql/roles.sql` を本番 DB に流す（ロール 3 つ・スキーマ `agent` の空箱） | **要** | `sql/roles-rollback.sql`（§6） |
| P3 | `set-role-passwords.sh prod --apply`（パスワード・秘密ファイル） | **要** | ロールを消せば無効。ファイルは `shred -u` |
| P4 | エージェント専用の管理者ユーザーを本番に作る（設計 §2-8・PO 決定 Q2） | **要** | 画面からそのユーザーを無効化 |
| P5 | migration 0091（スキーマ `agent` の表・別担当） | **要**（backend のデプロイ手順） | `alembic downgrade 0090` |
| P6 | image を build し、`compose up -d`（3 コンテナを立てる） | **要** | `compose down`（§6） |
| P7 | 箱の CLI に PO のアカウントでログイン（対話・PO 本人） | **要**（D11） | `claude /logout`・volume を消す |
| P8 | Cloudflare に `/agent-relay/*` の道を足す（ダッシュボード） | **要**（D4） | その行を消して保存（§5） |
| P9 | 機能の旗 `rakusuke_agent` を PO だけ有効（D10） | **要** | 旗を無効 |

順番: P1 → P2 → P3 → P4 → P5 → P6 → P7 → 箱の隔離の確認（§3-7）→ P8 → P9。どこかで想定外があれば止めて PO へ。

## 3. 手順（本番・VPS の上・root）

### 3-1. 置く・build する
```bash
cd /opt/carelink && git pull --ff-only origin develop
# 中継の依存（agent/relay/requirements.txt）が入っていること（無いと relay の build が止まる）
alias dca='docker compose -p carelink-agent -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.prod.env'
dca build            # 立てない。build だけ（数分・ディスク約 2GB: 箱の image 1.7GB）
```

### 3-2. DB のロール（P2）
```bash
docker exec carelink-postgres pg_dump -U carelink -d carelink | gzip > backups/pre-agent-roles-$(date -u +%Y%m%d-%H%M).sql.gz
docker exec -i carelink-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < agent/deploy/sql/roles.sql
```
- `agent_ro`: 全表 SELECT。ただし `kaipoke_credentials` は表ごと不可、`users.password_hash`・`patients.qr_token` は列ごと不可（列単位の権限なので `select * from users|patients` は権限エラーになる。`agent/sql/*.sql` は列を書いているので影響なし）。読み取り専用トランザクション・30 秒・接続 3 本。
- `agent_dump`: `pg_read_all_data`（バックアップ専用・読み取り専用トランザクション・接続 1 本）。
- `agent_relay`: スキーマ `agent` の表の読み書きだけ（DDL・DROP・public は不可）。スキーマの持ち主は `carelink`（migration が作る）。
- **migration の後に毎回**: 新しい表は既定の権限で `agent_ro` が読める。秘密の列が増えたら `roles.sql` の `deny` に足して流し直す（冪等）。`users`・`patients` に列が増えたら流し直すまで新しい列は読めない（安全側）。
- 手元の postgres:16 で、作る→もう一度→確かめる→各ロールの動き（読める/読めない/書けない）→pg_dump→戻す→もう一度戻す→作り直す、を確かめ済み（2026-10-12）。

### 3-3. パスワードと秘密ファイル（P3・P4）
```bash
bash agent/deploy/set-role-passwords.sh prod            # 表示だけ
bash agent/deploy/set-role-passwords.sh prod --apply    # 実行 → /opt/carelink/secrets/agent-relay.env（0400 root）
```
秘密ファイルの中身（中継の実装との約束）: `AGENT_RO_DSN`・`AGENT_DUMP_DSN`・`AGENT_RELAY_DSN`・`AGENT_URL_SIGNING_KEY`（印刷用の署名つき URL）・`AGENT_BACKEND_IDENTIFIER`・`AGENT_BACKEND_PASSWORD`（P4 のエージェント専用の管理者。PO が画面で作り、PO がファイルに書き足す）・試験中は `AGENT_ALLOWED_USER_IDS`。
**本番の `.env` の値は入れない**（`preflight.sh` が鍵の名前で検査する）。

### 3-4. バックアップ置き場と pg_dump の限界
```bash
install -d -o 10001 -g 10001 -m 0700 /opt/carelink/backups/agent
```
- 中継は反映の直前に `agent_dump` で `pg_dump` して `/opt/carelink/backups/agent/agent-<label>-<UTC>.sql.gz` に置く（モデルは飛ばせない）。毎日のバックアップ（UTC 17:30）とは別。
- **限界**: `pg_read_all_data` は `password_hash`・カイポケの暗号化済みログイン情報も読む（pg_dump は全部読む必要がある）。この鍵は中継の秘密ファイルにだけあり、箱には無い。中継が乗っ取られると全データを読まれうる（ただし中継は `agent_ro`・管理者トークンも持つので、増える危険は「パスワードのハッシュとカイポケの暗号文」）。
- もっと絞る案（段 4 で検討）: 中継からは pg_dump しない。ホストに root の固定スクリプト（例 `/usr/local/sbin/carelink-agent-backup <label>`・ラベルは `^[a-z0-9-]{1,40}$` だけ）を置き、中継は「頼む」ファイルを置くだけ（systemd の path unit が拾って `docker exec carelink-postgres pg_dump` する）。そうすると `agent_dump` ロールは要らない。
- 戻すのは今どおり PO がホストで（自動では戻さない）。

### 3-5. migration 0091（P5・別担当）
スキーマ `agent` と 6 表。0091 は `CREATE SCHEMA IF NOT EXISTS agent`（roles.sql が先に空のスキーマを作っているため）。流した後に `roles.sql` をもう一度流す（既にある表への権限を付け直す）。

### 3-6. 立てる（P6）
```bash
bash agent/deploy/preflight.sh prod      # NG が 1 つでもあれば立てない
dca up -d
dca ps                                    # 3 つとも healthy
docker stats --no-stream carelink-agent carelink-agent-sandbox carelink-agent-egress
free -m
```

### 3-7. 箱の隔離を確かめる（P7 の前後どちらでも・必須）
```bash
S=carelink-agent-sandbox
# backend・Postgres・ホストのポートへ届かない（すべて失敗が正しい）
docker exec $S python3 -c "import socket; socket.create_connection(('carelink-postgres',5432),3)"   # 名前が引けない
docker exec $S python3 -c "import socket; socket.create_connection(('172.24.0.2',5432),3)"          # 届かない
docker exec $S python3 -c "import socket; socket.create_connection(('172.17.0.1',18001),3)"         # 届かない
# 出口: 許可リストのホストだけ（1 行目は 2xx/4xx が返る＝通る、2 行目は 403 = 断る）
docker exec $S python3 -c "import urllib.request as u; o=u.build_opener(u.ProxyHandler({'https':'http://agent-egress:3128'})); print(o.open('https://api.anthropic.com/', timeout=10).status)"
docker exec $S python3 -c "import urllib.request as u; o=u.build_opener(u.ProxyHandler({'https':'http://agent-egress:3128'})); o.open('https://example.com/', timeout=10)"
# 秘密が無い
docker exec $S sh -c 'env | grep -ciE "postgres|jwt|kaipoke|secret" ; ls /var/run/docker.sock /opt/carelink 2>&1'
# runner は中継以外を断る（箱の uid からつなぐと切られる）
docker exec -u 10002:10000 $S python3 -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/agent/runner.sock'); s.send(b'{\"op\":\"status\"}\n'); print(s.recv(100))"
```

### 3-7b. 認証情報の隔離を確かめる（必須・お客様が使う前に）
考え方と残る危険は §9-(1)・§9-(8)。ログイン情報は**人ごとの席**（`/home/agent/u/<鍵>/.claude/.credentials*`・`/home/agent/u/<鍵>/.codex/auth.json`・2026-10-13〜。前の共有の置き場 `/home/agent/.claude`・`.codex` も同じ扱い）にあり、CLI の uid（10002）だけが読める（`/home/agent`・`/home/agent/u`・各席はどれも 0700）。
**モデルのコマンドは別 uid（10004）の実行係で走る**ので、持ち主の席も別の人の席も読めない。
```bash
# (1) 手元・CI でも: 偽のモデルで本物の claude / codex を動かし、モデルの cat が Permission denied になることを確かめる
#     （人ごとの席: 持ち主の席・別の人の席・前の共有の置き場の 3 つとも読めないこと、席が 0700 であることも見る）
bash agent/deploy/tests/isolation_e2e.sh carelink-agent-sandbox:latest
# (2) 立てた箱で: 3 つの係の uid・capability（CapEff/CapBnd が 0・NoNewPrivs が 1 のこと）
docker exec carelink-agent-sandbox sh -c 'for p in $(pgrep -f "execd.py|exec-server|agent_runner.py"); do grep -E "^(Uid|CapEff|CapBnd|NoNewPrivs)" /proc/$p/status | tr "\n" " "; echo; done'
# (3) ログイン後に、実行係の側から見えないこと（Permission denied が正しい）
# -u は名前で（agent の補助グループ 10005 が付かないと実行係のソケットに届かず 126・2026-10-12 staging で実測）
docker exec -u agent carelink-agent-sandbox /opt/runner/bx 'id -u; cat /home/agent/.claude/.credentials.json; cat /home/agent/.codex/auth.json'
# (3') 人ごとの席も（一覧も中身も Permission denied が正しい）
docker exec -u agent carelink-agent-sandbox /opt/runner/bx 'ls /home/agent/u; cat /home/agent/u/*/.codex/auth.json'
docker exec -u 10002:10000 carelink-agent-sandbox sh -c 'stat -c "%a %U %n" /home/agent /home/agent/u /home/agent/u/*'   # すべて 700 agent
```
- 箱の healthcheck は実行係のソケット（`/run/x/exec.sock`）も見る。無いと unhealthy（中継は起きない）。runner も `RUNNER_EXEC_SOCKET` が設定されているのにソケットが無ければ CLI を起こさない（`exec service unavailable`）。
- 仕組み（`runner/entrypoint.sh`）: 箱は root で起動（capability は SETUID/SETGID/SETPCAP だけ・何も待ち受けず何も読まない）→ **runner と CLI は uid 10002（agent）、execd と codex exec-server は uid 10004（agentx）**。どれも capability 0・no-new-privs。どれかが落ちたら箱ごと再起動。
  - claude: `CLAUDE_CODE_SHELL_PREFIX=/opt/runner/bx`。Bash の道具のコマンドはすべて `bx`（fd を unix ソケットで execd に渡す）経由で uid 10004 が走らせる。execd に届かなければ **走らせず 126**（CLI の uid では絶対に走らせない）。
  - codex: `CODEX_EXEC_SERVER_URL=ws://127.0.0.1:9911`（uid 10004 の `codex exec-server`）。この変数があると app-server は自分では何も走らせず、コマンド・ファイル操作とも実行係の環境「remote」だけを使う（実測 0.162.1: 無い環境は `local environment is not configured`）。実行係には app-server の環境が渡らないので、rk に要る `RK_*` と PATH は `-c shell_environment_policy.set` で渡し、ログインシェル（`/etc/profile` が PATH を戻す）は `allow_login_shell=false` で止める。
  - 会話の作業フォルダー（`/data/sessions/<id>/work`）はグループ 10005（agentwork）の 2770。CLI も実行係も書ける。実行係に渡る環境は `execd.py` の許可リスト（`RK_*`・`TMPDIR`・`PYTHONPATH`・言語・プロキシ。`HOME`・`CODEX_HOME`・`CLAUDE_CONFIG_DIR` は渡さない）。
  - CLI 自身の道具（Claude の Read/Edit/Grep/Glob は CLI と同じ uid で動く）: `permissions.deny`（`Read(//home/agent/**)` など・`runner/isolation.json`）と、中継の許可の返事（`relay/runner.py` の `_touches_secret`）で `/home/agent` と認証情報の場所を断る。
  - **実行係の手が届く範囲**（レビュー対応）: `/data/sessions` と各会話のフォルダー d は agent 持ち 0750（実行係は入れるが消す・付け替えるは不可）、書けるのは d/work（2770）の中だけ。**`--settings` は `/run/agent/<会話>.settings.json`**（実行係は入れない）に、起こすたびに消して `O_EXCL|O_NOFOLLOW` で新しく書く。
  - execd は subreaper。コマンドの終わりと 2 秒ごとに、要求から外れた子孫（setsid・二重 fork）と init の子になった uid 10004 の残り（codex 側）を止める。PID 名前空間（`unshare --pid`）は capability 0 では使えないため、この掃除で代える。
  - execd は要求の前後と 2 秒ごとに、`/data/sessions/*/work` の中の **work の外を指すシンボリックリンクを消す**（CLI の uid の道具がリンク経由で `/home/agent` へ辿れないように。CLI の deny に頼らない守り）。
  - codex のファイル操作: CODEX_EXEC_SERVER_URL があると app-server の `fs/readFile` は `local filesystem is not configured`（実測・試験に入れた）＝app-server（10002）自身はローカルのファイルを触らず、コマンド・ファイル操作とも実行係の環境「remote」に出る。**`apply_patch` の道具そのものは、偽のモデルでは道具の一覧に出ず直接は試せていない**（本物のモデルで staging にて確認: 作業フォルダーに作ったファイルの持ち主が 10004 になること）。
  - ログインの手順の `docker exec` には **`-u 10002:10000`** が要る（§3-8）。


### 3-8. CLI のログイン（P7・画面の ⚙ 設定から／予備は PO の端末から SSH で）

**決まり: 箱の CLI はサブスク（Claude アカウント）のログインだけで動かす。API キーは使わない。**
- 箱の環境に `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`ANTHROPIC_BASE_URL`・`OPENAI_API_KEY`・`OPENAI_BASE_URL`・`CODEX_API_KEY` などは入れない。三重に止める:
  1. compose の箱（`agent-sandbox`）に `env_file` を書かない・`environment` にも足さない。箱の healthcheck は、これらが環境にあると **unhealthy**（中継は `depends_on: service_healthy` なので起きない）。
  2. runner（`runner/agent_runner.py` の `FORBIDDEN_ENV`）が起動時に自分の環境から消し、CLI の環境は許可リストで一から作る（入っていたら CLI を起こさない）。
  3. CLI の `--settings`（`runner/isolation.json`）で `ANTHROPIC_*`・`CLAUDE_CODE_USE_*` を空・`forceLoginMethod: claudeai`・`apiKeyHelper: ""`。
- 中継は毎ターンの `system/init` の `apiKeySource` が **`none`（サブスクのログイン）のときだけ**続ける。ほかの値・項目が無いときは会話を止め、画面に「思っていない方法で AI につながろうとしたので止めました」を出す（`error` の `code: auth_source`）。`AGENT_ALLOWED_KEY_SOURCES` に別の値を書いても段 3 では無視する。

**画面からのサインイン（2026-10-13〜・ふだんはこちら）**

**人ごとの席（PO 2026-10-13: お客様は ChatGPT Team。ChatGPT Business/Team の約束は 1 席 1 人）**: らく助エージェントの相談は、**その相談を始めた人自身の AI のログイン**で動く（1 つのログインをみんなで使わない）。設計: `docs/plans/agent-ui/per-user-seats-2026-10-13.md`。
- 置き場: 箱の `/home/agent/u/<鍵>/.codex`（`CODEX_HOME`）・`/home/agent/u/<鍵>/.claude`（`CLAUDE_CONFIG_DIR`）。`HOME` もその席。`<鍵>` は中継が らく助の user id から作る 32 桁の 16 進（`relay.engines.seat_key`＝`sha256("rakusuke-agent-seat\n" + user id)` の先頭 32 桁・秘密ではなく、フォルダー名に user id を出さないための写像）。runner は中継から鍵だけを受け、形（`[a-f0-9]{32}`）が違えば断る。パスは受けない。
- 席は runner が 0700・持ち主 10002 で作る（様子を見るだけでは作らない）。実行係 10004 は `/home/agent` に入れない（§3-7b のまま）。
- 相談を始めるとき、中継は持ち主の席のログインを確かめ、**していなければ CLI を起こさず**、会話に「ChatGPT にサインインすると、相談を始められます」と ⚙ を開くボタンを出す（`error code=signin`）。
- ⚙ の「AI のつなぎ先」に出るのは**開いた本人の**様子（「あなたの ChatGPT」「あなたの Claude」）。サインイン・サインアウト・「あなたの新しい相談で使う AI」（`agent.settings` の `default_provider:<user id>`。無ければ全体の `default_provider` → `AGENT_DEFAULT_PROVIDER`）も本人の分だけが変わる。
- 管理者だけの一覧「管理者のサインインの様子」（`GET /agent-relay/engines/seats`）: らく助の管理者（users の admin/manager・名前は職員名かユーザー名）ごとの「サインインしています／していません…」の語だけ。AI のアカウントのメール・user id は出さない。
- Codex が利用上限に近い（`account/rateLimits/updated` の使った割合が 80% 以上）と言ったら、会話に「いまはこのアカウントの利用上限に近づいています（あなたの ChatGPT の契約の分です）」を 1 回だけ出す（会話は止めない）。上限に当たったら、いままでどおり「HH:MM 以降に続けられます」。
- サインインは**箱の中で同時に 1 つだけ**（だれのものでも。ほかの人の途中は「いま別のサインインの途中です（ほかの方の場合もあります）」で断る）。様子・やめる・コードの貼り付けは、本人のサインインだけに効く（他人の途中は見えない）。

管理者は、らく助の小窓の見出し（携帯は「相談」タブの ☰ の中）の **⚙ 設定 →「AI のつなぎ先」** から、SSH なしで次ができる。
- 様子: あなたの ChatGPT（Codex）・あなたの Claude それぞれ「サインインしています／していません／使えないつなぎ方（API キーなど）／分かりません」。アカウント・メールは出さない。
- サインイン（箱で同時に 1 つだけ・15 分で打ち切り・1 人 15 分に 5 回まで）:
  - ChatGPT: 箱の runner が `codex login --device-auth` を uid 10002（`CODEX_HOME=/home/agent/u/<鍵>/.codex`・`HTTPS_PROXY=http://agent-egress:3128`・席が無ければ 0700 で作る）で起こし、画面に `https://auth.openai.com/codex/device` と 1 回限りのコード（コピーのボタン・残り時間）を出す。ブラウザーで**自分の** ChatGPT（Team の自分の席）で入れ終わると「サインインできました」。
  - Claude: runner が `claude auth login --claudeai`（サブスクだけ・`--console` は使わない）を uid 10002（`CLAUDE_CONFIG_DIR=/home/agent/u/<鍵>/.claude`）で起こし、画面に Claude のサインインのページへのリンクを出す。ブラウザーでサインインすると出るコード（`code#state` の形）を画面の欄に貼る → 中継 → runner が CLI の stdin に渡す（2.1.296 で、TTY なしでも URL を出し stdin のコードを受けることを実測・2026-10-13）。トークンの交換先は `platform.claude.com`（出口の許可リストにある）。リンク先 `claude.com` は利用者のブラウザーが開くだけ（箱からは出ない）。
- サインアウト: `codex logout` / `claude auth logout`（その人の席だけ・3 択で確かめる: サインアウトする／サインアウトして別のアカウントでサインインし直す／やめる）。サインアウトすると、**その人の**その AI での相談は、本人がサインインし直すまで止まる（ほかの人の分は変わらない）。
- 新しい相談で使う AI: 画面で選ぶと `agent.settings`（migration 0094・表はそのまま）の `default_provider:<user id>` に残る（本人だけ）。無い人は全体の `default_provider` → `AGENT_DEFAULT_PROVIDER`。
- 守り: 中継の口は管理者だけ（`/auth/me`・旗・試験中の許可）。runner が返すのは状態の語と、決まったホストの URL・決まった形のコードだけ（CLI の文・トークン・メールは返さない・中継ももう一度形を確かめて捨てる）。貼ったコードは記録に残さない。誰がいつ始めた・終わった・サインアウトした・既定を変えたかは `agent.admin_events` に残る。ログインの CLI は runner と同じ uid 10002 で動く（実行係 10004 からは読めない・§3-7b のまま）。
- 道: `GET /agent-relay/engines`・`GET /engines/seats`（管理者の一覧）・`POST /engines/{codex|claude}/login`・`GET /engines/login`・`POST /engines/login/code`・`POST /engines/login/cancel`・`POST /engines/{provider}/logout`・`POST /engines/default`（`agent/relay/public.py` が正）。runner の op は `auth_status{provider,user_key}`・`login_start`・`login_poll`・`login_input`・`login_cancel`・`logout`・`adopt_legacy`（どれも `user_key` が要る・`spawn` も `user_key` が要る・`runner/agent_runner.py` の先頭）。

**前の共有のログインを、PO の席へ移す（1 回きり・2026-10-13 の版を入れるとき）**

それまでの共有のログイン（`/home/agent/.codex`・`/home/agent/.claude`＝PO がサインインしたもの）は、PO 本人の席に移す。user id はコードに書かない。
1. VPS の中継の秘密ファイル（`/opt/carelink/secrets/agent-relay.env`・git 管理外）に 1 行足す: `AGENT_LEGACY_OWNER_USER_ID=<PO の らく助の user id>`（代わりに `agent.settings` の `legacy_owner_user_id` でもよい）。
2. 中継を起こし直すと、起きたとき（runner に届かなければ、最初に ⚙ を開いたとき・相談を始めたとき）に runner へ `adopt_legacy` を 1 回頼む。runner は、PO の席にまだそのログインが無い（無い・空のフォルダー）ときだけ `rename` で移し、`/home/agent/u/.legacy-adopted` に印を残す。印があれば、だれの鍵で頼まれても二度と動かない。移したことは `agent.admin_events` に `legacy_adopted`（`codex,claude` など）で残る。
3. 確かめ: PO が ⚙ を開いて「あなたの ChatGPT: サインインしています」。ほかの管理者は「サインインしていません」→ 各自が自分の ChatGPT（Team の自分の席）でサインインする。
4. 移せなかった分（PO が先に自分の席へサインインしていた等）は `/home/agent/.codex`・`.claude` に残る。要らなければ PO が消す: `docker exec -u 10002:10000 carelink-agent-sandbox rm -rf /home/agent/.codex /home/agent/.claude`。
- 手で移す場合（自動を使わない・`AGENT_LEGACY_OWNER_USER_ID` を書かない）:
```bash
KEY=$(python3 -c 'import hashlib,sys;print(hashlib.sha256(("rakusuke-agent-seat\n"+sys.argv[1]).encode()).hexdigest()[:32])' <PO の user id>)
docker exec -u 10002:10000 carelink-agent-sandbox sh -c "umask 077; mkdir -p /home/agent/u/$KEY && chmod 700 /home/agent/u /home/agent/u/$KEY && mv /home/agent/.codex /home/agent/u/$KEY/.codex && mv /home/agent/.claude /home/agent/u/$KEY/.claude && touch /home/agent/u/.legacy-adopted"
```

**SSH でのログイン（画面が使えないときの予備・本人だけ・ログイン情報は volume `carelink-agent-home` の本人の席 `/home/agent/u/<鍵>/.claude/.credentials.json` に残る）**

人ごとの席なので、下の `CLAUDE_CONFIG_DIR`・`CODEX_HOME` は**その人の席**にする（`<鍵>` は上の「手で移す場合」の `KEY=$(python3 …)` で、その人の らく助の user id から作る）。下の例の `/home/agent/.claude`・`/home/agent/.codex` は `/home/agent/u/<鍵>/.claude`・`/home/agent/u/<鍵>/.codex` に読み替える（席が無ければ先に `mkdir -m 700 -p`）。

Claude Code の画面から PO が `!` で打つ（`-t` が要る）。URL が出たら自分の PC のブラウザーで開き、出たコードを貼る。
```bash
# 1) 対話の claude を箱の中で開き、/login →「Claude アカウント（サブスク）」を選ぶ → 終わったら /exit
! ssh -t root@72.60.211.213 docker exec -it -u 10002:10000 -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude -e DISABLE_AUTOUPDATER=1 carelink-agent-sandbox claude
# 2) 状態を見る（版によっては auth status が無い。その時は 3) だけで確かめる）
! ssh -t root@72.60.211.213 docker exec -it -u 10002:10000 -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude carelink-agent-sandbox claude auth status
```
- staging は同じことをコンテナ名 `stg-agent-sandbox` で（volume `stg-agent-home` は本番と別）。
- 箱は root で起動して CLI を uid 10002 に落とす作りなので、`docker exec` には必ず **`-u 10002:10000`** を付ける（付けないと root で入り、ログイン情報が 10002 の持ち主にならない・root は 10002 のファイルを読み書きできない）。
- 別案 `claude setup-token`: 長期の OAuth トークンを**表示するだけ**（保存しない）。段 3 の runner はこのトークン（`CLAUDE_CODE_OAUTH_TOKEN`）を CLI に渡さないので、**段 3 では `/login` を使う**。setup-token は段 4 の「ログイン情報をモデルから読めない所に置き、runner がトークンだけ渡す」形で使う（設計 §2-1）。

**ChatGPT（Codex）のログイン — 既定の AI（PO 2026-10-12: お客様は ChatGPT の契約が多い）**

決まり: ChatGPT の契約（Plus・Pro・Business など）のログインだけ。`OPENAI_API_KEY`・`codex login --with-api-key` は使わない（中継が `account/read` で `type: chatgpt` 以外なら会話を始めない＝画面に「ChatGPT のログイン以外の方法で…止めました」）。
ログイン情報は volume `carelink-agent-home` の `/home/agent/.codex/auth.json`（持ち主 10002）。

```bash
# 1) デバイスコードでログイン（codex-cli 0.162.1 で --device-auth があることを実測・2026-10-12）
! ssh -t root@72.60.211.213 docker exec -it -u 10002:10000 -e HTTPS_PROXY=http://agent-egress:3128 -e CODEX_HOME=/home/agent/.codex carelink-agent-sandbox codex login --device-auth
#    → 画面に「https://auth.openai.com/codex/device」と 1 回限りのコード（XXXX-XXXXX・15 分）が出る。
#      スマホか PC のブラウザーでその URL を開き、ChatGPT（PO のアカウント）でログイン → コードを入れる → 許可。
#      ターミナルに「Successfully logged in」が出たら終わり。
# 2) 確かめ（"Logged in using ChatGPT" が正しい。"Not logged in" なら 1) をやり直す。API キーと出たら PO へ）
! ssh -t root@72.60.211.213 docker exec -it -u 10002:10000 -e CODEX_HOME=/home/agent/.codex carelink-agent-sandbox codex login status
# 3) 締め付けの確かめ（ログインしていなくても見られる）: 中継が会話の始めに同じことをする
#    agent/deploy/README の §3-8b の probe（下）か、画面で相談を 1 つ送って返事が来ること
```
- staging は同じことをコンテナ名 `stg-agent-sandbox` で（volume `stg-agent-home` は本番と別。`down -v` で消える）。
- 通信先（実測）: ログインは `auth.openai.com` だけ（デバイスコード・トークンの更新 `/oauth/token`）、会話は `chatgpt.com` だけ（`/backend-api/codex`）。出口の許可リストはこの 2 つ（§8）。
- 画面の「ログイン済み／未」: 中継の `GET /agent-relay/engine`（管理者・鍵つき）が `login.codex.state` = `ok`（直近の会話の始めに ChatGPT のログインを確かめた）／`missing`（ログインなし）／`not_subscription`（API キーなど）を返す。トークン・メールは返さない。箱の runner も `{"op":"auth_status"}` で `codex login status` の結果を ok/missing/api_key/unknown の 1 語だけ返す（文は返さない）。
- ログインが切れた（トークンの更新に失敗した）ときは、会話の始めに `error code=login`（「ChatGPT にログインしていません」）が画面に出る → 1) をやり直す。

### 3-8b. Codex の起こし方と締め付け（中継が会話ごとに確かめる）

- 起こし方: `codex -c …（締め付け）app-server`（stdio の JSON-RPC）。決まりの正は `agent/relay/codex_config.py`（中継の direct と箱の runner が同じファイルを使う）。モデルは会話の始めに `model/list` から最新の Sol（`gpt-<版>-sol`）を選び、`thread/start`・`turn/start` で渡す。「このアカウントでは使えない」と言われたら、まだ何もしていないターンだけ一つ下の Sol でやり直す。
- sandbox は `danger-full-access`（**箱＝コンテナが砂場**）。codex 0.162.1 の Linux の砂場（bwrap）は Docker の既定の seccomp では動かない（`No permissions to create a new namespace`・2026-10-12 実測）。
- 承認: `approval_policy=on-request`・`approvals_reviewer=user`（承認をモデルに任せる guardian は切る）。PO の決まり「箱の中の計算は自由・書き込みだけ関所」: シェル・python・その場の script は聞かずに動く。問い合わせが来たら中継が答える（秘密を読む・外へ出る・中継の内側口の直打ち・`/srv/agent` の書き換えだけ断る）。**守りは承認に頼らない**（書き込みは中継の承認番号だけ）。
- 毎回の `-c`: ChatGPT ログインだけ（`forced_login_method=chatgpt`）・`model_provider=openai`・Web 検索なし・MCP なし・アプリ/プラグイン/フック/記憶なし・画像/ブラウザー/コンピューター操作/サブエージェントなし・`notify` なし・質問カード（`request_user_input`）あり。
- 始める前の確かめ: `config/read` と `experimentalFeature/list` で上が効いているか。効いていなければ会話を始めず「安全の設定が効いていないので始めませんでした」（`code: lockdown`）。0.162.1 の箱で「問題 0」を確かめた（2026-10-12・ログインなし）。
- runner は起動のたびに `CODEX_HOME/config.toml` を空に書き直し、`CODEX_HOME/rules/` を `rakusuke.rules`（rk は allow・curl/wget/git/pip/ssh などは forbidden）だけにする（モデルが書き足したものは消える）。`/srv/agent/bin` を PATH の先頭に（`rk …` がいちばん楽な道）。作業フォルダーは `/data/sessions/<id>/work`（`scratch/` = TMPDIR）。
- 走ったコマンドは中継の見張り（`agent/relay/watch.py`）が見る: 秘密らしい所を読む → その場で止めて琥珀の 1 行、内側口の直打ち・外への通信・`/srv/agent` の書き換え・同じ失敗 3 回・長い空回り → 最初は道案内をモデルへ、3 つ目で止める。印は `agent.tool_calls`（`kind=watch`・承認の答えは `kind=cx-approval`）に残る。

```bash
# 締め付けの probe（箱の中で・ログイン不要・中継と同じ物差し）
docker exec -i carelink-agent-sandbox python3 - <<'EOF'
import importlib.util, json, subprocess
s = importlib.util.spec_from_file_location("cc", "/srv/agent/relay/codex_config.py"); cc = importlib.util.module_from_spec(s); s.loader.exec_module(cc)
p = subprocess.Popen(cc.codex_argv("codex"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd="/srv/agent",
                     env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/agent", "CODEX_HOME": "/home/agent/.codex"})
def call(i, m, prm):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": prm}) + "\n"); p.stdin.flush()
    while True:
        o = json.loads(p.stdout.readline())
        if o.get("id") == i and "method" not in o: return o.get("result") or {}
call(1, "initialize", {"clientInfo": {"name": "probe", "version": "0"}, "capabilities": {"experimentalApi": True}})
p.stdin.write('{"jsonrpc":"2.0","method":"initialized"}\n'); p.stdin.flush()
print("account:", (call(2, "account/read", {"refreshToken": False}).get("account") or {}).get("type"))
cfg = call(3, "config/read", {"cwd": "/srv/agent"}).get("config")
print("lockdown problems:", cc.lockdown_problems(cfg, call(4, "experimentalFeature/list", {"limit": 200}).get("data")))
EOF
#   → account: chatgpt（ログイン前は None）／lockdown problems: [] が正しい
```

**`apiKeySource: none` の確かめ方（ログインの直後に必ず）**
```bash
# 3) 1 回だけ問いを流し、最初の行（system/init）の apiKeySource と model を見る（none・claude-opus-… が正しい）
docker exec -i -u 10002:10000 -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude -e DISABLE_AUTOUPDATER=1 \
  carelink-agent-sandbox sh -c 'printf "%s\n" "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"1+1\"}]}}" \
  | claude -p --input-format stream-json --output-format stream-json --verbose --model opus \
      --settings /opt/runner/isolation.json --strict-mcp-config --mcp-config /opt/runner/empty-mcp.json' \
  | head -1 | grep -o '"apiKeySource":"[^"]*"\|"model":"[^"]*"'
#   → "apiKeySource":"none" と "model":"claude-opus-…"。ANTHROPIC_API_KEY などが出たら止めて PO へ（箱の環境を確かめる）
# 4) 最後の行が "is_error":false なら、ログインできている（"Not logged in" なら 1) をやり直す）
```
- 画面からの確かめ: 相談を 1 つ送り、返事が来ること。`auth_source` のエラーが出たら apiKeySource が none ではない（中継が止めた）。
- 手元（Docker Desktop・2026-10-12）でログインなしの箱を runner 経由で起こした時、init は `apiKeySource: none` で通り、そのあと `Not logged in · Please run /login` → 画面のイベントは `error code=login`（「AI へのログインが切れています」）になることを確かめた。

### 3-9. 中継（agent/relay）の環境変数の約束

compose と秘密ファイルの名前のまま中継が読む（`agent/relay/config.py` の先頭に一覧）。

| 名前（どこから） | 中継での意味 |
|---|---|
| `AGENT_RELAY_ENABLED`（`agent.*.env` → compose） | 全体の止め。0 なら公開口は `healthz` 以外 404。本番は P9 まで 0・staging は 1 |
| `AGENT_RUNNER_SOCKET=/run/agent/runner.sock`（compose） | これがあると CLI は箱の runner に起こしてもらう（`launch=runner`）。無いと中継が直接起こす（手元の試験だけ） |
| `AGENT_SANDBOX_SUBNET`（compose） | 内側口 :8091 はこの網からだけ・公開口 :8090 はこの網以外からだけ受ける |
| `AGENT_RELAY_DSN`・`AGENT_RO_DSN`・`AGENT_DUMP_DSN`（秘密） | 会話の記録（`agent` スキーマ）・読み取り SQL（psql）・バックアップ（pg_dump） |
| `AGENT_BACKEND_IDENTIFIER`・`AGENT_BACKEND_PASSWORD`（秘密） | エージェント専用の管理者（中継の中の rk が反映に使う） |
| `AGENT_URL_SIGNING_KEY`（秘密） | 印刷用の署名つき URL（5 分） |
| `AGENT_ALLOWED_USER_IDS`（秘密） | 試験中に使える管理者の user id（カンマ区切り） |
| `AGENT_RELAY_SERVICE_TOKEN`（秘密） | サーバーどうしの口 :8092（backend → カルテの下書き）の合言葉。backend の `.env` の `AGENT_RELAY_SERVICE_TOKEN` と同じ値。無ければその口は全部 404 |
| `AGENT_RELAY_SERVICE_PORT=8092`（compose）・`AGENT_KARTE_TIMEOUT`（既定 90 秒） | サーバーどうしの口（ホストへ出さない・箱の網からは 404）・下書き 1 件の打ち切り |
| `AGENT_PUBLIC_ORIGIN`・`AGENT_BACKEND_URL`・`AGENT_PUBLIC_PORT`・`AGENT_INNER_PORT`・`AGENT_MAX_PROCS`・`AGENT_MIN_MEM_AVAILABLE_MB`・`AGENT_BACKUP_DIR`（compose） | POST を受ける Origin・`/auth/me` の確かめ先・口・同時の CLI・始める前の空きメモリ・バックアップの置き場所 |

- 健康確認は鍵なしの `GET /agent-relay/healthz`（`{"ok":true}`）。中継の作業の置き場所は volume `carelink-agent-relay-data`（`/data`・rk の `runs/` は利用者名入り）。

### 3-10. カルテの下書きを決めた管理者のサインインした AI の席で（2026-10-13・設計 `docs/plans/karte-via-seat-2026-10-13.md`）

1. 合言葉を作る（値はどこにも貼らない・git に入れない）: `openssl rand -hex 32`
2. 中継の秘密ファイルに 1 行: `/opt/carelink/secrets/agent-relay.env` に `AGENT_RELAY_SERVICE_TOKEN=<値>`（0400 root のまま。`set-role-passwords.sh` の作り直しで消えないよう、作り直したら足し直す）
3. backend の `.env`（`/opt/carelink/.env`）に `AGENT_RELAY_SERVICE_URL=http://carelink-agent:8092`・`AGENT_RELAY_SERVICE_TOKEN=<同じ値>`
4. 中継の image を作り直して立て直す（箱・出口は作り直し不要）: `dca build agent-relay && dca up -d agent-relay`
5. backend を作り直して立て直す（`docs/deployment` の手順どおり）。migration は無い
   - **Claude の席で作るときだけ**、箱（sandbox）の image も作り直す（runner の spawn の `purpose: "karte"` ＝ `claude -p --tools "" --no-session-persistence --max-turns 1`・空の作業フォルダー）: `dca build agent-sandbox && dca up -d agent-sandbox agent-relay`。古い箱は `purpose` を断る（`seat_unreachable` で Gemini に戻るだけ）。ChatGPT の席は箱の作り直し不要
6. 確かめ（backend のコンテナから・合言葉は env から読む）:
   `docker exec carelink-backend python -c "import os,httpx; r=httpx.get('http://carelink-agent:8092/svc/healthz', headers={'X-Rakusuke-Service-Token': os.environ['AGENT_RELAY_SERVICE_TOKEN']}); print(r.status_code, r.text)"` → `200 {"ok":true}`。
   合言葉なしは 401、ホストの `curl 127.0.0.1:8092` は届かない（ports に無い）、箱から（`docker exec carelink-agent-sandbox …`）は 404
7. 画面: 管理者が ⚙ →「カルテの下書きに使う席」で、AI（ChatGPT か Claude）にサインインしている管理者を選び、使う AI（自動／ChatGPT／Claude）を選ぶ
8. 画面: 「機能の切り替え」（/admin/feature-flags）で、試す拠点だけ「カルテの下書きをサインインした AI で」を ON
9. 録音 1 件で確かめる: 記録の「モデル」が `codex-seat / gpt-…-sol / seat-v1`（Claude なら `claude-seat / claude-opus-… / seat-v1`）になる。Gemini に戻ったときは `visit_recordings.error_kind` が `seat_<理由>`、`agent.admin_events` の `karte_draft` に `fallback=<理由>`

戻し方: 旗を OFF（すぐ Gemini だけに戻る）。口ごと止めるなら秘密ファイルから `AGENT_RELAY_SERVICE_TOKEN` を消して中継を立て直す（404 → backend は `seat_off` で Gemini）。

### 3-11. AI の席の割り当て（テスト期間・`docs/plans/agent-ui/per-user-seats-2026-10-13.md` §6）

- 既定では、割り当ての無い管理者は `AGENT_LEGACY_OWNER_USER_ID`（PO）の席で相談する。秘密ファイルにその行があることを確かめる。
- 中継の image を作り直して立て直す（`dca build agent-relay && dca up -d agent-relay`）。frontend も作り直す。migration は無い。
- migration 0095（`users.display_name`）を入れたら、`agent/deploy/sql/roles.sql` を流し直す（`agent_ro` に新しい列の権限を付ける）。流すまで ⚙ の名前はログイン ID。
- ⚙ →「AI の席の割り当て」で確かめる。割り当て先の席がサインアウトしたら、その席を使う人の相談は始まらない（「使う AI の席がサインインしていません。管理者に連絡してください」）。

## 4. メモリ（swap なし・守り）

| もの | 上限（compose） |
|---|---|
| 中継 | 256MB |
| 箱（CLI 1〜2 本＋rk の ortools） | 1GB（同時の CLI は中継が 1 本・runner の上限 2） |
| 出口 | 64MB |
| 合計 | 約 1.3GB（いまの available 約 2.5GB） |

- `preflight.sh` は available が **1,856MB**（上限の合計＋本番の余裕 512MB）未満なら NG。
- 中継は会話を始める前に `/proc/meminfo` の MemAvailable が **900MB** 未満なら始めない（`AGENT_MIN_MEM_AVAILABLE_MB`・設計 §2-7）。
- 上限に当たったコンテナは OOM で落ちて再起動する（本番の backend・postgres は巻き込まない）。`memswap_limit` = `mem_limit`。
- **staging（約 1.3GB）と本番の箱は同時に立てない**。staging を立てる前に `dca stop agent-sandbox agent-relay`。

## 5. Cloudflare の道（P8・ダッシュボード）

ローカルの `/etc/cloudflared/config.yml` は使われていない（§1）。ダッシュボードで足す:

1. https://one.dash.cloudflare.com → Networks → Tunnels → tunnel `ff143e2a-…` → Configure → **Public Hostname**
2. **先に今の `carelink` の 2 行の中身を控える**（スクリーンショット。Path の書き方・URL・追加の設定）。
3. **Add a public hostname**:

   | 項目 | 値 |
   |---|---|
   | Subdomain | `carelink` |
   | Domain | `kaipoke-api.net` |
   | Path | `^/agent-relay/.*`（今の `/api/v1` の行の Path が `api/v1/*` のような書き方なら、それに合わせて `agent-relay/*`） |
   | Type / URL | `HTTP` / `127.0.0.1:18201`（`localhost` は IPv6 の `::1` に向くことがある。compose は 127.0.0.1 にだけ出している） |
   | Additional application settings → HTTP | No Happy Eyeballs: On・Connect timeout 10s（今の行と同じ） |

4. **並び順**: この行を **`carelink`（Path なし）の行より上**へ（上から順に当てはめるため。下だと永久に当たらない）。`/api/v1` の行との前後はどちらでもよい。
5. 保存（30 秒ほどで効く）→ 確かめる:
   ```bash
   curl -sI https://carelink.kaipoke-api.net/                         # 今どおり（307/200）
   curl -s  https://carelink.kaipoke-api.net/api/v1/healthz           # 今どおり 200
   curl -sI https://kaipoke-api.net                                   # ほかのサービスも今どおり
   curl -s -o /dev/null -w '%{http_code}\n' https://carelink.kaipoke-api.net/agent-relay/healthz   # 200（Cloudflare Access が前にあるなら 302/403 = Access の画面）
   ```
   SSE が途中で溜められないか（設計 §7）は、この直後に PO と画面で確かめる。
6. **戻し方**: その行を消して保存（即時）。ほかの行は触らない。`curl -sI https://kaipoke-api.net` で既存サービスを必ず再確認。

ローカル管理に戻っている場合だけ（`journalctl -u cloudflared | grep "Updated to new configuration"` が出ず、ログの `ingressRule=` の番号が config.yml と合う）: `docs/deployment/cloudflared-config-fragment.yml` と同じやり方で、`/api/v1` の行の上に次を挿入し、`cloudflared tunnel ingress validate` → `systemctl restart cloudflared`。戻しは `config.yml.bak.*`。
```yaml
  - hostname: carelink.kaipoke-api.net
    path: ^/agent-relay/.*
    service: http://127.0.0.1:18201
    originRequest:
      noHappyEyeballs: true
      connectTimeout: 10s
```

## 6. 戻し方（本番）

```bash
# 1) 道を消す（§5-6）
# 2) コンテナを止めて消す（volume は残す＝ログイン情報・作業フォルダー）
dca down
# 2') 全部消す（ログイン情報・会話の作業フォルダーも）
dca down -v && docker image rm carelink-agent-relay carelink-agent-sandbox carelink-agent-egress
# 3) DB のロール（中継を止めてから）
docker exec -i carelink-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < agent/deploy/sql/roles-rollback.sql
shred -u /opt/carelink/secrets/agent-relay.env
# 4) スキーマ agent（会話の記録）を消すのは migration 0091 の downgrade（PO 了承・pg_dump の後）
```
本番の `carelink-postgres`・`carelink-backend`・`carelink-frontend` は、どの手順でも再起動しない。

## 7. staging で中継を立てる（`agent/staging/` と一緒に）

```bash
cd /opt/carelink/agent/staging && bash stg-up.sh                                   # 試験の backend と DB（網 carelink-stg）
docker exec -i stg-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < ../deploy/sql/roles.sql
bash ../deploy/set-role-passwords.sh staging --apply                                # → /opt/carelink/agent-staging/agent-relay.env
bash stg-create-agent-user.sh                                                       # 出たパスワードを上のファイルの AGENT_BACKEND_* へ
install -d -o 10001 -g 10001 -m 0700 /opt/carelink/agent-staging/backups
cd /opt/carelink
docker compose -p carelink-agent -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.prod.env stop   # 本番の箱を止める（立てていれば）
bash agent/deploy/preflight.sh staging
alias dcs='docker compose -p carelink-agent-stg -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.stg.env'
dcs up -d          # stg-agent（127.0.0.1:18211）・stg-agent-sandbox・stg-agent-egress。restart: no
```
- 画面は PC の手元の Next.js から SSH のポート転送で: `ssh -L 18101:127.0.0.1:18101 -L 18211:127.0.0.1:18211 root@72.60.211.213`。
- 箱の CLI のログインは staging の volume（`stg-agent-home`）に別に要る（§3-8 のコンテナ名を `stg-agent-sandbox` に）。
- 片付け（その日のうちに）: `dcs down -v` → `bash agent/staging/stg-down.sh` → `rm -rf /opt/carelink/agent-staging`（秘密ファイル・試験のバックアップ＝利用者名入り）→ 本番の箱を戻すなら `dca start`。

## 8. 確かめていないこと（段 3-0 の実測で埋める）

- 出口の許可リストが十分か: Claude は公式の一覧（code.claude.com/docs/en/network-config）から `api.anthropic.com`・`claude.ai`・`platform.claude.com`（OAuth の交換と更新）だけに絞った。`claude.com`（ログインのページはブラウザー側で開くので箱には不要の見込み）・`mcp-proxy.anthropic.com`（`ENABLE_CLAUDEAI_MCP_SERVERS=false` で不要）・`downloads.claude.ai`（自動更新を止めた）・Datadog（`DISABLE_TELEMETRY`）は入れていない。**Codex（2026-10-12 実測）**: `auth.openai.com`＝**確認済み**（0.162.1 の `codex login --device-auth` を記録つきのプロキシ越しに起こし、つないだのはここだけ。トークンの更新 `/oauth/token` も実行ファイルの文字列でここ）、`chatgpt.com`＝**確認済み**（0.153.4・ChatGPT ログインで 1 ターンをプロキシ越しに流し、つないだのはここだけ・`/backend-api/codex`）。`api.openai.com` は **外した**（API キーの道でだけ使う・ChatGPT ログインの会話では 0 回）。`ab.chatgpt.com`（OTLP の計測）は入れない（`otel.*=none`）。箱の中の 0.162.1 でのログイン後の通信は staging で `docker logs stg-agent-egress` を見て確かめる（未確認）。足りなければ断られたホストを見て、PO の了承で足す。
- 手元（Docker Desktop）で確かめ済み: 箱と出口の image の build、箱の中で uid 10002・`claude 2.1.296`・`codex-cli 0.162.1`・ortools 9.15、`/srv/agent`・`/usr/local` は書けない、直接の外向き通信は不可（Network is unreachable・名前が引けない）、出口は許可リスト外を 403、runner は箱の uid を切る、ログインなしの `claude -p` は「Not logged in」で終わり `~/.claude` と `/tmp` にだけ書いた。ログイン後の実際の会話（stream-json・`--permission-prompt-tool stdio`）は未確認。
- ~~codex 0.162.1 に `app-server` があるか~~ → ある（2026-10-12・箱の 0.162.1 で initialize・account/read・config/read・experimentalFeature/list・thread/start まで通した）。`-c` の上書きは config/read に効いて見える（締め付けの問題 0）。Rust の CLI は `HTTPS_PROXY` を見る（記録つきプロキシに CONNECT が来た）。
- `claude auth status`／`claude auth login` が今の版にあるか（無ければ対話の `/login` だけを使う）。
- 箱の中（Linux・0.162.1）でログインした後の本物の会話（Sol の選び方・質問カード・rk → 内側口）。手元（Windows・0.153.4）では通した。
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` の下での `rate_limit_event` の出方。
- Docker の userns-remap を使っていないこと（使うと SO_PEERCRED の uid がずれる。今は未使用の見込み）。

## 9. 守りの要点と残る危険（レビュー用）

- 箱の中のモデルが Bash で何をしても: backend・Postgres・ホストのポート・ほかのコンテナに届かない（網が internal・名前が引けない）。外へは許可リストの 443 だけ。本番 `.env`・docker.sock・らく助のトークン・DB のパスワードは箱に無い。
- 残る危険: (8) **1 つの箱に何人分ものログイン情報（人ごとの席・2026-10-13〜）**: 席は `/home/agent/u/<鍵>/`（0700・持ち主 10002）。モデルのコマンド（実行係 10004）は `/home/agent` に入れないので、だれの席も読めない（`isolation_e2e.sh` で、持ち主の席・別の人の席・前の共有の置き場の 3 つを確かめる）。runner は中継から鍵だけを受け（パスは受けない・形が違えば断る）、会話はいつも持ち主の席で起こす。**残る穴**: CLI 自身は全員 uid 10002 なので、ある人の会話の CLI の道具（Claude の Read など）が別の人の席を指すのを止めているのは (1)-(a) と同じ `permissions.deny`（`//home/agent/**`）＋中継の許可の返事＋リンク消し。席ごとに uid を分ける（人ごとに uid を作る）のは、人数が増えたら検討（段 4）。サインアウトしても席のフォルダー（会話の写し `.claude/projects` など）は残る＝§10 の片づけが席ごとに 7 日で消す。人が辞めたら、その人の席をサインアウト → PO が `rm -rf /home/agent/u/<鍵>`。
- 残る危険: (1) **認証情報の隔離（2026-10-12 実装）**: ログイン情報は CLI の uid 10002 だけが読め、モデルのコマンド（claude の Bash・codex のシェル/ファイル操作）は別 uid 10004 の実行係で走るので `cat` では読めない（`tests/isolation_e2e.sh` で実証）。**残る穴**: (a) Claude の Read/Edit/Grep/Glob は CLI の uid で動く。`permissions.deny`＋中継の許可の返事で断り、さらに execd が work の外を指すリンクを消す（試験あり）。リンクを張ってから消えるまでの最大 2 秒の隙間と、リンクを使わず別の道で CLI の道具に /home/agent を指させる手は、本物のモデルでは未確認（staging で）。(b) CLI の版を上げるたびに `isolation_e2e.sh` を流す（CLI 内の道具が実行係に出ない版が出ていないか）。(c) 実行係の uid 10004 のコマンドは、同じ箱の別の会話の `RK_RELAY_TOKEN`（`/proc/<pid>/environ`）も読める＝会話をまたいで中継の内側口を使える（同時 2 会話・どちらも管理者の相談。承認番号は画面からしか出ないので、書き込みの関所は破れない）。(d) 箱の root の親プロセスは SETUID/SETGID/SETPCAP を持つが、何も待ち受けず別 uid からは触れない。(e) 本物の OAuth ログインの `.credentials.json` では未確認（偽のモデル＋ダミーの認証ファイルで実証）。(2) 出口のコンテナは普通の網にいるので、ホストの 0.0.0.0 に出ているほかのアプリ（n8n 5678・couchdb 5984 など）へは届く。ただし tinyproxy は許可リストのホスト名の 443 だけをつなぐので、箱からは使えない（IP 直書きは許可リストに当たらない）。(3) runner が箱の中から返す行は中継にとって信用しない入力（中継の担当: 1 行の大きさ・JSON の形を検査）。(4) 中継が乗っ取られた場合は `agent_dump`（全部読める）と管理者トークンが漏れる（§3-4 の絞る案）。(5) `/home/agent/.claude/projects/`（人ごとの席では `/home/agent/u/*/.claude/projects/`・`.codex/sessions`）に会話の写し（利用者名入り）が残る → 箱の片づけ（`retention.py sandbox`・7 日）が共有の置き場と各席の両方を見る。(6) Codex: `~/.codex/auth.json` は (1) の隔離で、モデルのコマンドからは読めない。見張り（`relay/watch.py`）は引き続き拾って記録する。(7) Codex のコマンドは聞かずに動く（計算は自由の決まり）。守りは網・鍵なし・中継の承認番号で、承認の問い合わせや見張りは道案内と記録。

## 10. 保存期間と片づけ（PO 決定 2026-10-12・`cleanup/`）

**お客様には出さない**: 片づけはサーバーの裏の仕事。アプリの画面にもエージェントの返事にも出さない。記録はサーバーのログ（`/var/log/carelink/agent-cleanup.log`・`app-backup-prune.log`）と作業の記録（journal）だけ。

| もの | 期間 | どこ |
|---|---|---|
| 案・実行の記録（JSON） | 1 年 | 中継 `/data/runs/<run-id>/`・`/data/runs/plans/`・会話の記録 DB の plans/approvals/runs |
| 会話・イベント | 90 日 | DB の events・tool_calls（案も実行も無い会話は会話ごと。1 年使っていない会話は全部） |
| 反映前の pg_dump `agent-*.sql.gz` | 14 日・**新しい 5 個は必ず残す**（置き場所ごと） | `/opt/carelink/backups/agent/`・`/opt/carelink/backups/` |
| CLI の会話の写し・作業フォルダー | 7 日 | 箱 `~/.claude/projects`・`~/.codex/{sessions,log,logs}`・`/data/sessions/<id>/`、中継 `/data/reports`・`/data/sessions` |
| 作業の記録（journal） | **ずっと** | 中継の data volume `journal/work-journal.jsonl`（中継を立てる前は `/var/lib/carelink-agent/journal/`） |
| アプリの `daily-*.sql.gz` | 30 日＋各月の最初の 1 個を 12 か月 | `/opt/carelink/backups/` |
| アプリの `pre-deploy-*`・`pre-*` | 60 日 | 〃（種類ごとに新しい 3 個は必ず残す。`pre_*`・`predeploy_*`・フォルダーなどは触らない） |

- journal は 1 行 1 件（UTC の時刻・user id・会話 id・種類・案の id・操作の数・了承の有無・反映の数・結果、片づけは消した件数とバイト数）。**利用者さん・職員の名前は入れない**（英数字の値だけを通す・`rk/journal.py`）。反映（`rk apply`・画面の承認）のたびに `rk/applyrun.py` が 1 行書く。
- 決まりの本体は `relay/retention.py`（試験 `relay/tests/test_retention.py`）。どの片づけも**既定は表示だけ（dry-run）**で、消すのは `--apply` のときだけ。時刻はすべて UTC。
- タイマー（UTC）: アプリのバックアップ 18:30（毎日のバックアップ 17:30 の後）・エージェント 18:45・ディスクの見張り 毎時 15 分（空きが **10GB 未満**のときだけ片づけを早めに動かす。アプリのバックアップは、そのタイマーが有効なときだけ早める）。

**入れ方（1 コマンド・root・タイマーはまだ有効にしない）**
```bash
bash /opt/carelink/agent/deploy/cleanup/install.sh
```
unit を置き、両方の片づけを dry-run で 1 回動かして、消す候補の一覧（名前・大きさ・理由）を `/var/log/carelink/*-dryrun-<UTC>.txt` に残す。**director が一覧を見て了承してから** `bash /opt/carelink/agent/deploy/cleanup/install.sh --enable`（止めるのは `--disable`）。
- 手で: `bash agent/deploy/cleanup/prune-app-backups.sh`（表示だけ）／`--apply`、`bash agent/deploy/cleanup/agent-cleanup.sh [--apply] [--env stg]`。
- 2026-10-12 の一覧（読むだけで取得・手元で計算）: アプリのバックアップは pre-deploy 180 個・pre 30 個・約 273MB が消す候補。daily は 6 個とも残る。agent-* は 1 個で消さない。

**残っていること**
1. 毎日のバックアップ（`docs/deployment/scripts/backup-carelink-db.sh`）は更新済み（30 日＋各月の最初の 1 個を 12 か月・UTC。`prune-app-backups.sh` と同じ決まりなので二重に消しても食い違わない）。**VPS への入れ方**（cron は `/etc/cron.d/carelink-backup` がこの path を直接呼ぶので、ファイルを置き換えるだけ・cron の書き換えは不要）:
   ```bash
   cd /opt/carelink && git pull          # develop 取り込み後
   bash -n docs/deployment/scripts/backup-carelink-db.sh && chmod +x docs/deployment/scripts/backup-carelink-db.sh
   cat /etc/cron.d/carelink-backup       # 呼んでいる path がこのファイルか確かめる
   ```
   次の 17:30 UTC の実行から新しい決まりになる（すぐ試すなら手で 1 回実行。ダンプが 1 個増える）。入れ替えるまでは従来どおり 7 日で消える。
2. ~~案の HTML を DB に入れている~~ → **直した（2026-10-12）**: `plans.html_a4`・`html_a3` 列を無くし（0091 は未適用なので書き換え）、`GET …/plans/{v}/html` と署名つきの印刷 URL は保存した案の JSON から開くたびに描く（`RelayService.plan_html`）。A3 は時間割のある案だけ。
3. ~~中継の状態に「空きが少ない」を出す~~ → **済み（2026-10-12）**: `GET /agent-relay/engine` の `disk`（`free_gb`・`low`・`blocked`・`warning`）。空き **5 GB 未満**は新しい相談・新しい CLI を起こさず、穏やかな日本語で断る（503・作業中の相談は続く）。10 GB 未満は警告だけ。パスは返さない。
4. 中継を立てる前に `/var/lib/carelink-agent/journal/` に書いた分は、立てた後に volume の journal の頭に足し合わせる（`cat` で連結・持ち主 10001）。
