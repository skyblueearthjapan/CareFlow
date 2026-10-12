# PC Assistant 調査と「らく助エージェント（VPS で Claude Code・ブラウザーで画面）」の実現性

調査日 2026-10-12。読むだけの調査（ビルド・実行・サーバー接続なし）。
表記：**【事実】**＝ファイルで確かめたこと（パス:行）。**【推測】**＝私の見立て。**【未確認】**＝資料からは確定できないこと。
略記：`PA` = `C:\Users\imaizumi.LINEWORKS-NET\Documents\PC-Assistant`、`PAW` = `...\Documents\PC-Assistant-work`。

---

## 0. 結論（先に）

- PC Assistant（製品名「PCアシスタント＋」）は **Windows 専用の WPF アプリ**で、Claude Code を **PTY ではなく `claude -p` の stream-json（標準入出力の JSON 行）** で動かし、アプリ内蔵の **HTTP MCP サーバー**で道具・承認・質問を仲介する作り。Codex は `codex exec --json` / `codex app-server`（JSON-RPC）。Agent SDK・ACP は使っていない。
- 「エージェントは別の機械、画面は別の端末」の前例は **スマホ連動（PWA）** が唯一の本格実装：PC アプリ自身が loopback で Web 画面と `/api/*`（POST＋SSE）を出し、`tailscale serve` で tailnet 内だけに HTTPS 公開。中央の中継サーバーは作らない方針。Mac・iOS のフォルダーは**空**、Android Mini は端末内で完結する別製品（リモート接続ではない）。
- **コード（C#）はそのまま流用できない**（Core も `net10.0-windows`＋WPF）。流用できるのは **実測済みの CLI プロトコル知識・起動引数・隔離の設定・承認／質問の往復の形・画面の出し方（イベント→表示の対応表）・スマホ PWA の画面設計と安全策の考え方**。
- 新案は **技術的には十分可能**。おすすめは **案 (a)：らく助の Next.js に「エージェント」画面＋VPS に専用のエージェント中継サービス（Python/TS）が `claude -p --input-format stream-json --output-format stream-json` を利用者ごと・隔離した Linux ユーザー／コンテナで動かす**。PC Assistant の AG0 実測（DESIGN.md §2）がほぼそのまま設計書になる。
- 最大の論点は技術ではなく **(1) 本番 VPS でエージェントにシェルを渡す危険** と **(2) 顧客自身の Claude/ChatGPT サブスクをサーバー上で使わせる規約** と **(3) 患者の個人情報が LLM に出ること**。(2) は Anthropic の文書に「プラットフォームがホストする Claude Code に利用者が自分のサブスクでサインインすることは妨げない」との一文がある一方、「第三者が Claude.ai の資格情報を集める・保存する・仲介することは不可」ともあり、**サーバー側に顧客ごとの資格情報ファイルが残る形はグレー＝要確認**。

---

## 1. PC Assistant とは何か

### 1.1 目的・利用者【事実】
- 画面右下の小窓に頼むと、Claude Code（または Codex＝ChatGPT）が PC を操作して片付け、うまくいった作業を「スキル」として覚える（`PA\README.md:1-4`、`PA\DESIGN.md:1-5`）。
- 汎用製品を目指す。今の利用者はオーナー 1 人（`PA\docs\HANDOVER_SESSION_2026-10-12.md` §1）。
- 機能：小窓（依頼・スキル）、PCエージェント＝「係」（フォルダーごとの長生きする会話）、PCコンシェルジュ（係への振り分け、C0 で停止中）、録音と議事録、音声入力、スマホ連動（Tailscale PWA）、自動更新（同 §1「主な機能」）。

### 1.2 構成（UI 技術・プロセスモデル）【事実】
- .NET 10 WPF（`PA\src\PCAssistant.App\PCAssistant.App.csproj:10-12`、Core も `net10.0-windows10.0.17763.0`＋UseWPF：`PA\src\PCAssistant.Core\PCAssistant.Core.csproj:5-7`）。`PCAssistant.Hook` だけ `net10.0`（PreToolUse フック exe）。
- 図（`PA\DESIGN.md:13-37`）：
  - Runner が `claude -p … --output-format stream-json` を起動（Windows の Job object で子プロセスの木ごと管理）。stdout の JSONL → `StreamParser` → 画面の作業行・結果。
  - **アプリ内蔵の MCP サーバー** `http://127.0.0.1:<random>/mcp`（実行ごとの Bearer）。道具：`approve`（許可の問い合わせ）・`route`・`progress`・`ask_user`・`confirm_write`・`skill_*`・`file_*`・`pc_*`（jev-hands への中継）。Claude は `--mcp-config --strict-mcp-config` でここだけにつながる。
  - 「アプリが正面の扉」：組み込み道具は固定（WebSearch/WebFetch/Read/Glob/Grep/PowerShell/Bash）、ユーザー設定・他の MCP は読ませない（`DESIGN.md:31-35`）。
- 起動引数（小窓、1 回ごと）：`DESIGN.md:71-91`（`--setting-sources ""`、`--disable-slash-commands`、`--permission-prompt-tool mcp__pa__approve`、`--append-system-prompt-file`、`--max-turns 250`、`--resume`、プロンプトは stdin の UTF-8）。
- 係（エージェント）は **長生きする双方向 stream-json**（`PA\AGENT_PLAN.md:126-139`）：
  ```
  claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages
    --permission-prompt-tool stdio --setting-sources project,local
    --strict-mcp-config --mcp-config tools.json --settings isolation.json --model … [--resume id]
  cwd = 係のフォルダー
  ```
  最初の送信で起動、30 分アイドルで終了、次の送信で `--resume`（`AGENT_PLAN.md:141-143`）。

### 1.3 Claude Code / Codex との話し方【事実】
- **PTY は使っていない。Agent SDK も ACP も使っていない。** CLI を子プロセスとして起動し、stdin/stdout の JSON 行でやり取り。
- stdin へ送るもの：発言 `{"type":"user","message":{...}}`（画像は base64 の image ブロック）、中断 `control_request{subtype:"interrupt"}`、モデル変更 `control_request{subtype:"set_model"}`（`AGENT_PLAN.md:191-195`、実測 `DESIGN.md:100-111`）。
- CLI から来る `control_request` に返事：`can_use_tool`（許可）、`AskUserQuestion`（質問カード、答えは `updatedInput.answers`）、`elicitation`（MCP からの確認）。`--permission-prompt-tool stdio` が**必須**（無いと来ずに自動拒否）（`DESIGN.md:161-170`、`AGENT_PLAN.md:197-205`）。
- 受けるイベント：`system/init`（毎ターン）、`stream_event`（text_delta）、`assistant`、`tool_use/tool_result`（Write/Edit は `structuredPatch` で差分表示）、`system/task_*`（サブエージェント・コマンドの経過）、`rate_limit_event`、`system/api_retry`、`result`（`terminal_reason`、`total_cost_usd`、`permission_denials`）（`AGENT_PLAN.md:207-222`、`DESIGN.md:51-56`）。
- 実測で分かった落とし穴（どれも新案でそのまま効く）：
  - コマンドの途中出力は stream-json では流れない、終わってからまとめて来る（`DESIGN.md:113-115`）。
  - `--resume` 時は `--model` を毎回付けないと既定（Opus）に戻る（`DESIGN.md:111`）。
  - 作業中の追加送信は同じターンに取り込まれ result は 1 つ（`DESIGN.md:105`）。
  - `--bare` はサブスクの OAuth を読まない（`DESIGN.md:61`）。
- Codex：小窓は `codex exec --json`、係は `codex app-server`（stdio JSON-RPC：`thread/start|resume`、`turn/start|interrupt`、`model/list`、`account/rateLimits/read`、承認の往復）（`PA\docs\HANDOVER_SESSION_2026-10-12.md` §1「技術」、`PAW\providers-research\PROVIDERS.md:76`）。ファイル群は `PA\src\PCAssistant.Core\Agents\Codex*.cs`。

### 1.4 会話の表示・許可・道具・スキル・セッション【事実】
- 表示：イベント→画面の対応表（`DESIGN.md:202-230`）。道具名は画面に出さず日本語の作業行にする（`StepWording`）。
- 許可：小窓は `PermissionRules.Decide`、係は ① 慎重 / ② 標準 / ③ おまかせ の 3 段（`AGENT_PLAN.md:155-168`、`PA\src\PCAssistant.Core\Permissions\`）。承認は内容に紐づけ、送信・支払い・削除はカード。削除はごみ箱へ、PreToolUse フックで削除コマンドを止める（`DESIGN.md` §4、1353〜1720 行台）。
- スキル：SKILL.md＋Python スクリプト、動作ごとに read/write（`DESIGN.md` §5）。
- セッション：`session_id` を `agents.json` に保存、`--resume`（`AGENT_PLAN.md` §3.4）。係の自動メモリは係のフォルダー内 `.claude\memory`（`DESIGN.md:129-131`）。
- 隔離（`isolation.json` を `--settings` で渡す＝flagSettings は project/local に勝つ）（`DESIGN.md:121-141`）：
  ```json
  { "forceLoginMethod": "claudeai", "apiKeyHelper": "",
    "env": { "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_BASE_URL": "", "ANTHROPIC_CUSTOM_HEADERS": "" },
    "autoMemoryEnabled": true, "autoMemoryDirectory": "<フォルダー>/.claude/memory",
    "claudeMdExcludes": ["~/.claude/CLAUDE.md", "~/.claude/rules/**", "祖先の CLAUDE.md …"] }
  ```
  実測：エージェント自身が途中で `settings.local.json` に `ANTHROPIC_BASE_URL` を書くと**次のターンからサブスクのトークン付きで偽サーバーへ送られた**。上の上書きで防げた（`DESIGN.md:134-136`）。→ 新案でも必須の知見。

### 1.5 CLI のサインイン・課金の前提【事実】
- Claude：`claude auth status`（JSON の `loggedIn`）で判定、無ければ `claude auth login --claudeai` を**見える端末の窓で**起動（ブラウザーが開く）し、3 秒ごとに status を最大 5 分ポーリング（`DESIGN.md:2071-2073`）。
- Codex：`codex.exe -c forced_login_method="chatgpt" login status` で判定、`codex … login`（ローカルのコールバックで受けるブラウザー方式）を隠れて実行、出る一回限りの URL は表示もログもしない（`DESIGN.md:2183-2187`、`DESIGN.md:145-150`）。
- 課金：既定は**利用者自身のサブスク**。PC に `ANTHROPIC_API_KEY` があると黙って API 課金になるため、子の環境から `ANTHROPIC_*`・`CLAUDE_CODE_*` 等を消す（`DESIGN.md:62`、BillingGuard）。後に AK1 で「本人が選んだ時だけ API キー」も可能に（`DESIGN.md` §13 の AK1 行、`PA\docs\HANDOVER.md:275-278`）。
- 規約の調べ（`PAW\providers-research\PROVIDERS.md:97-117`、`PA\docs\HANDOVER.md:278,452`）：後述 §4.5。

---

## 2. Windows 専用か／リモートの層はあるか

### 2.1 Windows 専用度【事実】
- 本体（App・Core）は Windows＋WPF。Job object（子の木を止める）、DPAPI（鍵の保存）、UI Automation（jev-hands）、WASAPI（録音）、トレイ通知など Windows 固有の API に依存（`DESIGN.md:57,65`、HANDOVER_SESSION §1「技術」）。
- 内部 HTTP は手書きサーバー（`DESIGN.md:2467-2469`）、スマホ用は `PhoneHttpServer.cs`。

### 2.2 スマホ連動 = 「エージェントは PC、画面は別端末」の前例【事実】
- 計画 `PA\PHONE_PLAN.md`、実装 `PA\src\PCAssistant.Core\Phone\`（`PhoneHub*.cs`・`PhoneGate.cs`・`PhonePairing.cs`・`PhonePush.cs`・`TailscaleCli.cs` 等）、画面 `PA\src\PCAssistant.Core\Resources\phone\`（`index.html`・`app.js` 7,093 行の素の JS・`sw.js`）。報告 `PA\docs\REPORT_PH1.md`。
- 方式：
  - PC アプリが **127.0.0.1 で listen**、`tailscale serve --bg --https=443 http://127.0.0.1:<port>` で **tailnet 内だけ HTTPS 公開**（Funnel＝インターネット公開は拒否）（REPORT_PH1 L10-13、PHONE_PLAN P3・方式）。
  - **中央の中継サーバーもストアのアプリも作らない**。PWA をホーム画面に追加（PHONE_PLAN P10）。
  - 通信：書き込みは `POST /api/...`、画面への流れは **SSE**（`text/event-stream`、`PhoneHub.cs:484`）、状態は `GET /api/state`、ファイル木・アップロード・新しい係・Web Push（`DESIGN.md:2716-2743, 2784-2789, 2935-2937`）。
  - **同じ会話を PC とスマホで共有**（`IAgentUi` のイベントをスマホにも流す）。質問カードは先に答えた方が勝ち（REPORT_PH1「動くもの」）。
  - ペアリング：PC に QR（10 分で変わる合言葉入り）→ スマホで開く → PC で［このスマホを使う］→ その端末（`Node.StableID`）1 台だけ許可（REPORT_PH1 C1）。
  - 身元確認 8 段（REPORT_PH1「身元の確認」）：loopback のみ・接続元プロセスが tailscaled・Host が MagicDNS 名・`Tailscale-User-Login` が本人・`tailscale whois` で端末確認・承認済み 1 台・Origin＋専用ヘッダー（CSRF）・パス指定の CSP。成果物はエージェントごとの HMAC 鍵付き URL、sandbox iframe。
- その前に **「頭脳を SSH 先（Z2）で動かす」も設計・一部実測**したが見送り（`DESIGN.md:2444-2461`）：`ssh -R` で遠隔の claude からアプリの MCP につながった、日本語は ssh のコマンド行に載せない、ローカルの ssh を殺しても遠隔プロセスは残る、遠隔のログインが切れていた等。`IBrainTransport` で差し替えられる作り（`DESIGN.md:39-41`）。

### 2.3 Mac・iOS・Android・その他のフォルダー【事実】
- `PC Assistant for Mac`、`PC Assistant Mini for iOS`：**空のフォルダー**。
- `PC Assistant Mini for Android`：Windows 版の「依頼」だけを Android に移した**端末内で完結する別アプリ**。Codex の `codex-app-server` Linux musl 版を APK に同梱し、ChatGPT のサブスクでサインイン、ユーザー補助サービスで画面操作（`README.md:1-30`、`DESIGN.md:1-60`）。PC にはつながらない。サインインは app-server の `account/login/start`（chatgpt）で URL を受け Custom Tabs で開き、`localhost:1455` のコールバックを端末内の codex が受ける。駄目なら **`chatgptDeviceCode`**（rust-v0.162.1 にあり）に切り替える計画（`DESIGN.md:100-101`、`README.md:242`）。→ **「Linux 上の codex app-server を自前 UI からサインインさせる」前例**として新案に直結。
- `PCアシスタント`：利用者データ（エージェント・スキル・議事録・録音）。`PC-Assistant-work`：レーンの worktree・レビュー・`director-scripts`・`providers-research`（規約・資格情報の調べ）。

---

## 3. 流用できるもの／できないもの

| 区分 | もの | 新案での扱い |
|---|---|---|
| ◎ 知識として流用 | `claude -p` stream-json の実測（`DESIGN.md` §2・AG0 S1〜S6）、起動引数（`AGENT_PLAN.md:126-139`）、`control_request` の往復の形（can_use_tool / AskUserQuestion / interrupt / set_model / elicitation） | Linux でも同じ CLI なのでそのまま設計の正典にできる【推測：OS 差は小さい。ただし Linux での再実測は必要】 |
| ◎ | `isolation.json`（BASE_URL 書き換え攻撃の封じ込め、`claudeMdExcludes`、`autoMemoryDirectory`）、BillingGuard（環境変数の消去） | 必須。VPS では `/opt/carelink/.env` 等を子に継がせない設計と組み合わせる |
| ◎ | イベント→画面の対応表（`DESIGN.md:202-230`、`AGENT_PLAN.md:207-222`）、作業行・差分・サブエージェントのまとめ方 | Next.js の会話画面の仕様としてそのまま使える |
| ○ 設計・見た目の参考 | スマホ PWA（`Resources\phone\app.js`・`index.html`）：会話、≡ の一覧、質問カード、止める、成果物シート、SSE で流す方式、CSRF・CSP・成果物の別オリジン | 素の JS なので読み物としては参考になるが、らく助は Next.js/React なので**書き直し**【推測】 |
| ○ | 3 段の許可（慎重・標準・おまかせ）、「アプリが正面の扉」＝道具を MCP に集めてアプリが門番をする考え、係＝フォルダー＝会話、コンシェルジュ（目標ごとのボード） | らく助では「らく助 API を叩く MCP 道具」に置き換え、門番をバックエンドが持つ |
| ○ | Codex app-server の扱い（`Codex*.cs`）、Android の device-code サインイン計画 | ChatGPT を選べるようにするなら参考 |
| ○ | 規約・課金の調べ（`PAW\providers-research\PROVIDERS.md`） | §4.5 の土台 |
| × 不要／流用不可 | **TypeSafe Jev・jev-hands・UI Automation・`pc_*` 道具・画面の片付け・ScreenLease**（PO 指示：PC 操作は不要＝対象外） | 使わない |
| × | WPF の画面、Job object、DPAPI、WASAPI 録音、トレイ通知、自動更新（GitHub releases＋署名）、Windows のプロセス木の判定 | Linux/ブラウザーでは不要または別物 |
| × | Tailscale 固有の門番（tailscaled のプロセス確認・whois・1 台の承認） | らく助は Cloudflare Tunnel＋らく助のログイン。考え方（多段の門番）だけ参考 |
| × | C# のコード一般（Core が `net10.0-windows`＋WPF） | 再利用は不可。新しく Python（FastAPI 側）か Node で書く |

---

## 4. 新案の実現性

### 4.1 前提（らく助側）【事実：CareFlow02 と MEMORY】
- FastAPI（`backend\`）＋Next.js（`frontend\`）＋Postgres、本番は `docs\deployment\docker-compose.production.yml`（postgres・backend・frontend・kaipoke）、Cloudflare Tunnel で `carelink.kaipoke-api.net`。秘密は `/opt/carelink/.env`。VPS は UTC。
- らく助には既に ロール（admin/staff）・ログイン・レートリミットがある（MEMORY：ロール二軸・ログイン レートリミット）。

### 4.2 案 (a) らく助の画面＋VPS 上のエージェント中継サービス（おすすめ）
**作るもの**【推測】
1. `agent-gateway` サービス（新しいコンテナ。Python なら FastAPI＋asyncio、Node なら素の TS。**Claude Agent SDK を使うか、CLI を直接 stream-json で動かすか**）：
   - 利用者（または事業所）ごとに `claude -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages --permission-prompt-tool stdio --setting-sources project,local --strict-mcp-config --mcp-config <らく助の MCP> --settings isolation.json --model … [--resume]` を起動・保持（PC Assistant の §3.1 と同じ）。
   - ブラウザーとは **WebSocket か SSE＋POST**（スマホ連動と同じ SSE＋POST が Cloudflare Tunnel 越しでも素直【推測】。WebSocket も Tunnel は通す）。
   - `can_use_tool`・`AskUserQuestion` を画面のカードに中継、`interrupt` で止める、`--resume` で続き。
2. **らく助の MCP サーバー**（backend に `/mcp` を足すか別サービス）：道具は「訪問予定を読む」「ならし案を作る（week-leveling の考え方）」「カイポケ RPA のジョブを積む（送信は承認カード必須）」等、**らく助 API をユーザーの権限で叩くもの**に限定。DB 直結や任意 SQL は渡さない。
3. Next.js に `/agent` ページ（らく助のブランド・会話・質問カード・承認カード・止める・成果物）。PC Assistant の対応表とスマホ PWA を仕様書にする。
4. 隔離：エージェントの子プロセスは **backend とは別コンテナ、別 Linux ユーザー、`/opt/carelink/.env` も Docker ソケットもマウントしない、ネットワークは Anthropic と らく助の MCP だけ許可**。組み込みの Bash/Read/Write は**切る**（`--tools ""` 相当、または許可を厳しく）か、作業用の空ディレクトリに閉じ込める。

**Agent SDK を使う場合**【推測】：Python/TS の Claude Agent SDK は内部で同じ CLI を stream-json で動かすので、PC Assistant の知見と同じ土俵。SDK だと許可コールバック・MCP（in-process の SDK MCP サーバー）が楽。ただし Anthropic の文書は「Agent SDK で作った製品で claude.ai ログインを提供するのは事前承認なしに不可」としている（§4.5）ので、**顧客のサブスクを使う方針なら「改変していない Claude Code CLI に利用者自身がサインインする」形のほうが文言上は安全側**。

**リスク**
- 本番 VPS でのシェル：最大の危険。患者データ・カイポケの資格情報・DB パスワードが同じ機械にある。→ 組み込みのシェル道具は切り、らく助の道具だけにするのが基本。どうしてもシェルが要るなら別 VPS／使い捨てコンテナ。
- マルチ利用者：会話・自動メモリ・資格情報が混ざらないよう、利用者ごとに `HOME`（または `CLAUDE_CONFIG_DIR`）・作業ディレクトリ・Linux ユーザーを分ける。PC Assistant の実測でも既定のメモリ置き場が潰れて混ざる問題があった（`DESIGN.md:131`）。
- 同時実行・メモリ：CLI 1 つあたり Node プロセス 1 つ。VPS のメモリ次第で同時数に上限（PC Assistant も同時 3 で頭打ちにしている：`AGENT_PLAN.md` D11）。
- 個人情報：患者名・住所・病状が Anthropic（または OpenAI）へ出る。個人のサブスクは**既定で学習に使われる**設定がある（`PA\docs\HANDOVER.md:278` ⑥）。医療・介護の要配慮個人情報なので、送る範囲（仮名化・ID 化）・同意・契約（API／商用条件なら学習なし）を先に決める。PC Assistant の「外へ出るデータの一覧」（`DESIGN.md` §13）と同じ表をらく助でも作るべき。
- 監査：誰が何を頼み、どの道具でカイポケに何を送ったかの記録（PC Assistant の `data\記録\*.jsonl` 相当）。

**ざっくり工数**【推測】：中継サービス＋MCP 道具 5〜10 個＋Next.js 画面＋隔離のコンテナ＝ **2〜4 週間（1 人＋Claude Code）**。サインイン（§4.4）と多利用者の隔離を本気でやると +1〜2 週間。

### 4.3 案 (b) PC Assistant のリモート層（スマホ連動の方式）を Linux ホストで再利用
- 方式自体（ホストが Web 画面と SSE/POST を出し、別の端末のブラウザーで見る）は新案と同じ形。ただし実装は C#・Windows・Tailscale 前提で、**コードは持っていけない**。
- 顧客に Tailscale を入れてもらうのは「ブラウザーだけで使える」という狙いに反する。らく助は既に Cloudflare Tunnel＋自前ログインがあるので、門番はそちらで作るのが自然。
- 結論【推測】：**案 (a) の中に「設計と安全策の手本」として取り込む**のが現実的。単独の案としては採らない。

### 4.4 案 (c) 顧客の PC Assistant アプリが VPS につながる
- PC Assistant には「頭脳を遠隔で動かす」`IBrainTransport` の口と、SSH 先の実測がある（`DESIGN.md:39-41, 2444-2461`）。理屈では「頭脳＝VPS の claude、画面＝顧客 PC の PC Assistant」もありうる。
- しかし ① 顧客にアプリ（Windows 専用・282 MB の ZIP）を入れてもらう必要がありブラウザーだけにならない ② VPS に SSH を開ける・顧客ごとの鍵配布が要る ③ PC Assistant の価値の中心（PC 操作・スキル・録音）は今回不要 ④ 見送り済みの方式（SSH の遠隔プロセスが残る、ログイン切れ等の未解決点）。
- 結論【推測】：**採らない**。ただし「らく助の MCP サーバーを公開して、PC Assistant の係からも使える」ようにすることは将来の相乗効果としてあり（PC Assistant は係ごとに MCP を足せる：`DESIGN.md:172-179`）。

### 4.5 顧客自身の Claude / ChatGPT アカウントで画面からサインインできるか（PO 追加要件 1）

**PC Assistant のやり方【事実】**：どちらも**利用者の PC 上で CLI 自身のサインイン画面**に任せている（Claude は `claude auth login --claudeai` を見える端末で、Codex は `codex login` のローカルのコールバック）。アプリは資格情報に触れない設計（§1.5）。このやり方は「CLI とブラウザーが同じ機械」が前提で、**VPS ではそのままは使えない**（ブラウザーの戻り先 `localhost` が顧客の PC を指すため）。

**VPS で動く CLI に、顧客の PC のブラウザーからサインインさせる方法【推測／一部未確認】**
- Claude Code：
  - `claude auth login` / `claude setup-token` は、ブラウザーを開けない環境では URL を表示し、ブラウザーで認可した後に出るコードを**貼り付けさせる**流れがある【推測：Claude Code の一般的な SSH 先での挙動。現行版の非対話での正確な出力形式は未確認】。中継サービスがこの CLI を PTY で動かし、URL を画面に出し、顧客が貼ったコードを CLI の入力に流せば成立する見込み。
  - `claude setup-token` は 1 年有効の OAuth トークン（`CLAUDE_CODE_OAUTH_TOKEN`）を出す（`PAW\providers-research\PROVIDERS.md:92`【事実：その資料の記載】）。ただし**トークンを中継サービスが受け取って保存する形**は、下の「資格情報を集める・保存する・仲介する」に当たりやすい。
  - 保存先：Linux では `~/.claude/.credentials.json`（Windows は `%USERPROFILE%\.claude\.credentials.json`：`PROVIDERS.md:92`）。顧客ごとに別の `HOME`／`CLAUDE_CONFIG_DIR`・別 Linux ユーザー・権限 600 で分ける必要。
- Codex（ChatGPT）：
  - `codex app-server` の `account/login/start` に **ChatGPT の device code 方式（`chatgptDeviceCode`）**がある（`PC Assistant Mini for Android\README.md:242`【事実：その資料の記載】）。VPS の codex が出すコードと URL を画面に見せ、顧客が自分の PC で認可する形がそのまま合う。CLI にも `codex login --device-auth` 相当があるはず【未確認】。
  - 保存先は `CODEX_HOME`（既定 `~/.codex`、auth.json）。顧客ごとに `CODEX_HOME` を分ける。
- 多顧客の隔離【推測】：1 顧客＝1 Linux ユーザー（または 1 コンテナ）＋専用 HOME。中継サービスは資格情報ファイルを**読まない**（CLI が自分で読む）。ログ・画面に URL やコードを残さない（PC Assistant も authorize URL を表示・ログしない：`DESIGN.md:2186`）。

**規約【資料の事実＋未確認】**
- Anthropic「Legal and compliance」（`PROVIDERS.md:97-103` に引用）：
  - 「Each end user must authenticate with their own … Claude subscription plan credentials」→ 顧客自身のアカウントという方針自体は合う。
  - 「Anthropic does not permit third-party developers to offer Claude.ai login into their own applications … developers may not collect, store, or intermediate Claude.ai credentials or session tokens」→ **らく助ブランドの画面でサインインを仲介し、VPS に顧客のトークンを置く形はこれに触れうる**。
  - 一方「Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription, **including where a platform hosts Claude Code**」→ プラットフォームが Claude Code をホストし、利用者が**改変していない CLI に自分で**サインインする形は許容と読める。
  - 「Pro and Max plans assume ordinary, individual usage」→ 業務での常用・自動実行は枠と規約の両面で注意。Consumer Terms は「API キーか明示の許可がない自動アクセス」を禁止（`PROVIDERS.md:105`）。
  - → **判定：グレー。「顧客がブラウザーで CLI 自身の認可に進み、コードを CLI に渡す」形は文言上の救いがあるが、「中継が URL とコードを運び、トークンを VPS に保存する」実態が "intermediate / store" とみなされるかは Anthropic に確認が必要**（PC Assistant 側も「公開前に Anthropic・OpenAI に確認」を残タスクにしている：`PROVIDERS.md:141-142`、`HANDOVER.md:452`）。
- OpenAI：Codex CLI を ChatGPT でサインインして使うのは公式の使い方。他社製品からプランの枠を使う「Sign in with ChatGPT」は**プレビューで、オープンソース・ローカル実行向け、有料・リモート製品は申込制**（`PROVIDERS.md:65,107,117`）。**サーバーでホストする有料製品は申込・確認が必要＝未確認**。
- 安全な代替：**顧客自身の API キー（BYOK）**は両社とも正規の道（`PROVIDERS.md:13,116`、`HANDOVER.md:278` ⑦「持ち込みの API キーは正規の道。会社のキーを客に配らない」）。API は既定で学習に使われず、個人情報の面でも有利。ただし従量課金で上限設定が要る。

**おすすめ【推測】**：① まずは**事業所の API キー（顧客自身の Anthropic 組織のキー）を画面で登録**する形で始める（規約・学習・監査が明快）。② サブスクでのサインインは、Anthropic／OpenAI に「プラットフォームがホストする未改変 CLI に、利用者が自分のアカウントでサインインする」形の可否を書面で確認してから。③ どちらでも、らく助の運営者（PO）のキーやアカウントを顧客に使わせない。

### 4.6 案の比較

| 案 | 作るもの | 主なリスク | 工数【推測】 | 評価 |
|---|---|---|---|---|
| (a) らく助画面＋VPS の中継＋らく助 MCP | 中継サービス、MCP 道具、/agent 画面、隔離コンテナ、サインイン／キー登録 | 本番 VPS のシェル、個人情報、規約、同時数 | 2〜4 週＋サインイン／隔離 1〜2 週 | **おすすめ** |
| (b) PC Assistant のリモート層を Linux に | 実質 (a) と同じものを C# 抜きで書き直し | Tailscale 前提が合わない | (a) と同等以上 | (a) の手本として吸収 |
| (c) 顧客の PC Assistant が VPS につながる | SSH／トンネル、遠隔の頭脳、顧客 PC へのアプリ配布 | ブラウザーだけにならない、見送り済みの方式 | 大 | 採らない |

### 4.7 最初の一歩（提案）【推測】
1. 規約の確認（Anthropic・OpenAI）と、患者データを LLM に出す範囲の決定（PO・お客様）。
2. 読むだけの MCP 道具 3 つ（今週の予定・職員の空き・実現性チェック）＋/agent 画面＋**シェルなし**の claude で試作（ステージングまたは別コンテナ、本番 DB は読み取り専用ロール）。
3. Linux で AG0 相当の実測（stream-json・`--permission-prompt-tool stdio`・isolation.json・サインインのコード貼り付け／device code）を再確認。
4. 書き込み系（カイポケ送信など）は承認カード必須・監査記録つきで後から。

---

## 5. PC Assistant の現状（HANDOVER_SESSION_2026-10-12）【事実】
- 最新版 **0.0.8**（main `96dded4`）、GitHub releases に公開済み、オーナー PC も 10-12 06:02 に更新済み（§0）。
- このセッションで 0.0.4〜0.0.8 を 5 回リリース。0.0.8 = DN1（「✓ 完了」表示と Windows 通知）、CF1／D73（小窓の要らない承認を減らす）、CF2／D74（係の ② 標準でカードを減らす：フォルダー内を読むだけのコマンド・WebSearch・公開 URL の WebFetch・ごみ箱）（§2-1・2-2）。
- 試験 10,379 件中 合格 10,353・失敗 1（負荷時の揺れ）・スキップ 25（§2-2「検証」）。
- **コードの途中作業なし**、worktree も消去済み（§2-4）。
- 次：オーナーと 0.0.8 の実機確認（§3-1 の 8 項目）、0.0.7 以前の未確認分（TM1・MD1・CC1・PH8・CW1、B-1 の 10 項目）（§3-2）。
- あとで直す一覧：CF2 の抜け道の再探索（本物のシェル）、jev-hands の部分一致、DN1 の係の完了表示、UP3 の更新競合、揺れる試験 5 つ（§3-3）。
- 停止中：PCコンシェルジュ（C0 で止め、再開はオーナー指示時のみ）（§3-4、`PA\docs\CONCIERGE_PLAN.md`）。
- 既知の制約：PC のメモリー 16GB で全件試験中にシェルが落ちることがある（§5）。
- 依存：Claude Code CLI、Codex CLI 0.159.2、jev-hands（TypeSafe Jev）、Tailscale、GitHub（§5「依存」）。

## 6. PC Assistant の資料の中で、新案に近い計画
- `DESIGN.md` §10「頭脳を Z2 で SSH 越しに動かす」：遠隔の claude からローカルの MCP に `ssh -R` でつなぐ実測あり、見送り（`DESIGN.md:2444-2461`）。
- `PHONE_PLAN.md`：ホストが PWA を出し別端末のブラウザーで操作。P10「中央サーバーを作らない」。将来「オープンソース版は別に作る予定」。
- `PROVIDERS.md` §2.6：「including where a platform hosts Claude Code」の引用＝ホスト型の可否に関する一番近い記述。
- **らく助・VPS・Linux ホストの Web 版**を直接扱った計画は見当たらなかった【事実：README・DESIGN・AGENT_PLAN・PHONE_PLAN・HANDOVER 系を grep した範囲】。
