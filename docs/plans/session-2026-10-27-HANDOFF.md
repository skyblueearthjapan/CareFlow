# 2026-10-27 セッション引き継ぎ（訪問の音声記録 v2：文字起こし・補正・まとめを分ける／長い録音を区切る／見せ方／マイク）

このセッションでは、訪問の音声記録を v2 に作り替えた。**ブランチ `feature/voice-record-v2` を PO の了承で develop へ push 済み（`5390e082`）・本番は未反映**（本番は別に了承を取る）。
正典の設計は `docs/plans/visit-voice-record-v2-design-2026-10-27.md`（§8 に P0 の実測と P1b の確かめ）。

## 0. 再開したら最初に確かめること
- `git status --short --branch` → `feature/voice-record-v2`。`origin/develop` = このブランチ（`5390e082` 以降）。
- このブランチには、PO が先に入れた `79c94ec9`（CLAUDE.md の試し運転の反映）・`540439c3`（uv.lock を pyproject に合わせる）・README の試し運転（`1a0a2d92`）も乗っている。develop へ出すときはまとめて出る。
- 作業ボード `.parallel/BOARD.md` の「メイン：音声記録 v2」の行は「済」へ移した（develop へ push 済み・本番は未反映）。

## 1. 何を変えたか（PO 了承 2026-10-27）
| 段 | 内容 | 主なファイル |
|---|---|---|
| P0 実測 | 作り物の会話の音声で今の仕組みを測った。**今の本番は 10 分を超える記録がほぼ必ず失敗する**（出力 8192 で切れて JSON が壊れる） | 設計 §8・道具は scratchpad `p0\` |
| P1 | ① 文字起こしを独立（行の JSON だけ・時刻は「分:秒」・出力 65,535・切れたら続きから・ループ/重複除去） | `services/voice/transcribe.py`・`prompts.py`・`vertex_client.py` |
| P1b | **15 分を超える録音を約 12 分ずつ無音で区切って送る**（ffmpeg）。56 分の誤り率 約45% → **3.9%** | `services/voice/audio_split.py`・`jobs.py`・`backend/Dockerfile` |
| P2 | ② 補正（文字だけ・時刻を写す・直しすぎは元の行へ・失敗しても進む）。列 `transcript_fixed_json` | `services/voice/correction.py`・migration 0101 |
| P3 | ③ まとめは形を AI に任せ、本文（Markdown・各記述に `[m:ss]`）＋事実 JSON（バイタル・申し送り・次回・要確認）。席（relay）も v2 で答える | `services/voice/summary.py`・`karte_seat.py`・`agent/relay/karte.py` |
| P4 | 見せ方：本文を安全に表示（HTML として通さない）・「12:05 ごろ」を押すと 15 秒前から再生・上にバイタルと【要確認】・文字起こしは畳んで「整えた／元の」切替・A4 も同じ | `components/records/VoiceMarkdown.tsx`・`voiceMarkdownParser.ts`・`VoiceV2Summary.tsx`・`RecordDetailDialog.tsx`・`mobile/VisitRecordCard.tsx`・`record_report_html.py` |
| P5 | マイク：iPhone でも自動ゲインが効く制約・音量メーター（聞こえています／声が小さい／音が入っていません）・効いた設定を `mic_settings` でログへ | `lib/voice/recorder.ts`・`level-meter.ts`・`mobile/VoiceLevelMeter.tsx`（サブが作業・取り込み済み） |
| 守り | 処理ごとの印 `job_token`（掃除→再試行・削除との二重実行を止める）・処理中の再処理は 409・途中で止まった答え（SAFETY 等）を成功にしない・失敗にも費用を残す | `jobs.py`・`errors.py`・`api/v1/visit_recordings.py`・migration 0101 |

- 今までの記録（v1・6 欄の要約）は作り直さず、表示も今のまま。
- モデルは **Gemini 2.5 Flash（東京）のまま**。東京で 3.5 Flash が使えるようになっていたが、同じ音声で誤り率 16.3%（2.5 は 4.8%）で劣った。

## 2. 確かめたこと
- backend：`python -m pytest tests/test_voice_*.py tests/test_visit_recordings.py -q` 全件通過（232）・`tests/test_agent_transcribe.py` 通過・`ruff check app tests alembic` 通過。
- agent：`python -m pytest relay/tests/test_karte.py relay/tests/test_karte_claude.py -q` 54 件通過。
- frontend：`pnpm vitest run components/records components/mobile lib app/(app)/records app/(mobile)/m/today` 全件通過・`pnpm tsc --noEmit` エラーなし・`pnpm lint` エラーなし（警告は前からの物）。
- 箱：Debian の ffmpeg を入れた backend の箱を作り、作り物の 56 分を webm/Opus・mp4/AAC にしたものが 10〜13 分の 5 つに区切れた（箱の大きさ 883 MB → 1.51 GB。PO 了承済み）。
- 画面：偽のデータ（作り物の患者・ログインは手元用の印・API は偽の答え）で、PC の詳細・スマホの訪問詳細のカード・A4 を実際に表示して撮った。時刻を押すと 15 秒前から再生・`<script>` やリンクは文字のまま・文字起こしの切替を確かめた。
- レビュー：P5（frontend）・backend P1〜P3＋P1b・P4＋手当て の 3 回。高の指摘は backend の 1 件（長さの無い webm が区切られない）で、手当て済み。中・低も手当て済み（残りは §4）。
- 費用：P0 と P1b の確かめで Vertex に計 約 1.14 ドル（約 170 円）。すべて作り物の音声。

## 3. 本番に出すとき（PO の了承を 2 回：develop への push・本番への反映）
1. **VPS のディスクの空きを確かめる**（backend の箱が約 630 MB 増える）。
2. pg_dump を取る。
3. backend の箱を作り直す（ffmpeg が入る）。agent の箱も作り直す（relay の v2）。frontend も作り直す。
4. `alembic upgrade head` を手で流す（**0101：`transcript_fixed_json` と `job_token` の 2 列を足す**）。
5. 1 件録って確かめる：文字起こし → 補正 → まとめが通るか、`docker logs` に `voice:` の行（区切った・mic_settings）が出るか、画面に v2 の表示が出るか。

## 4. 気になる点・やっていないこと
- **実機の確かめ**（どれも未）：
  - iPhone のホーム画面アプリ・Android で、メーターの段階が実際の声に合うか、メーターを付けても録音の音質が変わらないか（AudioContext を同じマイクにつなぐため）。悪ければ iPhone だけメーターを止める旗を足す。
  - iPhone で最初に「ごろ」を押したとき、音声の取り寄せの後で再生が始まらないことがある（操作の外の `play()`）。位置は合う。
  - **本物の Android の録音（MediaRecorder の webm）を箱の ffprobe/ffmpeg にかけて区切れるか**。作り物は ffmpeg が書いた webm なので長さの情報があった。端末の webm は長さが無いことが多く、ffmpeg が最後まで読んだ時刻（`time=`）→ 端末の値 の順で補う作りにした（試験はある）。
- **出来の確かめ**：看護師に実際の記録 10〜20 件を読み比べてもらう（9 月からの宿題）。P0 は静かな所の合成音声 1 声で、実際の訪問では誤りが増えるはず。「コップ何杯」→「7 杯」のような**元に無い数字**が出ることがあった。
- 時刻は区切っても 20〜60 秒ずれる。画面は「ごろ」にして 15 秒前から再生する。
- 指示の約束が backend（`prompts.py` の SUMMARY_RULES・FACTS_BLOCK）と relay（`karte.py` の `instructions_v2()`）の 2 か所にある。変えるときは両方。同じ Markdown の読み取りも TS と Python の 2 か所。
- 1 本の長い録音（56 分）の処理は順に送って約 200 秒。区切りを並行に送れば縮められる（未）。
- `vertex_client.ERROR_KINDS` の一覧に `blocked`・`cut`・`incomplete` が載っていない（説明の古さだけ）。
- 古いエージェントの写し 23 個（`.claude/worktrees/agent-*`）は PO の指示で触っていない。

## 5. 並行作業の試し運転で分かったこと（CLAUDE.md に反映済み）
- サブの写しは `orca worktree create`、片づけは `orca worktree rm --worktree branch:<ブランチ> --json`。rm の後も `git worktree list` に prunable で残ることがあり、`git worktree prune` と `git branch -d` で消えた。
- pre-commit は本体の `.git/hooks/pre-commit` で写しにも効いた。pre-commit は未コミットの変更を一時的に外して戻すため、並行に作業していると改行（CRLF/LF）が変わることがある（prettier で落ちたら整形し直す）。
