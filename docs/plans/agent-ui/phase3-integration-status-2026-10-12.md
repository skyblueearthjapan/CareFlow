# らく助エージェント 段 3 — 組み合わせの状況（P3-A 中継 × P3-B 画面 × P3-C 置き方）2026-10-12

正典: `phase3-implementation-design-2026-10-12.md`（PO 了承済み・モデルは Opus／パーミッションは ② だけ）。
この文書は「3 つを合わせた結果」と「staging に置く前の残り」と「staging に置く手順」をまとめる。**本番には何もしていない。VPS も触っていない。**

---

## 1. 合わせたこと（約束の正はどこか）

| 約束 | 正 | 合わせた内容 |
|---|---|---|
| 画面 ⇄ 中継の道・中身 | `agent/relay/public.py` の先頭の一覧 | 画面（`frontend/components/agent/relay.ts`）を中継に合わせた。中継に足したもの: `GET /agent-relay/healthz`（鍵なし）、`PATCH /sessions/{id}`（名前・☆）、`plan_ref`（案の版の番号）つきの送信、`POST …/plans/{v}/print-url` と `GET /agent-relay/print/…`（5 分・その人・その版だけの HMAC 署名）、一覧の `needs_you`、`approval.request` イベント（設計 §1-5 の表にあるもの）、元に戻すの `approval.result` に `run_id` |
| 承認番号（ap_…） | 中継 | **画面には返さない・流さない**。`approve` の返事は `{ok, version, expires_at}` だけ。番号は中継が CLI に直接送る（試験で「SSE にも返事にも ap_ が無い」ことを確かめる） |
| 画面用の共通イベント | `agent/relay/events.py` | 画面の型・reducer・mockRelay を同じ形に（message に id が無い・`progress.text`・`error.code/message`・`run.*` の項目・`question.answered.answers` は {質問: 答え}・`turn_id` は数字・状態 `queued`）。中継の金型（`tests/fixtures/*.expected.json`）を画面の試験がそのまま読む |
| つなぎ直し | 中継 | `GET …/events?after=<seq>`（`id:` は seq・15 秒ごとに `: ping`・トークンの期限で中継が閉じる → 画面が新しいトークンで続きから） |
| 中継 ⇄ 置き方 | `agent/deploy/docker-compose.agent.yml`・`set-role-passwords.sh` | 中継が compose と秘密ファイルの名前をそのまま読む（`AGENT_PUBLIC_PORT`・`AGENT_RELAY_DSN`・`AGENT_RO_DSN`・`AGENT_BACKEND_IDENTIFIER` など。一覧は `agent/relay/config.py` の先頭と `agent/deploy/README.md` §3-9） |
| CLI の起こし方 | 中継の設定 | `AGENT_RUNNER_SOCKET` があれば **箱の runner に unix ソケットで頼む**（`RunnerSession`）。無いと中継が直接起こす（手元の試験だけ）。`AGENT_RELAY_LAUNCH=direct|runner` で明示もできる |
| 網の分け方 | 中継 | `AGENT_SANDBOX_SUBNET` があれば、内側口 :8091 は箱の網からだけ、公開口 :8090 は箱の網以外からだけ受ける |
| 箱の鍵 | runner・compose・中継 | `ANTHROPIC_API_KEY`・`ANTHROPIC_BASE_URL`・`OPENAI_API_KEY` など（`FORBIDDEN_ENV`・中継と runner で同じ一覧）を CLI に渡さない。runner は起動時に自分の環境からも消す。箱の healthcheck はこれらがあると unhealthy。中継は `apiKeySource` が `none` のときだけ続ける（項目が無い・API キー・ヘルパーは止める。設定で他の値を書いても無視） |
| 画面の約束文 | `agent/relay/relay-prompt.md` | 中継の direct と箱の runner が同じファイルを `--append-system-prompt-file` に渡す |
| staging の画面 | 画面・中継 | 画面は `NEXT_PUBLIC_AGENT_RELAY_BASE`（例 `http://127.0.0.1:18211/agent-relay`）で別オリジンの中継へ。中継は `AGENT_PUBLIC_ORIGIN` の Origin にだけ CORS を返す（本番は同じオリジンなので使わない） |

ほかに: 中継の作業の置き場所 `/data` を volume `<頭>-agent-relay-data` に（image の中で持ち主 10001 にしておく）、全体の止め `AGENT_RELAY_ENABLED`（本番 0・staging 1）を compose に。

## 2. 手元で通したこと（2026-10-12・Docker Desktop）

| # | 何を | 結果 |
|---|---|---|
| L1 | Python の試験 `PYTHONPATH=agent python -m pytest agent/tests agent/relay/tests -q -p no:asyncio` | **317 件すべて通る**（組み合わせの試験 `agent/relay/tests/test_integration.py` 13 件を足した） |
| L2 | 画面の試験 `cd frontend && pnpm typecheck && pnpm vitest run components/agent` | 型エラー 0・**41 件すべて通る**（中継の金型を読む試験・HttpRelay の道と中身の試験を足した） |
| L3 | `Dockerfile.relay` の build（`agent/relay/requirements.txt` の版で） | 通る。fastapi 0.115.12・psycopg 3.2.6・psql/pg_dump 16.15 |
| L4 | 中継の container をダミーの環境で起こして `GET /agent-relay/healthz` | 200 `{"ok":true}`。止めている時（`AGENT_RELAY_ENABLED=0`）は他の道が 404。compose の healthcheck のコマンドも 200 |
| L5 | `Dockerfile.sandbox`・出口の build | 通る（箱 1.7GB） |
| L6 | 中継の image の中で本物の公開口・内側口（uvicorn）＋ FakeSession（取った stream-json）→ curl | 作る → 送る → SSE（`user.message`→`status`→`question`→答える→`question.answered`→`message`→`turn.end`…18 件）→ `?after=10` で 11 から続く → 内側口から案を出す → `plan`・`approval.request`・`waiting_approval` → 承認 → `approval.result`・`applying`。**SSE に ap_ は 0 件**、番号は DB の承認の表と CLI への文にだけ。案の HTML 200、印刷用 URL は鍵なしで 200＋CSP sandbox、署名を変えると 403。内側口は合言葉違いで 401、書き込みの POST は 403 |
| L7 | compose 一式（中継・箱・出口）を手元の網で立て、**runner 経由**で本物の claude 2.1.296（ログインなし）を起こす | 3 つとも healthy。中継（uid 10001）→ unix ソケット → runner（SO_PEERCRED）→ claude。init は `apiKeySource: none` で通り、`Not logged in · Please run /login` → 画面のイベントは `error code=login`。止めると runner がプロセスを終える |
| L8 | 網の分け方（L7 の一式で） | 箱 → 中継の公開口 :8090 = 404、箱 → 内側口 :8091 = 401（合言葉なし）、backend の網 → 内側口 = 403、箱の環境に `ANTHROPIC_*`/`OPENAI_*` は 0 件 |

## 3. staging に置く前の残り（まだ確かめていない・作っていないこと）

| # | 残り | 重さ | 次の手 |
|---|---|---|---|
| G1 | **ログインした本物の claude との会話**（`--permission-prompt-tool stdio` の質問カード・道具の自動許可・`--resume`・rate_limit）は手元で未確認。L7 はログインなしまで | 大 | staging の箱で PO がログイン（手順 S9）→ 読むだけの質問 → 質問カード → 案 → 承認の順で確かめる |
| G2 | 箱の中の rk（`RK_MODE=relay`）→ 中継の内側口 `http://agent-relay:8091` の本物の往復（GET の関所・`rk sql`・`rk plan submit`・`rk apply --approved`）。試験は偽の中継だけ | 中 | G1 と同じ会話で `rk whoami`・`rk sql staff_roster` から |
| G3 | 会話の記録を Postgres の `agent` スキーマ（`AGENT_RELAY_DSN`・psycopg）に置く動き。手元は SQLite だけ | 中 | staging で migration 0091 → roles.sql → 中継を立て、`agent.events` に行が増えることを見る（S4・S12） |
| G4 | 機能の旗の判定（`office_flag_gate` が `agent_ro` で psql）。旗が無いと公開口は 404 | 小 | staging の DB で `rakusuke_agent` を有効にする（S7） |
| G5 | 出口の許可リストでログイン後の通信が足りるか（`api.anthropic.com`・`claude.ai`・`platform.claude.com` だけ） | 中 | S9 の後 `docker logs stg-agent-egress` で断られたホストを見る |
| G6 | 保存期間の掃除（events・tool_calls 90 日／plans・approvals・runs 1 年）と `/home/agent/.claude/projects/` の会話の写しの掃除は**まだ作っていない** | 中 | 本番の前に中継の定時処理として足す（staging では不要） |
| G7 | Cloudflare の道で SSE が溜められないか（本番だけで確かめられる） | 小 | 本番の P8 の直後に PO と |
| G8 | Codex（段 3-5）: **作った・既定の AI にした**（§6）。残りは箱（Linux・0.162.1）でログインした後の本物の会話 | 中 | S10c・S12・S13 を Codex で |

## 4. staging に置く手順（ディレクター が VPS で順に流す・本番には触れない）

前提: VPS の `/opt/carelink` を `feature/agent-skills`（この組み合わせを含むコミット）にしてから。各段の「確かめ」が合わなければ、その場で止めて片付け（S14）へ。時刻は UTC。

| 段 | すること | 確かめ |
|---|---|---|
| S0 | `cd /opt/carelink && git fetch && git checkout <この組み合わせのコミット>`。本番の箱が立っていれば止める: `docker compose -p carelink-agent -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.prod.env stop`（立っていなければ何もしない） | `git log -1 --oneline` が目的のコミット。`docker ps --format '{{.Names}}' \| grep -c '^carelink-agent'` が 0。`free -m` の available が 2,300MB 以上 |
| S1 | 試験の backend と DB: `cd agent/staging && bash stg-up.sh` | 最後に healthz OK。`curl -s 127.0.0.1:18101/api/v1/healthz` が 200。`docker exec stg-postgres psql -U carelink -d carelink -Atc "select version_num from alembic_version"` |
| S2 | migration 0091 を試験の DB にだけ。stg-backend は**本番と同じ image（9111655・0091 を含まない）**なので、ファイルを写してから流す: `docker cp backend/alembic/versions/0091_agent_schema.py stg-backend:/app/alembic/versions/ && docker exec stg-backend alembic upgrade head`（写せない作りなら、`backend/` の今の image を build して stg-backend の image に使う。本番の image には触れない） | `docker exec stg-postgres psql -U carelink -d carelink -Atc "select version_num from alembic_version"` が `0091_agent_schema`、`\dt agent.*` に 6 表 |
| S3 | ロール: `docker exec -i stg-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < ../deploy/sql/roles.sql`（0091 の後なので表への権限も付く） | `docker exec -i stg-postgres psql -X -U carelink -d carelink < ../deploy/sql/verify-roles.sql` がすべて OK |
| S4 | パスワードと秘密ファイル: `bash ../deploy/set-role-passwords.sh staging --apply` | `/opt/carelink/agent-staging/agent-relay.env` が 0400・`AGENT_RO_DSN`/`AGENT_DUMP_DSN`/`AGENT_RELAY_DSN`/`AGENT_URL_SIGNING_KEY` の 4 行 |
| S5 | エージェント専用の管理者: `bash stg-create-agent-user.sh` → 出た identifier・password を S4 のファイルに `AGENT_BACKEND_IDENTIFIER=`・`AGENT_BACKEND_PASSWORD=` で書き足す。試験で使う管理者（PO）の user id を `AGENT_ALLOWED_USER_IDS=` に | `grep -c '^AGENT_' /opt/carelink/agent-staging/agent-relay.env` が 7。`chmod 400` のまま |
| S6 | バックアップ置き場: `install -d -o 10001 -g 10001 -m 0700 /opt/carelink/agent-staging/backups` | `stat -c '%u %a' /opt/carelink/agent-staging/backups` が `10001 700` |
| S7 | 機能の旗（試験の DB だけ）: 試験の backend に PO でログインして `POST /api/v1/office-feature-flags`（`feature_key: rakusuke_agent`・拠点 1 つ）。または `docker exec stg-postgres psql …` で同じ行 | `docker exec stg-postgres psql -U carelink -d carelink -Atc "select count(*) from office_feature_flags where feature_key='rakusuke_agent' and enabled_at is not null"` が 1 以上 |
| S8 | 立てる: `cd /opt/carelink && bash agent/deploy/preflight.sh staging` → NG 0 なら `docker compose -p carelink-agent-stg -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.stg.env up -d --build` | `… ps` で `stg-agent`・`stg-agent-sandbox`・`stg-agent-egress` が healthy。`curl -s 127.0.0.1:18211/agent-relay/healthz` が `{"ok":true}`。`docker logs stg-agent` の 1 行目が `launch=runner … enabled=1 sandbox_subnet=172.31.251.0/24`。`docker stats --no-stream` で合計 1.3GB 以下 |
| S9 | 箱の隔離（README §3-7 をコンテナ名 `stg-agent-sandbox`・出口 `agent-egress` で） | backend・Postgres・ホストのポートへ届かない／許可リストだけ通る／`env` に秘密 0 件／runner は箱の uid を切る |
| S10c | **ChatGPT のログイン（既定の AI・PO 本人）**: `docker exec -it -e HTTPS_PROXY=http://agent-egress:3128 -e CODEX_HOME=/home/agent/.codex stg-agent-sandbox codex login --device-auth` → 出た URL（`https://auth.openai.com/codex/device`）をスマホか PC で開き ChatGPT でログイン → 出たコードを入れる（README §3-8） | `docker exec -e CODEX_HOME=/home/agent/.codex stg-agent-sandbox codex login status` が `Logged in using ChatGPT`。README §3-8b の probe が `account: chatgpt`・`lockdown problems: []`。`docker logs stg-agent-egress` に断られたホストが無い（あれば PO の了承で足す） |
| S10 | （Claude も試すときだけ）CLI のログイン（PO 本人・README §3-8 を `stg-agent-sandbox` で）→ apiKeySource の確かめ | `"apiKeySource":"none"` と `"model":"claude-opus-…"`、最後の行 `"is_error":false`。`docker logs stg-agent-egress` に断られたホストが無い（G5） |
| S11 | 画面: PO の PC で `ssh -L 18101:127.0.0.1:18101 -L 18211:127.0.0.1:18211 root@72.60.211.213` を張ったまま、`frontend/.env.local` に `BACKEND_API_BASE_URL=http://127.0.0.1:18101`・`NEXT_PUBLIC_AGENT_RELAY_BASE=http://127.0.0.1:18211/agent-relay`・`NEXT_PUBLIC_AGENT_PILOT_USERS=<PO の user id>` を書いて `pnpm dev`（:3000）→ 試験の backend に PO でログイン | 右下に丸が出る。開くと一覧（空）。ブラウザーの開発ツールで `/agent-relay/sessions` が 200（CORS エラーが無い） |
| S12 | 読むだけの相談: 「来週の人数は足りていますか」を送る | 作業の行が出て返事が来る。`docker exec stg-postgres psql -U carelink -d carelink -Atc "select type, count(*) from agent.events group by 1"` に行。`agent.tool_calls` に GET/SQL の記録。`docker logs stg-agent` に本文が出ていない |
| S13 | 書く相談（e2e I9 と同じ週43）: 急な休み → 質問カードに答える → 案（A4/A3）→［この案で反映する］→ 反映 → 照合 →［元に戻す］ | 質問カードが PC とスマホ（同じ会話）で先に押した方だけ通る。案の欄が開き印刷用が新しいタブで開く。反映の前に `/opt/carelink/agent-staging/backups/agent-*.sql.gz` が増える。`agent.approvals` に `ap_…` が 2 行（apply・undo）・`consumed_at` 入り。画面の通信（開発ツール）に `ap_` が 0 件。試験の backend の `audit_logs.user_agent` に `approval=ap_…; approver=<PO の id>` |
| S14 | 片付け（その日のうちに）: `docker compose -p carelink-agent-stg -f agent/deploy/docker-compose.agent.yml --env-file agent/deploy/agent.stg.env down -v` → `bash agent/staging/stg-down.sh` → `rm -rf /opt/carelink/agent-staging` → 本番の箱を S0 で止めていたら `… agent.prod.env start` | `docker ps -a \| grep -c stg-` が 0。`docker volume ls \| grep -c stg-agent` が 0。`/opt/carelink/agent-staging` が無い。`free -m` が元に戻る |

注意:
- S8 の `--build` は箱の image（約 1.7GB）を作る。ディスクの空きを preflight が見る。
- S10・S10c のログインは staging の volume（`stg-agent-home`）に入る。S14 の `down -v` で消える（本番のログインとは別）。
- **S12・S13 は既定（Codex）で流す**。画面の新しい相談は既定で ChatGPT（中継の `AGENT_DEFAULT_PROVIDER`・既定 `codex`）。Claude で比べるときは `POST /sessions {provider: "claude"}`。S12 の確かめに足す: 見出しのモデルが「Sol …」（`agent.sessions.resolved_model` が `gpt-…-sol`）、`agent.tool_calls` に `kind=watch` が無い（ふつうの相談で見張りが鳴らない）、質問カードが ChatGPT の問い（`question_id` が `cq_…`）で出る。
- どの段でも本番の `carelink-postgres`・`carelink-backend`・`carelink-frontend` は再起動しない。Cloudflare の道は staging では足さない（SSH の転送だけ）。

## 5. 変えたファイル（この組み合わせ・コミットはまだしていない）

- 中継: `agent/relay/{config,public,checkpoint,service,runner,normalize,events,__main__}.py`（Codex: `codex_session.py`・`codex_normalize.py`・`codex_config.py`・`watch.py`〔新〕・`step_labels.py`・`tests/{test_codex.py,fake_codex.py,fixtures/codex_*}`〔新〕）・`agent/relay/relay-prompt.md`（新）・`agent/relay/tests/{test_integration.py（新）,relaykit.py,test_approvals.py,test_public.py}`
- 置き方: `agent/deploy/{docker-compose.agent.yml,Dockerfile.relay,agent.prod.env,agent.stg.env,README.md,runner/agent_runner.py}`
- 画面: `frontend/components/agent/**`（`relay.ts`・`types.ts`・`reducer.ts`・`AgentProvider.tsx`・`Conversation.tsx`・`parts.tsx`・`mockRelay.ts`・`__tests__/*`・`__tests__/relayContract.test.ts`（新））
- backend（`0091_agent_schema.py` を含む）は触っていない。

## 6. Codex（ChatGPT）— 段 3-5（2026-10-12・PO「お客様は ChatGPT の契約が多い → ChatGPT を主に」）

**既定の AI = Codex（ChatGPT のサブスクのログイン）。Claude も選べる。** 画面（`frontend/components/agent/**`）には既定の AI の決め打ちが無かったので変えていない（中継が決める）。

### 6-1. 作ったもの

| もの | 中身 |
|---|---|
| `agent/relay/codex_session.py`（新） | `CodexSession`（direct）・`CodexRunnerSession`（箱の runner）。`codex app-server` と JSON-RPC: initialize → account/read（`type: chatgpt` だけ・無い/API キーは始めない）→ model/list（`SolPicker` で最新の Sol）→ config/read・experimentalFeature/list（締め付けの確かめ）→ thread/start｜resume → turn/start｜steer｜interrupt。質問（`item/tool/requestUserInput`）の答え、承認の問い合わせへの答え（`_audit` で記録）、「このアカウントでは使えないモデル」で一つ下の Sol へ（何もしていないターンだけ） |
| `agent/relay/codex_normalize.py`（新） | app-server の行 → 共通イベント（message.delta/message・thinking・step.start/end〔rk の言葉は Claude と同じ `step_labels`〕・question・turn.end・status・error〔login/model/context/busy/cli〕・rate_limited〔`account/rateLimits/updated` の resetsAt〕）。文の終わりの「1. … 2. …」（2〜4 択・問いかけの行つき）も質問カードにする（`source: "text"`）が、**押した答えはモデルへの文として送るだけで、反映の承認には決してならない** |
| `agent/relay/codex_config.py`（新） | 起こし方の決まりの 1 か所（中継と runner が同じファイル）: `-c` の締め付け・確かめる項目・承認の問い合わせの物差し（`command_decision`・`file_change_decision`） |
| `agent/relay/watch.py`（新） | 脇道の見張り（§6-3） |
| `agent/deploy/runner/rakusuke.rules`（新） | Codex の execpolicy: `rk` は allow、curl/wget/nc/ssh/git/pip/npm/sudo/psql/printenv など forbidden。python・シェル・読み書きは決まりなし（＝聞かずに動く） |
| runner（`agent_runner.py`） | codex の起動を `codex_config.codex_argv()` に。起動のたびに `CODEX_HOME/config.toml` を空に・`rules/` を rakusuke.rules だけに。PATH の先頭に `/srv/agent/bin`（rk の包み）、TMPDIR = `work/scratch`。`{"op":"auth_status"}`（ok/missing/api_key/unknown の 1 語）。codex の thread/resume も受ける |
| 中継（`service.py`・`config.py`・`public.py`・`step_labels.py`） | `AGENT_DEFAULT_PROVIDER`（既定 codex）・`AGENT_PROVIDERS`・`AGENT_CODEX_BIN`・`AGENT_RUNNER_SESSIONS_DIR`・`AGENT_RUNNER_AGENT_DIR`。`POST /sessions {provider?}`、`GET /agent-relay/engine`（既定の AI と「ログイン済み/未」・トークンなし）。始める前に断ったとき（login・auth_source・lockdown）はその 1 行を画面へ。`_cmd`・`_audit` は画面に出さず見張りと監査（`tool_calls`）へ。rk 以外のシェル（スキルを読む・python の計算）にも平易な言葉 |
| `Dockerfile.sandbox` | build の時に `codex execpolicy check` で rules が効くこと（rk=allow・curl=forbidden）を確かめる。`bin/rk` を実行可能に |
| 出口の許可リスト | `api.openai.com` を外した（§6-4） |

### 6-2. 決めたこと（理由）

| 項目 | 決め | 理由（実測） |
|---|---|---|
| つなぎ方 | `codex app-server`（`exec --json` ではない） | 0.153.4・0.162.1 ともにある。thread の続き・作業中の追加（steer）・質問・承認の往復・model/list・config/read が 1 本で揃う |
| sandbox | `danger-full-access`（箱が砂場） | 0.162.1 の Linux の砂場（bwrap）は Docker の既定の seccomp で `No permissions to create a new namespace`（実測）。read-only/workspace-write だとすべてのコマンドが失敗 |
| 承認 | `on-request`・`approvals_reviewer=user`・中継が答える | PO「計算は自由・書き込みだけ関所」。書き込みは中継の承認番号だけ（守りは承認に頼らない＝code mode の教訓）。問い合わせが来たら秘密・外へ・内側口の直打ち・`/srv/agent` の書き換えだけ断る |
| 質問 | `request_user_input`（`features.default_mode_request_user_input=true`） | gpt-5.6-sol が実際に使った（4 択・`isOther`）。答えは id ごと |
| モデル | model/list → 最新の Sol | 手元の一覧 `gpt-6-astra, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna` → `gpt-5.6-sol`（Astra は使わない） |
| ログイン | `codex login --device-auth`（0.162.1 にある・実測） | URL とコードをスマホでも開ける |
| 締め付けの確かめ | config/read・experimentalFeature/list で 1 つでも違えば始めない | 手元（利用者の設定に MCP サーバーあり）では `config mcp_servers not empty` で正しく止まった。0.162.1 の箱（空の CODEX_HOME）では問題 0 |

確かめた／確かめていない:
- 確かめた（手元・Windows・0.153.4・ChatGPT ログイン・本番には届かない形〔1 本目の rk whoami だけ手元の設定で本番を読んだ＝読むだけ〕）: 締め付けを付けた app-server で 4 つの会話（rk sql --list・質問カード・計算・内側口への POST の試み）を取り、`tests/fixtures/codex_*.jsonl`（伏せ字済み）と金型の試験に。`CodexSession` を本物の codex で通し（Sol を選ぶ → 質問カードに答える → 返事）。
- 確かめた（Docker・0.162.1・ログインなし）: `-c` の締め付けが config/read に効く（問題 0）・thread/start が `approvalsReviewer: user` で通る・rules の判定（rk=allow・curl=forbidden）・device-auth は `auth.openai.com` だけにつなぐ・Linux の砂場は動かない・箱の image の build と runner の codex 部分。
- まだ: 箱（Linux・0.162.1）でログインした後の本物の会話・rk → 中継の内側口の往復（S12・S13）・rate limit の実物の形（schema から作った）・`ab.chatgpt.com` を断ったときに会話へ影響が無いこと。

### 6-3. 見張り（`agent/relay/watch.py`）

計算（シェル・python・その場の script）は拾わない。拾うのは: 秘密らしい所を読む（`~/.codex/auth.json`・`/proc/<pid>`・`.env` など → **その場で止めて琥珀の 1 行**）、中継の内側口を rk を通さずにたたく、外へ出る道具・直接の通信、`/srv/agent` の書き換え、同じコマンドの失敗 3 回、話さずに 25 件以上の作業。ターンで最初の印 → モデルへ道案内（「【らく助の中継から】らく助の記録は rk で読むと確実です（例: rk sql --list …）。…計算やその場の script は作業フォルダーで自由に使ってください」）、3 つ目で止める。印はすべて `agent.tool_calls`（`kind=watch`）。Claude の Bash/PowerShell にも同じ見張りが効く（中継の Claude の正規化が `emit_commands=True` で `_cmd` を出す）。実物の金型で: 計算・rk・質問の会話は 0 件、内側口への POST は `relay_bypass` 1 件（道案内 1 回・止めない）。

### 6-4. 出口の許可リスト（Codex）

| ホスト | 状態 | 根拠 |
|---|---|---|
| `chatgpt.com` | 確認済み・入れた | ChatGPT ログインで 1 ターンを記録つきプロキシ越しに流し、CONNECT はここだけ（6 回） |
| `auth.openai.com` | 確認済み・入れた | 0.162.1 の `login --device-auth` がつないだのはここだけ。トークンの更新（`/oauth/token`）も実行ファイルの文字列でここ |
| `api.openai.com` | 外した | API キーの道だけで使う（ChatGPT ログインの会話では 0 回）。使わない決まり |
| `ab.chatgpt.com` | 入れない | OTLP の計測（`otel.*=none` で切る） |

### 6-5. 試験

`PYTHONPATH=agent python -m pytest agent/tests agent/relay/tests -q -p no:asyncio` → **414 件通る・3 件 skip**（Codex の試験 `agent/relay/tests/test_codex.py` 44 件を含む: 金型 4・伏せ字の確かめ・質問カード・文の選択肢は承認にならない・承認の物差し 11・締め付け・rules〔手元に codex があれば `execpolicy check` も〕・偽の app-server〔`tests/fake_codex.py`〕での流れ・始めない 3 通り・承認の答えと監査・一つ下の Sol・止める・見張り 6・中継の組み込み）。

---

## 7. 堅くした 4 点（staging の前・2026-10-12 追記）

### 7-1. 認証情報の隔離（お客様が使う前に必須だったもの・実装と実測）
- **変えたこと**: ログイン情報（`~/.claude/.credentials*`・`~/.codex/auth.json`）は CLI の uid **10002** だけが読む。**モデルのコマンドは別 uid 10004（agentx）の実行係で走る**（`agent/deploy/runner/entrypoint.sh`・`execd.py`・`bx`）。箱は root で起動し（capability は SETUID/SETGID/SETPCAP だけ・何も待ち受けない）、runner/CLI と実行係を capability 0・no-new-privs で起こす。
  - claude: `CLAUDE_CODE_SHELL_PREFIX=/opt/runner/bx` → Bash の道具の全コマンドが execd 経由。届かなければ走らせず 126。
  - codex: `CODEX_EXEC_SERVER_URL`（uid 10004 の `codex exec-server`）。app-server はローカルで何も走らせず（実測: 無い環境は `local environment is not configured`）、コマンドもファイル操作も実行係の環境に出る。`RK_*`・PATH は `-c shell_environment_policy.set` で渡し、`allow_login_shell=false`（ログインシェルは PATH を戻して rk が見えなくなる）。
  - CLI の uid で動く道具（Claude の Read/Edit/Grep/Glob）: `permissions.deny`（`//home/agent/**`）＋中継の許可の返事で断る。
  - compose: 箱を `user: "0:0"` ＋ `cap_add: SETUID/SETGID/SETPCAP`、tmpfs `/run/x`（ソケット）・`/home/agentx`、healthcheck は agent の uid で実行係のソケットも見る。作業フォルダーはグループ 10005 の 2770。
- **実証**（箱の中・本物の claude 2.1.296 / codex 0.162.1・偽のモデル・ダミーの認証ファイル）: `bash agent/deploy/tests/isolation_e2e.sh <image>` → **13/13**。モデルの `cat` は `Permission denied`・`id -u`=10004・ダミーの秘密が結果に出ない（claude の Bash・Read の道具・codex のシェル）／CLI 自身（10002）はダミーを読める／実行係に届かなければ bx は走らせない。codex の実行係で `which rk`=`/srv/agent/bin/rk`・`RK_RELAY_TOKEN` あり。部品の試験: `agent/relay/tests/test_credential_isolation.py`（9 件）。
- **残る危険**（`agent/deploy/README.md` §9-(1)）: Claude の道具のシンボリックリンク経由は未確認（staging で）／実行係のコマンドは別会話の `RK_RELAY_TOKEN` を `/proc` から読める（同時 2 会話・承認番号は画面だけなので書き込みの関所は破れない）／本物の OAuth のログインでの実証は staging で／CLI の版を上げるたびに `isolation_e2e.sh` を流す。
- **運用が変わる所**: 箱の `docker exec` には **`-u 10002:10000`** が要る（README のログイン手順・`cleanup/agent-cleanup.sh` は直した）。image の `ENTRYPOINT` が `entrypoint.sh` になった（`CMD` ではない）。

### 7-2. 案の HTML を保存しない
`plans.html_a4`・`html_a3` 列を無くし（0091 は未適用なので `backend/alembic/versions/0091_agent_schema.py` と `relay/db.py` を書き換え）、`RelayService.plan_html` が保存した案の JSON から A4/A3 を開くたびに描く。署名つきの印刷 URL（5 分）は同じ。A3 は時間割のある案だけ（無ければ 404・案のイベントの `html_urls` にも出さない）。試験: `test_public.py::test_plan_html_is_rendered_on_demand_not_stored`。

### 7-3. ディスクの空き
`GET /agent-relay/engine` に `disk{free_gb, low, blocked, warning}` を追加（パスは返さない）。空き **5 GB 未満**は新しい相談・新しい CLI を起こさず、穏やかな日本語で 503（作業中の相談は続く）。10 GB 未満は警告だけ。`retention.disk_status` に `blocked` を足し、見るのは中継のデータ置き場（`AGENT_RELAY_DATA_DIR`＝`/data`）。試験: `test_public.py::test_disk_status_in_engine_and_new_sessions_stop_below_5gb`。**画面側**（`frontend/`）が `disk.warning` を出す部分は別担当。

### 7-4. 試験（手元）
`PYTHONPATH=agent python -m pytest agent/tests agent/relay/tests agent/evals/tests -q -p no:asyncio` → **447 件通る・7 件 skip**（skip は unix 専用の試験で Windows 上のもの。箱の中では 9 件とも通った）。箱の image・中継の image は Docker Desktop で build 済み。compose は `docker compose config` で構文を確認（外部の網があるので `up` は未実施）。

### 7-5. staging に置く手順（追加分・§4 の前後に）
1. 箱の image を**作り直す**（`ENTRYPOINT`・ユーザーが変わった）。volume `stg-agent-home` は持ち主 10002 のまま使える。`stg-agent-sessions` は作り直すか、`entrypoint.sh` が最初に `chgrp -R 10005` するので古い会話はそのままでよい。
2. 立てたら `deploy/README.md` §3-7b の (2)(3) を流す（capability 0・no-new-privs・`bx` で読めないこと）。
3. PO のログイン後、**本物のログイン情報**で §3-7b (3) と `bash agent/deploy/tests/isolation_e2e.sh` の考え方（モデルに `cat ~/.codex/auth.json` を頼んで断られること）を確かめる。Claude の Read で作業フォルダー内のリンク→`/home/agent` を試して断られることも見る。
4. migration は 0091（html 列なし）を流す。ディスク: `curl` ではなく画面かログインした状態で `/agent-relay/engine` の `disk` を見る。

### 7-6. レビュー指摘の対応（隔離の追加・2026-10-12 夜）
- **[HIGH] `--settings` の差し替え**: 会話のフォルダー d を agent 持ち 0750 にし（実行係は入れるが消せない・付け替えられない）、書けるのは d/work（2770）だけ。`--settings` は `/run/agent/<会話>.settings.json`（実行係は入れない）に `O_EXCL|O_NOFOLLOW` で毎回新しく書く。`entrypoint.sh` の chmod は `*/work` だけ。試験: 設定の差し替え・削除・フォルダーの付け替えが Permission denied、設定のハッシュが変わらない。
- **[MEDIUM] 逃げる子**: execd を subreaper にし、コマンド終了時と 2 秒ごとに、要求から外れた子孫・init の子になった uid 10004 の残りを止めて reap。PID 名前空間は capability 0 では使えないため掃除で代えた。試験: `setsid`・二重 fork・`nohup` の子が残らない（claude 経路）／codex 経路の `setsid` の子も定期の掃除で片づく。
- **[確認] リンク**: execd が work の外を指すシンボリックリンクを要求の前後と定期に消す（試験: /home/agent へのリンクが消え、work 内を指すリンクは残る）。codex のファイル操作は app-server 側ではなく実行係に出る（`fs/readFile` が `local filesystem is not configured`・試験に入れた）。`apply_patch` の道具そのものは偽のモデルで出ず未実行（staging で本物のモデルにて確認）。
- **[MEDIUM] plan_html**: A4 の描画失敗は 422「この案は帳票にできませんでした…」の穏やかな文（500 にならない）。`(plan_sha256, size)` の小さな cache（32 件）。
- **[MEDIUM] ディスク**: 5 GB 未満で止めるのは「まだ CLI の会話を持たない新しい相談」だけ（`_ensure_live` の 1 行を `_disk_guard(session)` に）。会話のある相談の再開は止めない。
- 試験: `isolation_e2e.sh` **19/19**、`pytest agent/tests agent/relay/tests agent/evals/tests` **495 件通る・8 skip**（skip は unix 専用で、箱側の python 3.12 では `test_credential_isolation.py` 10 件とも通った）。
