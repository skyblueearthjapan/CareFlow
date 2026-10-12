# ならしの部品箱 — 変わった注文を自作の条件で解く

## いつ使うか
1. まず **引数（knobs）**で言えるか見る（`rk level week|day` の `--days / --prefer-staff / --avoid-staff / --keep-current / --cancel / --fast / --only-staff --window / --not-same-day / --pair-staff / --forbid-pair`。一覧は `docs/tools/week-leveling/level_knobs.py` の先頭）。
2. 言えない注文だけ、**作業用の小さなスクリプト**を scratch に書いて `tools.leveling.api` の 3 つの関数を呼ぶ。
3. **共有の道具は書き換えない**（`docs/tools/week-leveling/*`・`agent/tools/leveling/*`・`agent/rk/*`）。スクリプトは `/work/scratch/`（無ければ `agent/runs/scratch/`）に置き、終わったら残さなくてよい。
4. 自作の条件・`weights` を使うときは `notes=["…"]` に**日本語で条件を書く**（必須）。A4 の「今回だけの条件」と案の `warnings`（決まりをゆるめる条件として）に出る。管理者には、この文を見せて「この条件でよいですか」と確かめる。
5. 案ができたら、いつもどおり `rk plan check` → `rk plan report`（上の文が「今回だけの条件」に出る）→ 了承 → `rk backup` → `rk apply` → `rk verify`。

## 3 つの関数
```python
from tools.leveling.api import load_week, solve, to_plan

week = load_week("2026-10-19", out_dir="/work/scratch/w43")   # 本番を読む（読み取り専用）。from_dir=前の出力フォルダ でも可
res  = solve(week, days=["2026-10-22"], knobs={"prefer": {"S004": 150}},
             extra_constraints=[my_rule], weights={"cross_office": 100}, notes=["…日本語の説明…"])
out  = to_plan(res, week)          # agent/runs/plans/…-leveling-custom.json  → out["plan_path"]
```
- `load_week(week, out_dir=None, *, from_dir=None, config_path=None)` … `out_dir` に Excel/A4/result.json が出る（リポジトリの中なら `docs/reports` の下）。
- `solve(week, *, days, weekdays, off, allow_over, over_before_manager, balance, drop, add_special, knobs, weights, extra_constraints, notes, seconds, no_app_check, no_pdf, include_trainee)` … 計算して結果（dict）を返す。
  - `off` = `["S004@2026-10-22", …]`（休みとして使わない）。新人は自動で外れる。
  - `knobs` = 引数と同じ指定: `prefer {コード: 重み}`・`avoid {コード: 重み}`・`keep_current 重み`・`cancel [訪問ID先頭]`・`only_staff [{"codes": […], "window": [開始分, 終了分]}]`・`not_same_day [[A, B]]`・`pair_staff [[利用者, コード]]`・`forbid_pair [[利用者, コード]]`・`fast True`。利用者は ID の先頭か名前の一部（空白なし）。
  - `weights` = 重みの上書き。名前: `manager_visit(240)`・`manager_overtime_per_min(20)`・`cross_office(480)`・`move_per_min(3)`・`rotation((200,100,50))`・`drop(1000000)`・`over_per_patient(300)`・`balance_per_patient(60)`・`prefer(100)`・`avoid(600)`・`keep_current(300)`。
  - `extra_constraints` = 日ごとに `fn(model, vars, data)` が呼ばれる（下）。
- `to_plan(res, week, *, kind="week-leveling", title=None, save=True)` … `{"plan", "plan_path", "out_dir"}`。急な休みの 1 日なら `kind="staff-absence"` と `title=` を付ける。

## `fn(model, vars, data)` で使えるもの
| 名前 | 中身 |
|---|---|
| `model` | OR-Tools の `RoutingModel`。`model.VehicleVar(index)`（その訪問を持つ職員の番号・外れたら -1）、`model.End(i)`、`model.solver()` |
| `vars["index_of_visit"]` | 訪問 ID → 訪問の index（その日の分だけ。同じ建物の 2 名は同じ index） |
| `vars["vehicle_of_code"]` / `["vehicle_of_staff"]` | 職員コード／職員 id → 職員の番号 `i`（その日に出勤する人だけ） |
| `vars["time"]` | 時刻の軸（分）。`vars["time"].CumulVar(model.End(i)).SetMax(15*60)` で「15 時までに終える」 |
| `vars["count"]` | 件数の軸。`vars["count"].CumulVar(model.End(i)).SetMax(3)` で「3 名まで」 |
| `vars["solver"]` / `["jobs"]` / `["vehicles"]` / `["day"]` | CP ソルバー・その日の仕事（同じ建物をまとめた単位）・職員（Staff）・日 |
| `data.day` / `data.visits` / `data.staff` / `data.history` | その日の日付・訪問（Visit: `id`・`patient_id`・`patient`・`fixed_start`・`dur`・`cur_staff` …）・職員 id→Staff・利用者ごとの担当歴 |

決まり（上限・勤務時刻・NG・女性限定・昼休み・移動・希望の時間帯）は常に効いている。自作の条件は**足す**もので、決まりを外すには `allow_over` などの引数か `weights` を使い、必ず `notes` に書く。解が無くなる条件は「入らない訪問」としてプールへ出る（`rk plan resolve` で決める）。

## 例（動くことは `agent/tests/test_level_knobs.py` で確かめている）
共通の頭:
```python
import sys
sys.path.insert(0, "C:/Users/imaizumi.LINEWORKS-NET/Documents/CareFlow02/agent")   # リポジトリの agent/（環境に合わせる）
from tools.leveling.api import load_week, solve, to_plan
week = load_week("2026-10-19", out_dir="/work/scratch/w43")
```
実行: `uv run -q --python 3.12 --with ortools --with openpyxl --with httpx python /work/scratch/my_plan.py`（`agent/` から。ortools が入った Python なら直接）。

**例 1: 「S004 さんは 1 日 3 名まで」（個人別の上限）**
```python
def cap_staff(code, n):
    def rule(model, vars, data):
        i = vars["vehicle_of_code"].get(code)          # その日に出勤しない人は無い
        if i is not None:
            vars["count"].CumulVar(model.End(i)).SetMax(n)
    return rule

res = solve(week, extra_constraints=[cap_staff("S004", 3)], notes=["S004 さんは 1 日 3 名まで（今回だけ）"])
```

**例 2: 「〇〇様と△△様は同じ職員で」（続けて同じ人が行く）**
```python
def same_staff(pid_a, pid_b):
    def rule(model, vars, data):
        ids = {v.patient_id: vars["index_of_visit"][v.id] for v in data.visits}
        if pid_a in ids and pid_b in ids:
            model.solver().Add(model.VehicleVar(ids[pid_a]) == model.VehicleVar(ids[pid_b]))
    return rule

res = solve(week, extra_constraints=[same_staff("<利用者 ID>", "<利用者 ID>")],
            notes=["〇〇様と△△様は同じ職員が回る（今回だけ）"])
```

**例 3: 「S007 さんは 15 時に上がる」（早上がり）＋ 拠点またぎをゆるめる（重みの上書き）**
```python
def finish_by(code, hhmm):
    h, m = map(int, hhmm.split(":"))
    def rule(model, vars, data):
        i = vars["vehicle_of_code"].get(code)
        if i is not None:
            vars["time"].CumulVar(model.End(i)).SetMax(h * 60 + m)
    return rule

res = solve(week, days=["2026-10-22"], extra_constraints=[finish_by("S007", "15:00")],
            weights={"cross_office": 120},            # 拠点またぎを避ける重みを 480 → 120 に
            notes=["S007 さんは 15:00 に上がる（今回だけ）", "拠点またぎを許す度合いを大きくした"])
out = to_plan(res, week, kind="staff-absence", title="10/22（木） 早上がりの組み直し")
print(out["plan_path"])
```
※ 早上がりは勤務時刻そのものを変えたいのではなく「その日だけ」なので、休みの登録（半休・時間変更）が要るなら `rakusuke-absence` の手順で別に行う（案の検査は元の勤務時刻で数えるため、自作の上限は「より厳しい」側にだけ働く）。

## 注意
- 1 本ずつ流す（PC のメモリが少ない）。`seconds` を増やすほど良い案になるが 1 日ごとにその秒数かかる。急ぎなら `knobs={"fast": True}`（10 秒）。
- 自作の条件が効いたかは、`res["days"]` の順路と A4 で確かめる。効かない・解が出ないときは条件を緩めるか、管理者に相談する。
- `notes` を書いていないスクリプトの案は出さない（`solve` が断る）。
- 新人の同行（`--must-accompany`）は計算に入れない: 新人はコース・担当を持てないので、案の反映後に `accompaniment-set` の案で付ける（`rakusuke-week-optimize` §1-9）。
