# Codex コードレビュー — 割当エンジン・差分検出（重要発見多数）

**結論**: CareLink への本命は **案B: 部分移植**。`AllocationEngine` は巨大な可変状態マシンで、**制約漏れと stale state があるため、そのまま中核に据えるのは危険**。完全書直しは現時点では過剰。

## 品質評価

| ファイル | 可読性 | テスト容易性 | 保守性 | 主な問題 |
|---|---|---|---|---|
| `lib/allocation_engine.py` | 4/10 | 3/10 | 3/10 | 1クラス2521行、`_run_pipeline()` が13段階化、可変状態依存が強い |
| `lib/allocation_models.py` | 8/10 | 8/10 | 7/10 | dataclass中心で良いが、文字列enumが無検証 |
| `lib/allocation_utils.py` | 7/10 | 8/10 | 7/10 | 純関数中心。ただし時間タイプdefaultが広すぎる |
| `lib/diff_engine.py` | 4/10 | 4/10 | 3/10 | `print`副作用、未使用index、曖昧substring matching |
| `commands/auto_apply.py` | 3/10 | 2/10 | 2/10 | Playwright手順が巨大関数群、例外握りつぶし多数。参考実装扱いが妥当 |

## ロジック監査の重大発見

### 1. 公称8段階は古い（実は13段階）
docstring は8段階だが `allocation_engine.py:8-16`、実際は events→Level0→GapPack→Level1→Ejection→Relaxed→DayShift→Route→Overlap→Mentor→FinalSweep→Rescue→CoupledRescue→unassigned再構築 まである `:235-349`

### 2. 多試行5 orderings — 状態リセット不完全
- 各 trial 前に `_reset_state()` 呼ばれる `:158-160`、主要mapも消える `:378-386`
- ただし `_coupled_debug` / `_day_shift_failures` は **reset 対象外**
- best保存も **shallow** `:172-176`
- **最良結果復元後に `assign_count/staff_day_visits/pid_date_staff` を復元していない** `:183-185` → 危険

### 3. 重大な状態不整合
- `_unregister_assignment()` は `staff_day_visits` と `assign_count` だけを戻し、**`pid_date_staff` / `last_assigned_by_patient` を戻さない** `:401-409`
- `_fix_cross_patient_overlaps()` と `_final_overlap_sweep()` は **staff_id を消すだけで unregister しない** `:1447-1449`, `:2176-2178`
- その後の再挿入で**古い index が `_has_overlap()` に残り、空き枠を誤って塞ぐ** `:865-872`

### 4. 制約漏れ（迂回経路）
- 通常候補は NG/勤務曜日/soft cap/性別を見る `:640-658`
- **「必須指定」と「同じ人希望」経路は `_is_staff_available()` のみで、性別・soft cap・同一患者同日回避を迂回** `:585-614`
- `_is_staff_available()` 自体は勤務日・休み・時間・重複中心 `:776-825`

### 5. EjectionChain の固定時刻 direct insert
- overlap だけで、**シフト境界・部分休を再確認しない** `:2286-2290`
- Level1 はそこを見ている `:1120-1131`

### 6. 2名体制の hard constraint が弱い
- `_enforce_coupled_atomicity()` で片割れ解除可能 `:1226-1286`
- **`_sync_coupled_times()` が同期失敗時に warning だけで時刻不一致のまま残す** `:918-952`

### 7. mentor_pair は事後追加のみ
- mentor訪問を拾い `:1582-1602`、trainee既存予定との時間衝突のみ確認 `:1603-1614`
- **勤務曜日、休み、partial block、max_per_day、登録map更新なし** `:1615-1638`

### 8. 異体字マッチングが diff_engine 側に欠落
- `auto_apply` 側だけ `commands/auto_apply.py:45-71`
- `diff_engine` は `target_users` を**生文字列一致で絞り込み** `diff_engine.py:300-302`、利用者集合も生文字列 `:345`
- **差分生成前に異体字正規化されない**

### 9. 距離スコア
- missing=99、10km超=5 `allocation_utils.py:59-66`
- sort key上は最後なので tie-breaker としては妥当 `:755-765`
- **必須/同じ人経路では距離評価は使われない**

### 10. 時間タイプdefault
- 「午前/午後/終日」以外を 09:00-18:00 に落とす `allocation_utils.py:19-24`, `:122-133`
- **`固定` や `時間帯` に時刻境界が欠けると広すぎる解釈**

## 既知バグ/未実装（Codex 追加発見）

- **`_add_missing_coupled_entries()` 定義のみ、呼び出しなし** `:1903` — 2名体制 placeholder 補完の旧実装が死んでいる（dead code）
- `intersect_gaps` / `route_length` は import されているが engine 内未使用 `:31-32`
- diff_engine の `current_by_key` / `optimized_by_key` は構築のみ、使われていない `diff_engine.py:327-340`
- `pass2_matched` 加算されているが参照されない `:474`, `:498`
- `unassigned_set`, `coupled_partners`, `p064_groups`, `is_coupled` は実効利用なし `:1710-1720`, `:1735-1736`, `:1796`
- **diff_engine の substring matching は空文字境界が危険** — `"" in service_type` が真になるため、サービス名欠落時に誤マッチ `:481-483`, `:541-543`
- **`target_week_start/end` は日だけを `int(entry.date)` する** — `yyyy/MM/dd` 形式や月跨ぎは落ちる `:313-320`

## 性能分析

- **Level0 ホットループ**: `R × need_staff × S` `:488-497`, `:640-645`
- 各候補で `_is_staff_available()` が既存訪問と休みを走査し `compute_gaps()` `:780-825`
- さらに `patient_count` が都度 `self.results` 全走査 `:673-677`
- **`two_opt()` は最大50反復、各候補で `route_length()` 全走査 → 概ね `O(50 n^3)`** `allocation_utils.py:184-206`（staff-date 単位なので通常 n は小さい）
- `diff_engine.compare_schedules()` は利用者ごと・日付ごとに nested loop `O(Cd×Od)`、日付変更で `O(Cu×Ou)` `:432-521`, `:523-565`（**key index 未利用、改善余地大**）
- 300訪問×50スタッフなら通常メモリは小さい（数MB級）、CPU が支配的

## CareLink 方針

採用: **案B 部分移植**

### 優先順
1. 現行 API 入出力の golden test を固定
2. **`TrialState` を導入**し、`results/assign_count/staff_day_visits/pid_date_staff` を trial-local に隔離
3. 制約判定を純関数化: NG / 性別 / 勤務日 / 休み / shift / max / 2名体制
4. 2名体制と mentor_pair を **hard constraint として先に検証**
5. diff 側に**名前正規化**と**key index** を導入
6. CareLink adapter は最後に薄く作る

### 検証必須エッジケース10件

1. `固定` だが `earliest_min=None`
2. `時間帯` だが earliest/latest 欠落
3. 必須指定スタッフが性別制限に反する
4. 必須指定スタッフが soft cap 超過
5. 同じ人希望が NG staff に入っている
6. 2名体制の片方だけ空き、同期時刻が取れない
7. FinalSweep 後の再挿入で stale overlap が残らないこと
8. mentor trainee が休み/勤務日外/partial block
9. CSV利用者名が異体字違い
10. service_type 空文字または包含関係だけの別サービス

**テストは実行せず、実コード読解ベースの監査。**
