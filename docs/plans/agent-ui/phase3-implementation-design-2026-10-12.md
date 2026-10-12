# らく助エージェント 段 3（小窓・相談タブ・中継）— 実装設計書（PO 了承用・2026-10-12）

状態: **設計案（PO 未了承）**。実装・本番操作はまだしない。
対象: 構想 `concept-2026-10-12.md` §5-2 の段 3「小窓の画面と中継（まず Claude、次に Codex）」＋ PO 追加要望「/m に管理者だけの相談タブ」。
前提として読んだもの: `concept`・`step1-facts`・`pc-assistant-survey`（中継の知見）・`pc-assistant-ui-survey`（小窓）・`pc-assistant-artifact-view-survey`（案の欄）・`pc-assistant-mobile-ui-survey`（相談タブ）・`../agent-skills/design-2026-10-12.md`・`agent/AGENTS.md`・`PERSONA.md`・`README.md`・`agent/staging/README.md`・`../agent-skills/e2e-staging-2026-10-12.md`・モック 2 本・`docs/deployment/docker-compose.production.yml`・`runbook.md`・frontend（`AppShell`・`MobileShell`・`lib/auth.ts`・`lib/api-client.ts`・`middleware.ts`・`lib/cfAccess.ts`）・backend（`core/deps.py`・`core/security.py`・`middleware/security_headers.py`・`models/office_feature_flag.py`）・PC-Assistant `AGENT_PLAN.md` §3。

表記: **【事実】**＝ファイルで確かめたこと／**【案】**＝この設計の提案／**【要実測】**＝段 3-0 で Linux 上で確かめること。

---

## 0. 要点（先に）

1. **コンテナを 2 つ足す**: 中継 `carelink-agent`（Python・FastAPI）と、CLI を動かす箱 `carelink-agent-sandbox`。箱は**らく助の backend・DB へ道が無い網**にだけ置き、外へは Anthropic／OpenAI だけに絞った出口（中継の中の許可リスト付きプロキシ）を通す。
2. **エージェント（モデル）は書き込みの鍵を持たない**。rk は新しい `RK_MODE=relay` で中継に話し、中継が「読む API（GET と読むだけの POST）」「読み取り専用 SQL」だけを通す。**書き込みは、画面で人が押した承認番号を中継が確かめた時だけ、中継自身が行う**（rk の applyrun を中継の中で動かす）。モデルが承認を偽ることはできない。
3. **ブラウザーの鍵はらく助のログインをそのまま使う**（NextAuth が持つ backend の access JWT を Bearer で送る）。中継は backend の `GET /auth/me` で確かめ、admin だけ通す。`JWT_SECRET` は中継に渡さない。
4. **公開は既存の Cloudflare Tunnel の同じホスト名に道を 1 本足す**（`carelink.kaipoke-api.net/agent-relay/*` → `127.0.0.1:18201`）。新しいホスト名は作らない。Cloudflare Access もそのまま効く。
5. **画面への流れは SSE**（fetch のストリームで読む。EventSource は Bearer を付けられないため）。送る・答える・承認は POST。
6. **会話・案・承認・反映の記録は らく助の DB の別スキーマ `agent`**（migration 0091）。中継は `agent` スキーマだけ書ける専用ロールでつなぐ。
7. **案の HTML は中継がサーバー側で rk の帳票関数から作る**（スクリプト無し【事実: `rk/report.py` に `<script` 0 件】）。画面では `iframe srcdoc` ＋ `sandbox=""`（スクリプト不可・不透明オリジン）。印刷用は署名つき短命 URL＋CSP `sandbox`。サブドメインは作らない。
8. **同時に動かす CLI は 1 本（上限 2）**。VPS の空きは約 2.2〜2.5GB・swap なし【事実: staging README・e2e §4b】。
9. 見積もり **約 27〜33 人日**（段 3-0〜3-6）。Codex 対応は段 3-5 に分ける。

---

## 1. 全体の形（アーキテクチャ）

### 1-1. 図

```
ブラウザー（管理者・PC の小窓 / スマホ /m/agent）
   │ https://carelink.kaipoke-api.net  （Cloudflare Access ＋ らく助のログイン）
   ▼
cloudflared（systemd・ダッシュボード管理）
   ├─ /agent-relay/*  ──────────────▶ 127.0.0.1:18201 → carelink-agent:8090（公開口）   ★新しい道
   ├─ /api/v1/*       ──────────────▶ backend（今のまま）
   └─ その他          ──────────────▶ 127.0.0.1:18000 frontend（今のまま）

┌ 網 deployment_default（今の網）────────────────────────────────┐
│ carelink-postgres   carelink-backend:8000   carelink-frontend   │
│        ▲ agent_ro / agent_dump / agent_relay ロール   ▲ 18001  │
│        │                                              │          │
│  carelink-agent（中継・FastAPI）──────────────────────┘          │
│    ・公開口 :8090（ブラウザー向け）                               │
│    ・内側口 :8091（箱の rk 向け：読む API の関所・SQL・案・反映） │
│    ・出口プロキシ :3128（許可: api.anthropic.com ほか／OpenAI）   │
└─────────┬───────────────────────────────────────────────────────┘
          │ 網 agent-internal（internal: true ＝ 外へ出られない）
          │ ＋ unix ソケット /run/agent/runner.sock（共有ボリューム）
┌─────────▼────────────────────────────────┐
│ carelink-agent-sandbox（箱）              │
│  runner（小さな常駐・決まった起動だけ）    │
│   └ claude -p … / codex app-server …     │  cwd = /srv/agent（agent/ の写し・読み取り専用）
│        └ Bash → rk（RK_MODE=relay）      │  HOME = /home/agent（ログイン情報の volume）
│  backend・DB・.env・docker.sock なし      │  作業 = /data/sessions/<id>/
└──────────────────────────────────────────┘
```

### 1-2. なぜ 2 コンテナか【案】

| 案 | 中身 | 長所 | 短所 |
|---|---|---|---|
| **A（推奨）** 中継と箱を分ける | 中継は backend・DB の網、箱は外へ出られない網だけ。中継⇄箱は unix ソケット | **箱の中のモデルが Bash で何をしても backend・DB に届かない**（網が無い）。鍵（agent 用の admin トークン・DB パスワード）は中継にしか無い | 箱の中に小さな runner が要る（中継は docker.sock を持たないので、箱の中で CLI を起こす係が要る） |
| B 1 コンテナで Linux ユーザーを分ける | 中継 uid 10001・CLI uid 10002 | 作るものが少ない | 同じ網にいるので CLI から backend に届く（iptables の owner 判定には NET_ADMIN が要る）。鍵のファイルの権限だけが守り |

- A の runner は「`claude` か `codex` を、決まった引数の型でだけ起こす」係。中継から来るのは `{op: spawn, session_id, provider, model, resume_id}`・`stdin`・`interrupt`・`kill` だけで、任意のコマンドは受けない（中継が乗っ取られても箱で任意コマンドにはならない二重の守り）。
- 箱の出口: 箱の網は `internal: true`（既定の経路なし）。CLI には `HTTPS_PROXY=http://carelink-agent:3128` を渡し、中継の出口プロキシが CONNECT 先を許可リスト（Claude: `api.anthropic.com`・`claude.ai`・`console.anthropic.com`・`statsig.anthropic.com` 等【要実測: 実際に使うホスト一覧】／Codex: `chatgpt.com`・`api.openai.com`・`auth.openai.com`【要実測】）に絞る。`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`・`DISABLE_TELEMETRY=1` で余計な通信を減らす。
- 中継の技術: **Python 3.12＋FastAPI＋asyncio**。理由: rk が Python（`rk/report.py`・`rk/plan.py`・`rk/applyrun.py`・`rk/opsbase.py` を中継の中でそのまま import して使える）、backend と同じ言語でレビューしやすい。

### 1-3. CLI の起こし方（Claude）【事実: PC-Assistant AGENT_PLAN §3.1・3.3 を Linux へ】

```
claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
  --permission-prompt-tool stdio
  --permission-mode default
  --setting-sources project
  --strict-mcp-config --mcp-config /srv/agent-runtime/empty-mcp.json
  --settings /data/sessions/<id>/isolation.json
  --append-system-prompt-file /srv/agent-runtime/relay-prompt.md   （画面の約束: 承認は画面のボタン・rk plan submit を使う 等）
  --max-turns 80
  --model <sonnet|opus>          ← --resume の時も毎回付ける（付けないと既定に戻る：AG0 S1）
  [--resume <cli_session_id>]
cwd = /srv/agent（agent/ の写し・読み取り専用 bind）
env = 許可リストだけ（PATH, HOME=/home/agent, LANG=C.UTF-8, TZ=Asia/Tokyo, HTTPS_PROXY, NO_PROXY=carelink-agent,
      RK_MODE=relay, RK_RELAY_URL=http://carelink-agent:8091, RK_RELAY_TOKEN=<会話ごとの合言葉>, RK_WORK_DIR=/data/sessions/<id>/work）
```
- `--permission-prompt-tool stdio` は**必須**（無いと AskUserQuestion の質問カードが来ない）【事実: AGENT_PLAN §3.1】。
- 道具の許可（`can_use_tool`）への中継の答え【案】: 箱の中は鍵も道も無いので **Bash・Read・Glob・Grep・Write/Edit（作業フォルダーの中）は自動で allow**、`WebFetch`・`WebSearch` は deny（出口でも止まるが、モデルに無駄をさせない）。管理者に「道具の許可カード」は**出さない**（非 IT の管理者に Bash の許可を聞いても判断できない。守りは箱と関所が持つ）。カードに出すのは **AskUserQuestion（質問）と 案の承認だけ**。
- isolation.json（会話ごとに中継が作る・PC-Assistant の確定形を Linux のパスに）:
  ```json
  { "forceLoginMethod": "claudeai", "apiKeyHelper": "",
    "env": { "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_BASE_URL": "", "ANTHROPIC_CUSTOM_HEADERS": "",
             "CLAUDE_CODE_USE_BEDROCK": "", "CLAUDE_CODE_USE_VERTEX": "", "CLAUDE_CODE_USE_FOUNDRY": "" },
    "autoMemoryEnabled": false,
    "claudeMdExcludes": ["/home/agent/.claude/CLAUDE.md", "/home/agent/.claude/rules/**", "/CLAUDE.md", "/srv/CLAUDE.md"] }
  ```
  - 自動メモリは**切る**（会話をまたいで患者情報が残り、別の管理者の会話に混ざるのを防ぐ。覚えさせたいことはスキルに書く）。
  - PC-Assistant の実測「エージェントが途中で `ANTHROPIC_BASE_URL` を書くと次のターンから偽サーバーへトークンごと送られた」は、`--settings`（flagSettings が勝つ）＋出口の許可リストの二重で止める。
- 終わり方: 最初の送信で起動、**20 分使わなければ stdin を閉じて終了**（質問・承認の待ちはアイドルに数えない）、次の送信で `--resume`。止める＝`control_request{subtype:"interrupt"}`。10 秒で result が来なければ kill。
- 作業中の追加送信は同じターンに入り result は 1 つ【事実: AG0 S1】→ 画面は「送信待ち」を出す。

### 1-4. CLI の起こし方（Codex）【案・要実測】
- `codex app-server`（stdio の JSON-RPC: `thread/start|resume`・`turn/start|interrupt`・承認の往復）【事実: pc-assistant-survey §1.3】。`CODEX_HOME=/home/agent/.codex`。
- 設定は起動のたびに `-c` で上書き（`sandbox_mode="danger-full-access"`＝箱そのものが砂場・`approval_policy="never"`・`model_provider` 固定・base_url を触らせない）。config.toml はモデルが書き換えうるので、`-c` の上書きと出口の許可リストで守る。
- Codex には AskUserQuestion が無い → AGENTS.md の「選択肢つきで 1 問ずつ」を文で出させ、中継は**文の中の「1. … 2. …」を拾ってボタンにする**（拾えなければ文のまま）。【要実測: app-server に利用者への問い合わせの仕組みがある版か】

### 1-5. 画面用の共通イベント（両 CLI をこの形に揃える）【案】

SSE の 1 件 = `id: <seq>` ＋ `data: <JSON>`。JSON の共通部: `{seq, ts, session_id, turn_id, type, ...}`。`seq` は会話ごとの通し番号（つなぎ直しは `?after=<seq>` で続きから）。

| type | 中身 | Claude の元 | Codex の元 |
|---|---|---|---|
| `status` | `state`: idle / starting / working / waiting_answer / waiting_approval / applying / error / rate_limited・`pose`（マスコット）・`line`（吹き出し 15〜25 字） | system/init・result・rate_limit_event・api_retry | thread/turn の状態通知 |
| `message.delta` / `message` | らく助の文（差分／確定） | stream_event text_delta / assistant text | agentMessage の delta / completed |
| `thinking` | 「考えています… 0:08」用の開始・終了だけ（中身は送らない） | thinking ブロック | reasoning item |
| `step.start` / `step.end` | `step_id`・`label`（平易な日本語）・`ok`・`secs`。ターン終わりに `steps.summary`「✓ 調べました（6 件・42 秒）」 | tool_use / tool_result | commandExecution item |
| `progress` | 長い作業の途中経過「組み合わせを比べています（3/6 日）」 | **rk が中継へ直接送る**（stream-json はコマンドの途中出力を流さない【事実: DESIGN.md:113-115】） | 同じ |
| `question` | `question_id`・`questions[{question, header, options[{label, description}], multiSelect}]` | can_use_tool(AskUserQuestion) | 文から拾った選択肢 |
| `question.answered` | 誰がどの端末で答えたか（先に答えた方が勝ち） | — | — |
| `plan` | `plan_id`・`version`・`title`・`summary[]`・`counts`・`undoable`・`ready`・`blockers[]`・`html_urls`（a4/a3）・`status` | rk plan submit（中継の内側口） | 同じ |
| `approval.request` / `approval.result` | 承認カード（案の版に結ぶ）／承認・やめる・期限切れ | 中継 | 中継 |
| `run.progress` / `run.result` | 反映の 1 件ずつの進み・結果（成功 n／止まった所・元に戻せるか・バックアップ名） | 中継の反映の係 | 同じ |
| `error` | 平易な 1 行＋`retry` の可否（ログイン切れ・上限・CLI が落ちた） | result error・プロセス終了 | 同 |
| `turn.end` | 所要秒（費用は出さない） | result | turn completed |

- **作業の言葉（step の label）**: 中継の `step_labels.py` で Bash の中身から決める。例 `rk get /visits…`→「予定を読んでいます…」、`rk sql staff_week_overrides`→「職員のお休みを確かめています…」、`rk level day`→「振り分け方を考えています…」、`rk plan submit`→「案をまとめています…」、`Read SKILL.md`→「やり方を確かめています…」。知らないものは「調べています…」。**道具名・パス・SQL は画面に出さない**（PERSONA §3）。
- 画面では「動いている間は 1 行＋時計 → 終わると 1 行に畳む・⚠ は畳まない」（PC-Assistant と同じ）。

### 1-6. 会話の保存（どこに置くか）

| 案 | 中身 | 長所 | 短所 |
|---|---|---|---|
| **A（推奨）** らく助の DB に `agent` スキーマ（backend の alembic で 0091） | 下の 6 表 | **毎日のバックアップ（UTC 17:30）に入る**・SQL で監査できる・保存期間の掃除が SQL 1 本・migration の鎖が 1 本のまま | DB に新しいロール（`agent_relay`）が要る（インフラの変更）。migration の追加は backend のデプロイ手順に乗る |
| B 中継の volume にファイル（JSONL／SQLite） | 会話ごとの JSONL | インフラ変更なし・すぐ作れる | バックアップに入らない（別に cron が要る）・監査を DB と突き合わせにくい・保存期間の掃除を自前で |

推奨 A の表（案）:
- `agent.sessions`（id・user_id・title・provider claude/codex・model・cli_session_id・status・pinned・created_at・last_active_at・archived_at）
- `agent.events`（session_id・seq・type・payload jsonb・created_at。**画面用の共通イベントをそのまま追記**。つなぎ直し・履歴表示・監査を 1 つで）
- `agent.plans`（id・session_id・version・kind・title・plan_json・plan_sha256・check_result・basis_hash・html_a4・html_a3・status draft/approved/applied/rejected/stale/superseded・created_at）
- `agent.approvals`（id `ap_…`・plan_id・plan_sha256・kind apply/undo・user_id・device pc/mobile・decision・created_at・expires_at・consumed_at・run_id）
- `agent.runs`（id・plan_id・approval_id・backup_path・status・log jsonb・undo jsonb・started_at・finished_at）
- `agent.tool_calls`（session_id・kind get/post-read/sql・path か SQL 名・SQL 本文の先頭・行数・status・ms・created_at）＝**エージェントが読んだものの記録**（backend は GET を監査しない【事実: step1-facts §1-3】ので中継で残す）
- 保存期間（案）: 90 日で `events`・`tool_calls` を消す。`plans`・`approvals`・`runs` は 1 年（反映の記録なので長め）。日次の掃除は中継の中の定時処理（UTC で書く）。

### 1-7. ブラウザーの認証【案】

- ブラウザーは NextAuth のセッションが持つ `session.accessToken`（backend の access JWT・寿命 1 時間、NextAuth が 55 分で更新）【事実: `lib/auth.ts:143-189`】を `Authorization: Bearer` で中継へ送る。
- 中継の確かめ方の比較:

| 案 | 中身 | 判定 |
|---|---|---|
| **A（推奨）** backend の `GET /api/v1/auth/me` を中継から呼ぶ | 署名・期限・ユーザーの存在を backend が判定。中継は `role` が admin（旧 manager 含む）かを見る。結果は トークンの sha256 をキーに 60 秒だけ覚える | 鍵を中継に置かない。ユーザーを消せば 60 秒以内に効く |
| B `JWT_SECRET` を中継と共有して自分で decode | backend に聞かずに済む | **中継が任意のトークンを作れてしまう**（全員になりすませる鍵）。採らない |

- 加えて中継で: ① 機能の旗（§4-5）が有効か ② 試験の間は `AGENT_ALLOWED_USER_IDS`（許可した管理者だけ）③ 会話は**作った本人だけ**が見られる（PO 判断 D8）。
- SSE は **fetch＋ReadableStream** で読む（EventSource は Authorization を付けられない）。トークンの期限で中継が流れを閉じ、画面は新しいトークンで `?after=<seq>` からつなぎ直す。
- CSRF: Cookie ではなく Bearer なので基本的に効かないが、念のため公開口は `Origin` が `https://carelink.kaipoke-api.net` 以外の POST を断る。
- 「印刷用に開く」の新しいタブは Bearer を付けられないので、中継が**5 分・その人・その版だけの署名つき URL**を出す（HMAC の鍵は中継の中だけ）。

### 1-8. 公開のしかた（Cloudflare Tunnel）

- 【事実】tunnel はダッシュボード管理（runbook Phase G）。`carelink` のホスト名は `localhost:18000`（frontend）。`next.config.js` のコメントでは `/api/v1/*` は tunnel の path ルールで backend に直行。
- 【案】同じホスト名に **Path `^/agent-relay/` → `http://localhost:18201`** の公開ホスト名の行を 1 つ足す（今の行より上）。新しいホスト名・DNS は不要。Cloudflare Access も同じ Cookie で効く。
- SSE の注意: 15 秒ごとに `: ping` を送る（Cloudflare は約 100 秒無通信で切る【事実: 524 の知見】）。応答に `Cache-Control: no-cache, no-transform`・`X-Accel-Buffering: no`。
- 代わりの道（tunnel を触れない場合）: Next.js の `rewrites()` に `/agent-relay/:path*` → `http://carelink-agent:8090` を足し、frontend を中継の網に入れる。ただし Next の圧縮で SSE が溜まるおそれ（`no-transform` で避けられる見込み【要実測】）・Next の middleware の対象から外す必要（今の matcher は `/api` だけ除外【事実: `middleware.ts`】）。

### 1-9. 案の HTML の出し方（プレビューのオリジン）

| 案 | 中身 | 判定 |
|---|---|---|
| **A（推奨）** `iframe srcdoc` ＋ `sandbox=""` | HTML は中継が rk の帳票関数で作る（エージェントは JSON だけ渡す）。`sandbox=""`（allow-scripts も allow-same-origin も付けない）で不透明オリジン・スクリプト不可・`referrerpolicy="no-referrer"` | 新しいオリジンが要らない。帳票はスクリプト無し【事実: `rk/report.py` に `<script` 0 件】なので失うものが無い |
| B 別オリジン `preview.carelink…` | PC-Assistant のスマホ版と同じ（HMAC 鍵つき URL・CSP sandbox・frame-ancestors） | エージェントが自分で HTML を書く・スクリプト入りの成果物を出す時に要る。DNS・tunnel・証明書が増える。**その要望が出てから** |

- 印刷用（新しいタブ）は §1-7 の署名つき URL で、応答ヘッダーに `Content-Security-Policy: sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'`（トップレベルで開いても不透明オリジン＝らく助の Cookie・API に届かない）。印刷はブラウザーの印刷。
- A4 は常に白地（テーマに従わない）。「幅に合わせる」は CSS `zoom`／`transform: scale`。

---

## 2. 安全

### 2-1. 箱（carelink-agent-sandbox）に渡すもの・渡さないもの

| 渡さない | どう渡さないか |
|---|---|
| `/opt/carelink/.env`・`/opt/carelink/secrets` | compose で `env_file` を書かない・mount しない。runner が CLI の環境を許可リストで作り直す |
| docker.sock | mount しない（中継にも渡さない） |
| backend・Postgres への道 | 箱は `agent-internal`（internal）にだけ参加。backend・postgres は別の網。ホストの 127.0.0.1:18001 はコンテナから届かない |
| 書き込みの鍵 | 箱には らく助のトークンが一切無い。rk の呼び出しは中継の内側口へ、会話ごとの合言葉 `RK_RELAY_TOKEN` だけで行く（合言葉はその会話の読む権限だけ・会話が終われば無効） |
| インターネット | 出口プロキシの許可リスト（Anthropic／OpenAI）だけ |
| スキルの書き換え | `/srv/agent` は読み取り専用。更新は VPS で `git pull` → 中継が写しを作り直す（次の会話から効く） |

- 箱の中で Bash を許す理由: rk を動かすのに要る。箱の中には患者情報（rk が中継から受け取った読み取り結果）と CLI のログイン情報しか無く、出口は AI の会社だけ。**箱の中で起こりうる最悪 = 読んだ患者情報が AI の会社へ送られる**（これはエージェントを使う以上避けられない。§2-6 の同意で扱う）。
- CLI のログイン情報（`~/.claude/.credentials.json`・`~/.codex/auth.json`）はモデルから読める位置にあるが、外へ持ち出せる先が Anthropic／OpenAI しか無い。【案】段 3 では受け入れ、段 4 以降で「ログイン情報を別 uid に置き、runner が `CLAUDE_CODE_OAUTH_TOKEN` だけを渡す」形を検討。

### 2-2. 中継の内側口（rk の関所）【案】

| 内側口 | 許すもの | 断るもの |
|---|---|---|
| `GET /rk/api/v1/<path>` | すべての GET（中継が agent 用の admin トークンを付けて backend へ）。`tool_calls` に記録 | — |
| `POST /rk/api/v1/<path>`（読むだけ） | `rk/readonly_posts.py` の許可リスト（14 本）【事実】と一致するものだけ | それ以外の POST／PUT／PATCH／DELETE は **403「反映は画面の承認を通してください」** |
| `POST /rk/sql` | 名前つき SQL（`agent/sql/*.sql`）と 1 文の SELECT（`rk/sqlrun.py` の検査を中継でも行う）を **`agent_ro` ロール**で。`statement_timeout 30s`・結果 5,000 行まで | 複文・書き込み |
| `POST /rk/plans` | 案の提出（§3） | 許可リストに無い op を含む案（`check_plan` で断る） |
| `POST /rk/apply`・`/rk/undo` | **有効な承認番号**がある時だけ（§3-3） | それ以外すべて |
| `POST /rk/progress` | 途中経過の 1 行（画面の吹き出し用・80 字まで・その会話だけ） | — |
| `POST /rk/backup` | 中継が `agent_dump` で pg_dump（§2-3） | — |

- **カイポケのロボットを動かす読み取り**（`diff`・`export`・`smart-inbound-*` など・AGENTS.md §4-2 の最下行）は GET でも関所で止める（ロボットの枠を使う・了承が要る）。段 3 ではカイポケ送信も扱わない（承認の種類を増やす段 4 で）。
- 関所の許可リストは rk の `readonly_posts.py`・`opsbase` をそのまま import して使う（rk と中継で一覧がずれない）。

### 2-3. 読み取り専用 SQL とバックアップ（docker exec を使わない）

【事実】今の rk は `docker exec carelink-postgres psql`（`rk/sqlrun.py:163`）と `docker exec … pg_dump`（`rk/backup.py:38`）。中継・箱は docker.sock を持たないので使えない。

| 案 | 中身 | 長所 | 短所 |
|---|---|---|---|
| **A（推奨）** Postgres に専用ロールを 3 つ作る（インフラ変更・PO 了承） | `agent_ro`: 全表 SELECT（**`users.password_hash` と `kaipoke_credentials` は列・表ごと REVOKE**）・`default_transaction_read_only=on`・`CONNECTION LIMIT 3`・`statement_timeout=30s`。`agent_dump`: `pg_read_all_data`（PG16 の既定ロール）でバックアップ専用・中継の backup の道からしか使わない。`agent_relay`: `agent` スキーマだけ読み書き | アプリ（backend）を変えずに済む・今の `extract.py` と同じ SQL がそのまま動く・バックアップも中身が全部入る | ロールのパスワードを中継の秘密ファイルに置く（`/opt/carelink/secrets/agent-relay.env`・0400・`.env` とは別）。中継は postgres の網に入る |
| B backend に `/agent/sql` を足す | admin だけ・名前つき SQL だけ | DB ロールが要らない | backend の変更・自由な SELECT は足せない・バックアップは別に要る |

- バックアップ: 反映の直前に**中継が必ず自分で取る**（モデルが飛ばせない）。`/opt/carelink/backups/agent/agent-<label>-<UTC>.sql.gz`（中継だけが書ける bind mount）。中継の image に `postgresql16-client` を入れる。戻すのは今どおり PO がホストで（自動では戻さない）。
- `rk backup` は relay モードでは「中継にバックアップを頼む」になる（30 分以内の記録があれば反映の時にもう一度は取らない＝今の rk の決まりと同じ）。

### 2-4. CLI のログイン・アカウント（後で決める・段 3 の暫定）
- 試作の間は **PO のアカウント 1 つ**で、箱の volume `carelink-agent-home`（`/home/agent`）にログインを置く。ログインは PO が VPS の端末で 1 回だけ: `docker exec -it carelink-agent-sandbox claude auth login`（URL を自分の PC のブラウザーで開き、出たコードを貼る）／`codex login --device-auth`【要実測: 現行版のフラグ】。画面からのログインは作らない。
- 画面は `claude auth status` の結果だけを見て、切れていたら「らく助の係の方へご連絡ください」を出す（管理者に直させない）。
- **お客様の事業所で使う前に**: 誰の契約で動かすか（事業所の API キー／事業所のサブスク）と規約の確認（pc-assistant-survey §4.5 の「グレー」）を PO が決める。API キーにするなら `isolation.json` の `forceLoginMethod` と env の空欄を変える（設計の形は同じ）。

### 2-5. 隔離の決めごと（PC-Assistant isolation.json の教訓）
- `--settings` の flagSettings で `ANTHROPIC_*`・`CLAUDE_CODE_USE_*` を空にし、`forceLoginMethod: claudeai`・`apiKeyHelper: ""`（§1-3）。毎回の `system/init` で `apiKeySource` が none 以外・`model` が思ったものでなければ、その会話を止めて琥珀の 1 行（PC-Assistant と同じ検査）。
- runner は CLI の環境を**許可リストで作る**（親の環境を継がない）。
- `claudeMdExcludes` で HOME と祖先の CLAUDE.md を読ませない（`/srv/agent/CLAUDE.md`＝`@AGENTS.md` だけが効く）。
- Codex は `-c` の上書きを毎回付ける。
- 出口の許可リストが最後の守り（偽の BASE_URL へ向けられても届かない）。

### 2-6. 個人情報
- エージェントが読んだ患者名・住所・状態は AI の会社へ送られる。**お客様の事業所で有効にする前に、お客様の了承と契約を確かめる**（構想 §5-2 段 6）。段 3 は PO と私たちだけ（本番の写しの staging → 本番は PO のみ）。
- 通知（Web Push）は段 3 では作らない。作るときは本文に名前を入れない（PERSONA §5）。
- 会話の記録は らく助の DB の中だけ（§1-6）。中継のログ（標準出力）には本文を出さない（会話 ID・種類・秒数だけ）。

### 2-7. 量の上限（同時実行・メモリ）

【事実】VPS 7.9GB・swap なし・空き実質 2.2〜2.5GB（staging README・e2e §4b）。本番 backend 約 210MB・postgres 約 130MB・RPA 約 500MB。`rk level week` は 1 回 約 200 秒（e2e §4a）。

| もの | 上限（案） |
|---|---|
| 中継 `carelink-agent` | `mem_limit: 256m` |
| 箱 `carelink-agent-sandbox` | `mem_limit: 1g`（CLI 1 本 約 250〜400MB【要実測】＋ rk level の ortools 約 150〜300MB【要実測】） |
| 同時に動く CLI | **1 本**（上限の設定 2）。2 本目は「ほかの相談が作業中です。終わったら始めます」で待たせる（順番待ちの表示） |
| 会話を始める前の空き | 中継が `/proc/meminfo` の MemAvailable を見て **900MB 未満なら始めない**（「いまサーバーが混んでいます」） |
| staging との同時 | staging（約 1.3GB）と本番の箱は**同時に立てない**（staging を立てる時は本番の箱を止める手順を runbook に） |
| 1 人あたり | 動かせる相談 1 つ・1 時間 30 送信・1 ターン 15 分（超えたら interrupt して「時間がかかりすぎたので止めました」） |
| アイドル | 20 分で CLI を終える（会話は残る・次の送信で `--resume`） |
| 上限到達（サブスクの枠） | `rate_limit_event` を「HH:MM 以降に続けられます」で見せる |

### 2-8. 監査の記録（だれが・何を頼み・何を読み・何を書いたか）
| 何 | どこ |
|---|---|
| 頼んだ文・らく助の返事・質問と答え・承認とやめる | `agent.events`（端末 pc/mobile・user_id つき） |
| エージェントが読んだもの（GET・読むだけの POST・SQL） | `agent.tool_calls`（中継の関所が記録） |
| 案（版ごと）と承認・反映・元に戻す | `agent.plans`・`agent.approvals`・`agent.runs`（バックアップ名つき） |
| backend への書き込み | 今の `audit_logs`（actor＝エージェント専用の管理者ユーザー【PO 決定 Q2】）。中継は User-Agent に `rakusuke-agent-relay/1 (approval=ap_…; approver=<user_id>)` を入れる → **アプリを変えずに「だれの承認で」が監査ログから辿れる**【事実: 監査は User-Agent を保存 `middleware/audit.py:394-407`】 |
| 反映の 1 件ずつ | 中継の中の rk applyrun の `log.jsonl`・`undo.json` を `agent.runs` に保存（`runs/` の代わり） |

---

## 3. 案（plan）の流れと承認

### 3-1. 流れ
```
モデル: rk level day … → plan.json（作業フォルダー）
モデル: rk plan submit plan.json         ← 新しいコマンド（relay モード）
  └▶ 中継 /rk/plans: check_plan（許可リスト・ready・blockers）→ plan_sha256 → 版番号（この会話で 1,2,3…）
       → rk.report で A4 縦・A3 横の HTML を作って保存 → 画面へ `plan` イベント（前の版は superseded）
画面: 会話に案カード（要点 3〜5 行・件数・元に戻せるか）＋［案を見る］→ 右の欄（PC）／92% シート（スマホ）
人:  ［この案で反映する］→ 確かめの小窓「5 件の担当を替えます。カイポケへはまだ送りません［反映する］［やめる］」→［反映する］
  └▶ 中継（公開口・admin の Bearer）: 版が最新か・状態が待ちか → 承認番号 ap_xxx を発行（15 分・1 回限り・その版の sha256 に結ぶ）
       → 画面へ `approval.result`（PC とスマホの両方の承認が「反映しています…」に変わる）
       → CLI へ利用者の発言として送る:
         「【らく助の画面から】〇〇さんが案 2 で反映すると決めました（承認番号 ap_xxx）。
           rk apply --approved ap_xxx で反映し、rk verify で確かめて、結果を報告してください。」
モデル: rk apply runs/…/plan.json --approved ap_xxx
  └▶ 中継 /rk/apply: 承認を確かめる（§3-3）→ basis の読み直し（予定が変わっていたら止めて案を stale に）
       → バックアップ（30 分以内が無ければ取る）→ rk applyrun を中継の中で実行（agent 用 admin トークン）
       → 1 件ずつ `run.progress` → `run.result` → rk に結果を返す（rk は終わるまで待つ）
モデル: rk verify → 報告（AGENTS.md の報告の型）
画面: 結果カード「✓ 反映しました 14:12」［元に戻す］→ 押すと undo の承認（同じ仕組み・kind=undo）
```
- ［やめる］→ 案を rejected にし、CLI へ「〇〇さんは案 2 をやめると決めました」。［直してほしい所を書く］→ 入力欄に「案 2 について：」のチップを付けて送る。
- 予定が先に変わった案（basis が違う）は欄に琥珀の帯「この案を作った後に予定が変わりました。作り直しますか」＋反映ボタンを止める。
- 反映が途中で止まったら: 結果カードに「3 件目までは反映済み・4 件目からはまだ」＋［元に戻す（反映済みの 3 件）］（rk の `--partial` と同じ考え）。

### 3-2. なぜ「モデルに apply させるが、中継が強制する」か【案】
- AGENTS.md の作業の型（案 → 了承 → バックアップ → 反映 → 照合 → 報告）とスキルの手順をそのまま使える（中継専用の別の流れを作らない）。
- それでも**書き込みは中継の中でしか起きない**（箱に鍵も道も無い §2-1）ので、モデルが手順を飛ばしても・承認番号をでっち上げても書けない。
- 代わりの案「押したら中継がすぐ反映し、モデルには結果だけ伝える」も可能（部品は同じ）。モデルが 10 分以内に apply しなければ承認は期限切れ → 画面に「もう一度押してください」。PO 判断 D6。

### 3-3. 承認番号の検査（偽れない理由）
中継の `/rk/apply` は次をすべて満たす時だけ書く:
1. 承認番号が `agent.approvals` にあり、`decision=approve`・`kind` が一致・`consumed_at` が空・期限内。
2. 承認の `session_id` が、呼んできた `RK_RELAY_TOKEN` の会話と同じ。
3. 送られてきた plan.json の **sha256 が承認した版の sha256 と一致**（承認の後に案を書き換えても通らない）。
4. その版が会話の最新版で、状態が approved。
5. 1 回使ったら `consumed_at` を入れる（同じ番号で 2 回反映できない）。

承認番号を作れるのは**公開口の承認 API だけ**で、それは admin の Bearer（人のログイン）が要る。箱は公開口へ届かない（網が無い・出口は AI の会社だけ）。番号を推測しても、人が押していない番号は `decision` が無いので通らない。
→ rk 側の変更: relay モードの `--approved` は「誰が・いつ」の文ではなく承認番号 `ap_…` を受け、検査は中継に任せる（pc モードの今の動きは変えない）。

### 3-4. 質問カード（AskUserQuestion）
- `can_use_tool(AskUserQuestion)` → `question` イベント → 画面にボタン（2〜4 択＋「ほかの答え」）→ 先に答えた端末の答えを `{behavior:"allow", updatedInput:{questions, answers}}` で返す【事実: AGENT_PLAN §3.3】。
- **承認は質問カードで取らない**（モデルの質問で「反映してよいですか→はい」となっても書けない。書けるのは §3-1 の承認ボタンだけ）。中継の追加プロンプトに「反映の了承は rk plan submit の後、画面のボタンで受ける。質問では聞かない」と書く。

---

## 4. 画面（frontend）の作業

### 4-1. 共通の部品（`frontend/components/agent/`）
| 部品 | 役目 |
|---|---|
| `lib/agent/relayClient.ts` | 中継の API（会話の一覧・作る・名前変更・送る・止める・答える・承認・やめる・印刷用 URL）。`session.accessToken` を Bearer で |
| `lib/agent/useAgentStream.ts` | fetch で SSE を読む。`?after=<seq>`、切れたら 3→30 秒で張り直し・45 秒無音で張り直し・画面に戻ったら即読み直し・「つながっていません。最後に受け取った内容です」の帯（mobile 調査 I-6） |
| `lib/agent/reducer.ts` | イベント → 画面の状態（会話・作業行・質問・案・承認・実行）。**差分で描く**（key を固定・作り直さない） |
| `AgentProvider` | 会話の一覧・今の会話・つながりを AppShell／MobileShell の外側に 1 つ（ページを移っても会話が続く） |
| `Conversation`・`MessageBubble`・`StepGroup`・`LiveLine`（マスコットのポーズと 1 行） | 会話の表示。らく助の文は**文字として描く**（`dangerouslySetInnerHTML` を使わない）。簡単な強調・箇条だけ安全な Markdown 部品で |
| `QuestionCard`・`PlanCard`・`ApprovalBar`・`RunResultCard` | 出てから 400ms は押せない・Enter で承認しない（artifact 調査 §6.5） |
| `PlanViewer` | `iframe srcdoc sandbox=""`・案 1／案 2 のタブ・［幅に合わせる］［100%］・［印刷用に開く］・下端に固定の［この案で反映する］［やめる］［直してほしい所を書く］ |
| `SessionList` | 相談の一覧（決めてほしいもの／とめておいた／今日・昨日・今週・それより前）・✎ 名前・☆・消す |
| `Composer` | 入力欄（Ctrl+Enter で送る・IME の確定中は送らない）・止めるボタン |
| `Mascot` | ポーズ（`docs/mockups/mascot-candidates/rakusuke-pose-*`）を状態に合わせる（PERSONA §6） |

### 4-2. PC の小窓（AppShell）
- `AppShell` の中に `<AgentWidget />` を 1 つ（`isAdminRole(session.user.role)` ＆ 機能の旗 ＆ 中継の health が OK の時だけ描く）。
- 形: **丸（最初はたたんだ状態・PO 決定）→ 小窓 400×560 → 縦長 460×全高 → 案を広げる（右側に会話 380＋左に案の欄・`min(1240px, 100vw-32px)`）→ 欄を全画面**（モック `rakusuke-agent-widget-mock.html` の mini／compact／tall＋artifact 調査 §6.1）。
- **動かせる**: 見出しをつかんで画面の中で動かせる。位置は localStorage（try/catch・壊れていたら右下）。ダブルクリックで右下へ戻す。案を広げる時は右端に寄せ、閉じると元の位置・形に正確に戻す。
- 後ろの画面は暗くしない（反映前に今の予定と見比べるため）。
- 知らせ方: たたんでいる時は丸に点（青＝作業中・琥珀＝待ち）とタブの題名「(1) らく助」。**勝手に開かない・フォーカスを奪わない**。
- キー: Esc＝欄を閉じる／止める、Ctrl+Enter＝送る。数字キーで承認しない。
- Document Picture-in-Picture（他のアプリの上に浮かせる）は段 3 では作らない（構想 §2-2 (b)・要望が出てから）。

### 4-3. スマホの相談タブ（/m・管理者だけ）
- `MobileShell` の `TABS` に 5 つ目「相談」（`/m/agent`）を **admin の時だけ**足す（スタッフには出さない）。構想 §2-2b「/m には出さない」からの変更＝PO 判断 D9。
- `app/(mobile)/m/agent/page.tsx`（前回の相談を開く・無ければ空の新しい相談）と `app/(mobile)/m/agent/[sessionId]/page.tsx`。ページでも `isAdminRole` を確かめ、staff はホームへ戻す。
- 会話中は**下タブを隠す**（入力欄と取り合うため）。戻り道は ☰ ドロワーの「ホームへ戻る」。
- ☰ ドロワー＝相談の一覧（mobile 調査 I-1・I-3 ②）、案は**下から 92% のシート**（下端に固定の［直してほしい所を書く］［この案で反映する］）、見出しの［案 N］＝この相談の案の一覧シート。
- PC とスマホで同じ会話を同時に開ける（同じ SSE）。質問・承認は先に押した方が勝ち、もう片方は「〇〇で答えました」に変わる。

### 4-4. 文字・色
- 主色 #e15a7f（送る・新しい相談・反映する）。意味の色: 青＝作業中、琥珀＝あなた待ち、赤＝失敗、緑＝反映済み。ピンクを「待ち」に使わない。
- 言葉: 「承認」→「この案で反映する」、「会話」→「相談」、作業→「調べました／作りました」。「直す・修正」を使わない。

### 4-5. 機能の旗
- 既存の `office_feature_flags`（拠点ごと・`enabled_at`・API `GET/POST/DELETE /office-feature-flags`）【事実】に `feature_key = 'rakusuke_agent'` を使う。**アプリの変更は不要**。
- 判定（案）: その事業所のどれかの拠点で有効なら、管理者に小窓・相談タブを出す（エージェントは事業所全体を扱うため）。中継も同じ旗を `agent_ro` で読み、無効なら公開口が 404 を返す。
- 加えて中継の環境変数 `AGENT_RELAY_ENABLED`（全体の止め）と `AGENT_ALLOWED_USER_IDS`（試験中は PO だけ）。

---

## 5. 段取り・見積もり・試験

### 5-1. 段取り（見積もりは 1 人＋Claude Code の人日）

| 段 | 中身 | 人日 | 終わりの条件 |
|---|---|---|---|
| 3-0 実測 | Linux の箱で claude の stream-json・`--permission-prompt-tool stdio`・AskUserQuestion・isolation.json・`--resume`・出口プロキシ越しの通信先・メモリを測る。codex app-server も同様 | 2〜3 | 実測メモ（イベントの実物を試験用の記録として保存） |
| 3-1 インフラ | 2 つの image（中継・箱）、`docs/deployment/docker-compose.agent.yml`、網 2 つ、DB ロール 3 つの SQL、migration 0091（`agent` スキーマ）、runbook の追記、staging 版 | 3〜4 | staging で箱から backend・DB・外の任意のホストへ届かないことを確かめた |
| 3-2 中継（Claude） | runner、会話の管理（起動・アイドル・止める・再開）、イベントの正規化、SSE、認証、保存、関所（GET・読む POST・SQL）、案・承認・反映・元に戻す、上限 | 6〜7 | 偽フロントで「休み → 案 → 承認 → 反映 → 元に戻す」が staging で通る |
| 3-2b rk の relay モード | `RK_MODE=relay`（api・sql・backup・apply/undo・`plan submit`・`progress`）、`RK_WORK_DIR`。pc モードは変えない | 2 | 今のテスト 189 件＋relay の偽中継テストが通る |
| 3-3 PC の小窓 | §4-1 の部品＋§4-2 | 5〜6 | 管理者だけに出る・旗で消える・案を広げて反映できる |
| 3-4 スマホの相談タブ | §4-3 | 3〜4 | 360px 幅の実機で入力・シート・承認ができる |
| 3-5 Codex | app-server のつなぎ・イベントの正規化・文の選択肢 | 3〜4 | 同じ通しの試験が Codex でも通る |
| 3-6 仕上げ | 別担当のコードレビュー・安全のレビュー（§5-3 の攻め）・本番へ（PO だけ有効） | 3 | PO が本番で 1 件、反映と元に戻すを確かめる |
| 合計 | | **27〜33** | |

### 5-2. 試験のやり方
- **中継の単体**: 3-0 で取った本物の stream-json を記録として使い、「この入力ならこの画面イベント」を照合（正規化の金型試験）。偽 CLI（決まった行を吐くだけ）で会話の管理・止める・再開・アイドル終了。
- **承認の偽り試験（必須）**: 承認なし／他の会話の番号／期限切れ／使用済み／版の sha256 違い／古い版／`decision=reject` → すべて断る。箱から公開口・backend・DB・任意のホストへつなげない（コンテナの中から curl して失敗を確かめる）。関所に POST/PUT/PATCH/DELETE の書き込みを投げて 403。
- **rk**: 今の `agent/tests`（本番に触れない）＋relay モードの偽中継。
- **画面**: vitest（reducer・部品・admin 以外に出ないこと・旗が無効なら出ないこと）、Playwright（偽中継を相手に小窓の形・案の欄・承認・つなぎ直し）。
- **staging（`agent/staging/` を広げる）**: `docker-compose.staging.yml` に `stg-agent-relay`・`stg-agent-sandbox` を足し、`stg-backend`（18101）と `stg-postgres` にだけつなぐ。画面は PC の手元の Next.js を、SSH のポート転送で staging の backend と中継へ向けて動かす（staging には frontend が無い【事実】）。本物の claude で「急な休み → 案 → A4/A3 → 承認 → 反映 → 照合 → 元に戻す」（e2e I9 と同じ週43）を通す。**staging を立てる間は本番の箱を止める**（メモリ §2-7）。
- 本番の初回: PO だけを許可・本番は読む質問から → 1 件の担当替えで反映と元に戻すを確かめる。

### 5-3. 作る順の考え方
- 3-0 → 3-1 → 3-2／3-2b を先に（中身の正しさと安全）。画面（3-3・3-4）は 3-2 のイベントの形が決まってから並行。Codex（3-5）は Claude で通ってから。
- 各段は 設計メモ → PO 了承 → 実装（別担当レビュー）→ pg_dump → 反映（構想 §5-2 の約束）。

---

## 6. PO に決めてほしいこと

**決定（PO 2026-10-12）: D1〜D14 すべて推奨どおり。** **モデル（PO 2026-10-12）**: Claude は常に最新の Opus（CLI の別名 `--model opus`・再開時も付ける。今は Opus 5.5）。ChatGPT（Codex）は常に最新の Sol（会話の始めにモデル一覧から `gpt-<版>-sol` の最新版を選ぶ。基準 `gpt-6.1-sol`・使えなければ一つ下の版へ。手本 PC-Assistant `AgentModelChoices.cs`）。モデルの切り替えは不要。 **パーミッション（PO 2026-10-12）**: 段 3 は「②標準」だけ（案ごとに人が 1 回押す。重い操作 C は案の中で強調。段階は中継だけが決め、CLI の設定には渡さない）。①③は使ってから足す。調査 `pc-assistant-permissions-survey-2026-10-12.md`。 本番への反映（DB の役割・Cloudflare の道・画面）はその都度 PO の了承を取る。

| # | 論点 | 推奨 |
|---|---|---|
| D1 | 中継と箱の 2 コンテナ・箱は外へ出られない網＋出口の許可リスト（§1-2・§2-1） | 推奨案 A で |
| D2 | DB に専用ロール 3 つ（`agent_ro`・`agent_dump`・`agent_relay`）を作る（インフラ変更・§2-3） | 作る。`password_hash`・カイポケのログイン情報は `agent_ro` から外す |
| D3 | 会話の記録を らく助の DB（`agent` スキーマ・migration 0091）に置く（§1-6） | DB に置く（毎日のバックアップに入る） |
| D4 | Cloudflare の tunnel に `/agent-relay/*` の道を足す（ダッシュボード操作・§1-8） | 足す（新しいホスト名は作らない） |
| D5 | 案の表示は `srcdoc`＋`sandbox`、サブドメインは作らない（§1-9） | そうする |
| D6 | 反映は「モデルが承認番号で頼み、中継が確かめて書く」か「押したら中継がすぐ書く」か（§3-2） | 前者（AGENTS.md の手順のまま・どちらでも偽れない） |
| D7 | 同時に動かす CLI の数・アイドルの時間（§2-7） | 1 本・20 分（上限の設定は 2） |
| D8 | 相談を見られる人（作った本人だけ／管理者全員）と保存期間（§1-6） | 本人だけ・会話 90 日／案と反映の記録 1 年 |
| D9 | /m に管理者だけの相談タブを出す（構想 §2-2b の変更）・会話中は下タブを隠す | 出す・隠す |
| D10 | 機能の旗 `rakusuke_agent`（拠点ごと）と、試験中は PO だけ許可 | そうする |
| D11 | 試作の間の CLI のログインは PO のアカウント（箱の volume）。Codex も同じく PO の ChatGPT で試すか | Claude は PO のアカウントで。Codex は 3-5 の時に決める |
| D12 | お客様の事業所で有効にする条件（個人情報を AI へ送る了承・契約・誰の契約で動かすか） | 段 6 の前に必ず。段 3 は PO と私たちだけ |
| D13 | 管理者に「道具の許可カード」を出さない（質問と案の承認だけ・§1-3） | 出さない（守りは箱と関所） |
| D14 | 段 3 の範囲外: カイポケ送信の承認・Web Push 通知・小窓を他のアプリの上に出す（PiP）・音声入力 | 段 4 以降 |

---

## 7. 確かめていないこと
- Linux の箱の中での claude／codex の実際の通信先・メモリ・`--permission-prompt-tool stdio` の挙動（3-0 で実測）。
- Cloudflare Tunnel のダッシュボードで path の道を 1 本足した時の優先順位と、SSE が途中で溜められないか（staging では確かめられないので、本番の道を足した直後に PO と確かめる）。
- `codex login --device-auth` 相当の現行版のフラグ、app-server の利用者への問い合わせの仕組み。
- 本番の Postgres の版と `pg_read_all_data` の利用可否（compose は `postgres:16-alpine`【事実】なので使える見込み）。
