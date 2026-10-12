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
| 使っている網の範囲 | 172.17〜172.29/16・10.78.0/24・10.79.0/24。箱の網に **172.31.250.0/24（本番）・172.31.251.0/24（staging）** を使う（空き） |
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
docker exec $S python3 -c "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/agent/runner.sock'); s.send(b'{\"op\":\"status\"}\n'); print(s.recv(100))"
```

### 3-8. CLI のログイン（P7・PO が自分の端末で・対話）

**決まり: 箱の CLI はサブスク（Claude アカウント）のログインだけで動かす。API キーは使わない。**
- 箱の環境に `ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`ANTHROPIC_BASE_URL`・`OPENAI_API_KEY`・`OPENAI_BASE_URL`・`CODEX_API_KEY` などは入れない。三重に止める:
  1. compose の箱（`agent-sandbox`）に `env_file` を書かない・`environment` にも足さない。箱の healthcheck は、これらが環境にあると **unhealthy**（中継は `depends_on: service_healthy` なので起きない）。
  2. runner（`runner/agent_runner.py` の `FORBIDDEN_ENV`）が起動時に自分の環境から消し、CLI の環境は許可リストで一から作る（入っていたら CLI を起こさない）。
  3. CLI の `--settings`（`runner/isolation.json`）で `ANTHROPIC_*`・`CLAUDE_CODE_USE_*` を空・`forceLoginMethod: claudeai`・`apiKeyHelper: ""`。
- 中継は毎ターンの `system/init` の `apiKeySource` が **`none`（サブスクのログイン）のときだけ**続ける。ほかの値・項目が無いときは会話を止め、画面に「思っていない方法で AI につながろうとしたので止めました」を出す（`error` の `code: auth_source`）。`AGENT_ALLOWED_KEY_SOURCES` に別の値を書いても段 3 では無視する。

**ログインのしかた（PO 本人・1 回だけ・ログイン情報は volume `carelink-agent-home` の `/home/agent/.claude/.credentials.json` に残る）**

Claude Code の画面から PO が `!` で打つ（`-t` が要る）。URL が出たら自分の PC のブラウザーで開き、出たコードを貼る。
```bash
# 1) 対話の claude を箱の中で開き、/login →「Claude アカウント（サブスク）」を選ぶ → 終わったら /exit
! ssh -t root@72.60.211.213 docker exec -it -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude -e DISABLE_AUTOUPDATER=1 carelink-agent-sandbox claude
# 2) 状態を見る（版によっては auth status が無い。その時は 3) だけで確かめる）
! ssh -t root@72.60.211.213 docker exec -it -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude carelink-agent-sandbox claude auth status
```
- staging は同じことをコンテナ名 `stg-agent-sandbox` で（volume `stg-agent-home` は本番と別）。
- `docker exec` は箱の利用者（uid 10002）で入るので、ログイン情報の持ち主も 10002 になる（runner が起こす CLI と同じ）。`-u root` を付けない。
- 別案 `claude setup-token`: 長期の OAuth トークンを**表示するだけ**（保存しない）。段 3 の runner はこのトークン（`CLAUDE_CODE_OAUTH_TOKEN`）を CLI に渡さないので、**段 3 では `/login` を使う**。setup-token は段 4 の「ログイン情報をモデルから読めない所に置き、runner がトークンだけ渡す」形で使う（設計 §2-1）。

**ChatGPT（Codex）のログイン — 既定の AI（PO 2026-10-12: お客様は ChatGPT の契約が多い）**

決まり: ChatGPT の契約（Plus・Pro・Business など）のログインだけ。`OPENAI_API_KEY`・`codex login --with-api-key` は使わない（中継が `account/read` で `type: chatgpt` 以外なら会話を始めない＝画面に「ChatGPT のログイン以外の方法で…止めました」）。
ログイン情報は volume `carelink-agent-home` の `/home/agent/.codex/auth.json`（持ち主 10002）。

```bash
# 1) デバイスコードでログイン（codex-cli 0.162.1 で --device-auth があることを実測・2026-10-12）
! ssh -t root@72.60.211.213 docker exec -it -e HTTPS_PROXY=http://agent-egress:3128 -e CODEX_HOME=/home/agent/.codex carelink-agent-sandbox codex login --device-auth
#    → 画面に「https://auth.openai.com/codex/device」と 1 回限りのコード（XXXX-XXXXX・15 分）が出る。
#      スマホか PC のブラウザーでその URL を開き、ChatGPT（PO のアカウント）でログイン → コードを入れる → 許可。
#      ターミナルに「Successfully logged in」が出たら終わり。
# 2) 確かめ（"Logged in using ChatGPT" が正しい。"Not logged in" なら 1) をやり直す。API キーと出たら PO へ）
! ssh -t root@72.60.211.213 docker exec -it -e CODEX_HOME=/home/agent/.codex carelink-agent-sandbox codex login status
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
docker exec -i -e HTTPS_PROXY=http://agent-egress:3128 -e CLAUDE_CONFIG_DIR=/home/agent/.claude -e DISABLE_AUTOUPDATER=1 \
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
| `AGENT_PUBLIC_ORIGIN`・`AGENT_BACKEND_URL`・`AGENT_PUBLIC_PORT`・`AGENT_INNER_PORT`・`AGENT_MAX_PROCS`・`AGENT_MIN_MEM_AVAILABLE_MB`・`AGENT_BACKUP_DIR`（compose） | POST を受ける Origin・`/auth/me` の確かめ先・口・同時の CLI・始める前の空きメモリ・バックアップの置き場所 |

- 健康確認は鍵なしの `GET /agent-relay/healthz`（`{"ok":true}`）。中継の作業の置き場所は volume `carelink-agent-relay-data`（`/data`・rk の `runs/` は利用者名入り）。

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
- 残る危険: (1) 箱の中の CLI のログイン情報はモデルから読める（持ち出し先は Anthropic/OpenAI だけ）。(2) 出口のコンテナは普通の網にいるので、ホストの 0.0.0.0 に出ているほかのアプリ（n8n 5678・couchdb 5984 など）へは届く。ただし tinyproxy は許可リストのホスト名の 443 だけをつなぐので、箱からは使えない（IP 直書きは許可リストに当たらない）。(3) runner が箱の中から返す行は中継にとって信用しない入力（中継の担当: 1 行の大きさ・JSON の形を検査）。(4) 中継が乗っ取られた場合は `agent_dump`（全部読める）と管理者トークンが漏れる（§3-4 の絞る案）。(5) `/home/agent/.claude/projects/` に会話の写し（利用者名入り）が残る → 中継の掃除（90 日）で消す対象に入れること。(6) Codex: `~/.codex/auth.json` は CLI と同じ uid なので、モデルが動かすコマンドからも読める（Codex の砂場は Docker の中で動かない・ファイルの権限では分けられない）。見張りが読みを見つけたらその場で止めて記録するが、出力はもうモデルに渡っている。持ち出し先は出口の許可リスト（OpenAI/Anthropic）だけ。段 4 で「ログイン情報を別 uid に置く」案を検討。(7) Codex のコマンドは聞かずに動く（計算は自由の決まり）。守りは網・鍵なし・中継の承認番号で、承認の問い合わせや見張りは道案内と記録。

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
2. 案の HTML が会話の記録 DB に入っている（`plans.html_a4`・`html_a3`・`relay/service.py` が案ごとに作って保存、`relay/public.py` が返す）。決まりは「HTML は保存しない・JSON から都度作る」。`service.py`・`public.py`・`db.py`（と migration 0091 の列）を直す必要があり、ここでは直していない。
3. 中継の状態に「空きが少ない」を出す: `relay.retention.disk_status("/data")` の `low`・`warning` を中継の状態（`/healthz` か会話の一覧の応答）に足す（`public.py`・別担当）。
4. 中継を立てる前に `/var/lib/carelink-agent/journal/` に書いた分は、立てた後に volume の journal の頭に足し合わせる（`cat` で連結・持ち主 10001）。
