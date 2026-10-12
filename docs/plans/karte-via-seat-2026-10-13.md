# カルテの下書きを、決めた管理者の ChatGPT の席で作る（2026-10-13）

PO 決定（2026-10-13）: 訪問の音声記録のカルテ（看護記録）の下書きは、**当面は指定した管理者 1 人の ChatGPT の席**で作る。
看護師全員が ChatGPT Team の席を持ったら「録音した本人の席」に切り替えられるようにしておく。
文字起こしと聞き間違いの補正は **Vertex AI 東京の Gemini 2.5 Flash のまま**。席が使えないとき（サインアウト・上限・打ち切り・中継が止まっている）は、**今の Gemini の要約を使う**（カルテは止めない）。

関連: 調査 `docs/plans/voice-input-and-karte-engine-research-2026-10-13.md`（§1・§3 は「サブスクでのカルテ自動下書きは採らない」と勧めていた。今回はそれを承知のうえでの当面の PO 判断）・人ごとの席 `docs/plans/agent-ui/per-user-seats-2026-10-13.md`・音声記録 `docs/plans/visit-voice-record-design-2026-09-17.md`。

---

## 1. データの流れ

```
スマホ（録音） ─▶ backend 受領 ─▶ jobs.run_transcribe_job
                                   │
                                   ├─ ① Vertex AI 東京 Gemini 2.5 Flash（今までどおり 1 回）
                                   │     音声 → 文字起こし（補正つき）＋ Gemini の要約
                                   │
                                   ├─ ② 拠点の旗 karte_via_seat が OFF → ①の要約を保存（今までと同じ・中継は呼ばない）
                                   │
                                   └─ ② 旗が ON → 中継のサーバーどうしの口へ（backend の網の中だけ）
                                         POST http://carelink-agent:8092/svc/karte/draft
                                         見出し X-Rakusuke-Service-Token（合言葉）
                                         本文 {recording_id, transcript, context{sex, visit_date, visit_time, staff_role}, presser_user_id}
                                           │
                                           ▼ 中継（relay.karte.KarteDrafter・同時に 1 件）
                                         席を決める（agent.settings: karte_mode / karte_seat_user_id）
                                         席の ChatGPT のサインインを確かめる（runner の auth_status）
                                         箱の runner に codex app-server を 1 本だけ起こしてもらう（その席の CODEX_HOME）
                                           thread/start: sandbox=read-only・approvalPolicy=never・ephemeral=true・cwd=空の作業フォルダー
                                           turn/start: 文脈＋文字起こし → JSON（summary）だけを返させる
                                           道具（コマンド・ファイル・質問）が動いたら打ち切り（tool_use）・90 秒で打ち切り（timeout）
                                         → {ok, summary, model, prompt_version} か {ok:false, kind}
                                           │
                                   ┌───────┘
                                   ▼
                     成功: summary を差し替えて保存（provider=codex-seat・model=gpt-…-sol・prompt_version=seat-v1）
                     失敗: ①の Gemini の要約を保存（provider=vertex・error_kind=seat_<kind>）
                     どちらも status=summarized・本人へ「要約ができました」の通知（今までと同じ）
```

- 文字起こし・トークン・費用（`tokens_in/out`・`cost_usd`）は Gemini の分のまま。席の分は ChatGPT の定額なので数えない。
- 画面（記録の詳細の「モデル」欄）は `codex-seat / gpt-6.1-sol / seat-v1` のように出る。要約の形は Gemini と同じ（主訴・様子 / バイタル / 処置・ケア / 申し送り / 次回 / free）なので、画面・帳票・人手の修正の退避（`previous_manual`）は変わらない。
- 1 件の所要: Gemini（30 分の録音で 1 分前後）＋席（数秒〜90 秒）。非同期のジョブのままなので待たせない。stale の判定（20 分）は十分に長い。

## 2. どこでデータが処理されるか（大事）

| 段 | 処理する所 | 送るもの |
|---|---|---|
| ① 文字起こし＋補正（＋予備の要約） | Google Vertex AI **東京**（asia-northeast1） | 音声・患者名・職員名・訪問日（今までどおり） |
| ② カルテの下書き（旗 ON の拠点だけ） | **OpenAI（ChatGPT）＝米国**（ChatGPT Business/Team は日本でのデータ保管を選べない） | **文字起こしの文**（会話の中で呼ばれた呼称・病状・服薬などはそのまま入る）＋性別・訪問日時・職種。**患者名・職員名の欄は送らない** |

- 2026-09-17 の「国内で処理する」方針から、旗 ON の拠点だけ外れる。**要配慮個人情報が国外（米国）の事業者で処理される**ので、患者様・ご家族への説明と同意（外国にある第三者への提供）の見直しが要る。旗を ON にする前に、お客様（事業所）と確かめること。法的な判断は顧問の専門家に。
- ChatGPT Business/Team は学習に使われない（契約の約束）。会話の写しは席に残さない（`ephemeral` の thread）。中継の記録（`agent.admin_events`）とログには本文を書かない（録音の id・結果・秒・モデルだけ）。

## 3. 規約上の注意（当面の PO 判断）

- ChatGPT Business/Team の約束は **1 席 1 人**（ログインを共有しない・席を貸さない）。指定した管理者 1 人の席で全職員の録音の下書きを作るのは、この約束から外れる。**一時的な PO の判断**として扱い、⚙ にもその旨を 1 行出している。
- OpenAI の利用規約の「自動・プログラムでの出力の取り出し」の禁止、Codex の公式の「自動処理は API キーが既定」の案内にも触れうる。看護師全員が席を持った時点で、§5 の「録音した本人の席」に切り替えるか、API（Bedrock の jp. 経路など）の契約に移す。
- 席の利用上限はその管理者の契約の分を使う。上限に当たったら `rate_limited` で Gemini に戻る（カルテは止まらない）。その管理者の相談（らく助エージェント）の上限も減る。

## 4. 部品

| 層 | ファイル | 中身 |
|---|---|---|
| 中継 | `agent/relay/karte.py` | `KarteDrafter`（席の選び方・確かめ・1 回きりの codex・打ち切り・答えの形をそろえる・記録）、指示文（`instructions()`・backend の看護記録のテンプレを文字起こしの文から作る形に合わせた）、`PROMPT_VERSION = "seat-v1"` |
| 中継 | `agent/relay/service_port.py` | サーバーどうしの口 :8092（`/svc/healthz`・`/svc/karte/draft`）。合言葉・箱の網からは 404・合言葉の設定が無ければ全部 404 |
| 中継 | `agent/relay/public.py` | `GET/PUT /agent-relay/karte/settings`（管理者だけ・⚙ 用） |
| 中継 | `agent/relay/codex_session.py` | `thread_extra`（thread/start に足す項目）を受けられるように |
| 中継 | `agent/relay/config.py`・`__main__.py` | `AGENT_RELAY_SERVICE_PORT`（8092）・`AGENT_RELAY_SERVICE_TOKEN`・`AGENT_KARTE_TIMEOUT`（90）・3 つ目の口を起こす |
| backend | `app/services/voice/karte_seat.py` | 旗の確かめ・名前の無い文脈・中継への 1 回の呼び出し・答えの形をそろえる・`SeatDraftError(kind)` |
| backend | `app/services/voice/jobs.py` | Gemini の後に `_maybe_seat_draft`。provider / model / prompt_version / error_kind を保存 |
| backend | `app/core/config.py` | `AGENT_RELAY_SERVICE_URL`・`AGENT_RELAY_SERVICE_TOKEN`・`KARTE_SEAT_TIMEOUT_SECONDS` |
| backend | `app/schemas/office_feature_flag.py` | `FeatureKey` に `karte_via_seat`（入れ忘れると旗の一覧 GET が 500） |
| 画面 | `components/agent/EngineSettings.tsx`（`KarteSeatSection`）・`engines.ts` | ⚙ の「カルテの下書きに使う席」（管理者の一覧が見られる人だけ） |
| 画面 | `app/(app)/admin/feature-flags/page.tsx`・`lib/schemas/g21.ts` | 「カルテの下書きを ChatGPT で」の旗（拠点ごとの ON/OFF） |

migration は無い（`agent.settings`・`agent.admin_events`〔0094〕と `visit_recordings` の既存の列を使う）。

### 4-1. 設定（`agent.settings`）

| キー | 値 | 既定 |
|---|---|---|
| `karte_mode` | `designated`（決めた管理者の席）/ `presser`（録音した本人の席） | `designated` |
| `karte_seat_user_id` | らく助の管理者の user id | なし（なしの間は `no_seat` で Gemini） |

- ⚙ で変える（管理者だけ）。選べるのは ChatGPT にサインインしている管理者だけ（中継も確かめる・していなければ 409）。変更は `agent.admin_events` に `karte_settings`（`mode=a->b seat=x->y`）。
- 席の管理者がサインアウトしたら、次の録音から `signed_out` で Gemini に戻る（設定はそのまま。サインインし直せば戻る）。

### 4-2. 失敗の種類（`visit_recordings.error_kind` = `seat_<kind>`・status は `summarized`）

| kind | 意味 |
|---|---|
| `config` | backend に合言葉が無い（中継を呼ばない） |
| `empty` | 文字起こしが空 |
| `off` / `auth` / `http` / `unreachable` / `timeout` / `parse` | 中継の口が無い（404）・合言葉が違う・中継の誤り・届かない・100 秒で打ち切り・答えの形が違う |
| `disabled` | 中継の全体の止め（`AGENT_RELAY_ENABLED=0`） |
| `no_seat` | 席が決まっていない（presser なら録音した人が分からない） |
| `signed_out` | 席の ChatGPT がサインインしていない・切れた |
| `busy` | 箱の runner が埋まっている・サーバーのメモリが足りない |
| `rate_limited` | 席の ChatGPT の利用上限 |
| `tool_use` | モデルが道具を使おうとした（打ち切り） |
| `failed` | そのほか（ターンの失敗・CLI が終わった） |
| `error` | backend の想定外（ログに例外） |

中継側の記録: `agent.admin_events` の `action=karte_draft`・`user_id`=使った席の管理者・`detail`=`rec=<録音 id> mode=… ok model=…` か `fallback=<kind>`・`secs=…`。本文は書かない。

## 5. 「録音した本人の席」への切り替え方（presser）

1. 看護師が ChatGPT Team の席を持ち、らく助の ⚙ の「あなたの ChatGPT」でサインインできるようにする。**いまの中継は管理者だけ**（`relay/auth.py` の `AGENT_USES = {"consult": 管理者}`）なので、職員が ⚙ を開けるよう、目的 `karte` と職員の役割を足し、サインインの口だけ `can_use(user, "karte")` で通す（相談の口は管理者のまま）。
2. ⚙ の「カルテの下書きに使う席」で「録音した本人の ChatGPT で作る」を選ぶ（`karte_mode=presser`）。
3. 以後、backend が送る `presser_user_id`（録音を受け取ったユーザー＝`visit_recordings.created_by_user_id`）の席で作る。サインインしていない人の録音は `signed_out` で Gemini に戻る。

## 6. 守り（だれが何をできるか）

- **口**: 公開口 :8090（Cloudflare の `/agent-relay/*`・ブラウザー）には下書きの道は無い。サーバーどうしの口 :8092 は compose の `ports` に書かない＝ホストにも Cloudflare にも出ない。backend の網（`deployment_default`）から `carelink-agent:8092` で届く。箱の網（`AGENT_SANDBOX_SUBNET`）からは 404。合言葉（32 バイトの乱数・`hmac.compare_digest`）。
- **合言葉の置き場**: 中継 = `/opt/carelink/secrets/agent-relay.env` の `AGENT_RELAY_SERVICE_TOKEN`（0400 root）。backend = `/opt/carelink/.env` の `AGENT_RELAY_SERVICE_TOKEN`。値は git に入れない。漏れたら両方を作り直して立て直す。
- **AI の側**: 1 回きりの codex app-server・その席の CODEX_HOME だけ（ほかの席・`/home/agent` は見えない＝人ごとの席と同じ隔離）。`ephemeral`（席に会話の写しを残さない）・`sandbox=read-only`・`approvalPolicy=never`（問い合わせが来ない＝中継が許すことも無い）・作業フォルダーは空（AGENTS.md・スキルを読ませない）。rk の合言葉は中継に登録しない使い捨て（内側口は断る）。出口は箱の許可リストのまま。道具が 1 つでも動いたら打ち切る。締め付け（`codex_config` の -c と `lockdown_problems`）は相談と同じ。
- **同時**: 中継の中で 1 件ずつ（待ちも 90 秒の打ち切りに含む）。runner の上限 2 本のうち 1 本を使う（相談の CLI は 1 本）。

## 7. 本番に入れる手順（deploy）

`agent/deploy/README.md` §3-10 が正。要点:

1. `openssl rand -hex 32` で合言葉を作る。
2. `/opt/carelink/secrets/agent-relay.env` に `AGENT_RELAY_SERVICE_TOKEN=<値>`。
3. `/opt/carelink/.env` に `AGENT_RELAY_SERVICE_URL=http://carelink-agent:8092`・`AGENT_RELAY_SERVICE_TOKEN=<同じ値>`（`KARTE_SEAT_TIMEOUT_SECONDS` は既定 100 のままでよい）。
4. 作り直す image: **中継（`carelink-agent-relay`）・backend・frontend**。箱（sandbox）・出口（egress）は作り直し不要。migration なし。
5. backend のコンテナから `/svc/healthz` が 200 になることを確かめる（README §3-10 の 6）。
6. ⚙ で席を選ぶ → 「機能の切り替え」で試す拠点だけ ON（**ON にする前に §2 の同意の確認**）。
7. 録音 1 件で `codex-seat` になること、席をサインアウトした状態で `seat_signed_out` の Gemini に戻ることを確かめる。

戻し方: 旗を OFF（すぐ戻る）。口ごと止めるなら中継の秘密ファイルから合言葉を消して立て直す。

## 8. 残ること・確かめていないこと

- 本番の箱（codex 0.162.1）で `thread/start` の `ephemeral`・`sandbox=read-only`・`approvalPolicy=never` が受け付けられること（開発 PC の 0.153.4 の schema にはある）。`read-only` の Linux の砂場は Docker の中では動かない（相談が `danger-full-access` にしている理由）が、下書きはコマンドを使わないので問題にならない想定。使おうとしたら `tool_use` で打ち切る。最初の 1 件で確かめる。
- 品質: 同じ録音で Gemini の要約と並べて看護師に見てもらう（10〜20 件）。指示文を変えたら `PROMPT_VERSION` を上げる。
- runner は spawn ごとに `/run/agent/<session_id>.settings.json`（約 1 KB）を書く。下書きの分（`karte-…`）も残る（月 700 件で 1 MB 未満）。作業フォルダー `/data/sessions/karte-…/` は空で、7 日の片づけが消す。
- 年代（年齢層）は患者に生年月日が無いので送っていない。
