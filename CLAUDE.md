# CareFlow（らく助）— エージェント共通の決まり

訪問看護スケジューリングアプリ。本番運用中。このファイルは、本体フォルダーでも worktree（作業用の写し）でも、すべての頭が読む。
詳しい経緯はメモリと `docs/plans/session-YYYY-MM-DD-HANDOFF.md`（日付の新しいもの）を読む。

## まず自分の役を確かめる（毎回、作業の前に）
```
git rev-parse --show-toplevel
git worktree list
git status --short --branch
```
- 1 行目が `git worktree list` の先頭（本体フォルダー `CareFlow02`）と同じ → **メイン（頭 1）**
- 違う（worktree の中）→ **サブ（頭 2 以降）**
- 分からないときは作業を始めず、PO（今泉さん）に聞く。

## 役割
同時に動かすのは、メイン 1 体とサブ 1 体まで（PC のメモリが 16GB のため）。

### メイン（本体フォルダー・1 体だけ）
- 計画、作業の切り分け、サブへの割り振り、取り込み（マージ）、develop への push、本番デプロイ、引き継ぎ書。
- 取り込みは手元で行う。サブのブランチの差分を読み、本体でマージし、テストを通してから `git push origin <作業ブランチ>:develop`。
- サブを出す前に、自分の変更をコミットしておく（未コミットの変更は写しに入らない）。
- 取り込んだ後に、もう一度テストを流す（各ブランチで通っていても、合わせると落ちることがある）。
- 差し戻しは、終わったサブに話を続けず、新しい作業として出し直す。

### サブ（worktree・1 つの作業だけ）
- 渡された 1 つの作業だけをする。範囲の外に気づいたら、直さずに作業ボードへ書く。
- 任せるのは実装とテストまで。自分のブランチにだけコミットする。
- GitHub へは自分のブランチだけ push してよい（`git push origin <自分のブランチ>`）。develop・main・ほかのブランチを checkout / merge / push しない。
- 終わったら「完了の書き方」のとおり作業ボードに書いて止まる。

## 作業ボード（頭どうしの連絡）
- 置き場所：本体フォルダーの `.parallel/BOARD.md`（git 管理外）。サブは `git worktree list` の先頭のパスから開く。
- 書く前に読む。1 作業 1 行で、担当・ブランチ・触る場所・状態（作業中／完了／止まった）を書く。
- 触る場所が、ほかの「作業中」の行と重なったら、手を付けずにメインに知らせる。

## 完了の書き方（サブ）
- ブランチ名と最後のコミット
- 変えたファイルと、何のためか
- 通した確認（テスト・型・lint）と、その結果
- 気になること・やっていないこと

## メインだけがすること（サブはしない）
- alembic の migration を作る・当てる（番号がぶつかり、head が 2 つになるため）
- 依存の追加（`frontend/pnpm-lock.yaml`・`backend/uv.lock` を変える作業）
- 共有の登録場所を変える：API のルーター登録、`.env.example`、画面のメニュー
- develop への push、本番 VPS への接続とデプロイ、秘密の値の扱い
- サブがこれらを要るときは、作業ボードに書いて止まり、メインに頼む。

## git の禁止事項（全員）
- `git stash` を使わない。stash は全 worktree で共有され、2026-08-17 に別の頭の変更が流れ込む事故が起きた。一時退避は WIP コミットで行う。
- `--no-verify` を使わない。`git reset --hard`・強制 push は、PO の了承なしにしない。
- 同じブランチを 2 つの worktree で使わない（git が許さない）。サブのブランチは Orca が `wt/<作業の要約>` と付ける（元は `origin/develop`）。

## worktree で動かすとき
- git 管理外のものは写しに入らない。Orca が写しを作るときに `pnpm install --frozen-lockfile`（frontend）と `uv sync --no-install-project`（backend）を自動で流す。入っていなければ同じコマンドを手で流す（素の `uv sync` は依存が入らない）。
- 写しの場所は `C:\Users\imaizumi.LINEWORKS-NET\orca\workspaces\` の下（リポジトリの外）。
- 開発サーバーのポートは頭ごとに分ける（メイン：frontend 3000・backend 8000／サブ：3001・8001）。
- Docker Desktop は閉じておく（箱の試験のときだけ開く）。
- `.secret/`・`.env` を写しにコピーしない。要るときはメインに頼む。

## テストと確認
- backend：`cd backend && python -m pytest <files> -q`（`uv run pytest` は使わない）
- frontend：`pnpm vitest run <files>`／型 `pnpm tsc --noEmit`／`pnpm lint`
- 前から落ちている試験（引き継ぎ書の「気になる点」）と、新しい失敗を混ぜない。

## 共通の決まり
- 返事も AskUserQuestion の選択肢も日本語で。
- 画面の文言は「合わせる／調整」を使い、「直す／修正」は使わない。「楽スケ」と書かない（正しくは「らく助」）。
- 日本語のファイルを PowerShell の Get-Content / Set-Content で読み書きしない（文字化けする）。
- 本番に書く前に、PO の了承・pg_dump・別の担当のレビュー。本番のコンテナで pytest を流さない。
