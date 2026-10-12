# らく助エージェントの道具「rk」

らく助（CareFlow）の専門エージェントが使う共通の道具です。標準ライブラリだけで動きます（追加の pip は不要）。
設計: `../docs/plans/agent-skills/design-2026-10-12.md` §3・§4。エージェントの約束は `AGENTS.md`。

## 動かし方
```bash
# agent/ で
PYTHONPATH=. python -m rk --help
# どこからでも（Git Bash・VPS）
agent/bin/rk --help
# cmd・PowerShell
agent\bin\rk.cmd --help
```

## 資格情報（リポジトリに置かない）
エージェント専用の管理者ユーザーを推奨（監査ログで人と区別できる）。どちらか一方:
- 環境変数 `RK_IDENTIFIER`（職員コードかメール）と `RK_PASSWORD`
- `~/.rakusuke/credentials.json` … `{"identifier": "...", "password": "..."}`

トークンは `~/.rakusuke/token.json` に保存し、期限内は使い回します（access 1 時間・refresh 30 日）。
**ログインは refresh が使えないときに 1 回だけ**。失敗しても繰り返しません（らく助は 15 分に 5 回で締め出す）。
rk 自身も 15 分に 3 回までに抑えます。パスワード・トークンは画面に出しません。

## つなぎ方（`RK_MODE`）
| 値 | API | 読み取り SQL |
|---|---|---|
| `pc`（既定） | 実行のたびに `ssh -N -L <空きポート>:127.0.0.1:18001 <RK_SSH>` を張り、終わったら閉じる | `ssh <RK_SSH> docker exec -i carelink-postgres psql ...` |
| `vps` | `http://127.0.0.1:<RK_API_PORT>/api/v1` に直接 | `docker exec -i ... psql ...` |
| `relay` | 中継（`relay/`）の内側口だけ（`RK_RELAY_URL`・`RK_RELAY_TOKEN`・`RK_WORK_DIR`）。GET と読むだけの POST だけ通り、書き込みは画面の承認番号 `ap_…` で `rk apply --approved ap_…` を中継が確かめて中継自身が行う | 中継が agent_ro ロールで |

`RK_ENV`（`prod` 既定／`staging`）: `staging` は VPS の使い捨て試験環境（`agent/staging/`）に向ける。
ポート 18101・コンテナ `stg-postgres`・バックアップ `/opt/carelink/agent-staging/backups`・資格情報/トークンは `~/.rakusuke/*-staging.json`・実行記録は `agent/runs/staging/`。
新しい操作を本番で初めて流す前は staging で試す。

その他の環境変数: `RK_SSH`（既定 `root@72.60.211.213`・ssh 鍵で入れること）・`RK_API_PORT`（18001。試験用のらく助なら別の番号）・
`RK_PG_CONTAINER`・`RK_PG_USER`・`RK_PG_DB`（既定 carelink-postgres / carelink / carelink）・`RK_REMOTE_DIR`（/opt/carelink）。

## コマンド
| コマンド | 働き |
|---|---|
| `rk whoami` | つながるか・誰として動くか |
| `rk get <path> [-q k=v ...]` | GET。path は `/api/v1` より後 |
| `rk post-read <path> --json <file\|->` | 読むだけの POST（`rk/readonly_posts.py` の許可リストだけ） |
| `rk sql <名前> [-p k=v ...]` / `--list` / `--file x.sql` | 読み取り専用の SQL。結果は JSON の配列 |
| `rk plan check <plan.json>` | 案の形・操作の許可・操作ごとの検査。`"ready": false` の案は `blockers` を 1 件ずつ出して断る |
| `rk plan report <plan.json> [--a3] [--out x.html]` | 案の帳票（A4 縦の一覧／A3 横の時間割。`timelines` があれば 1 日 1 枚・無ければ `timeline` の 1 日）→ `docs/reports/agent/<日付>/` |
| `rk plan submit <plan.json>` | （relay だけ）案を画面に出す。反映は人が画面で承認してから |
| `rk progress "<1 行>"` | （relay だけ）長い作業の途中経過を画面に。ほかのモードでは表示だけ |
| `rk backup --label <名前>` | VPS で pg_dump → `/opt/carelink/backups/agent-<名前>-<UTC>.sql.gz`。記録は `runs/backups.jsonl` |
| `rk apply <plan.json> --approved "<誰が・いつ>" [--backup <path>] [--no-basis-check]` | 反映。案に `source.basis_hash` があれば先に週を読み直して比べる（下） |
| `rk verify <plan.json か run-id>` | 反映後に読み直して照合 |
| `rk undo <run-id> --approved "..." [--partial]` | 元に戻す |
| `rk level week\|day ...` | ならし案（`rk/commands/level.py`・本番は読むだけ）→ `runs/plans/*.json` |
| `python bin/sync_skills.py [--check]` | スキルの正典 `.agents/skills/` を `.claude/skills/` へそのまま写す（余分は消す） |

## 安全の仕組み
- **SQL**: `begin transaction read only` ＋ `PGOPTIONS=default_transaction_read_only=on` ＋ 最後は `rollback`。1 文だけ（途中の `;` は拒否）。
  パラメータは `-p key=value` → psql の `-v`（SQL では `:'key'`）。名前は英小文字・数字・`_`、値に改行は不可。
- **apply**: 了承の記録（`--approved`）と **30 分以内の `rk backup`**（または `--backup` で明示）が無いと、つなぐ前に止まります。
  `runs/<UTC 時刻>-<kind>/` に `plan.json`・`meta.json`・`before.jsonl`（反映前の状態）・`log.jsonl`（1 件ずつの結果）・`undo.json`（元に戻す案）。
  1 件ずつ API を呼び、**最初の想定外で止まります**。
- **undo**: `undo.json` を同じ手順で反映（了承・バックアップが要る）。API で戻せない操作があれば `--partial` が無い限り断ります。同じ記録は 2 回戻せません。
- **操作の許可リスト**: `rk/ops/*.py` で `@register` された操作だけ。部品の形は `rk/opsbase.py`。
  一覧: `PYTHONPATH=. python -c "from rk.opsbase import load_all; print(load_all().names())"`。
  週を作る（前の週をコピー）は `copy-week`（`rk/ops/week.py`）: 画面の「週を作る」と同じ API・**写す先が空の週だけ**（上書きは `"allow_overwrite": true`）・自動スタッフ割当なし・API では戻せない（バックアップから）。
- **反映してよい案か**: `"ready": false` の案は `rk plan check`・`rk apply` が断る（理由は `blockers`）。
- **案を作った後に予定が変わっていないか**: `source.basis_hash`（`rk level` が入れる・`tools/leveling/plan.py` の `basis_hash`）のある案は、
  `rk apply` がつなぐ前に同じ週の SQL（`docs/tools/week-leveling/extract.py` の週の読み取り・1 本）を読み直して比べ、違えば
  「案を作った後に予定が変わりました。案を作り直してください」で止まる。`--no-basis-check` で飛ばせる（PO の了承のうえで）。
- `runs/` は git 管理外。利用者名入りの帳票は `docs/reports/agent/`（git 管理外）。

## テスト（本番・ssh に触れません）
```bash
# リポジトリの直下で
PYTHONPATH=agent python -m pytest agent/tests -q -p no:asyncio
# 中継（agent/relay・要 agent/relay/requirements.txt）
PYTHONPATH=agent python -m pytest agent/relay/tests -q -p no:asyncio
```
- 画面の相談（中継）の既定の AI は **ChatGPT（Codex・`codex app-server`）**、Claude も選べる（`AGENT_DEFAULT_PROVIDER`）。Codex の起こし方・締め付け・ログイン（`codex login --device-auth`）は `deploy/README.md` §3-8・§3-8b、正は `relay/codex_config.py`。Codex の試験は `relay/tests/test_codex.py`（実物の金型 `relay/tests/fixtures/codex_*.jsonl`・偽の app-server `relay/tests/fake_codex.py`）。
- スキルを直すのは `.agents/skills/` だけ。直したら `python agent/bin/sync_skills.py`（`tests/test_skills_sync.py` が写しの違い・frontmatter・AGENTS.md §8 の一覧を見張る）。
