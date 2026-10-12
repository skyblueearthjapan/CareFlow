# 赤ピン（完全固定）撤去 棚卸し — backend/app/services/** ・ core ・ utils

調査日 2026-10-12 / 読むだけ（リポジトリ・サーバーは無変更）。
対象: `PatientFixedVisit.movability`（unknown / time_flexible / day_flexible / locked）と非推奨ミラー `PatientFixedVisit.is_pinned`。
青ピン（`visits.week_pinned` / source=`manual_week`）は対象外。ただし赤と青が絡む箇所は §4 で挙げる。

---

## 1. 要約（件数）

| 区分 | 件数 |
|---|---|
| 直接ヒット（`is_pinned` / `movability` / `time_flexible` / `day_flexible` / `完全固定` / `_check_pinned` / `CODE_PINNED` / `is_movability_locked`）の行数 | **146 行・7 ファイル** |
| └ `scheduling/auto_allocator_v2.py` | 76 |
| └ `scheduling/improvement_engine.py` | 27 |
| └ `scheduling/scope_optimizer.py` | 18 |
| └ `scheduling/pfv_validator.py` | 12 |
| └ `scheduling/unblock_search.py` | 10 |
| └ `patient_status_sync.py` | 2 |
| └ `scheduling/pool_bulk_inserter.py` | 1 |
| 上の 146 行を「意味のかたまり」にまとめた関連箇所 | **44 件**（§2 の表） |
| `core/`・`utils/`・`services/kaipoke/` の赤ピン関連 | **0 件**（`blocked` / 青ピン `week_pinned` のみ。§3） |
| 赤ピンの値を**明示的に書き込む** services のコード | **0 件**（書き込みは api/ 側。services は PFV を作るとき既定値 `unknown`/`False` に任せている。§2-G） |
| 呼ばれていないコード（デッドコード） | `pfv_validator._check_pinned` / `_identity_of_item` / `_identity_of_row` / `CODE_PINNED` / `CODE_MOVABILITY_CORRECTED`（テストからしか参照されない） |

**要点**

1. 赤ピンを読んで振る舞いを変えるのは 5 つのエンジン。
   - 提案系 3 つ（improvement_engine / scope_optimizer / unblock_search）: その枠を候補から**外す**。
   - 自動割当（auto_allocator_v2）: その枠の時刻とコースを**凍結**する（`_apply_corrections_to_visits` で元の値に戻すフェンスと、G-32 のコース固定）。
   - 適用系 2 つ（apply_week_only / apply_individual_proposal）: **422 で拒否**する。
2. 「希望の範囲内なら可」（P4-A 層 2: `_slot_within_preference`）は提案系 3 エンジンに**すでに入っている**。ただし次の 2 点が残る。
   - 希望の範囲外のときに `movability` へフォールバックする（層 3）。
   - 自動割当側は希望の範囲を「動かさない」根拠として使っていない。`time_type='固定'` は補正で後ろへずらさないだけで、凍結はしない。
3. 赤ピンを外したあと、「動かせない患者」を止める仕組みは **`time_type='固定'`（幅ゼロの希望）しか残らない**。止まり方はエンジンで違い、そろっていない（§4-1, §4-2）。

---

## 2. 関連箇所の表

凡例 — **削除後の扱い案**: 削除 / 置換（希望範囲チェックへ）/ 現状維持 / 名前変更。**要判断**: ● = PO か設計の判断が必要、○ = 実装者の判断で足りる。

### A. `scheduling/auto_allocator_v2.py`（自動割当・週生成・適用）

| # | ファイル:行 | 役割（赤ピンで何をしているか） | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| A1 | auto_allocator_v2.py:552-566 | `V2Visit.is_pinned` / `V2Visit.is_movability_locked` の 2 フィールド。凍結フェンス・コース固定・UI の 📌 表示の入力になる | 2 フィールドが消え、この下の全経路の凍結が効かなくなる | 削除（`is_movability_locked` は無条件で削除。`is_pinned` は schemas/v2/auto_schedule_v2.py の 3 ヒットと FE の 📌 も合わせて削除） | ○ |
| A2 | :579-589 `_pfv_movability_locked()` | `movability=='locked'` を判定する唯一の関数 | ― | 削除（tests/test_movability_lock_fence.py も削除が必要） | ○ |
| A3 | :2205-2227 `build_visits_for_pool` | 患者ごとに `pinned_pfv_by_wd` と `frozen_pfv_by_wd`（ピン ∪ locked）を作る | 凍結する曜日の一覧がなくなる | 削除 | ○ |
| A4 | :2295-2342（同関数・固定枠を元に作る分岐） | 曜日ごとに `is_pinned_eff` / `is_locked_eff` を V2Visit に渡す | この分岐の visit はもともと `time_type='固定'` なので、補正で後ろへはずらさない。ただしフェンスがなくなり、Fix E（同時刻衝突の自動シフト）と不足 5 分以上での未割当は効くようになる | 削除（引数を外すだけ） | ●（§4-2） |
| A5 | :2347-2470（同関数・weekly_pattern 分岐の凍結版） | 凍結 PFV がある曜日は weekly_pattern を飛ばし、**PFV の時刻・コース（course_template_id）・拠点**で 1 件だけ作る（G-30.1 / G-31 / G-33） | 全面最適化では PFV の時刻ではなく**希望（weekly_pattern）の時刻**で作られ、コースも PFV から引き継がなくなる | 削除（新ルール「希望の範囲で動かす」と合う）。ただしコースを引き継ぐ部分（G-31）を残すかは判断が必要 | ● |
| A6 | :2519 `is_pinned=False` | weekly_pattern 由来の visit にフラグを付けない | ― | 引数を削除 | ○ |
| A7 | :2530-2720 `build_visits_for_pool_v2`（G21 経路） | ピン付き PFV を `time_type='固定'`・`source_kind='fixed'` で作る経路と、Invariant G21-A（同じ曜日の weekly_pattern を飛ばして warning） | ピン経路が消え、全部 weekly_pattern 経路になる。G21-A の warning も出なくなる | ピン経路と G21-A を削除。関数ごと legacy の `build_visits_for_pool` にまとめる手もある | ●（§4-5） |
| A8 | :4761-4820 `_apply_corrections_to_visits` 凍結フェンス | `is_pinned or is_movability_locked` の visit の開始・終了・コースを控え、補正後に元へ戻し、未割当からも外す | **自動割当で「動かさない」を保証する仕組みがなくなる**。`time_type='固定'` の visit も Fix E で動き得るし、移動時間の不足 5 分以上で未割当になり得る | 削除、または「希望の幅ゼロ（`time_type=='固定'` かつ PFV 由来）」をキーにしたフェンスに置き換える | ●（§4-2） |
| A9 | :5216-5240 G-98 `redirect_to_prev` | `cur.is_pinned or source_kind=='fixed'` なら、不足の原因を直前の pool 提案（`not prev.is_pinned`）に付け替える | `source_kind=='fixed'` 側の判定は残るので影響は小さい | `cur.is_pinned` を `cur.time_type=='固定'` などに置き換え、`prev.is_pinned` の条件は削除 | ○ |
| A10 | :6217-6224 `_load_before_visits_from_pfv` | 変更前（Before）の visit にピンと locked のフラグを渡す | Before が凍結されなくなる（差分追加でもピン印が出ない） | 引数を削除 | ○ |
| A11 | :6251-6520 `_load_before_visits_v2` 経路 2 と 3 | 「ピン付き PFV → ピンなし PFV」の優先順で重複を除く。`_make_v2_visit_from_pfv`（:6344-6377）でフラグを渡す | キーが (患者, 曜日, slot) で同じなので、2 経路を 1 つにまとめても結果は同じ | 経路 2 と 3 を 1 つにまとめ、フラグを削除 | ○ |
| A12 | :7800-7860 `run_v2_pipeline` の `pinned_pfv_by_patient_for_pool` 事前読み込み | A5 の凍結版で作るため、PFV と course_template（コース名・拠点）を先に読み込む | A5 を消すと使われなくなる | 削除（`fixed_by_patient` をほかで使っていないことは確認済み。use_fixed_as_source=False の経路） | ○ |
| A13 | :7925-8010（G21 再生成と G-92 `pool_origin` の対応付け） | ピン PFV（`source_kind='fixed'`）を `pool_origin='fixed'` に対応付け、「固定を優先し、だめなら希望」の候補にする | G21 の拠点では `fixed` 由来の候補が 0 件になり、**G-92 の固定優先フォールバックが働かなくなる** | A7 と一緒に作り直す（G21 経路でも PFV を固定候補として出す） | ●（§4-5） |
| A14 | :8280-8290 / :8370-8378 / :8400-8413（G-32 Stage 4 のコース固定） | ピン付き visit は未割当にせず、コース記号を書き換えない | ピン付きだった患者も、セットの確定コースへ振り替わる（＝**担当職員が変わり得る**） | 削除。「動かせない」にコースや職員の固定まで含めるなら、別の仕組みに置き換える | ●（§4-2） |
| A15 | :699-715 `PinnedVisitMovedError` ＋ :9082-9250 `apply_week_only` の検証 (a)〜(d) ＋ :9551-9600 上書き（overlay）後の再検証 | ドラッグ＆ドロップの「週のみ適用」で、ピン付き PFV の時刻・時間・拠点・曜日が変わっていれば 422 | 人の D&D がピン起因で拒否されなくなる。仕様 §1.2「人手は縛らない」と合う | 削除（api/schedule_v2 の 422 変換も合わせて削除） | ○ |
| A16 | :9510-9535 `v2_is_pinned_apply` | D&D の予定がピン PFV の時刻と一致すれば凍結 | 補正で後ろへずれ得る。ピンのない visit は今もそうなっている | 削除 | ○ |
| A17 | :10190-10240 `reset_visits_to_fixed` | `mode='auto'` かつピンなしなら weekly_pattern の幅で補正する。ピンありか `legacy` なら `'固定'`。`v2_is_pinned = is_pinned or legacy` | auto モード（patient_status_sync:1165 などで使用）で**全 PFV が希望の幅で動く**。その曜日に希望がない場合は `'時間帯'`・開始=PFV の時刻・終了=None になり、**終業時刻まで後ろへずれ得る** | ピン条件を削除。希望がない曜日の扱いを決める（§4-1） | ● |
| A18 | :10458-10478 `apply_individual_proposal` | 既存の PFV にピンが 1 件でもあれば 422 `pinned_pfv_cannot_be_applied` | 個別提案の採用がピン起因で拒否されなくなる | 削除。提案はエンジン側で希望範囲チェック済みなので置き換え不要（api/FE の文言も合わせて） | ○ |
| A19 | :10682 `is_pinned=False`（検証用アイテム） | 検証カーネルへ渡すアイテムのフィールド | ― | 引数を削除（schemas 側でフィールドを消すとき一緒に） | ○ |
| A20 | :10706 新規 `PatientFixedVisit(...)` | movability と is_pinned を指定しない（既定値任せ） | 影響なし | 現状維持 | ― |
| A21 | :87 / :1612 / :2526-2560 / :4779-4801 / :6267-6272 / :9767-9768 / :10204-10222 / :10259 のコメント・docstring | ピン経路の説明 | ― | コード修正と一緒に書き直す | ○ |

### B. `scheduling/improvement_engine.py`（改善提案）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| B1 | :205-251 `_slot_within_preference`（docstring :218, :230） | 希望の範囲内か判定する（層 2）。読むのは **weekly_pattern だけ**。その曜日のエントリがなければ False（層 3 の movability ルールへ） | この関数が「動かせるか」を決める唯一の根拠になる | 現状維持（新ルールの中心）。special_weekly_pattern に対応するか決める（§4-6） | ● |
| B2 | :446-466 `FilteredSummaryData.pinned` / `.locked`（docstring :452-453） | 除外件数の内訳 | 常に 0 になる | 削除（schemas/v2/improvement_suggestion.py の 15 ヒットと FE も合わせて） | ○ |
| B3 | :691-695 `_swap_candidates_for_pfv` | 入れ替え相手 Y の PFV が ピン / locked なら候補にしない | Y も入れ替え候補になる | 削除 | ○ |
| B4 | :723-737（同関数） | 曜日をまたぐ入れ替えは `day_flexible` か希望内、同じ曜日なら `unknown` かつ希望外で要確認 | movability がなくなると判定を変える必要がある | 置換: 曜日またぎ = 双方が希望内のときだけ。同じ曜日 = 希望外なら要確認（または出さない） | ●（§4-1） |
| B5 | :1003-1013 メインループの除外 | `is_pinned` なら pinned++、`locked` なら locked++ で除外。`day_change_allowed` と `requires_confirmation` を movability から決める | **すべての PFV が候補になる**。止まるのは希望の範囲で絞った分だけ | 除外は削除。`day_change_allowed=False`、`requires_confirmation=True`（＝すべて unknown 扱い）で固定するか、§4-1 の方針に置き換える | ● |
| B6 | :1032（コメント） | 「movability が可否の権威」 | ― | 「希望範囲が権威」に書き直す | ○ |
| B7 | :1105-1116 | 曜日変更で day_flexible でなく希望外なら抑制（day_restricted） | 方針が同じなら変わらない | 置換: 希望外の曜日変更は一律に抑制 | ●（§4-1） |
| B8 | :1193-1195 `requires_patient_confirmation` | 時刻変更 × unknown × 希望外 → 要確認 | 幅ゼロの患者も**「要確認」付きの時刻変更提案として出続ける** | §4-1 で決める（希望外は出さない／要確認で出す） | ● |
| B9 | :1218-1221 | `x_movability` / `x_day_flexible` を入れ替え処理へ渡す | ― | 引数を削除 | ○ |
| B10 | :961 / :691（コメント） | 「可動域 / pin 判定用」 | ― | 書き直す | ○ |

### C. `scheduling/scope_optimizer.py`（範囲最適化）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| C1 | :21-28（docstring D-1 / D-2） | 「pinned / locked は動かさない」「希望外は time_flexible / day_flexible だけ」 | ― | 書き直す | ○ |
| C2 | :183-184 `excluded.pinned` / `.locked` | 会計 | 常に 0 | 削除（schemas/v2/scope_optimization.py の 2 ヒットも） | ○ |
| C3 | :242-251 `_SimPfv.is_pinned` / `.movability` | 模擬 PFV（B3 と互換にするため） | ― | 削除 | ○ |
| C4 | :296-303 `compute_state_token` | 楽観ロックの指紋に `is_pinned` と `movability` を含める | 指紋の値が変わる（デプロイ直後に計算中だったものは 1 回だけ 409） | 2 項目を指紋から外す | ○ |
| C5 | :490 `_enumerate_step_candidates` | ピン / locked なら飛ばす | 全 PFV が手の候補になる | 削除 | ○ |
| C6 | :529-530, :604-617 D-2 の判定 | 希望外のとき、同じ曜日なら `time_flexible`/`day_flexible`、曜日変更なら `day_flexible` だけ許す。unknown は確認対象として除外 | 本番は time_flexible / day_flexible が 0 件なので、**今も実質「希望内だけ」**。movability をなくしても挙動は同じ | 置換: `if not within_pref: 除外`（希望内だけ） | ○（unknown しかない前提を確認） |
| C7 | :680-681 | 入れ替え処理へ movability を渡す | ― | B4 と一緒に | ○ |
| C8 | :700（コメント） | 「両患者とも非 pinned/locked のときのみ」 | 鏡像の重複除去の前提が「両方とも候補になれる」に変わる。ピン条件がなくなるだけなので前提は崩れない | 書き直す | ○ |
| C9 | :1004-1005, :1031-1034 | 模擬 PFV を作る／除外会計（no_current → pinned → locked → two_staff の順） | pinned / locked の会計が消える。two_staff の会計は残る | 削除 | ○ |

### D. `scheduling/unblock_search.py`（詰まり解消）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| D1 | :16, :22, :24, :774（docstring） | P-2「pinned / locked は動かさない」、P-3「希望内か movability が許す範囲」 | ― | 書き直す | ○ |
| D2 | :176-177 `summary.pinned` / `.locked` | 会計 | 常に 0 | 削除（schemas/v2/unblock.py の 2 ヒットも） | ○ |
| D3 | :375-376, :443-452 `_enumerate_retreats` | 退避先が希望外なら movability で許可（unknown は confirmation_blocked） | C6 と同じく、本番では実質「希望内だけ」 | 置換: 希望外は一律 confirmation_blocked | ○ |
| D4 | :517-523 `_blocker_to_move` | ブロッカーの PFV がピン / locked なら pinned++/locked++ で動かさない | ブロッカーも退避の候補になる（希望内に退避先があれば動く） | 削除 | ○ |
| D5 | :557（コメント） | 「確認要 (希望外+movability不明)」 | ― | 書き直す | ○ |

### E. `scheduling/pool_bulk_inserter.py`（プール一括投入）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| E1 | :263-266 `compute_bulk_state_token` | 指紋に `is_pinned` と `movability` を含める | 指紋の値が変わるだけ。**このエンジンは既存の訪問を動かさない**ので、赤ピンによる除外ロジックはもともとない（仕様 §1.2 の「除外」は実質この指紋だけ） | 2 項目を外す | ○ |

### F. `scheduling/pfv_validator.py`（PFV の再検証カーネル）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| F1 | :7（docstring「V2 pinned 保護 → error」） | 撤廃済みの説明が残っている | ― | 書き直す | ○ |
| F2 | :59 `CODE_PINNED`、:65 `CODE_MOVABILITY_CORRECTED` | 撤廃済みの警告コード（FE が寛容に読むために残している） | ― | 削除（tests/test_pfv_validator.py の参照と schemas/v2/patient_fixed_visit.py:162 の説明も） | ○ |
| F3 | :133-190 `_IdentityTuple` / `_identity_of_item` / `_identity_of_row` / `_check_pinned` | **呼ばれていない**（validate からの呼び出しは 2026-08-09 に撤去済み） | なし | 削除 | ○ |
| F4 | :254-255, :404, :409-426（コメント） | 旧 V6 / 旧 V2 の撤廃の経緯 | ― | 短くする | ○ |

### G. PFV を作る・更新する（services の書き込み経路。明示的に書き込む箇所は 0）

| # | ファイル:行 | 役割 | 削除後の挙動の変化 | 扱い案 | 要判断 |
|---|---|---|---|---|---|
| G1 | patient_status_sync.py:1068-1080 `_remove_schedule` | 終了時に消す PFV の中身を audit_logs.before に控える（`is_pinned` と `movability` を含む） | ― | 2 キーを削除（過去の監査ログはそのまま。復元処理がこのキーを読むなら api 側で要確認） | ○ |
| G2 | pending_request_applier.py:645-665 | slot0 を DELETE して INSERT し直す（movability と is_pinned を引き継がない → **今は赤ピンが黙って外れる**） | 撤去後は問題でなくなる | 現状維持 | ― |
| G3 | proposed_visits_pfv.py:212-235 | normal の PFV を全部消して入れ直す（同じく赤ピンが黙って外れる） | 同上 | 現状維持 | ― |
| G4 | patient_excel/importer.py:2597、patient_excel/replace_all.py:1667 | Excel から PFV を作る。データに movability と is_pinned はない（既定値） | 影響なし | 現状維持 | ― |
| G5 | auto_allocator_v2.py:10693-10714（A20） | 個別採用で PFV を UPDATE / INSERT（既存行は movability を保持、新規行は既定値） | 影響なし | 現状維持 | ― |

---

## 3. 無関係と判定した箇所の一覧

すべて確認済み。赤ピンとは関係しない。

- **`core/`（config, deps, rate_limit, security）・`utils/`（db, geo）**: 検索語のヒットなし。
- **`services/kaipoke/`**:
  - `replace_inbound.py` / `inbound.py` の `ReplaceBlockedError`・`blocked_days`・`_blocked_by`（実績ガード）
  - `rpa_capability.py` の `blocked_keys`
  - `plan_actual_compare.py` / `reconcile_report_html.py` の「赤」（表示色）
  - `inbound_snapshot.py:53,310` の `week_pinned`（青ピンの保存と復元）
- **`allocation/engine.py`・`allocation/utils.py`**: `blocked` 区間（休みや午前休）。旧エンジン。
- **`checkin/notify.py`・`checkin/purge.py`・`geocoding/audit.py`** の `"locked"`: advisory lock を取れたかどうか（DB 行ロック）。
- **`scheduling/auto_allocator.py`（v1）**: `blocked` = 受け入れカレンダーで ×。「既存固定枠は動かさず」は PFV 全般の話で、赤ピンではない。
- **`scheduling/propose_slots_service.py`**: `pair_blocked` / `acceptance_blocked`（同じ住所のペアと受け入れ ×）。`time_type in ("固定", …)` は希望の型。
- **`scheduling/proposal_solver.py`**: `time_type=="固定"`（希望の型）。新ルールの「幅ゼロの希望」の判定元（`_time_type_allows`）なので、むしろ**残すべき部品**。
- **`scheduling/layer3_assignment.py`**: 「固定割当」= W16 の事前固定（manager → M コース、都賀 A）。`*_blocked_no_candidate` = 性別や NG で候補なし。
- **`scheduling/layer1_expander.py`**: `week_pinned`（青ピン）のスキップと「固定枠」（PFV 全般）、`time_type or "固定"`。
- **`scheduling/copy_week.py:70,239,284`**、**`course_staff_mirror.py:31,76,105`**、**`substitute_candidates.py:641`**: `week_pinned`（青ピン）。
- **`op_log_service.py:102-105,723`**、**`patient_status_sync.py:278-339,727,1030`**: 青ピンで取消を止める処理。
- **`auto_allocator_v2.py:9278-9279, 9850-9851, 9907-9911`**: `week_pinned`（青ピン）を削除対象から外し、再生成もスキップ。
- **`auto_allocator_v2.py:337-397, 3071-3345, 5816-5822, 6633`**: `blocked`（pair_mode=blocked / acceptance_blocked）。
- **`scheduling/guards.py`**: `_blocked_message`（予定に入れられない患者のガード）。
- **`pending_request_applier.py:132-847` の「赤警告」**: 満員や時刻重複の red level。
- **`schedule_fix_service.py:168,183`**: `time_type="固定"`。
- **`patient_excel/*`、`staff_excel/*`、`staff_event_defaults.py`、`manager_course_sync.py`、`voice/*`、`diff/engine.py`、`checkin/monitor.py`、`checkin/history_report_html.py`、`board_service.py`、`acceptance_matrix_service.py`**: 「固定セル」「固定枠」「グルーピング」「赤系の色」など、文字列が一致しただけ。
- **`pin`（単語）**: services では improvement_engine:691/961 のコメントだけ（B10 に計上）。

---

## 4. 判断が必要な論点

### 4-1. 「その曜日に希望がない → movability（unknown = 要確認）」のフォールバックをどうするか

現状は次のとおり。`_slot_within_preference` は weekly_pattern だけを見て、移動先の曜日にエントリがなければ False を返す。

- **improvement_engine**: 希望外のとき
  - 時刻変更は unknown なら**要確認付きで提案**する（B8）。
  - 曜日変更は day_flexible がなければ抑制する（B7）。
  - 同じ曜日の入れ替えは unknown なら要確認で提案する（B4）。
- **scope_optimizer / unblock_search**: 希望外のとき、unknown なら**提案しない**（C6 / D3）。
- 本番は time_flexible / day_flexible が 0 件（仕様 §1.1）。このため **movability 列そのものを捨てても、locked 以外の行の挙動は今と同じ**になる。

案:

- **(a) 推奨**: movability を丸ごと廃止し、希望外は「常に unknown」とみなす。scope / unblock は今と同じ挙動になる。improvement_engine だけ、希望外の時刻変更が「要確認付き」で出続ける。
- **(b)**: 新ルール「動かすのは希望の範囲内だけ」を厳密に守り、improvement_engine でも希望外は出さない（B4 / B5 / B8 で `within_pref` が False なら continue）。幅ゼロの患者に提案が一切出なくなる。そのかわり、weekly_pattern が未登録の患者にも改善提案が出なくなる。
- **(c)**: (a) に加え、weekly_pattern が空（未登録）の患者だけ従来どおり「要確認」で出す。

幅ゼロの表し方:

- `time_type='固定'`（`_time_type_allows` は開始時刻が完全一致のときだけ True）
- `'時間帯'` で preferred_start == preferred_end

どちらでも表せる。ただし (a) では、幅ゼロの患者も**要確認付きの時刻変更提案には出てくる**。「動かせない」をどこまで厳密にするかで (a) と (b) が分かれる。

曜日の意味の確認も必要:

- 赤ピンの患者が月曜だけの固定で、weekly_pattern に水曜のエントリもあると、新ルールでは**月曜から水曜へ動かせてしまう**。
- 曜日も動かせない患者は、weekly_pattern にその曜日のエントリだけを持たせる必要がある。
- 移行時に、locked の行と weekly_pattern の曜日やエントリが合っているかを本番データで調べるべき（api/alembic 担当と連携）。

### 4-2. 自動割当の凍結がなくなったあと、本当に動かせない患者を何が止めるか

赤ピンのフェンス（A8）が保証していたのは次の 3 点。

1. 開始・終了時刻を元に戻す
2. コース記号を元に戻す
3. 移動時間の不足による未割当を取り消す

さらに G-32（A14）が、Stage 4 でコースが振り替わるのを止めていた。

撤去後に残るのは `time_type='固定'` の扱いだけで、次の点が違う。

- 補正で後ろへはずらさない（○）。
- ただし Fix E `_auto_shift_same_time_conflicts`（docstring に「固定時刻も例外的に動く」）で**動き得る**。
- 移動時間の不足が 5 分以上なら**未割当になる**（G-98 で直前の提案に付け替える救済はある）。
- **コース（担当職員）は Stage 4 で変わり得る**。

選択肢:

- **(a)**: フェンスを「`time_type=='固定'` かつ PFV 由来（`source_kind=='fixed'`）」をキーにして残す。ただし差分追加で固定枠から作る分岐（A4）はすべて `'固定'` なので、**PFV 由来の全 visit が凍結**になる。今の legacy 相当で、範囲が広すぎるおそれがある。
- **(b)**: フェンスは廃止し、`'固定'` の扱い（Fix E の対象外にするかなど）は補正側で決める。
- **(c)**: 幅ゼロの希望を持つ患者だけフラグ（例: `V2Visit.zero_width_pref`）を立ててフェンスに使う。

**コースや職員の固定（G-32）を「動かせない」に含めるかは PO に確認が必要**（新ルールは時刻の範囲しか語っていない）。

あわせて決めること:

- 全面最適化の weekly_pattern 分岐（A5）は、凍結 PFV の時刻・コース・拠点で作るのをやめると、**希望の時刻で作られる**ことになる。
- `reset_visits_to_fixed(mode='auto')`（A17）で希望がない曜日は、`'時間帯'`・終了=None（終業時刻まで）になり、PFV の時刻から後ろへ自由にずれる。幅ゼロとみなす（`'固定'` にする）のが新ルールに近い。

### 4-3. 2 名体制の枠

- scope_optimizer / unblock_search / pool_bulk_inserter は `requires_multiple_staff` で除外している（赤ピンとは独立。撤去後も残る）。
- **improvement_engine は 2 名体制を除外していない**。PFV の slot_index=0 だけを動かす提案になり、片方だけ動く。今は 2 名体制の患者が赤ピンで守られていた可能性があり、撤去後にこの穴が出る。B5 に `requires_multiple_staff` の除外を足すべき。
- 入れ替え相手 Y（`_swap_candidates_for_pfv`。scope_optimizer も同じ関数を使う）も 2 名体制を確認していない。scope 側は X だけ除外しているので、**2 名体制の患者が入れ替え相手 Y として動く**可能性がある（もともとある穴）。撤去と同時に Y 側にも除外を足すことを勧める。
- 自動割当の凍結は slot_index=0 だけが対象だった（slot1 は今も凍結されていない）。apply_individual_proposal も slot0 だけを UPDATE する。どちらも赤ピン撤去で悪くはならない。

### 4-4. 赤と青が絡む箇所

- **提案系 3 エンジン（improvement / scope / unblock）は `visits.week_pinned` を一切読んでいない**（`load_week_course_buckets` でも見ていない）。
  - エンジン側で既存の訪問を守っていたのは赤ピンだけ。
  - 仕様 §5.1 の「今の予定を変えずに提案してほしい → 青を刺す」は、エンジン側では効いていない。適用側（api）で 422 にしている可能性はあるので、api 担当の結果と照合すること。
  - 赤を外すと、青ピンの訪問にも移動提案が出る。青を除外する処理（バケットを読むときに `week_pinned` の visit を動かせないものとして扱う）を、撤去と同時に入れるか判断が必要。
- `V2Visit.is_pinned` と UI の 📌（schemas/v2/auto_schedule_v2.py）は、名前の上で「ピン」を共有しているが、中身は赤だけ。青を自動割当の `V2Visit` に流す経路はない。
- G-98（A9）の「動かせない」判定 `cur.is_pinned or source_kind=='fixed'` は赤だけが対象。
- 週生成で消さない判定（:9907-9911 の青 / manual_week / import）は赤と独立している（そのままでよい）。

### 4-5. G21 経路（`build_visits_for_pool_v2`）と G-92 の固定優先フォールバック

- G21 の拠点（`OfficeFeatureFlag g21_new_algorithm` が enabled）では、PFV を固定候補として出すのは**ピン付き PFV だけ**（A7）。
- 赤を外すと固定候補が 0 件になり、G-92 の「固定を優先し、だめなら希望」が成立しなくなる（A13）。
- 本番で G21 が有効な拠点があるかは未確認（DB を読む必要があるため今回は見ていない）。
- 有効な拠点がなければ、G21 経路ごと廃止を検討してよい。

### 4-6. special_weekly_pattern

- 新ルールは「weekly_pattern / special_weekly_pattern の範囲」と言っている。
- しかし `_slot_within_preference`（と、その元の `_extract_weekly_entries`）は **weekly_pattern しか読まない**。special_weekly_pattern を読む services は layer1_expander と pending_request_applier だけ。
- 特別訪問週間に提案系をどう扱うか（special を優先するか、両方の和にするか）は判断が必要。

### 4-7. そのほか（実装者の判断で足りる）

- state_token（C4 / E1）から 2 項目を外すと、デプロイ直後の simulate → apply が 1 回だけ 409 になる（再実行で解消）。
- 422 のコード `pinned_pfv_cannot_be_applied`（A18）と `PinnedVisitMovedError`（A15）を消すとき、api の例外変換と FE の文言も合わせて消す（他の担当範囲）。
- tests: `test_movability_lock_fence.py`・`test_pfv_validator.py`（CODE_PINNED / CODE_MOVABILITY_CORRECTED）ほか、ピンを前提にしたテストの整理が必要（他の担当範囲）。
- pending_request_applier と proposed_visits_pfv は、今でも赤ピンを黙って外している（G2 / G3）。撤去すれば問題ではなくなる。
