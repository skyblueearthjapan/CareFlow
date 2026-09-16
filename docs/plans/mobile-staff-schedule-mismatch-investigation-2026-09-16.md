# スマホ盤（職員別スケジュール）と らく助盤面／カイポケの不一致 調査（2026-09-16・非破壊・読み取りのみ）

**発端**: PO 松岡様 9/16 09:27「QR コード運用で、個別の看護師のスケジュールにカイポケからの情報がうまく反映されていない」。
社内仮説「スマホ盤が固定訪問スケジュール(PFV)と連動していて、今週分の任意変更が反映されていないのでは」。

## 0. 結論（3 行）
1. 仮説は **不成立**。スマホ盤は PFV を読まず、`GET /api/v1/visits?staff_id=` で **今週の visits の `primary_staff_id`（＋secondary/mentor/visit_staff_assignments/同行）** を読む（`backend/app/api/v1/visits.py:76-92, 350-445`・`frontend/lib/queries/me.ts`）。
2. 「カイポケの情報が反映されていない」の正体は **9/15 のカイポケ→らく助取込で訪問の適用が 422 で失敗し、イベント取込だけ成功していた**こと（W38・W39 とも）。UI は失敗トーストの直後に「取り込み完了 — イベント: 追加 n…」の成功トーストを出すため、取り込めたように見える。
3. それとは別に、**9/16(水)・9/23・9/30 のコース A（高岡さん）7 件×3 週 = 21 件が訪問主担当 NULL** のまま（9/3 の週生成→プール一括投入の取りこぼし、修復 SQL が W37 のみだった）。PC はコース担当で高岡さんに見えるが、スマホ盤の高岡さんには出ない。**今日 9/16 がまさにこの状態**。

## 1. 各面のデータ源（コード事実）
| 面 | API | 担当の決め方 |
|---|---|---|
| スマホ 今日/今週 (`/m/today`, `/m/this-week`) | `GET /api/v1/visits?staff_id=&week_start=&week_end=` | `_staff_visibility_filter` = primary/secondary/mentor OR visit_staff_assignments OR 同行リンク。**コース担当は見ない** |
| PC 日タイムライン列・週リスト・日リスト見出し | `GET /api/v1/visits` + `GET /api/v1/courses` | **列見出し = `courses.assigned_staff_id`**（`CourseDayTablePanel.tsx:5632-5676` ほか） |
| PC 職員スケジュールタブ / スタッフ別タイムライン | 同上 | `primary_staff_id ?? コース担当 ?? 担当なし`（`StaffWeekBoard.tsx:346-353`, `StaffTimelineView.tsx:354-363`） |
| カイポケ送信 CSV | csv_builder | `primary_staff_id`、NULL ならコース担当へフォールバック（`csv_builder.py:266-288`） |
| カイポケ取込 | inbound / replace_inbound | CSV 職員名の名寄せ結果を **primary_staff_id に直接書く**（コース側を訪問に合わせて付け替える。ミラーは走らない） |

→ 「primary NULL でコース担当あり」の訪問は、PC では担当者に見えて、スマホと送信では欠落（送信はフォールバックで救われる）。

## 2. 本番 DB の実測（2026-09-16 10:30 JST・読み取りのみ）
### 2-1. 週別の不整合件数（取消除く）
| 週(月曜) | コース担当あり×主担当 NULL | 主担当≠コース担当 (override=true) | 合計 |
|---|---|---|---|
| 9/7 (W37) | 0 | 9 | 136 |
| 9/14 (W38) | **8** | **24** | 150 |
| 9/21 (W39) | 7 | 0 | 139 |
| 9/28 (W40) | 7 | 0 | 138 |

### 2-2. 主担当 NULL の中身
- W38 9/16(水) コース A（コース担当=高岡）: 篠原 09:00 / 山岡 11:00 / 重城 13:00 / 清水 13:00 / 井川 14:00 / 安永 15:30 / 菅原 16:15 の 7 件。created/updated とも `2026-09-03 15:45:49`（`generate-week-only W38`）。コース `bd81af7c` は `course_fixed`・`staff_assigned_at NULL`・updated `15:58:59`（= `pool-bulk-apply W38` 15:59:00 の直前）。W39 9/23・W40 9/30 も同じ 7 患者・同じ形。
- **原因**: 9/3 15:45〜15:59 に W38〜W40 を週生成→自動割付→プール一括投入した際、コース担当が付いたのに訪問へミラーされなかった。同日夜に根治した `f90ee71`（コース担当→訪問主担当ミラー）はこの操作の後にデプロイされ、修復 SQL は W37 稲毛A 9/9 のみに当てた。
- W38 のもう 1 件 = 麻生様 9/14 16:45（コース B 川名）。9/8 11:20 `place-and-fix` → `visit-move-week-only` の経路で担当なしのまま置かれたもの（別経路・1 件）。
- **今日 9/16 の高岡さんのスマホ**: D コース 6 件（岩船/植田/小宮/松崎/小俣/馬場）だけが出て、A コース 7 件が出ない。

### 2-3. 主担当≠コース担当 24 件（W38）
全件 `manual_staff_override=true`・9/6 14:16〜9/8 11:57 の手動配置（例: 9/14 海老澤 09:30 B 列(川名)に本名さん）。スマホ・PC 職員スケジュールタブ・送信 CSV はいずれも主担当で一致しており **不具合ではない**。ただし PC の日タイムライン列見出しはコース担当なので「PC では川名さんの列、スマホでは本名さん」に見える。

### 2-4. カイポケ取込の履歴（audit_logs / kaipoke_jobs）
| 時刻 | 操作 | 結果 |
|---|---|---|
| 9/14 13:03 | W38 smart-preview + events-preview | 200 / 202（適用なし） |
| 9/15 09:22 | W38 smart-preview + events-preview | 200 / 202 |
| 9/15 09:25:37 | **W38 smart-inbound-apply**（sheetId null・dryRun false） | **422** |
| 9/15 09:25:38 | W38 events-inbound-apply | 200（イベント 追加 20 / 削除 6） |
| 9/15 10:46 / 11:59 | W39 smart-preview + events-preview | 200 / 202 |
| 9/15 11:59:56 | W39 smart-inbound-apply | **409**（kaipoke busy） |
| 9/15 12:01:06 | **W39 smart-inbound-apply** | **422** |
| 9/15 12:01:06 | W39 events-inbound-apply | 200（イベント 追加 43） |
| 9/16 08:57 | W39 smart-preview + events-preview | 200 / 202（適用なし） |

DB 側の裏付け: W38・W39 に `source='import'` の行 0、9/13 以降の削除 0、9/10 以降の作成 0。
`kaipoke_jobs` には smart-apply のジョブが無い（422 で例外→ジョブ未作成）ので、履歴画面には「イベント取込 完了」だけが残る。

### 2-5. 422 の理由（最有力）
`smart_inbound_apply` の 422 は 3 種（`integrations.py:4800-4812, 4845-4853, 4965-4975`）。
- 未来週ゲート → 2026-08-09 に撤廃済み（常に eligible）。
- 0 件 CSV → 否（snapshot 147 行）。
- **置換ブロック `ReplaceBlockedError`**（`replace_inbound.py:195-217`）: 「らく助側で取消済みの訪問があります（今週だけ取消／ステータス連動）。⇧送信でカイポケへ反映してから置換してください（対象日: …）」。W38 には 小湊様 9/14 13:30（manual_cancel・9/9 19:45 取消）、W39 には 小湊様 9/21 13:30（同）が **ちょうど 1 件ずつ**あり、両週の 422 と一致する。
  （実績ガード「打刻が n 件」は 9/15 09:25 時点で W38 に打刻が無かったため該当しない。）

### 2-6. カイポケ現況との差（例: 9/16）
カイポケ W38 snapshot（9/15 09:22）では 篠原 09:00=川名 / 重城 13:00=川名 / 山岡 11:30=高岡 / 井川 15:30=高岡 / 安永 15:30=髙梨 / 菅原 16:15=髙梨。らく助はコース A(高岡)・主担当なし。取込が通っていないので、この差は残ったまま。9/11 総括の「W38 方針（らく助正 vs カイポケ正）」は未決のまま、現場はカイポケ正で取り込もうとして失敗した形。

## 3. 是正案（未実施・PO 判断待ち）
### A. 今日の運用（即応）
1. **W38 方針の決定**。カイポケ正なら: 小湊様の「今週だけ取消」を ⇧送信でカイポケへ反映（または当該日を除いて置換）→ 取込を再実行。らく助正なら: 🔄突合 → ⇧送信。
2. **主担当 NULL 22 件の修復**（W38 8 / W39 7 / W40 7）: `pg_dump` → PO 承認 → `UPDATE visits SET primary_staff_id = c.assigned_staff_id ... WHERE primary_staff_id IS NULL AND manual_staff_override = false AND status='planned'`（対象 id を列挙して実行）。カイポケ正で取込む場合は取込で上書きされるので、取込を先に通すなら不要（W38 9/16 は取込で消えて再作成される）。

### B. 再発防止（コード）
1. `useInbound.ts runApply`: 訪問 apply が失敗したらイベント apply を止める、または「取り込み完了」トーストを出さない。422 detail を画面に固定表示し、失敗ジョブを `kaipoke_jobs` に残す。
2. 置換ブロックの案内を「対象日を外して取り込む」導線に（現状は全週 422）。
3. コース担当を変える全経路（`PATCH /courses/{id}`・pool-bulk-apply・assign-staff 系）で訪問主担当ミラーを必ず通す。あわせて実現性チェック／突合に「コース担当あり×主担当 NULL」を検出項目として追加。
4. 設計判断: スマホの `_staff_visibility_filter` にコース担当フォールバックを足すか（PC 職員スケジュールタブと同じ規則に揃える）。

## 4. 参照
- 当日の SQL は本ファイル §2 のとおり（全て SELECT）。バックアップ・更新は一切行っていない。
- 関連: `session-2026-09-11-HANDOFF.md` §3-A1（W38 方針）、`session-2026-09-03-HANDOFF.md`（f90ee71 ミラー根治）、memory `careflow-staff-assignment-source`。

---

# 追補（同日午後）: 目標「スマホ版に職員スケジュール（そのスタッフの患者訪問＋イベント）を映す」を前提にした再調査

## 5. 真因①（取込 422）の連鎖 — 確定
1. 9/8 12:05 らく助→カイポケ送信（job 0ff737af・166 件）で 小湊様 9/14 13:30（川名）をカイポケへ **追加**（kaipoke_job_items に success で記録）。
2. 9/9 19:45 患者ステータス連動 Phase 0 一掃（PO 指示・21 件）で らく助側を `visit-cancel-week` → `source='manual_cancel'`。**その後 W38 の送信ジョブは無い**（取消はカイポケへ未送信）。
3. カイポケ側は事務が手で消した: 9/15 09:22 の W38 snapshot（147 行）に 小湊様 9/14 は **存在しない**。
4. 9/15 09:25 取込 apply: 9/14 に打刻なし（visit_checkins は 9/15 12:56 が最初）→ `protected_days=0`・6 日全て置換 → `replace_week_from_kaipoke` の「らく助側の取消」ガード（`replace_inbound.py:195-217`）が **らく助の cancelled 行だけを見て**（カイポケ現況は見ない）`ReplaceBlockedError` → 422。W39 も 9/21 13:30 の同じ取消で同型。W37 は取消 0 件だったので 9/11 に通った。
5. FE `useInbound.ts runApply` は訪問 apply の失敗後もイベント apply を続行し、「取り込み完了 — イベント: 追加 20 / 削除 6」の成功トーストを出す。ジョブ履歴にも失敗は残らない（例外でジョブ未作成）。
→ **設計欠陥 2 点**: (a) ガードが「カイポケにまだ残っているか」を確認せず一律ブロック（今回はカイポケ側で既に消えており、置換しても取消は復活しない）。(b) 部分失敗を成功と表示する UI。

## 6. 真因②（主担当 NULL）の連鎖 — 確定
- 9/3 15:45:49 `generate-week-only W38`（同 15:46 W39・15:46:58 W40）でコース A(水) `bd81af7c` 配下に 7 件が **primary NULL** で生成（当時コース担当なし）。
- 9/3 15:59:00 `pool-bulk-apply W38`（15:59:19 W39・15:59:45 W40）→ `reset_visits_to_fixed` のローテーションがコース担当を 高岡 に書き戻し（`staff_assigned_at NULL`・`updated_at 15:58:59` がその署名）。既存 7 件はミラーされず NULL のまま。
- ミラー修正 `f90ee71` の commit は 9/3 20:20 JST、本番反映は 9/3 20:20 JST（`git reflog`: 11:20:49 UTC）= **上記操作の 4 時間後**。修復 SQL は W37 稲毛A 9/9 の 5 件のみに適用。
- 別経路 1 件: 麻生様 9/14 16:45 = `place-and-fix`（`schedule.py:1033-1044`）が `Visit(...)` に `primary_staff_id` を渡さない → 担当ありコース B に置いても訪問主担当は NULL。
- スマホ `_staff_visibility_filter` にコース担当フォールバックが無く、`GET /courses` は admin 専用なので FE 側でも補えない。

## 7. 目標に対する差分（PC 職員スケジュールタブ vs スマホ）
| 論点 | PC タブ | スマホ現状 | 必要な手当 |
|---|---|---|---|
| 訪問の帰属キー | primary → **コース担当フォールバック** → 担当なし（`StaffWeekBoard.tsx:349-352`） | primary/secondary/mentor/VSA/同行のみ（`visits.py:76-92`） | BE のフィルタに「primary NULL かつ courses.assigned_staff_id=自分」を OR 追加（`staff-off-week` に同型実装あり `schedule_v2.py:1840-1848`） |
| 職員イベント（manual/kaipoke） | 緑チップ・📝ゼロ長メモ・`cancelled_at`=「今週除外」（:715-781） | **どこにも出ない**（staff-events の import ゼロ） | `GET /api/v1/staff/{me}/events?from&to`（staff 本人は許可済 `staff_events.py:37-41`）を hook 化し today/this-week に合成 |
| 固定イベント（朝会 fixed） | 最上段の帯（`CourseDayTablePanel.tsx:4176-4195`） | 無し | 同上（スマホは分離不要） |
| 休み/時間変更 | 週セルにバッジ（:613-624） | `/m/shifts` の月カレンダーのみ | `GET /staff/{me}/overrides` を today/this-week に重畳 |
| 今週だけ取消 | 打消線＋赤「取消」バッジ | 打消線のみ | バッジ追加 |
| コース見出し | 拠点＋コース名で塊表示 | 無し（visits DTO に course_label 無し） | 任意 |
| 同行・2 名体制 | 描かない | 同行バッジあり | スマホの方が厚い。変更不要 |
| 非稼働バッジ | `visitVisibility.ts` | 同じ | 一致 |
| 一発で返す BE | 無し | — | 3 本（visits / events / overrides）で足りる |

## 8. イベントを映すときの地雷: 手入力の朝会 × カイポケ取込の朝会が二重
- 本番 W38: manual 朝会 31 行（8/24 手入力）＋ kaipoke 朝会 24 行（9/11 08:52 の W38 イベント取込）＋ 9/15 1 行 → ほぼ全職員で同時刻に 2 行（月次MTG も同型）。
- 取込は `external_id`（個別業務ID:職員ID:日付）だけで突合し、対象は `source='kaipoke'` 行のみ（`events_inbound.py:237-253, 325-365`）。**manual 行を吸収・削除する仕組みは存在しない**。8/25b の「二重吸収」是正は fixed 展開が既存行の上に重ならないためのもので、取込側には無関係。
- 解消経路は 2 つのみ: manual 行をらく助→カイポケ送信で昇格（`events_outbound.py:166-209`）するか、manual 行を消す。
- スマホにイベントを出すなら、先に (a) この二重を解消する運用（送信 or 削除）か、(b) 表示側で「同一職員・同一開始・同一タイトル」を畳む処理を入れる。

## 9. 是正の順序（案・未実施）
1. **データ**: 主担当 NULL 22 件の修復（またはカイポケ正で W38 取込を通す）。W38 方針は PO 判断。
2. **取込ガード**: 「らく助側の取消」ブロックを、カイポケ現況にその行が残っている場合だけに限定（残っていなければ置換して問題ない）。＋ FE の部分失敗表示・失敗ジョブの記録。
3. **担当 NULL を生む経路**: `place-and-fix` で担当ありコースなら primary を埋める／全コース担当変更経路でミラー／突合・実現性チェックに検出を追加。
4. **スマホ**: BE フィルタにコース担当フォールバック → events/overrides の hook → today/this-week に合成表示（`cancelled_at`・📝・休みバッジ）。二重イベントの畳み込みを同時に。
