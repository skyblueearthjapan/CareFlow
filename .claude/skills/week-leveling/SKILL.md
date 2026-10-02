---
name: week-leveling
description: らく助（CareFlow）の週や 1 日の訪問を、決まり（1 人の上限・勤務時刻・NG・女性限定・希望の時間帯・同じ建物の 2 名・昼休み・ローテーション・移動）を守って職員へ割り振り直す「ならし案」を作る。週を作った後の重なりの解消、職員が休む/余る日の組み直し、「少しオーバーしても全員でならす」試算に使う。本番は読むだけで、案は Excel と A4 で出す。
---

# 週のならし（診断＋案）

本番のデータを**読むだけ**で、週（または指定日）の訪問を職員へ割り振り直した案を作る。反映は人がアプリの操作で行う（このスキルは本番へ書かない）。

- 決まりの正典: `docs/plans/week-leveling-rules-2026-10-02.md`（アプリの全エンジンの決まりと PO の決定）。決まりを変えるときは先にここを直し、PO に確かめる。
- 道具: `docs/tools/week-leveling/`（`level.py` が入口）。設定: `docs/tools/week-leveling/config.local.json`（git 管理外。無ければ `config.example.json` をコピーし、職員コードで書く）。

## 手順

1. **何をしたいかを確かめる**（AskUserQuestion で選択式）: 週全体か特定の日か／休む人・増える人がいるか（`--off`）／上限を少し超えてよいか（`--allow-over 1`、マネージャーより先なら `--over-before-manager`）／件数をならすか（`--balance`）／Jev の目安を付けるか（`--jev`）。
2. **実行**（リポジトリ直下・Bash）:
   ```bash
   PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with httpx \
     python docs/tools/week-leveling/level.py --week 2026-10-12 [--day 2026-10-13] \
     [--off S004@2026-10-13] [--allow-over 1] [--over-before-manager] [--balance] [--jev] [--seconds 30]
   ```
   - 出力: `docs/reports/<日時>-week<NN>-leveling<印>/`（`leveling.xlsx`・`leveling-a4.pdf`/`.html`・`summary.json`・取り出した `week.json`/`history.json`）。**利用者名入り。git に入れない・Artifact 等で公開しない。**
   - 同じデータで条件だけ変えて試すときは `--from-dir <前回の出力>` で本番に繋がずに回す。
   - 1 日 30 秒 × 日数ほどかかる。長い週は `run_in_background`。
3. **結果を確かめる**: `summary.json` の `rule_errors` が 0 であること（0 でなければ案を出さずに原因を調べる）。`app_check`（アプリの物差しでの診断）の `yardstick_match` が true であること・`one_more_move_checked` が true かつ `one_more_move_missed`（あと 1 手で良くなる見落とし）が 0 であること（0 でなければ `--seconds` を増やして計算し直す）。移動が平均の 1.5 倍を超える順路は A4 の 1 ページ目と Excel の「アプリの物差し」に理由（いちばん長い移動）付きで出る。`unplaced`（入らない）・`manager_overtime_visits`・`cross_office_visits`・`same_as_last_visits`・`max_move_min` を PO に伝える。A4 は Chrome の画面写し（`--screenshot`）で崩れていないか見る。
4. **PO へ報告**（日本語・平易に）: 今の盤面と案の比較（重なり・担当なし・移動）、気を付ける点、ファイルの場所。反映は PO の了承の後に、アプリの操作（今週だけ移動・担当変更）で行う。

## 守ること
- 本番 DB へは書かない（`extract.py` は読み取り専用トランザクション）。
- Jev へ送るのは記号と数字だけ（名前・住所・メモは送らない）。Jev は「要確認」の目安で、決まりはソルバーと検査が守る。
- 職員名・事業所名をコードに書かない（`config.local.json` に職員コードで書く）。
- `staff_events` の時刻を psql で見るときは `starts_at AT TIME ZONE 'UTC'`（そのまま見ると 9 時間後に見えるが不具合ではない）。
- 言葉: 実績の時刻は「合わせる」「調整」。アプリ名は「らく助」。
