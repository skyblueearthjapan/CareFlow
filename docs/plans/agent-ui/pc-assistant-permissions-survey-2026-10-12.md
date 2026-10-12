# PC-Assistant の「確認の 3 段階」（D27）調査 と らく助エージェントの確認の段階【案】（2026-10-12）

状態: **調査（事実）＋提案（PO 未了承）**。コード・本番は触っていない。
読んだもの: `PC-Assistant\AGENT_PLAN.md` §3.1・§3.1a・§3.3・§6、`DESIGN.md` L2644〜2699（Codex の承認・code_mode）、`src\PCAssistant.Core\Agents\AgentPermissions.cs`・`AgentProtocol.cs`・`AgentSession.cs`、`src\PCAssistant.App\MainViewModel.AgentPermission.cs`・`Settings\SettingsAgentsPage.xaml`、`docs\REPORT_PM2.md`・`REPORT_CG1.md`・`REPORT_CG3.md`・`REPORT_CG4.md`・`REPORT_PV3.md`・`REPORT_TH1.md`・`HANDOVER.md`・`CONCIERGE_PLAN.md`・`MA1_PLAN.md`。らく助側は `phase3-implementation-design-2026-10-12.md`（以下「段 3 設計」）、`agent/AGENTS.md`、`agent/rk/ops/*.py`。

書き方: **【事実】＝ファイルと行で確かめたこと**、**【案】＝この文書の提案**、**【推測】＝確かめていない読み**。

---

## 0. 要点（先に）

1. 【事実】PC-Assistant は「① 慎重／② 標準（既定）／③ おまかせ」を**エージェントごと**に持ち、設定に「新しいエージェントの既定」を 1 つ持つ。Claude には CLI の permission mode＋isolation.json の deny/ask を、Codex には sandbox の種類を当て、**最後の判断はアプリの `AgentPermissions.Decide` 1 か所**が `can_use_tool`／承認の問い合わせに答える。
2. 【事実】どの段階でも止めるもの（鍵のファイル＝KeyFileGuard、削除のコマンド＝D64、自動で動く設定の書き換え、追加の権限の要求）があり、段階は「その外側で、どこまでカードを出すか」だけを変える。
3. 【事実】**10-09 の本物の確認で、Codex（Sol）が code mode の中からコマンドを 4 回動かし、承認の問い合わせが 1 度もアプリに来なかった**（DESIGN.md:2652）。「CLI が聞いてくれること」を守りにすると破れる、という教訓。
4. 【案】らく助では**道具の許可カードは引き続き出さない（D13 を保つ）**。段階を当てるのは「**反映（書き込み）の承認のしかた**」だけにし、操作を危なさで 4 つに分ける（読む／戻せる小さな直し／戻せない・広い・外へ出る／いつも禁止）。
5. 【案】段階は「**慎重／標準（既定）／小さな直しは任せる**」の 3 つ。**PC-Assistant の ③「確認なし」に当たる段は作らない**（戻せない操作・カイポケ送信は、どの段でも 1 回ごとに人が押す）。段の上限は**事業所ごとに PO だけ**が決め、管理者は上限の中で自分の段を選ぶ。会話の中の「この会話では確かめない」は戻せる小さな直しにだけ出す。
6. 【案】守りの芯は今の段 3 設計のまま（箱に鍵も道も無い・中継だけが承認番号を確かめて書く）。段階は中継の中の「承認番号を出す条件」として実装し、**CLI 側（Claude の permission mode・Codex の approval_policy）には一切頼らない**——これが code_mode の教訓のらく助への当てはめ。

---

## 1. PC-Assistant の事実

### 1-1. 3 つの段階と既定

| 段階 | 説明の 1 行（画面の文言） | 出典 |
|---|---|---|
| ① 慎重 `Careful` | 「ファイルの編集やコマンドの前に、毎回確認します」 | `AgentPermissions.cs:895` |
| ② 標準 `Standard` | 「フォルダーの中の読み書き・読むだけのコマンド・Web の検索と閲覧・ごみ箱へ移すことは確認なし。ほかのコマンド・フォルダーの外・送信は確認します。画面の操作は、決まりで見分けられないことにも届きます（画面を確認なしで動かしたくないエージェントは「慎重」に）」 | `AgentPermissions.cs:896` |
| ③ おまかせ `Trust` | 「確認せずに進めます」 | `AgentPermissions.cs:897` |

- 【事実】新しいエージェントの既定は ② 標準（`AgentPermissions.cs:248` `NewAgentDefault = Standard`）。D27 より前からのエージェントは更新のときに一度だけ明示の `Trust`＋`legacyTrust` に移し、設定の行に「前からあるエージェントなので「おまかせ」のままです」と出す（`AgentPermissions.cs:250-254`・`:926`、`AGENT_PLAN.md:162`、`REPORT_PM2.md:39`）。
- 【事実】**段階が読めない・無いときは ② 標準に倒す**（「never fail open to おまかせ」、`AgentPermissions.cs:256-258`、レビュー PM2 F4）。ファイルの読み込み失敗も標準（`AgentPermissions.cs:868-881`）。
- 【事実】置き場所: エージェントごとの段階は `agents.json` の `permission`、全体の既定は Windows ユーザーごとの `%LOCALAPPDATA%\PCAssistant\agent-permission.json` `{"newAgents":"standard"}`（`AgentPermissions.cs:858-886`、`AGENT_PLAN.md:162`）。**事業所・フォルダー単位の設定は無い**（エージェント＝フォルダーなので、実質フォルダーごと）。
- 【事実】変えられるのは PC の持ち主（単一利用者のアプリ。設定 › エージェントのセグメント「確認のしかた」、`SettingsAgentsPage.xaml:76`、保存 `MainViewModel.AgentPermission.cs:55-78`、保存失敗は errors.log に 1 行）。PCコンシェルジュ（窓口）は**標準で固定**・段階の行を出さない（`AgentPermissions.cs:258`、`CONCIERGE_PLAN.md:71`）。
- 【事実】段階の変更は**次の依頼から**（作業中の依頼は始めた段階のまま。`AgentPermissionText.DefaultHint` `AgentPermissions.cs:917`、`AgentSession.cs:1643` `PermissionChanged()` で次の送信の前に `--resume` で起こし直す）。
- 【事実】オーナーの PC は実は新しい係の既定が「おまかせ」になっていて、確認なしで `rm` が通る状態だった、と 10-09 に判明（`HANDOVER.md:688`）→ D64（削除は全段階でごみ箱経由）の発端。**「既定を変えられる」こと自体が事故の元になった**。

### 1-2. 段階 → Claude Code のフラグと settings

| 段階 | 引数（`AgentPermissions.cs:280-285`） | isolation.json の `permissions`（`:300-333`） |
|---|---|---|
| ① | `--permission-mode default --permission-prompt-tool stdio` | deny＝KeyFileGuard＋削除コマンド（D64）、ask＝`Bash`・`PowerShell`＋フォルダーの自動で動くファイル（`.mcp.json`・`.claude/settings*.json`・`.claude/agents/**`・`.claude/commands/**`・`.codex/**`・`.agents/skills/**`、絶対パス）（`:291`・`:298`） |
| ② | `--permission-mode acceptEdits --permission-prompt-tool stdio` | ① と同じ |
| ③ | `--dangerously-skip-permissions --permission-prompt-tool stdio` | deny だけ（ask なし） |

- 【事実】`--permission-prompt-tool stdio` はどの段でも必須（無いと `can_use_tool` も AskUserQuestion も来ない。`AGENT_PLAN.md:131`）。
- 【事実】deny ルールは bypassPermissions（③）でも効く、明示の ask は acceptEdits でも自動にならない、を前提にしている（`AgentPermissions.cs:236-243` の注、公式文書 2026-10-05 読み）。
- 【事実】API キーの経路では Claude Code が permission mode を `default` に強制するので、② はアプリ側で「フォルダーの中の編集は許可」を補う（`AgentPermissions.cs:79-84`・`:513-516`、レーン AK1）。**CLI の mode に頼り切らず、アプリの判定で同じ結果になるようにしてある**。

### 1-3. 段階 → Codex の承認・sandbox

- 【事実】固定版 codex 0.159.2 は `approval_policy="untrusted"` を断るので、**どの段階も `on-request`**、段階は sandbox とアプリの答えで分ける（`DESIGN.md:2650`・`:2689-2693`、`REPORT_CG1.md:97`）。
  - ① `read-only`（書き込み・書くコマンド・ネットワークはすべて問い合わせ→カード）
  - ② `workspace-write`（係のフォルダーだけ書ける・ネットワークなし）
  - ③ `danger-full-access`、問い合わせにはアプリが「はい」。**`never` にしない理由**: `never` だと Codex は何も聞かないので、鍵（KeyFileGuard）と削除（D64）の確かめに通せなくなる（`DESIGN.md:2692`）。
- 【事実】承認をモデルに任せる機能（`approvals_reviewer`・`guardian_approval` ほか）は**必ず off**、始める前に `config/read` と `experimentalFeature/list` で off を確かめ、違えば始めない（`DESIGN.md:2686`・`:2688`）。
- 【事実】問い合わせの種類: `item/commandExecution/requestApproval`・`item/fileChange/requestApproval`・`item/permissions/requestApproval`（request_permissions：①② カード、③ 許可、範囲はそのターンだけ）・`mcpServer/elicitation/request`・`item/tool/requestUserInput`（Codex の質問 → 質問カード）（`DESIGN.md:2694-2699`）。
- 【事実】利用者の `~\.codex\rules\*.rules`（execpolicy）で許したコマンドは承認なしで動き D27 を通らない、と記録（`REPORT_PV3.md:20`）。

### 1-4. どの段でも止めるもの（段階の外側）

| 何 | どう止める | 出典 |
|---|---|---|
| 鍵・登録簿・段階の設定ファイル（`agents.json`・`agent-permission.json`・アプリの `settings.json` など） | isolation.json の deny（Read と Edit）。Codex はブリッジがカードなしで断る | `AgentPermissions.cs:316-333`、`REPORT_PM2.md:39`、`DESIGN.md:2656` |
| 削除のコマンド（rm・del・Remove-Item…） | deny＋カードなしの Deny、「ごみ箱へ移す」道具だけ（D64）。スクリプトの中も読む（DEL2） | `AgentPermissions.cs:419-436`、`HANDOVER.md:688` |
| アプリのフォルダーに触るコマンド | Deny | `AgentPermissions.cs:422-423` |
| 自動で動くファイル（MCP・フック）の書き換え | ①② は専用カード（「確認しない」なし）、③ は Deny | `AgentPermissions.cs:442-459`・`:503-508` |
| 追加の権限・決まりの変更の要求（Codex） | **③ でも**カード | `AgentPermissions.cs:437-441` |
| 起動前に新しい・変わった自動で動くもの | ①② は開始前にカード（承認は指紋で保存） | `AGENT_PLAN.md:164`・`:166` |
| いちばん上のモデル（Fable・Astra） | 渡さない・init で来たら止める（D28） | `AGENT_PLAN.md:168` |
| 課金の付け替え（BASE_URL・API キー） | isolation.json の上書き＋init の `apiKeySource` 検査 | `AGENT_PLAN.md:417-420` |

### 1-5. 画面に出るもの（カード）

- 【事実】確認カードは**質問カードと同じ道**（`AgentQuestion.IsPermission`）。PC とスマホの両方に出て**先に答えた方が勝つ**（`AGENT_PLAN.md:167`、`AgentProtocol.cs:163-185`）。
- 【事実】見出しは**アプリの言葉**（モデルの言葉ではない）＋中身（コマンド・パス・アドレス。長ければ頭と尾＋「…（途中 N 字を省略）…」、1200 字まで、秘密は［伏せました］）（`AgentPermissions.cs:44-50`・`:710-737`、`AgentProtocol.cs:176-180`）。見出しの例:「コマンドを実行します」「ファイルに書き込みます」「送信・削除・購入などに当たる画面の操作をします」「ID やトークンを含むアドレスを開きます」（`AgentPermissions.cs:949-980`）。
- 【事実】ボタンは 3 つ: **承認**（今回だけ許します）／**この会話では確認しない**（この会話の残りは、同じ種類の操作を確認せずに進めます）／**やめる**（この操作はしません）（`AgentPermissions.cs:941-947`）。スマホで書いた言葉は「やめる＋その言葉」をモデルに返す（`AgentProtocol.cs:189-205`）。閉じる・止める・新しい会話は「やめる」と同じ。数字キーでは答えない（`AGENT_PLAN.md:167`）。
- 【事実】「この会話では確認しない」は**危なくない繰り返しにだけ**出す（フォルダーの外の読み・① のフォルダーの中の編集・① のふつうの画面の操作。送信・コマンド・削除には出さない。`AgentPermissions.cs:49`・`:405-407`、`REPORT_PM2.md:40`）。出していないのに来た「確認しない」は「やめる」扱い（`AgentProtocol.cs:197-198`）。会話または段階が変わると消える（`AgentSession.cs:3549-3560`）。
- 【事実】窓口（PCコンシェルジュ）は係のカードを自分の会話に写すが**カードに答えない・権限を上げない**。写したときの言葉は「この依頼では確認しない」（`CONCIERGE_PLAN.md:44`・`:71`、`MA1_PLAN.md:27`・`:250`、`DeskText.cs:295-297`）。MA1 の論点に「流れの中の送信は係が『おまかせ』でも必ずカード」の選択肢がある（`MA1_PLAN.md:370`）。
- 【事実】見た目: 承認＝主色の塗り、この会話では確認しない＝主色の枠線、やめる＝ふつうの枠線、オレンジの盾（`REPORT_TH1.md:20`、`REPORT_PM2.md:11`）。見出しに段階は出さない（読む量を増やさない、`REPORT_PM2.md:12`）。

### 1-6. 記録

- 【事実】段階の保存失敗は errors.log に 1 行（`MainViewModel.AgentPermission.cs:68-71`）。始める前に止めた理由は赤い 1 行（`AgentPermissionText.HookMissing` ほか `:989-998`）、Codex の on の機能は診断用にログ 1 行（`DESIGN.md:2688`）。
- 【推測】カードとその答えは会話の控え（`chat.jsonl`）に質問カードとして残るが、**「誰がどの段階で何を承認したか」の専用の監査記録は見当たらなかった**（単一利用者の PC アプリなので要らない、という作り）。

### 1-7. 本物で分かったこと（教訓）

| 日 | 分かったこと | 出典 |
|---|---|---|
| 10-06 | ② で `rm`・`Remove-Item`・外の Read・`ls`・WebSearch はカード、フォルダーの中の Write/Edit はカードなし。① は CLI が `--permission-mode default` を受け付けた | `REPORT_PM2.md:60-65` |
| 10-06 | **動いている claude はフォルダーの `.claude/settings.local.json` を会話の途中でも読み直す**（足した ask がすぐ効く）。`--settings`（isolation.json）は読み直さない。→ settings.local.json の書き込みを承認すると、その中の allow がその場で効く | `REPORT_PM2.md:66`、`HANDOVER.md:365` |
| 10-09 | **Sol 5.6 の係が code mode（`custom_tool_call` の `exec` の JS から `tools.exec_command`）でコマンドを 4 回動かし、承認の問い合わせは 1 度もアプリに来なかった**。カードも後からの止めも働かず、ほかの係の引き継ぎ書が読まれた恐れ。今の ChatGPT のモデルはすべて `tool_mode: code_mode_only`。しかも動いたのはアプリの固定版でなく利用者の npm の codex 0.153.4 | `DESIGN.md:2652` |
| 10-09〜10 | 対策 CG3: Codex 自身の道具を切る・始める前に `config/read`／機能一覧／MCP 一覧で確かめる・許した種類以外の出来事でターンを止め会話を「毒入り」に・コマンドはアプリの `run_command` だけ・固定版の codex だけ（署名を確かめる）・モデルの目録を上書き（`tool_mode: null`） | `DESIGN.md:2653-2681`、`REPORT_CG3.md:7`・`:27`、`REPORT_CG4.md:5-7` |
| 10-10 | 0.159.2 は消そうとした道具を既定で on にし、`unified_exec` は `-c` で止められず門がどの起動も断った → オーナー決定 CG8「Codex の道具はすべて使わせる。**承認だけ**をアプリのカードに通す」。係の守りは「承認をモデルに任せる機能が off」の確かめだけに縮めた | `DESIGN.md:2683-2688` |
| 10-09 | 既定が知らないうちに「おまかせ」→ 削除を全段階で止める D64 | `HANDOVER.md:688` |

**要約した教訓**: (a) CLI の承認の仕組みは版やモデルの「道具の出し方」で黙って素通りになる。(b) 段階の既定は安全側に倒し、知らないうちに上がらないようにする。(c) 「いつでも止めるもの」は段階と別に持つ。(d) 「この会話では確認しない」は戻せる・小さいものにだけ。(e) 段階の変更は次の依頼から。(f) モデルや窓口に承認を代わりに出させない。

---

## 2. らく助の今の設計（段 3）の事実

- 【事実】箱（sandbox）は backend・DB へ道が無く、外へは AI の会社だけ。rk は `RK_MODE=relay` で中継に話し、中継は読む API・読むだけの POST（許可リスト 14 本）・読み取り専用 SQL だけを通す（段 3 設計 §0-1・2、§2-2 L194-207）。
- 【事実】書き込みは、画面で人が押した**承認番号**（`ap_…`・15 分・1 回限り・案の版の sha256 に結ぶ・会話に結ぶ）を中継が確かめた時だけ、中継自身が行う（§3-1・§3-3 L264-304）。承認は質問カードで取らない（L308）。
- 【事実】Claude は `--permission-mode default --permission-prompt-tool stdio`、`can_use_tool` は中継が自動で答える（Bash・Read・Glob・Grep・作業フォルダーの Write/Edit は allow、WebFetch・WebSearch は deny）。管理者に道具の許可カードは**出さない**＝D13（§1-3 L87、§6 L403、PO 決定済み）。
- 【事実】Codex は `sandbox_mode="danger-full-access"`・`approval_policy="never"`（箱そのものが砂場）の案（§1-4 L103、要実測）。
- 【事実】カイポケのロボットを動かす読み取りは GET でも関所で止める、カイポケ送信の承認は段 4（§2-2 L206、D14）。
- 【事実】機能の旗は拠点ごと（`office_feature_flags`、D10）、試験中は `AGENT_ALLOWED_USER_IDS`（§4-5）。監査は `agent.plans`・`agent.approvals`・`agent.runs` と `audit_logs`（§2-8 L253-260）。
- 【事実】rk の書く操作と「戻せるか」（`undoable`、`agent/rk/ops/*.py`）:
  - 戻せる（既定 True）: `assign-staff-week`・`move-visit-week`・`cancel-visit-week`・`add-visit-week`・`place-from-pool`・`patch-course-staff`・`accompaniment-set`・`staff-day-off`・`patient-update`・`pfv-update`（pattern_only のとき）・`preference-update`・`ng-staff-update`・`staff-hours-update`・`staff-master-update`・`special-period-create`・`special-visit-place`（後ろ 2 つは明示なし＝既定 True と読んだ【推測】）
  - 戻せない（`undoable = False`）: `return-to-pool`（visits.py:458）・`staff-off-week`（staff.py:169）・`copy-week`（week.py:62）・`patient-status`（向きによる、masters.py:214）・`patient-create`（masters.py:759）・`kaipoke-send`（kaipoke.py:138）
  - いつも禁止（AGENTS.md §4-2）: 週の生成・今週全件解除・自動割当/最適化の適用・`sync-fixed-to-week`・一斉未割当・月初展開・置換取込・Excel 完全置換・患者/ユーザー削除・パスワード/QR 再発行・職員の全項目保存・カイポケのログイン情報・ロボットを動かす読み取り（了承なしでは）

---

## 3. 提案: らく助の「反映の確かめ方」3 段階【案】

### 3-1. 考え方
- **段階は「道具」ではなく「反映」に当てる**。箱の中の道具（Bash・Read・rk の読み）は段階に関係なく中継が自動で答える（D13 を保つ）。非 IT の管理者が判断できるのは「この案を反映してよいか」だけで、PC-Assistant の「コマンドを実行します」カードに当たるものは見せても判断できない。
- **段階を決めるのも守るのも中継**。CLI の permission mode・Codex の approval_policy・モデルの言葉（「反映してよいですか→はい」）には一切頼らない。承認番号を出せるのは人のログイン（admin の Bearer）を持つ公開口だけ、は全段階で同じ。
- **PC-Assistant の ③ おまかせに当たる「人が押さずに書く」段は作らない**。いちばん緩い段でも「人が 1 回押して、範囲を決めて任せる」。

### 3-2. 操作の 4 つの組（危なさ）

| 組 | 中身 | 例 |
|---|---|---|
| **A 読む・考える** | 読む API・読むだけの POST・読み取り専用 SQL・案を作る・A4/A3 を作る・バックアップを頼む | `rk get`・`rk sql`・`rk level day`・`rk plan submit` |
| **B 戻せる小さな直し** | `undoable=True` の書き込みで、件数が少なく（例 5 件まで）、その週の中だけ・マスターを変えない | `assign-staff-week`・`move-visit-week`・`add-visit-week`・`place-from-pool`・`accompaniment-set`・`staff-day-off` |
| **C 重い書き込み** | 戻せない（`undoable=False`）・件数が多い（例 6 件以上）・マスター（固定訪問の枠・患者・職員）を変える・外へ出る | `return-to-pool`・`staff-off-week`・`copy-week`・`patient-status`（終了など）・`patient-create`・`pfv-update`（pattern_and_week）・`staff-master-update`・**`kaipoke-send`**・カイポケのロボットを動かす読み取り |
| **D いつも禁止** | AGENTS.md §4-2 の一覧。関所（`check_plan`）で断り、どの段階でも承認番号を出さない | 週の生成・一斉未割当・月初展開・置換取込・患者削除 ほか |

- 組は**案の中でいちばん重い操作**で決める（B と C が混ざれば C）。決めるのは中継（rk の `opsbase` の `undoable`・件数・op 名から機械的に。モデルの申告は使わない）。
- D は PC-Assistant の KeyFileGuard・D64 に当たる「段階の外側」。PO が名前を挙げて頼む別の手順は、エージェントの外（今の手作業）のまま。

### 3-3. 3 つの段階

| 段階（画面の名前） | 説明の 1 行（案） | A 読む | B 戻せる小さな直し | C 重い書き込み | D |
|---|---|---|---|---|---|
| **① 慎重**「1 回ずつ確かめる」 | 「反映の前に、毎回案を見て押します。戻せないものは 2 回確かめます」 | 自動 | 案ごとに［この案で反映する］→確かめの小窓 | 案ごとに［この案で反映する］→**赤い帯「元に戻せません」**＋確かめの小窓で件数を打つ（または 2 段の確かめ）、**PC だけ**（スマホでは押せない） | 禁止 |
| **② 標準**（既定）「案ごとに確かめる」 | 「反映の前に、案を見て押します」＝段 3 設計の今の流れ | 自動 | 案ごとに［この案で反映する］→確かめの小窓 | 案ごとに［この案で反映する］→赤い帯＋確かめの小窓（PC・スマホ両方可、カイポケ送信は PC だけ・日中だけ） | 禁止 |
| **③ 小さな直しは任せる** | 「戻せる小さな直しは、この会話の中で任せます。戻せないものは毎回確かめます」 | 自動 | 最初の 1 回だけ案のカードに **［この会話では小さな直しを任せる］** を出す。押すと中継が「任せ」（grant）を作り、以後この会話の B の案は**押さずに**反映、結果カードと［元に戻す］だけ出す | ② と同じ（毎回押す） | 禁止 |

- ③ の「任せ」（PC-Assistant の「この会話では確認しない」に当たる）の決まり【案】:
  - 作れるのは公開口の API だけ（人のログイン）。範囲＝その会話・B の組だけ・件数の合計の上限（例 20 件）・期限（例 60 分／会話のアイドル終了で切れる）。
  - 中継は B の案が来たら「任せ」を確かめて、**中継自身が承認番号を出す**（`agent.approvals.decision='approve_by_grant'`・`grant_id`）。モデルから見た流れ（`rk apply --approved ap_…`）は ② と同じ。
  - 段階・事業所の上限が変わる、会話が変わる、C の案が出る、元に戻すが押される、のどれかで「任せ」は消える（PC-Assistant の `AgentSession.cs:3549-3560` と同じ考え）。
  - 「任せ」で反映したものは結果カードに「任せた範囲で反映しました（今日 3 回目・合計 7 件）」と出し、［元に戻す］はいつも押せる。
- **元に戻す（undo）**: どの段でも人が押す（undo の承認、段 3 設計 §3-1 のまま）。③ の「任せ」は undo に使わない。
- **カイポケ送信**（段 4）: 組 C の中でも別の承認の種類（`kind=kaipoke_send`）。どの段でも毎回押す・PC だけ・日中の窓（`send_window_problems`）を中継が確かめる・送る前に差分の確認（AGENTS.md §3-6）をカードに出す。MA1 の「送信は係が『おまかせ』でも必ずカード」と同じ考え。

### 3-4. だれが・どこで段階を決めるか

| 層 | 決める人 | 中身 | 既定 |
|---|---|---|---|
| 事業所（拠点）の上限 | **PO だけ**（開発・運用側。管理者の画面には出さない） | その事業所で選べるいちばん緩い段（①／②／③）。`office_feature_flags` の隣に `agent_max_level` | **②**（③ は staging と本番で通しの確認が済むまで出さない） |
| 管理者ごと | 管理者本人（上限の中で） | 自分の会話の段階。相談タブ／小窓の設定に 3 択 | ②（上限が ① なら ①） |
| 会話ごと | 会話の中で人が押す | ③ の「任せ」だけ（上の決まり） | なし |

- 読めない・無い・上限より緩い値は**②（上限が ① なら ①）に倒す**（PC-Assistant の F4「fail open しない」）。
- 段階の変更は**次の依頼から**。案には「出された時の段階」を記録し、承認のときにその段階で判断する（途中で緩めても、待っている案は緩まない）。
- 窓口のような「ほかのエージェント」が将来できても、**エージェントは承認・任せを作れない**（承認番号は人のログインからだけ）。

### 3-5. 画面（言葉と見た目）【案】
- 段階の選び方: 3 つのボタン＋1 行の説明（3-3 の表の言葉）。見出しに段階は出さない（PC-Assistant と同じ。代わりに案のカードの下に小さく「確かめ方：案ごと」）。
- 案のカード（② の例）: 要点 3〜5 行・件数・**元に戻せるか**（B なら「元に戻せます」、C なら赤い帯「元に戻せません（戻すにはバックアップから）」）・［案を見る］［この案で反映する］［やめる］［直してほしい所を書く］。
- C の確かめの小窓（① の例）: 「3 件をプールへ戻します。**元に戻せません**。よければ 3 と入れて［反映する］」。
- ③ の任せのボタン: 主色の枠線（PC-Assistant の「この会話では確認しない」と同じ扱い）で、注の 1 行「この会話の残りは、戻せる小さな直し（20 件まで・60 分）を確かめずに反映します。戻せないものは毎回確かめます」。
- 文言の約束: 「承認」より「この案で反映する」（何が起きるかを言う）。「直す/修正」は使わない（MEMORY の「合わせる/調整」の約束と同じ考え）。

### 3-6. Claude と Codex【案】
- **段階は CLI に渡さない**。Claude は段階によらず段 3 設計の `--permission-mode default` と中継の自動の答え、Codex は `danger-full-access`＋`approval_policy="never"` のまま（箱が砂場・道が無い）。
- 理由（code_mode の教訓）: PC-Assistant では「Codex が聞いてくること」を守りにしていたので、code mode で聞かずに動かれたときに破れた（`DESIGN.md:2652`）。らく助は守りを**網（道が無い）と中継の承認番号**に置くので、Codex が code mode・unified exec で何を動かしても書き込みには届かない。ここは段 3 設計の正しさの裏付けとして明記しておく。
- ただし同じ教訓から足すもの:
  1. **中継が受け付ける書き込みの入口は `/rk/apply`・`/rk/undo` だけ**、承認番号なしの書き込みを 403 にする試験を Claude と Codex の両方で行う（「モデルが聞かずに動く」前提の試験）。
  2. Codex の「承認をモデルに任せる」機能（`approvals_reviewer`・`guardian_approval`）は箱の中では意味が無いが、**承認の言葉を文で出させて人に聞いたふりをする**流れに注意。中継の追加プロンプトに「反映の了承は画面のボタンだけ」を Codex 用にも入れ、文の「1. はい」をボタンにする仕組み（§1-4）は**反映の了承には使わない**（質問の答えとしてモデルに返すだけ）。
  3. Codex 0.159.2 には `item/tool/requestUserInput`（質問の問い合わせ）がある（`DESIGN.md:2699`）。段 3 設計 §1-4 の「要実測」の手がかりになる。
  4. Codex は利用者の npm の別の版が動いていた（`DESIGN.md:2652`）。箱の image に**版を固定**して入れ、`system/init`・`thread/start` の版をログに出す。
  5. Claude はフォルダーの `.claude/settings.local.json` を途中で読み直す（`REPORT_PM2.md:66`）。箱の cwd は読み取り専用の bind（段 3 設計 §1-3）なので、**作業フォルダー（`RK_WORK_DIR`）の中に `.claude/` を作らせない・`--setting-sources` を絞る**ことを試験に入れる（守りの芯ではないが、模型の自由度を減らす）。

### 3-7. 記録【案】
- `agent.approvals` に `level`（出された時の段階）・`risk_group`（A〜D）・`grant_id`（③ の任せで出したとき）を足す。`agent.grants`（id・session_id・user_id・範囲・件数の上限・期限・使った件数・消えた理由）を足す。
- 中継が段階で判断したこと（承認番号を出した／出さなかった・理由）は `agent.events` に 1 行ずつ。PC-Assistant には無い「誰がどの段階で何を承認したか」をらく助では SQL で引けるようにする（複数の管理者が使うため）。

---

## 4. くらべ: PC-Assistant ⇄ らく助【案を含む】

| 観点 | PC-Assistant（事実） | らく助（案） |
|---|---|---|
| 守りの芯 | PC の中で動く CLI＋アプリの `Decide` が `can_use_tool`・承認の問い合わせに答える | 網（箱に道が無い）＋中継の承認番号。CLI の承認に頼らない |
| 段階の対象 | 道具ひとつひとつ（コマンド・編集・画面・送信・起動） | 反映（案）だけ。道具は段階によらず自動（D13） |
| 段階 | ① 慎重／② 標準／③ おまかせ（確認なし） | ① 1 回ずつ（C は 2 回）／② 案ごと／③ 小さな直しは任せる（**確認なしの段は無い**） |
| 既定 | ②（D27 前のエージェントは ③ に移行） | ②。③ は事業所の上限で開けるまで出ない |
| 設定の単位 | エージェント（＝フォルダー）ごと＋PC の「新しいエージェントの既定」 | 事業所の上限（PO）＋管理者ごと＋会話の「任せ」 |
| 変えられる人 | PC の持ち主（単一利用者） | 上限は PO だけ、段階は管理者本人（上限の中） |
| 無い・読めない時 | ② に倒す | ②（上限が ① なら ①）に倒す |
| いつも止めるもの | 鍵のファイル・削除のコマンド（ごみ箱へ）・自動で動く設定・アプリのフォルダー・最上位のモデル・課金の付け替え | AGENTS.md §4-2 の禁止の操作・DB への直接の書き込み・カイポケのログイン情報・承認番号なしの書き込み・課金の付け替え（isolation.json） |
| 「この会話では…」 | 「この会話では確認しない」（危なくない繰り返しだけ・会話/段階の変更で消える） | 「この会話では小さな直しを任せる」（③ だけ・B だけ・件数と時間の上限・C や undo で消える） |
| 外へ出るもの | 送信は ② でもカード、Codex の追加権限は ③ でもカード | カイポケ送信は全段で毎回・PC だけ・日中だけ |
| カードの出し先 | PC とスマホ、先に答えた方 | PC とスマホ、先に答えた方（C を ① ではスマホで押せない案） |
| Claude の当て方 | permission mode（default／acceptEdits／bypass）＋isolation.json の deny/ask | 段階によらず `default`＋中継の自動の答え |
| Codex の当て方 | どの段も `on-request`、sandbox を read-only／workspace-write／danger-full-access | 段階によらず `danger-full-access`＋`never`（箱が砂場） |
| code_mode の教訓 | 承認が 1 度も来ずに 4 回動いた → 道具を切る試み → 断念し「承認だけ」を確かめる（CG8） | そもそも承認を CLI に頼らないので影響なし。試験で「聞かずに動く」前提を確かめる |
| 記録 | errors.log・赤い 1 行・会話の控え。専用の承認の監査は無い【推測】 | `agent.approvals`（段階・組・任せ）・`agent.grants`・`agent.events`・`audit_logs` |

---

## 5. 話し合いたいこと（未決）

1. **③「小さな直しは任せる」を作るか**。作らずに ①② の 2 段にする方が非 IT の管理者には分かりやすい。作るなら「小さな」の線（件数 5・合計 20・60 分）をどう決めるか。
2. **組 B と C の線**: `cancel-visit-week`（今週だけ取消・プールに出ない）や `patch-course-staff`（コースの担当を替える＝次の週にも効く？）を B に入れてよいか。マスターを変える `preference-update`・`ng-staff-update`・`staff-hours-update` は戻せても C にするか。
3. **① の「2 回確かめる」の形**: 件数を打つ／2 段のボタン／PC だけ、のどれにするか。スマホから戻せない操作を押せなくすると、外出中の急な休みに困らないか。
4. **事業所の上限を PO だけにするか**、お客様の責任者（事業所の管理者の中の 1 人）にも変えさせるか。
5. 段階を**管理者ごと**にするか、**事業所で 1 つ**にするか（管理者が少人数なら 1 つの方が分かりやすい）。
6. **D13 との関係**: 道具の許可カードは出さないまま、で良いか（この文書は「保つ」を推奨）。例外として「カイポケのロボットを動かす読み取り」は読むだけでも承認の種類にするか（今は関所で止めるだけ）。
7. **「任せ」で反映したときの通知**: 結果カードだけで良いか、ほかの管理者にも知らせるか（D8 で会話は本人だけが見られる）。
8. **Codex の会話でも③を許すか**。Codex の文の選択肢（「1. … 2. …」）は反映の了承に使わない、で良いか。
9. **承認番号の期限**（15 分・apply は 10 分以内）を段階で変えるか（① は短く、② はそのまま）。
10. 段 3 の範囲: まず ② だけで作り（今の設計のまま）、`level`・`risk_group` の列と C の赤い帯だけ先に入れ、①③ は段 4 以降にするか。
