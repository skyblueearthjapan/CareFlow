# 週のならし（診断＋案）

週（または指定日）の訪問を、決まりを守って職員へ割り振り直した「案」を作る道具。本番は**読むだけ**。案は Excel と A4 で出し、反映は人がアプリの操作で行う。

- 決まり: `docs/plans/week-leveling-rules-2026-10-02.md`
- Claude Code から使うとき: スキル `week-leveling`（`.claude/skills/week-leveling/SKILL.md`）

## ファイル
| ファイル | 役目 |
|---|---|
| `level.py` | 入口。取り出し → 計算 → 検査 → Excel/A4 →（任意）Jev |
| `extract.py` | 本番から 1 週分と直前 4 週の担当歴を読み取り専用で取り出す（ssh → psql） |
| `solver.py` | 計算の本体（OR-Tools の配車問題・1 日ずつ・ローテーションは前日までの案を積む） |
| `report.py` | 決まりの数え直し（計算とは別）と Excel・A4 |
| `jev_check.py` | 変更ごとに Jev の「要確認」の目安（記号と数字だけ送る） |
| `config.example.json` | 設定の見本。`config.local.json`（git 管理外）にコピーして職員コードで書く |

## 実行
```bash
PYTHONIOENCODING=utf-8 uv run -q --python 3.12 --with ortools --with openpyxl --with httpx \
  python docs/tools/week-leveling/level.py --week 2026-10-12
```
オプションは `level.py` の先頭の説明を参照（`--day`・`--off`・`--allow-over`・`--over-before-manager`・`--balance`・`--jev`・`--from-dir`・`--seconds`）。

出力先 `docs/reports/` は git 管理外（利用者名を含む）。公開しない。

## 計算のしかた（要点）
- 1 日ごとに「出勤している職員 = 車」「訪問 = 立ち寄り先」の配車問題として解く。始点からの移動は数えない（アプリと同じ）。
- 同じ建物（約 100m 以内）の 2 名は 1 つにまとめ、1 人で 90 分の枠。
- 時刻は 5 分刻み。固定の時刻からずらすと 1 分ごとに重み、範囲は希望の時間帯の中だけ（固定はその時刻だけ）。
- 重み（移動 1 分 = 1）: マネージャーが持つ 1 件 240／拠点またぎ 480／勤務時刻を超えるマネージャー 1 分 20／前回と同じ職員 200・2 回前 100・3 回前 50／上限超え 1 名 300（マネージャーより先なら 120）／ならし 60。
