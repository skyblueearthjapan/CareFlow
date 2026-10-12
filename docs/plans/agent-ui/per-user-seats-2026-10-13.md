# らく助エージェント — 人ごとの席（AI のログインを 1 人 1 つに）設計メモ

2026-10-13・PO 決定: お客様は ChatGPT Team。ChatGPT Business/Team の約束は **1 席 1 人**。
→ らく助エージェントの相談は、**その相談を始めた人自身の AI のログイン**で動かす（1 つのログインを共有しない）。

## 1. 形

| 層 | 何をするか |
|---|---|
| 中継（`agent/relay`） | らく助の user id から **席の鍵** `seat_key(user_id)` = `sha256("rakusuke-agent-seat\n" + user_id)` の先頭 32 桁（16 進）を作る。秘密ではない（箱のフォルダー名に user id を出さないための写像）。runner へは鍵だけを渡す |
| 箱の runner（`agent/deploy/runner/agent_runner.py`） | 鍵の形 `[a-f0-9]{32}` だけを受ける（パスは受けない）。席 = `/home/agent/u/<鍵>/`（0700・持ち主 10002）。`CODEX_HOME=<席>/.codex`・`CLAUDE_CONFIG_DIR=<席>/.claude`・`HOME=<席>` |
| 実行係（uid 10004） | 変わらない。`/home/agent` に入れない＝だれの席も読めない（`isolation_e2e.sh` で 3 つの置き場を確かめる） |

runner の op（どれも `user_key` が要る）: `spawn`・`auth_status`・`login_start`・`login_poll`・`login_input`・`login_cancel`・`logout`・`adopt_legacy`。
サインインは箱の中で同時に 1 つだけ（だれのものでも）。様子・やめる・コードの貼り付けは、本人のサインインだけに効く（他人の途中は idle に見える）。

## 2. 画面と中継の約束

- 相談は持ち主の席で起こす（`sandbox_runner_factory` が `spawn.user_key = seat_key(session.user_id)`）。
- 相談を始める前に、持ち主の席のログインを確かめる（`RelayService._signin_guard`・20 秒の覚え）。`missing` なら CLI を起こさず、
  会話に `error code=signin`「ChatGPT にサインインすると、相談を始められます」＋ ⚙（AI のつなぎ先）を開くボタン。HTTP は 409。分からない（`unknown`）ときは止めない。
- `GET /engines` は呼んだ本人の席の様子（画面は「あなたの ChatGPT」「あなたの Claude」）。サインイン・サインアウトも本人の席だけ。
- 既定の AI: `agent.settings` の `default_provider:<user id>`（本人）→ `default_provider`（全体）→ `AGENT_DEFAULT_PROVIDER`。表（migration 0094）はそのまま。
- `GET /engines/seats`（管理者だけ）: らく助の管理者（users の admin/manager）の名前（職員名かユーザー名）と、各 AI の状態の語だけ。AI のメール・user id は出さない。読めないときは自分だけ。
- 利用上限: Codex の `account/rateLimits/updated` の使った割合が 80% 以上で「いまはこのアカウントの利用上限に近づいています（あなたの ChatGPT の契約の分です）」を会話につき 1 回（`level: warn`＝会話は止めない）。上限に当たったら、いままでどおり「HH:MM 以降に続けられます」。

## 3. 前の共有のログインの移し替え

- 鍵: `AGENT_LEGACY_OWNER_USER_ID`（VPS の秘密ファイル）か `agent.settings` の `legacy_owner_user_id`。コードに user id は書かない。
- 中継は起動時（届かなければ ⚙ を開いた・相談を始めたとき）に `adopt_legacy{user_key: 持ち主の鍵}` を 1 回頼む。
- runner は `/home/agent/.codex`・`.claude` を、持ち主の席にまだ無い（無い・空）ときだけ `rename` で移し、`/home/agent/u/.legacy-adopted` に印。印があればだれの鍵でも二度と動かない（残りが別の人の席へ移ることはない）。
- 手順・手で移す方法は `agent/deploy/README.md` §3-8「前の共有のログインを、PO の席へ移す」。

## 4. だれが使えるか（のちの継ぎ目）

`relay/auth.py` の `AGENT_USES = {"consult": 管理者の役割}` と `can_use(user, purpose)`。公開口の入口は `can_use(user, "consult")`。
職員へ限られた目的（例: カルテの下書き）を開くときは、目的（`"karte"`）と役割を足し、その目的の口だけで確かめる。画面はまだ作らない。

## 5. 残る危険（詳しくは `agent/deploy/README.md` §9-(8)）

- CLI は全員 uid 10002。ある人の会話の CLI の道具が別の人の席を指すのを止めているのは `permissions.deny //home/agent/**`＋中継の許可の返事＋リンク消し（段 3 の (1)-(a) と同じ）。人ごとに uid を分けるのは人数が増えたら検討。
- 席の会話の写し（`.claude/projects`・`.codex/sessions`）は箱の片づけ（7 日）が各席も見る。人が辞めたら席をサインアウト → `rm -rf /home/agent/u/<鍵>`。
- 以前の相談の続き（Codex の thread・Claude の会話 id）は持ち主の席に無いと続けられない。Codex は新しい thread で始め直す。PO の分は移し替えで続く。
