# 2026-10-30 セッション引き継ぎ（患者マスタの訪問の設定を予定づくりに反映する）

PO の相談（10/30）：患者マスタの「隔週」が週の生成に効かず毎週出る。どの週を基準にするかも決まっていない。隔週に限らず、設定できる項目をすべて網羅して対応したい。
設計（正典）：`docs/plans/biweekly-monthly-design-2026-10-30.md`（§9 に PO の決定と段 3 の実装メモ）。

## 1. 状態

- ブランチ `feat/visit-frequency`（origin/develop = 62090614 から）。22 コミット、129 ファイル。**10/30 に develop へ push（PO 了承）、同日 PO の了承で本番へ反映済み（71f996db・alembic 0102）**。記録は §8。
- 5 段をそれぞれ実装 → 別のレビュー役（Opus・code-reviewer）→ 指摘を直す、で仕上げた。どの段も判定は「軽微な修正で可」、重大な指摘は 0。指摘はすべて直した。
- 実際の画面での確認は**まだ**（Docker を閉じているため手元で動かしていない）。

## 2. 入ったもの

| 段 | 中身 | 主なコミット |
|---|---|---|
| 1 | 訪問頻度の判定（`visit_frequency.py`）。毎週／隔週（起点の週＝月曜・日数の差で A/B）／月の第何週（`visit_weeks`）。枠ごとの「訪問する週」（`patient_fixed_visits.visit_week` every/A/B・**alembic 0102**）。週の生成・保留プール・自動割当・作り直し・コピー・提案・エージェントのすべてでお休みの週は作らない。設定不足（起点なし・第何週なし）は毎週のまま＋注意。特別週の週は頻度で間引かない | 6944d802・17ebb25b・0b6e7862・a0e3f5ff |
| 2 | 画面：患者マスタ（PC・スマホ）の頻度の欄・次の訪問 6 回・枠の A/B。保留プールでお休みの週を「足りない」に数えない（`GET /schedule/v2/visit-frequency-week`）。**保存で weekly_pattern のキー（entries など）が消える不具合を解消** | 7aab81f7・67178bc6・ee6fb8e4・38986f98 |
| 3 | NG 曜日（`weekly_pattern.ng_weekdays`・`ng_weekdays.py`）を全経路で守る（日を選ぶ経路は候補にしない・置く/動かす API は 422 `ng_weekday`・既存の枠は消さず点検に出す）。ならしで「男性のみ」も守る。2 名体制の正を `requires_multiple_staff` に。時刻変更の申請の承認で weekly_pattern を丸ごと置き換えていたのを差分の重ねに | d72a3f17・a9ee67ae・81ac390b・b96bf832 |
| 4 | 保留プールの一括投入で 2 名で訪問の方も置く（2 人とも同じ時刻に空き・別の人・全部の決まり）。**2 名で訪問は本番と同じ 1 行（primary＋secondary・組 id なし）で作る**（組 id の 2 行は本番の索引 `uq_visits_pds_group_active` に当たるため。試験にも同じ索引を張った）。盤面の 2 人目の列に薄い写し | 057188cf・7325ed87・11056dbd・75ebba07 |
| 5 | Excel とカルテ（頻度・起点の週・第何週・NG 曜日・枠の訪問する週。固定訪問の時間タイプ列は廃止・古い形も取込可・読めない NG 曜日は行エラー）。エージェントの SQL・操作・スキル。週の画面のお休みの注記・2 人目のコースの件数 | 7191fad4・38489af9・a708e929・a3b8ca69・c657e9b6・466d8561 |

## 3. 本番へ出すとき（PO の了承の後・別に了承）

1. pg_dump を取る。
2. **alembic 0102 を先に流す**（`patient_fixed_visits.visit_week`）。エージェントの SQL 5 本はこの列を読むので、列が無いと止まる（10/30 に本番で読み取り確認：列を 'every' に置き換えた写しで 5 本とも結果が出た）。
3. backend・frontend・agent を反映。
4. 反映後、隔週・月次の方（稼働中：隔週 3・月次 2）は起点の週／第何週を人が入れるまで毎週のまま。隔週の 2 名は訪問週に "1,3" が入っている（2 週おきか第 1・3 週かを人が選ぶ）。

## 4. 確かめたこと（本番・読むだけ）

- 稼働中で `requires_multiple_staff` の方は 0 名。既存の 2 名の訪問（9 月以降 11 件）はすべて 1 行の形。
- 11 月分は、隔週・月次の 5 名が毎週で入っている（10/28〜29 のエージェントの組み直し）。事務所がカイポケで月次の 2 名を合わせ済み。らく助側は PO「システムの話であって予定の変更のお願いではない」で触っていない。案のファイル `agent/runs/plans/nov-biweekly-monthly-cancel.json` は使わない。

## 5. 残り・PO に聞くこと

- 実際の画面での確認（手元で Docker を開くか、本番反映の後に PO と確かめるか）。
- develop への push の了承、本番反映の了承（別々に）。
- 方針の差（判断待ち）：保留プールの一括投入で、担当が休み・性別が合わない枠は、2 名の組は候補から外し、1 名の方は警告付きで置く。1 名の方も外すかは PO 判断。
- 特別週（特別訪問週間・特別週パターン・特別週の枠・旧 special_weeks）は別の相談で設計する（PO 10/30）。そのときの材料：正として動いているのは特別訪問週間だけ。患者の保存で「特別週パターンを使用する」にチェックがあると、保存のたびに通常の設定で上書きする（前からの動き）。
- 保留プールの「お休みの週 N 名」は拠点で絞っていない。週の盤面（週リスト・職員別）に 2 人目の名前のカードは無い（件数だけ）。

## 6. 同じ日のほかの調べ（10/30・本番は読むだけ）

- 10/29 の朝のカイポケとの差分 92 件は、事務所がカイポケで組み直したため（PO「放置」）。`docs/plans/session-2026-10-28-HANDOFF.md` §4 に追記。
- 熊谷 直也様（P135・10/15 登録）の打刻ができない件：サーバーの QR は有効（v1・失効なし）。10/30 に PO の PC の画面のカードを熊澤さんのスマホで読めた（打刻は記録されていない）。お宅のカードの有無・刷り違いを現場で確かめる。登録から一度も読まれていない。

## 7. 気になる点（前から）

- 前から落ちている試験：`test_patients_v2` 4 件、`test_schedule_v2_api::test_reset_to_fixed_*` 2 件、`test_pfv_sub_office::test_pool_expansion_includes_sub_office_patients`、`test_patients_excel_replace_all::test_replace_all_manager_forbidden`。まとめて流すと時々落ちる：`test_visits::test_visits_delete_manager_returns_204`、`test_pfv_sub_office_validates_course_template_office`（単独では通る）。
- PC のメモリが少なく、試験は 1 ファイルずつ流した。pre-commit が未ステージのファイルを戻すときに改行が CRLF になり、prettier/ruff format で止まることがある（ステージしたファイルに format をかけ直して入れ直す）。

## 8. 本番の作業の記録（2026-10-30・時刻は UTC）

- 事前：本番 505fa1f0・alembic 0101。カイポケのロボットは止まっていた。ディスク空き 33 GB。11/26 前後の週の訪問 967 件・取消 6・2 名 1・固定訪問の枠 173（10/26〜12/5）。
- 10:49 pg_dump：`/opt/carelink/backups/pre-deploy-visit-frequency-20261030-1049.sql.gz`（6.9 MB・gunzip -t 済み）。
- develop を 71f996db まで fast-forward → backend・frontend を build → **入れ替えの前に** `run --rm --no-deps backend alembic upgrade head`（0101→0102・単一 head・既存 173 枠はすべて every）→ `up -d --no-deps --force-recreate backend frontend` → agent-relay・agent-sandbox を build・up（`-p carelink-agent`・agent.prod.env）。
- 確認：healthz 内側・公開・/agent-relay とも 200、全コンテナ healthy。訪問・取消・2 名・枠の件数は前と同じ。エージェントの SQL 5 本が本番で通った。`/schedule/v2/visit-frequency-week` と患者の visit-frequency-preview が応答。入れ替えの後の backend に 5xx・Traceback なし。
- 残り：検証用の患者様で画面を PO と確かめる。現場は Ctrl+Shift+R が要る（画面を変えたため）。
