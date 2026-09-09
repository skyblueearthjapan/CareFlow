# 患者ステータスと予定の連動 — 現状調査と設計検討（2026-09-09）

**状態（2026-09-09 21:00）: PO 決定済み（§6-C）・Phase 0 残骸一掃は本番実施済み（§4）・Phase 1 実装計画あり（§7・着手即可）。実装は次セッション。** 前身 = `backlog-2026-08-31-patient-status-leak.md`（事象と対応案 A〜D）。本書はその現状再確認（2026-09-09 HEAD 7e9d80a・本番 203a5c1）と、運用込みの仕組みの設計案。

## 0. 要約
- **目標（PO 方針）**: 患者ステータスが「稼働中」以外の間は基本スケジュール（盤面・プール・提案・カイポケ送信）にその患者は現れない。稼働中に戻ったら型（固定訪問）から予定に戻る。
- **現状**: ステータスは **「週を生成する瞬間」だけ** 見られる。生成済みの未来の予定・特別訪問週間の ○ プール・手動追加/配置の全経路・表示の全画面・カイポケ往復は **ステータスを一切見ない**。ステータス変更 API（PATCH）に連動処理は無い。
- **本番の残置（2026-09-09 時点）**: 非稼働 9 名の未来 planned 23 件（W38 3・W39 1・W40 1・W42 18）、藤原様（入院中）の特別訪問期間が active のまま ○ プール 25 枚、9/8 に入院となった小湊様の 9/14〜10/12 の 4 件がその直後の週生成で残った。
- **設計の骨子**: 「蛇口を 3 重にする」。①**変更時の連動処理**（未来の予定を取消・特別訪問期間を終了・復帰時は型から再生成、件数を確認ダイアログで提示）②**入口ガード**（非稼働患者を予定に入れる全 API を 422、プール/⭐/取込も除外）③**表示の保険**（訪問 DTO に患者ステータスを載せ、残骸はバッジで見える化）。カイポケ側は突合で「非稼働患者の行」を削除候補に出す＋週間パターン停止の運用手順。

## 1. 現状の確定事実（コード・本番 DB）

### 1-1. ステータスの定義
| 値 | 画面表記 | Excel | 意味（運用上） |
|---|---|---|---|
| active | 稼働中 | 稼働 | 訪問中 |
| pending | 開始前 | 未開始 | 契約前・開始待ち |
| suspended | 一時休止 | 休止 | 一時的に訪問停止 |
| admitted | 入院中 | 入院 | 入院により訪問停止 |
| cancelled | 解約済み | 解約 | 契約終了 |

- 単一ソース = `backend/app/schemas/v2/patient.py:52`（Literal）。DB は `String(16)` で CHECK 制約なし（テスト・コメントに `inactive` が残るが Literal 外。判定は `!= 'active'` で書く）。
- **日付列は無い**（`status_changed_at`・入院日・退院予定日・解約日のいずれも無し）。変更時刻は `audit_logs` の PATCH 行からしか追えない（before/after 列は未使用）。
- **遷移の検証・連動処理は無い**。`PATCH /patients/{id}`（`app/api/v1/patients.py:266-285`）は setattr して commit するだけ。申請経由 `patient_status_update`（`pending_request_applier.py:1092-1117`）も同じ。
- 変更 UI は患者フォームの `<select>` 1 つ（PC 編集ページ・盤面の患者編集ダイアログ・現場カルテの 3 入口が同じフォーム）。確認ダイアログ無し。
- 本番の分布: active 76 / admitted 8 / suspended 6 / cancelled 13 / pending 7。
- **スタッフには前例がある**: `app/api/v1/staff.py:200-210` で非 active 化時に未来の同行を purge（患者版はこれと同じ形になる）。

### 1-2. ステータスを見ている箇所（これだけ）
| 箇所 | 効き方 |
|---|---|
| 週生成 `layer1_expander.expand_week`（`:658-663`）・v1/v2 の患者ロード | 生成対象を active に絞る。**ただし冪等削除（`:680-683`）も active 患者に絞るため、変更前に生成済みの未来週を再生成しても非稼働患者の旧 visit は残る**（W42 残置の正体） |
| `reset_visits_to_fixed`（固定枠に戻す）`auto_allocator_v2.py:1471-1512` | 削除側は **status 不問**・再生成側は active のみ → 唯一「非稼働患者の残骸を掃除する」既存経路（2026-05-31 bacb442）。ただし手動実行・拠点週単位 |
| QR 打刻 `checkin/judge.py:108` | 非稼働患者の QR は **404**（盤面には出ているのに現地で打刻できない、という不整合） |
| PFV 検査・同住所候補・course-load・/monitor/nearby | 「他の患者」を数えるときだけ active に絞る（障害物としては消えるが、本人の予定は残る） |
| FE `CourseDayTablePanel` のプール・＋訪問・空き枠登録の候補（`:1424/:1441/:1842/:5121`） | クライアント側で `p.status === 'active'` に絞る（BE は絞っていない） |

### 1-3. 見ていない箇所（漏れ）
1. **生成済み未来の予定**: PATCH に連動なし・週再生成でも掃除されない。`apply_week_only` の掃除は **退行**（`tests/test_inactive_patient_visit_cleanup.py::test_apply_week_only_soft_deletes_inactive_patient_visit` が HEAD で失敗。`auto_allocator_v2.py:9182-9192` で削除対象を plan 内患者に絞った際に壊れた）。
2. **特別訪問週間**: `GET /special-visit-marks/pool` は period.status と deleted_at しか見ない（`special_visits.py:925-936`）。`place`（course_template_id / visit_id / weekday の拡張含む）も患者 status を見ない。期間作成も可。
3. **手動追加・配置の全経路が開いている**: `propose-slots`・`pool-overview`・`pool-bulk-simulate/apply`・`apply-individual`（PFV も書く）・`place-and-fix`・`POST /visits`・`visit-move-week-only`・`fix-or-pattern`・`update-fixed-time-master`・`PUT fixed-visits`。
4. **表示の全画面**: board / visits / monitor / mobile / 現場ボード / 実現性 / health のどれも `Patient.status` を join しない。訪問 DTO（visitRead・boardVisit・MyVisit・⭐ ticket）に患者ステータスが無い。
5. **カイポケ往復**: csv_builder（送信）・突合・未送信サマリ・取込（diff/replace/smart）のいずれも見ない。取込は名前一致で非稼働患者の visit を作り直す。月間展開はカイポケ側の週間パターン駆動で、らく助は関与できない。
6. **付随物**: PFV 行（非稼働 10 名 22 行）は残る＝復帰用の型として妥当。同住所リンク・未処理の申請（pending_requests）・現場シートの患者検索・PatientFixedVisitsPanel は status を見ない。

### 1-4. 本番 DB の残置（2026-09-09 読み取り）
| 対象 | 内容 |
|---|---|
| 未来 planned（非稼働 9 名・23 件） | 小湊(入院 9/8) 4 件 9/14〜10/12 auto／藤原(入院) 2 件 9/15・9/17 manual_week（⭐配置）／石塚・朝倉・小川・渡辺・清水・藤田・斎藤 = W42 17 件 auto |
| 特別訪問期間 | 藤原様 9/3〜12/2 active・○ プール 25 枚・配置済み 5 枚 |
| PFV | admitted 4 名 11 行・cancelled 4 名 8 行・suspended 2 名 3 行 |
| カイポケ | 9 月以降のスナップショットは DB に無い（8 月まで）。8/31 調査時点で月間展開に同患者が残っていた。**export で要再確認** |
| 生成済み週 | W37〜W40 と W42（W41 は未生成） |

## 2. 設計方針（原則）
1. **ステータスは予定の蛇口**。`active` 以外は「予定に存在しない」を BE が保証する（FE の絞り込みは補助）。
2. **型（PFV・weekly_pattern）は消さない**。休止・入院は必ず戻る前提。解約も当面は残す（§6-6）。
3. **過去は触らない**。取消対象は「当日以降の planned」のみ。completed / in_progress / 打刻済みは不変。
4. **消し方は「取消（cancelled）」で、出所を専用値で刻む**。カイポケ送信で delete 差分になる・取込の add で復活しない・undo できる、という既存の `manual_cancel` の保護をそのまま使う（§3-2）。
5. **人が決める瞬間は 1 回**: ステータスを変える保存時の確認ダイアログに「消える件数・特別訪問期間・復帰時の再生成」を出す。それ以外は自動。
6. **表示は隠さず「見える化」**: 連動処理が効いていれば残骸はゼロのはず。残っているなら不整合なので、バッジ（入院中）で PO に見せる。プール・候補・提案には出さない。

## 3. 仕組みの設計

### 3-1. 変更時の連動処理（core）— `apply_patient_status_to_schedule`
新設サービス `app/services/patient_status_sync.py`。PATCH（と申請適用）の status 変更を検知して呼ぶ。**冪等**（何度呼んでも同じ結果）にして、管理者ボタン「ステータスを予定に反映」と残骸一掃スクリプトからも同じ関数を使う。

**active → 非 active（pending/suspended/admitted/cancelled）**
| 手順 | 内容 | 既存部品 |
|---|---|---|
| 1 | `effective_from`（既定=今日 JST。§6-2）以降の当該患者の `planned` visit を全 source 対象に取得。2 名体制は `visit_group_id` ごと | `cancel_visit` の targets ロジック |
| 2 | `status='cancelled'`・`source='status_cancel'`（新定数 `VISIT_SOURCE_STATUS_CANCEL`、13 文字）。元 source は op-log の forward_payload に控える | `_set_visits_cancelled` 相当 |
| 3 | 特別訪問期間が active なら `status='ended'`（end_date=effective_from-1）。プールの ○ は `cancelled`、配置済み ● は手順 1 で visit が取消される | `PATCH /special-visit-periods` の内部関数化 |
| 4 | 未処理の申請（`pending_requests.target_patient_id`）は「患者が非稼働」で自動却下 or 警告（§6-8） | applier |
| 5 | `patients.status_changed_at` を記録（mig 追加・timestamptz）。任意で `status_note` | — |
| 6 | op-log に 1 グループ（`op_kind='patient_status_cancel'`、週をまたぐので **週単位の undo とは別枠**の「患者単位 undo」）。監査ログにも件数を残す | `record_op` |
| 7 | 通知: admin 向け「◯◯様 入院中: 予定 N 件を取消・特別訪問期間を終了・カイポケ送信対象 M 週」 | notifications |

**非 active → active（復帰）**
| 手順 | 内容 |
|---|---|
| 1 | 生成済みの未来週（visits が存在する ISO 週の集合。今は W37〜W42）を列挙 |
| 2 | 当該患者の `status_cancel` 訪問（当日以降）を soft-delete（打ち消し線の残骸を残さない） |
| 3 | 週ごとに `reset_visits_to_fixed(patient_id=…)` で型から再生成（既存・患者単位対応済み）。担当は型のコース担当 → 未割当なら L3 の対象 |
| 4 | 確認ダイアログで「W38〜W42 に N 件作ります。特別訪問週間を設定しますか（退院直後の増回）」を提示。⭐ の再開は自動ではやらない（新しい期間を PO が作る） |
| 5 | op-log グループ `patient_status_restore`・通知 |

**pending → active**（新規開始）も同じ復帰処理。**cancelled → 他**は原則無し（誤操作の戻しのみ想定・警告文言）。

### 3-2. 「取消」か「削除」か（判断根拠）
| | 取消（status=cancelled, source=status_cancel）**推奨** | soft-delete（deleted_at） |
|---|---|---|
| 画面から消えるか | 打ち消し線で残る → **§3-4 の表示層で「非稼働患者の取消は非表示」にして解決** | 消える |
| カイポケ送信 | csv_builder が除外 → delete 差分（8/31 復旧で実証済み） | 同じ |
| 取込(add)での復活 | `manual_cancel` と同じ扱いに 2 箇所追加すれば防げる（`inbound.py:906`・`replace_inbound.py:200`） | 防げない（§3-3 の取込ガードが必須） |
| undo | 既存 `cancel_visit` の逆操作をそのまま流用 | delete/place の逆操作（あり） |
| 復帰時 | soft-delete してから再生成 | 再生成のみ |
| 監査・打刻履歴 | visit 行が残り履歴が追える | 行は残る（deleted_at） |

→ 取消方式を採り、表示層で非稼働患者の `status_cancel` を非表示にする（通常の「今週だけ取消」の打ち消し線は従来どおり）。

### 3-3. 入口ガード（BE 422 + FE 除外）
共通ヘルパ `ensure_patient_schedulable(db, patient_id)` → 422 `{code: "patient_not_active", status, label}`。FE は既存の 422 トースト規約で「◯◯様は入院中のため予定に入れられません」。
| 経路 | 対応 |
|---|---|
| `propose-slots` / `pool-overview` / `pool-bulk-simulate` / `pool-bulk-apply` / `apply-individual` | 422（対象患者に非稼働が混じれば全体拒否ではなく **その患者だけ除外して warnings に載せる**） |
| `place-and-fix` / `POST /visits` / `visit-move-week-only` / `fix-or-pattern` / `update-fixed-time-master` / `apply-swap` | 422 |
| ⭐ `POST /special-visit-periods` / `place`（全モード）/ `restore` | 422。`GET /special-visit-marks/pool` と calendar は `Patient.status='active'` を join に追加 |
| `PUT /patients/{id}/fixed-visits` | **型の編集は許可**（復帰に備える）。`change_scope=pattern_and_week` の週反映だけ拒否 → 確認ダイアログ E に「非稼働のため型だけ」を固定表示 |
| カイポケ取込（diff/replace/smart） | 非稼働患者の行は **add しない**。プレビューで「非稼働患者（らく助側）」の分類を新設し、削除候補として突合へ（§3-5） |
| 週生成の冪等削除 `layer1_expander.py:680` | 削除対象を「拠点内の全患者（status 不問）」に広げ、source=auto の残骸を掃除（reset と同じ思想）。`apply_week_only` の退行も同じ修正で直す（既存テストが緑になる） |
| FE | ⭐ チケット・現場シートの患者検索・`PatientCombobox`（未使用）・PatientFixedVisitsPanel（バナー「入院中: 型のみ編集可」）・PatientCard の死んだ `before_start` バッジを `pending` に直す |

### 3-4. 表示の保険
- `_serialize_visit`・board・MyVisit・⭐ ticket の DTO に `patient_status`（nullish）を追加。
- 各ビュー（日/週タイムライン・週リスト・職員スケジュール・盤面セル・モニター・モバイル・現場ボード）は `patient_status !== 'active'` の visit を: `status_cancel` は非表示・それ以外（planned が残っている＝不整合）は **バッジ「入院中」＋薄色** で表示。トグル「非稼働を表示」で非表示分も出せる（残骸点検用）。
- 実現性チェック・health・提案系（improvement/scope/unblock/substitute）は非稼働患者の visit を対象外に。
- 患者詳細ダイアログ（`PatientScheduleDetailDialog.tsx:493`）の生値表示を `STATUS_LABEL` に。

### 3-5. カイポケ側
- **突合（🔄）**: らく助側が非稼働の患者に対応するカイポケ行を「非稼働患者の行」カテゴリで **削除候補** に出す（対応案 D）。週単位・過去日ガードは既存どおり。
- **未送信サマリ**: 取消による delete 差分を「◯◯様 入院中の取消 N 件」としてまとめ表示。
- **運用（人）**: カイポケの週間パターンを止めないと翌月の展開で復活する。ステータス変更の通知文に「カイポケの週間パターンを停止してください」を必ず入れ、runbook に手順を書く。RPA での自動停止は将来課題（現行 RPA に該当操作なし）。

### 3-6. 確認ダイアログ（FE）
- 発火点は **フォーム保存時**（`useUpdatePatient` の `values.status !== initial.status`）。3 入口（PC 編集・盤面ダイアログ・現場シート）を 1 箇所で拾う。
- 事前に `GET /patients/{id}/status-impact?to=admitted&from_date=…` で件数取得: `{planned_by_week: {W38: 3, …}, sources: {auto: 4, manual_week: 2}, special_period: {active: true, pool_marks: 25, placed: 5}, fixed_visit_rows: 3, kaipoke_weeks: 3}`。
- 文言例: 「小湊様を **入院中** にします。9/14〜10/12 の予定 4 件を取消し、カイポケ送信対象になります（3 週）。固定訪問の型は残ります。」＋ラジオ「当日から／明日から」＋チェック「特別訪問週間も終了する（既定 ON）」。
- 復帰時: 「稼働中に戻します。型から W38〜W42 に N 件を作ります。」＋リンク「特別訪問週間を設定」。
- 作法は `add-visit-anywhere-design.md` §3-5（幅・14px・行クリック）。

### 3-7. 挙動の説明（運用の目線・小湊様の例）

前提: 小湊様は月・木 10:00 の固定訪問（型）があり、W37〜W42 の予定が生成済み。9/8（火）に入院と分かった。

**A. 「稼働中」→「入院中」にしたとき（保存ボタンを押した瞬間）**
1. 保存の前に確認ダイアログが出る。「小湊様を入院中にします。9/8〜10/12 の予定 9 件を取り消します。特別訪問週間は設定されていません。固定訪問の型（月木 10:00）は残ります。カイポケへの取消送信が 5 週分できます。」当日分の扱い（当日から／明日から）を選び、「入院中にする」を押す。
2. その瞬間に、当日以降の **予定（planned）だけ** が「取消」になる。過去の訪問、訪問済み、打刻済み、訪問中は一切触らない。2 名体制のペアは 2 件まとめて取消。＋訪問や ⭐ から入れた予定も、カイポケから取り込んだ予定も、出所を問わず対象。
3. 特別訪問週間が動いていれば同時に終了し、プールの ○ チケットは消える。配置済みの ● は 2 で取消済み。
4. 画面上の見え方: 盤面・日/週タイムライン・週リスト・職員スケジュール・モニター・モバイル・現場ボードのすべてから **小湊様の予定が消える**（打ち消し線も出さない）。プール・候補提案・空き枠登録・＋訪問・⭐ プールの患者一覧にも出ない。患者一覧では「入院中」タブに移る。
5. 患者ページの固定訪問（型）はそのまま見え、編集もできる（復帰に備えて直しておける）。ただし「今週にも反映」はできない。
6. カイポケ: 取消した分は次の突合で「削除」差分になり、⇧送信で消せる。未送信バッジ ● に「小湊様 入院中の取消 N 件」と出る。カイポケの週間パターンは人が止める（通知に案内文を入れる）。止めないと翌月の月間展開で復活するが、その行は突合で「非稼働患者の行」として削除候補に出る。
7. 以後の週生成では小湊様は最初から対象外。誰かが手で入れようとしても「入院中のため予定に入れられません」で弾かれる。取込でも作られない。
8. 間違えたときは、盤面の「戻る」（op-log）で取消前に戻せる。監査ログには誰がいつ何件取り消したかが残る。

**B. 「入院中」→「稼働中」に戻したとき**
1. 保存の前に確認ダイアログが出る。「小湊様を稼働中に戻します。固定訪問の型（月木 10:00）から、生成済みの W39〜W42 に 8 件を作ります。」当日／明日からを選び、必要なら「特別訪問週間を設定する」を押す（退院直後の増回はここで作る）。
2. A で取り消した予定は消し（打ち消し線の残骸を残さない）、型から作り直す。担当は型のコース担当がそのまま付く。担当が空のコースなら「担当なし（M）」に入り、自動割当の対象になる。
3. 復帰日より前の週は触らない。未生成の週は、次の週生成で普通に入る。
4. 画面上に予定が戻り、プール・提案・＋訪問の対象にも戻る。QR 打刻もできる。
5. カイポケ: 作り直した分は突合で「追加」差分になり、⇧送信で入る。カイポケの週間パターンも人が再開する。
6. A の前と同じ状態に「自動で」戻るわけではない。A の後に型を直していればその型で作られる。A の前に ＋訪問で入れていた不規則な予定は戻らない（必要なら ＋訪問で入れ直す）。

**C. 一時休止・解約済み・開始前も同じ**
- 「稼働中以外」はすべて A と同じ扱い。差は運用上の意味だけで、システムの挙動は変えない（解約済みは型も残す＝再契約時に使える。§6-6）。
- 開始前 → 稼働中は B と同じ（型を先に作っておけば、稼働中にした瞬間に予定が入る）。

**D. 変わらないこと**
- 過去の訪問記録・打刻・写真・レビューは不変。
- 固定訪問の型・weekly_pattern・NG スタッフ・同住所リンクは不変。
- 他の患者の予定は動かさない（空いたコースの詰め直しは提案機能に任せる）。

## 4. 段階分け
| Phase | 内容 | 規模 |
|---|---|---|
| **0 運用（実施済み 2026-09-09 19:45）** | PO 回答（Q1 取消 OK／Q2 特別訪問週間は残す／Q3 カイポケ送信は保留／Q4 週間パターン停止は事務／Q5 過去は触らない／Q6 スクリプトは私が作成・実行）に従い、非稼働 8 名の未来 planned(auto) **21 件**を `POST /schedule/v2/visit-cancel-week` で取消（source=manual_cancel・reason 付き・週ごとの op_group 4 組・undo 可）。藤原様の ⭐ 配置 2 件と期間は不変。バックアップ `backups/pre-status-cleanup-20260909-1044.sql.gz`。検証: 非稼働×未来 planned = 藤原 2 件のみ・過去の更新 0・監査ログ 20/21（1 件はミドルウェアの取りこぼし・op-log は 21）。報告書 `docs/reports/2026-09-09-patient-status-cleanup-report.html`（A4 縦 5 頁）。**未了**: `apply_week_only` 退行の修正＋週生成の冪等削除拡張（Phase 1 に合流） | 完了 |
| **1 core** | §3-1 サービス＋PATCH/申請フック＋`status_changed_at`（mig 0082）＋`status_cancel` 定数と取込保護 2 箇所＋status-impact API＋確認ダイアログ（変更/復帰）＋op-log undo＋通知 | BE 中・FE 中 |
| **2 入口ガード** | §3-3 の 422 一式・⭐ プール/calendar の join・取込分類・FE ピッカー除外・PFV パネルのバナー | BE 中・FE 小 |
| **3 表示・突合** | §3-4 DTO 拡張＋各ビューのバッジ/非表示＋提案系除外、§3-5 突合カテゴリ＋未送信サマリ | BE 小・FE 中 |
| **将来** | 効果日（入院予定日・退院予定日）の事前指定と自動切替（§6-3）、RPA での週間パターン停止、解約後の型アーカイブ | — |

Phase 1 だけで「今後の変更」は漏れなくなる。Phase 2 は「非稼働患者を誰かが手で入れてしまう」事故防止、Phase 3 は不整合の見える化。

## 5. テスト（追加すべき最低限）
- BE: active→admitted で当日以降 planned が `status_cancel` に・過去/completed/打刻済みは不変・2 名体制ペア同時・特別期間 ended・op-log undo で元 source に戻る／admitted→active で `status_cancel` が soft-delete され型から再生成／各 422 経路／取込 add が `status_cancel` を復活させない／csv_builder が除外／週生成の冪等削除が非稼働の auto 残骸を掃除（既存テストの緑化）。
- FE: 確認ダイアログが保存前に出る・閉じたら PATCH が飛ばない（教訓「閉じたら API が飛ばない」を固定）／⭐ プールに非稼働が出ない／バッジ表示／`normalizePatientStatus` と `STATUS_LABEL` の単体。

## 6. PO への質問（推奨回答つき）

### 6-A. Phase 0（今ある残骸の掃除・すぐ実施）に向けて
| # | 質問 | 選択肢 | 推奨 |
|---|---|---|---|
| Q1 | 非稼働 9 名に残っている未来の予定 23 件（W38〜W42）は、らく助側で全部「取消」にしてよいですか？ | ①全部取消 ②患者ごとに相談 ③今は触らない | **①** 全部取消。今週だけ取消と同じ扱いで undo 可能 |
| Q2 | 藤原様（入院中）の特別訪問週間（9/3〜12/2・○ プール 25 枚・配置済み 5 枚）は終了してよいですか？ | ①終了 ②退院まで残す | **①** 終了。退院後に新しい期間を作る方が事故が少ない |
| Q3 | 取消した分は次の突合でカイポケ「削除」差分になります。9〜10 月分をカイポケへ送ってよいですか？ | ①送る ②らく助だけ直す | **①** 送る。送らないと現場のカイポケ予定と食い違う |
| Q4 | 小川様・朝倉様・清水様・藤田様・藤原様・石塚様・瀧本様・斎藤様・小湊様のカイポケ週間パターンは、事務側で止めていただけますか？（止めないと翌月の月間展開で復活します） | ①事務で止める ②らく助の突合で毎月消す | **①** 事務で止める。②は毎月の手間と事故の元 |
| Q5 | 開始前（pending）の 2 名に残る古い予定（福島千春様 1 件・中尾様 2 件）も一緒に掃除してよいですか？ | ①一緒に掃除 ②残す | **①** 一緒に掃除 |
| Q6 | 掃除の手段は、画面の「今週だけ取消」を週ごとに手で押す方式と、管理者が一括スクリプトで行う方式のどちらがよいですか？ | ①一括スクリプト（監査ログ付き） ②画面で手作業 | **①** 一括。23 件を 5 週にまたがって手で押すのは時間と押し忘れのリスクが大きい |

### 6-B. 仕組み（Phase 1 以降）の設計判断
| # | 質問 | 選択肢 | 推奨 |
|---|---|---|---|
| Q7 | 非稼働にしたとき、未来の予定は「取消（記録は残る・画面には出ない）」と「削除（行ごと消す）」のどちらにしますか？ | ①取消して非表示 ②削除 | **①** 取消。カイポケ削除差分・取込での復活防止・undo が既存の仕組みでそのまま効く |
| Q8 | ステータスを変えた当日の、まだ打刻していない予定も取り消しますか？ | ①当日から（既定）、ダイアログで「明日から」も選べる ②常に明日から | **①** 当日から。入院は当日に分かることが多い |
| Q9 | 「9/15 から入院」のように未来の日付を先に予約する機能は今回必要ですか？ | ①今回は不要（入院日に変える運用） ②必要 | **①** 不要。必要になったら次の段階で追加できる作りにしておく |
| Q10 | 稼働中に戻したとき、予定は確認ダイアログを経て型から作り直す、でよいですか？（自動で黙って作らない） | ①確認してから作る ②自動で作る | **①** 確認。件数と対象週を見てから入れたい場面が多い |
| Q11 | 復帰時、以前の特別訪問週間は自動では再開せず、必要なら新しく作る、でよいですか？ | ①新しく作る ②自動再開 | **①** 新しく作る。退院直後の増回は期間も回数も前と違う |
| Q12 | 取消の対象は、自動生成の予定だけでなく、＋訪問や ⭐ で手で入れた予定、カイポケから取り込んだ予定も含めてよいですか？ | ①全部 ②自動生成だけ | **①** 全部。残すと「入院中なのに予定がある」状態が再発する |
| Q13 | 解約済みの患者様の固定訪問の型は残しますか？ | ①残す（現状どおり） ②解約から一定期間後に削除 | **①** 残す。再契約時に使える。削除は将来の整理機能で |
| Q14 | 非稼働にした患者様に未処理の申請（変更申請など）が残っていたら、自動で却下してよいですか？ | ①自動却下して通知 ②警告だけ出す | **①** 自動却下。承認後に予定が作られる事故を防ぐ |
| Q15 | 非稼働の予定が万一残っていた場合、画面では「入院中」バッジ付きで薄く表示（点検用に見える）と、完全に非表示のどちらがよいですか？ | ①バッジ付きで見える ②完全非表示 | **①** 見える。不整合に気づけないと 8/31 の再発になる。取消済みは非表示 |
| Q16 | 誰かが非稼働の患者様を手で予定に入れようとしたとき、「入院中のため入れられません」と止めてよいですか？（例外として入れたい場面はありますか） | ①止める ②警告だけで入れられる | **①** 止める。例外は先にステータスを稼働中へ戻す |

### 6-C. 確定事項（2026-09-09 夜・PO 回答）
| # | 決定 | 設計への反映 |
|---|---|---|
| Q7 消し方 | **取消して非表示**（status=cancelled・source=status_cancel） | §3-2 のとおり |
| Q8 当日 | **既定「当日から」・「明日から」も選べる** | §3-6 ダイアログのラジオ |
| Q9 効果日 | **今回は不要**。`status_changed_at` だけ記録し将来追加できる作り | §3-1 手順 5 |
| Q10 復帰 | **確認ダイアログを経て型から再生成**。⭐ は自動再開せず新規作成へ誘導 | §3-1 復帰 |
| Q12 対象範囲 | **固定ルールにしない。特別訪問週間はタイミング次第なので、必ず確認して管理者の判断に従う。** 例: 退院後に医師から「特別訪問週間で頻度を上げて様子を見る」／特別訪問週間中の患者が急変して入院 | ① 非稼働化ダイアログに「特別訪問週間（期間・○ N 枚・配置済み M 件）をどうしますか」の選択（終了する／残す）を必ず出す。型由来・＋訪問由来の予定は取消、⭐ 配置分は選択に従う ② 非稼働患者に特別訪問週間を登録するときは「この患者様は現在 入院中 です」と確認し、承諾で登録可 |
| Q16 入口ガード | **基本は止める（案内文）。それでも進める場合は「ステータスを稼働中に変更しますか？」を確認し、承諾でシステムがステータスを稼働中に変えてから進める。** 特別訪問週間も同様に確認して許可可能。入院中のまま進む道は作らない | §3-3 の 422 に `can_override: true` を持たせ、FE は案内→「稼働中にして続ける」→ PATCH status=active（復帰フロー §3-1 が走る）→ 元の操作を再実行。⭐ の期間作成・配置も同じ導線 |
| Q15 残骸表示 | **バッジ付きで薄く表示**。取消済み（status_cancel）は非表示 | §3-4 |
| Q13/Q14 | **解約済みの型は残す／未処理の申請は自動却下して通知** | §3-1 手順 4 |

## 7. 実装計画（Phase 1・着手即可の粒度）— 2026-09-09 夜作成

> §3 の記述と食い違う点は **本章が正**（例: op-log は独自 op_kind ではなく既存 `cancel_visit` を再利用・特別訪問週間の既定は「残す」・pending も非稼働）。

> **実装済み（2026-09-10 未明・コミット済み・未デプロイ）。実装時の確定事項（レビュー是正で決めたもの・本節の記述より優先）:**
> - **op-log の undo/redo はステータス連動の取消には効かせない**（`cancel_source=status_cancel` の forward/inverse とも `OpLogConflictError`「稼働中に戻してください」）。undo で source を戻すと週生成の掃除で消える／復帰の再生成と二重になるため、戻し方は「ステータスを稼働中に戻す」に一本化。行は監査のため記録する。「今週だけ取消」画面の「取消をやめる」も `status_cancel` は 422。
> - `GET /status-impact` は `special_period_action=keep|end` を受け、`visits.total` はその選択込みの件数（FE は足し算しない・ラジオ切替で再取得）。
> - `WeekCount` のキーは `count`（本節の `expected` は誤記）。
> - 復帰の再生成は from_date の週 + 7 週（最大 8 週）。from_date の週は reset 前に `visit_staff_assignments` をスナップショットし、from_date より前の既存訪問と担当を復元する。
> - 通知は `reference_id=None` で毎回 insert（部分ユニーク索引の衝突回避）。申請の自動却下通知は変更後のステータス名を使う。
> - **Phase 3 へ送ったもの**: 現場ボード（`/schedule/v2/board`）とモニターの DTO に `source` が無く `status_cancel` を隠せない（BE で `source` を載せる）。週タイムラインは `manual_cancel` の打ち消し線自体が未実装。週生成ダイアログの「既に N 件」は `status_cancel` を含む（安全側）。

**Phase 1 の範囲**: ステータス変更の連動処理（非稼働化＝取消／復帰＝型から再生成）＋確認ダイアログ（影響件数・当日/明日・特別訪問週間の選択）＋`status_changed_at`＋op-log undo＋通知＋申請自動却下。**Phase 2**（入口ガード 422 と「稼働中にして続ける」上書き導線・取込除外・FE ピッカー）は契約だけ先に固定し、Phase 1 完了後に着手。**Phase 3** は表示 DTO と突合カテゴリ。

### 7-1. 用語・定数
- `VISIT_SOURCE_STATUS_CANCEL = "status_cancel"`（`app/models/visit.py`・13 文字・`manual_cancel` と同じ保護規則）。
- 非稼働 = `Patient.status != "active"`（pending も含む・Q「開始前」回答）。判定ヘルパ `is_schedulable_status(s) -> bool` を `app/services/patient_status_sync.py` に置く（列挙しない）。
- `from_date` = 取消/再生成の起点日（JST）。既定=今日。「明日から」= 今日+1。

### 7-2. DB（migration 0082）
- `patients.status_changed_at TIMESTAMPTZ NULL`／`patients.status_changed_by UUID NULL`（FK users・SET NULL）。既存行は NULL のまま（不明）。
- CHECK 制約は入れない（既存の `inactive` 残骸リスク回避。Literal で API 側は守る）。
- ファイル名 `0082_patient_status_changed_at.py`。down は列 drop。

### 7-3. BE 契約
**(a) `GET /api/v1/patients/{id}/status-impact?to=<status>&from_date=YYYY-MM-DD`**（admin・read-only）
```json
{
  "patient_id": "...", "current_status": "active", "to_status": "admitted", "from_date": "2026-09-09",
  "direction": "deactivate",                       // deactivate | reactivate | none
  "visits": { "total": 9, "by_week": [{"iso_year":2026,"iso_week":38,"count":3,"label":"9/14週"}],
              "by_source": {"auto": 7, "manual_week": 2, "inbound": 0}, "pair_groups": 0,
              "excluded": {"checked_in": 0, "in_progress": 0, "week_pinned": 0} },
  "special_period": { "id": "...", "start_date": "...", "end_date": "...", "pool_marks": 25, "placed_marks": 5, "placed_future_visits": 2 } | null,
  "fixed_visit_rows": 3,
  "pending_requests": 1,
  "kaipoke_weeks": 3,                              // 取消/追加が送信対象になる週数（生成済み週 ∩ 対象）
  "regenerate": { "weeks": [{"iso_year":2026,"iso_week":39,"expected":2}], "total": 8 } | null   // reactivate のみ
}
```
- `direction`: 現 status と `to` がともに active か非 active なら `none`（ダイアログ不要）。
- 件数の定義は実行と同じ関数（`_select_deactivation_targets` / `_plan_reactivation`）を dry-run で呼ぶ。**表示と実行のズレを構造的に無くす。**

**(b) `POST /api/v1/patients/{id}/status-change`**（admin）
```json
// request
{ "status": "admitted", "from_date": "2026-09-09",
  "special_period_action": "keep" | "end",         // deactivate 時のみ有効。既定 keep（Q⭐既定=残す）
  "regenerate": true,                                // reactivate 時のみ。false なら status だけ変える
  "note": "急変のため入院" }
// response 200
{ "patient": {...PatientRead}, "direction": "deactivate",
  "cancelled_visit_ids": ["..."], "cancelled_count": 9, "special_period": {"action":"end","id":"..."} | null,
  "rejected_requests": 1, "op_groups": [{"iso_year":2026,"iso_week":38,"op_group_id":"..."}],
  "regenerated": {"created": 8, "weeks": [...]} | null,
  "notification_id": "..." }
```
- 422: `from_date < today`（過去は触らない・Q5）／`status` が Literal 外／同 status（`direction=none` は 200 で no-op を返す）。
- 409: 訪問の並行更新（IntegrityError）。
- **PATCH /patients/{id} に status が含まれ、かつ変化する場合**: 上記サービスを **既定値**（from_date=今日・special=keep・regenerate=true）で内部呼び出し（API 直叩き・Excel 取込・申請適用の安全網）。FE は必ず (b) を使う（ダイアログ経由）。
- 申請適用 `_apply_patient_status_update` も同サービス経由（既定値）。

**(c) サービス `app/services/patient_status_sync.py`**
```python
async def compute_impact(db, patient, *, to_status, from_date) -> StatusImpact
async def apply_deactivation(db, patient, *, to_status, from_date, special_period_action, actor, note) -> DeactivationResult
async def apply_reactivation(db, patient, *, from_date, regenerate, actor, note) -> ReactivationResult
async def _select_deactivation_targets(db, patient_id, from_date) -> list[Visit]   # planned・deleted_at NULL・date>=from_date・打刻なし・week_pinned は除外して excluded に計上・visit_group_id はグループ全員
async def _plan_reactivation(db, patient, from_date) -> list[(iso_year, iso_week)]  # 生成済み週 = その週に office の visits が 1 件でもある週（from_date 以降）
```
- 取消の実体は `_set_visits_cancelled` 相当（status=cancelled・source=status_cancel）。**元 source は op-log forward_payload.sources に控える**（`cancel_visit` と同形）。
- op-log: 週ごとに `op_group_id` を 1 つ・`op_kind="cancel_visit"`（既存 executor を再利用＝undo がそのまま効く）・label「◯◯様 入院中により取消（ステータス連動）」・`strict=True`。
- 特別訪問期間 `end`: `status='ended'`・`end_date=max(start_date, from_date-1)`・pool の ○ を `cancelled`。placed の ● は訪問側が取消される（mark は placed のまま＝自己回復ロジック対象外なので `cancelled` に倒す）。`keep`: 一切触らない（○ はプールに残る＝Phase 2 でプール表示時に「入院中」バッジ）。
- 復帰: `status_cancel` かつ date>=from_date の visits を soft-delete → 週ごとに `reset_visits_to_fixed(db, iso_year, iso_week, office_ids=[primary_office_id], mode="auto", patient_id=patient.id)` を呼ぶ。戻り値の created 件数を集計。**reset は削除側が status 不問なので、同患者の `status_cancel` を先に消しておかなくても消えるが、明示的に消して意図を固定する。**
- 申請: `pending_requests` で `target_patient_id=patient.id AND status='pending'` を `rejected`（reason="患者が非稼働のため自動却下"）＋申請者へ通知（`leave_notify` の作法）。
- 通知: admin 全員へ `Notification(type="patient_status_sync", title="◯◯様を入院中にしました", body="予定 9 件を取消（9/14 週〜10/12 週）。特別訪問週間は残しています。カイポケの週間パターンを停止してください。", reference_type="patient", reference_id=patient.id)`。
- `status_changed_at/by` を更新。監査は既存ミドルウェア＋op-log。
- session は autoflush=False → 取消と期間終了の間で `await db.flush()`。

**(d) ガード（Phase 2 契約・先に固定）**
- `ensure_patient_schedulable(db, patient_id) -> Patient`：非稼働なら
  `HTTPException(422, detail={"code":"patient_not_active","patient_id":..,"status":"admitted","status_label":"入院中","can_override":true,"message":"入院中のため予定に入れられません"})`。
- 適用先: `place-and-fix`・`POST /visits`・`visit-move-week-only`・`fix-or-pattern`・`update-fixed-time-master`・`apply-swap`・`apply-individual`・`pool-bulk-apply`（対象に混在→その患者を除外して `warnings[]`）・`propose-slots`（existing_patient_id）・`POST /special-visit-periods`・`place`（全モード）・`restore`・`PUT fixed-visits?change_scope=pattern_and_week`（型だけは許可）。
- `GET /special-visit-marks/pool` と calendar は Phase 2 では **除外せず** `patient_status` を載せる（Q⭐「残す」を尊重し、バッジ表示にする）。
- 取込（diff/replace/smart）: 非稼働患者の add はスキップし `skipped_inactive[]` としてプレビューに出す。`status_cancel` は `manual_cancel` と同じ扱い（`inbound.py:906`・`replace_inbound.py:200` に `or source == STATUS_CANCEL`）。
- 週生成の冪等削除（`layer1_expander.py:680`）と `apply_week_only`（`auto_allocator_v2.py:9182`）の削除対象に「拠点内の非稼働患者の source=auto」を union（既存 failing テストが緑になる）。

### 7-4. FE 契約
- `lib/schemas/patient.ts`: `isSchedulableStatus(s)`、`STATUS_LABEL` は既存を使う（v2 側の重複 enum は import に寄せる）。
- `lib/queries/patients.ts`: `usePatientStatusImpact(id, to, fromDate, enabled)`（GET a）／`useChangePatientStatus(id)`（POST b・onSuccess で `['patients']`・`['visits']`・board・pool・special-pool・cockpit のキーを invalidate。既存の `usePlaceSpecialMark` の invalidate 群を共通関数 `invalidateScheduleAll(qc)` に切り出して両方から使う）。
- **新規** `components/patients/PatientStatusChangeDialog.tsx`
  ```ts
  props: { open; patientId; patientName; fromStatus; toStatus; onCancel(); onDone(result) }
  内部: impact を取得して表示 → deactivate: ラジオ「今日から/明日から」・特別訪問週間がある場合のみ選択「残す(既定)/終了する」（件数付き・必ず目に入る位置）・申請 N 件は自動却下の注記／reactivate: 週別の作成予定件数・チェック「型から予定を作る(既定 ON)」・リンク「特別訪問週間を設定」
  作法: add-visit-anywhere-design §3-5（max-w-5xl 相当は不要・md 幅・14px・h-9）
  閉じたら API は飛ばない（テストで固定）
  ```
- **共通フック** `usePatientStatusGate({ patientId, initial })`：`submit(values)` を包み、`values.status !== initial.status` かつ direction≠none ならダイアログを開き、確定で (b) を呼んでから **status を除いた** PATCH を流す。呼び出し 3 箇所: `app/(app)/patients/[id]/edit/page.tsx` handleSubmit・`components/schedule/v2/PatientEditDialog.tsx` handleSubmit・`components/field/FieldSheets.tsx` の edit 保存（`updateMut`）。
- 患者一覧・詳細に `status_changed_at` を「入院中（9/8〜）」の形で表示（小）。
- Phase 2: `components/schedule/v2/PatientNotActiveGate.tsx`（422 `patient_not_active` を捕まえて「入院中のため予定に入れられません」→「稼働中にして続ける」→ `PatientStatusChangeDialog(to='active')` → 成功後 `retry()`）。既存 422 トースト経路（`extractApiErrorDetail` 系）にフックする。⭐ チケット・プールカードに `patient_status` バッジ。

### 7-5. 並行実装の分担（ファイル所有）
| レーン | 担当ファイル | 触らない |
|---|---|---|
| コーディネータ（先に編集） | `app/models/visit.py`（定数）・`app/models/patient.py`（列）・`alembic/versions/0082_*.py`・`app/schemas/patient_status.py`（新規 pydantic）・`frontend/lib/schemas/patient.ts`（型追加） | — |
| BE-A | `app/services/patient_status_sync.py`（新規）・`app/api/v1/patients.py`（status-impact / status-change / PATCH フック）・`app/services/pending_request_applier.py`（1 関数）・通知 | inbound / expander / allocator |
| BE-B | `app/services/kaipoke/inbound.py`・`replace_inbound.py`（status_cancel）・`layer1_expander.py:680`・`auto_allocator_v2.py:9182`（掃除 union）・`app/services/scheduling/guards.py`（新規 `ensure_patient_schedulable`・Phase 2 で各 API に配線） | patients.py |
| FE-A | `components/patients/PatientStatusChangeDialog.tsx`・`lib/queries/patients.ts`・`lib/hooks/usePatientStatusGate.ts`・3 呼び出し箇所の配線 | schedule/v2 の盤面系 |
| FE-B（Phase 2） | `PatientNotActiveGate.tsx`・⭐/プールのバッジ・ピッカー除外 | patients ページ |
レビューは各レーン完了ごとに別レーンの code-reviewer（Opus）で **BE 契約と突合**（教訓 1）。git 操作はコーディネータのみ・stash 禁止。

### 7-6. テスト（Phase 1 で追加）
BE `tests/test_patient_status_sync.py`
1. active→admitted: from_date 以降の planned が cancelled/status_cancel・過去/completed/in_progress/打刻済み/week_pinned は不変（excluded に計上）・2 名体制はグループごと。
2. special_period_action=keep で期間・○ 不変／end で ended・○ cancelled・placed の未来訪問は取消。
3. op-log が週ごとに 1 グループ・undo で status=planned・source が元値に戻る。
4. pending_requests が rejected・通知が admin 全員と申請者に作られる。
5. admitted→active（regenerate=true）: status_cancel が soft-delete され、型から created>0・from_date より前の週は不変。
6. status-impact の件数 == 実行結果（同一関数の dry-run）。
7. from_date < today → 422／same status → direction none no-op。
8. PATCH /patients で status 変更 → 既定値で連動（安全網）／applier 経由も同様。
9. 取込 add が status_cancel を復活させない（inbound / replace）。
10. 週生成・apply_week_only の掃除 union（既存 `test_apply_week_only_soft_deletes_inactive_patient_visit` が緑）。
FE
1. `PatientStatusChangeDialog.test.tsx`: impact 表示・閉じたら mutate が呼ばれない・特別訪問週間の選択が既定 keep・「明日から」で from_date=+1。
2. `usePatientStatusGate.test.ts`: status 不変なら素通り・変化時はダイアログ→確定で status-change → PATCH（status なし）の順。
3. `patient_status.test.ts`: `isSchedulableStatus`・`normalizePatientStatus`・`STATUS_LABEL`。

### 7-7. 実機確認（本番・admin・テスト患者で）
(a) テスト患者に型を作り週生成 → 入院中へ → ダイアログ件数 = 盤面の件数 → 確定 → 盤面/タイムライン/職員スケジュールから消える → 「戻る」で戻る (b) 特別訪問週間ありのテスト患者で keep/end 両方 (c) 稼働中へ戻す → 週別件数 → 予定が戻る (d) 現場シート（/m）からの状態変更でもダイアログが出る (e) 突合で削除差分が出る（送信はしない） (f) 通知が届く。

### 7-8. 着手順（次セッションの最初の 5 手）
1. 本書 §6-C と §7 を読む → コーディネータが 8-5 の共有ファイル（定数・列・mig 0082・pydantic・FE 型）を先に編集しコミット。
2. BE-A と BE-B、FE-A を並行投入（Opus executor・ファイル所有を明記）。
3. 各レーン完了 → code-reviewer で契約突合 → 是正。
4. `pnpm tsc --noEmit`・`pnpm vitest run`・`python -m pytest tests/test_patient_status_sync.py tests/test_inactive_patient_visit_cleanup.py -q`（既存失敗 33 件は既知）。
5. デプロイは PO 確認後（バックアップ→pull→build --no-cache（mig あり）→recreate→healthz）。実機確認 8-7。

## 8. 参照
- 前身: `backlog-2026-08-31-patient-status-leak.md`／事故: `incident-2026-08-31-kaipoke-expand-wrong-month.md`
- 主要コード: `app/api/v1/patients.py:266-285`（PATCH）・`app/api/v1/staff.py:200-210`（スタッフ前例）・`app/services/scheduling/layer1_expander.py:658-683`・`auto_allocator_v2.py:1471-1512, 9182-9192, 9660-9750`・`app/api/v1/special_visits.py:925-936, 1194`・`app/api/v1/schedule_v2.py:6540-6620`（今週だけ取消）・`app/services/kaipoke/{inbound.py:906, replace_inbound.py:200, csv_builder.py:303-317}`・`app/services/checkin/judge.py:108`
- FE: `lib/schemas/patient.ts:45-84, 228-243`・`app/(app)/patients/_components/PatientForm.tsx:290`・`lib/queries/patients.ts:284-309`・`components/schedule/v2/CourseDayTablePanel.tsx:1424, 1441, 1842, 5121`・`lib/schemas/specialVisitWeek.ts:172-182`
- 本番確認 SQL（読み取り専用）: 非稼働×未来 planned・特別期間・PFV・監査ログ（本書 §1-4 の元）。手口は `session-2026-09-08-HANDOFF.md` §5。
