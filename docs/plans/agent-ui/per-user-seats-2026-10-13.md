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

## 6. AI の席の割り当て（PO 2026-10-13 追記・テスト期間の運用）

「1 人 1 席」の仕組みはそのままに、**だれがだれのサインインした席で相談するか**を管理者が ⚙ で割り当てる。

| 設定（`agent.settings`） | 意味 |
|---|---|
| `seat_assignment:<user id>` = 席の持ち主の user id | その人の相談に使う席（自分の席なら自分の id） |
| `default_seat_owner_user_id` | 割り当ての無い人の席。無ければ env `AGENT_LEGACY_OWNER_USER_ID`（PO）。どちらも無ければ自分の席。コードに user id は書かない |
| `session_seat:<session id>` | その相談を始めたときの席（続きは同じ席・割り当てを変えても続いている相談は変わらない）。`agent.admin_events` に `session_seat` |

- 振り分け（`RelayService.seat_for_session`）: 記録済みの席 → （割り当ての前に始めた相談は持ち主の席）→ 割り当て → 既定 → 自分の席。runner へは席の鍵（`seat_key(持ち主)`）だけ。
- 割り当て先がほかの人の席で、その席がサインインしていなければ、CLI を起こさず「使う AI の席がサインインしていません。管理者に連絡してください」（`error code=seat_signin`・409）。自分の席ならいままでどおりサインインのカード。
- ⚙「AI の席の割り当て」（管理者だけ）: 行 = 管理者（名前 = `users.display_name` → ログイン ID）、列 = 席（「A004 の ChatGPT」・自分の席）。ラジオを押した時点で保存。表の上の［全員をこの席にする ▾］で全員と既定をまとめて変える（念押しのボタン）。サインインしていない席は薄く・選べない（中継も 409）。割り当て先がサインアウトした行には「席がサインインしていません」。変更は `admin_events` の `seat_assign`（`user=<だれの> seat=<前>-><後>`・記録の user_id = 変えた人）・`seat_assign_all`。
- 口: `GET /agent-relay/seats/assignments`・`PUT …/seats/assignments {user_id, seat_owner_user_id}`・`POST …/seats/assignments/all {seat_owner_user_id}`。`GET /engines` に `consult_seat{owner_is_me, source, name, state}`（⚙ に「あなたの相談は、○○ の席の AI で動きます」）。
- カルテの下書きの席（`docs/plans/karte-via-seat-2026-10-13.md`）も、画面は同じ「席を選ぶ」部品（`SeatOption`）で選ぶ。
- 規約: ほかの人の席で相談するのは「1 席 1 人」から外れる。テスト期間の PO 判断。本番の運用に入るときは各自の席に戻す（全員を「自分の席」に割り当て直すか、`default_seat_owner_user_id` を消す）。
- `users.display_name`（migration 0095）を中継の `agent_ro` が読むには、0095 の後に `agent/deploy/sql/roles.sql` を流し直す（列ごとの権限）。流すまではログイン ID で出る（中継は読み直す）。
