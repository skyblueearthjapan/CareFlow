"""Phase F-1: User のスケジュール枠組み (仮) シートから抽出失敗パターンを可視化.

User 指摘:
  - 中点 ペア表記 ("牧・河野" 等) で 1 cell に 2 患者
  - 個人 ID 無し → 氏名のみで突合
  - 時刻が「行の位置」依存 → セル結合等で読み損なう可能性
  - 同名混同の懸念

本 script は:
  1. ファイル (1P5aH... の raw txt) を読み込み
  2. スケジュール枠組み (仮) の全テーブル (L335-L460) をスキャン
  3. 各 「氏名」 セルを resolve 試行 → 成功 / 部分成功 / 未解決 に分類
  4. 未解決パターンを markdown 表で出力

出力: backend/scripts/_unresolved_patterns.md
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE_FILE = Path(
    r"C:\Users\imaizumi.LINEWORKS-NET\.claude\projects"
    r"\C--Users-imaizumi-LINEWORKS-NET-Documents-CareFlow"
    r"\8175abfe-2af3-49cd-9fe8-2d8008ae0777\tool-results"
    r"\mcp-claude_ai_Google_Drive-read_file_content-1779169466214.txt"
)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_MD = SCRIPT_DIR / "_unresolved_patterns.md"

WEEKDAY_JP_MAP = {"月曜日": 0, "火曜日": 1, "水曜日": 2, "木曜日": 3, "金曜日": 4, "土曜日": 5}
WEEKDAY_JP_LIST = ["月", "火", "水", "木", "金", "土", "日"]

# ペア / 区切り文字検出パターン
SEPARATORS = ["・", "／", "/", "／", "、", ",", " と ", "&"]


def unwrap_json(raw: str) -> str:
    if raw.startswith("﻿"):
        raw = raw[1:]
    raw = raw.lstrip()
    if raw.startswith('{"fileContent":"'):
        raw = raw[len('{"fileContent":"') :]
        if raw.rstrip().endswith('"}'):
            raw = raw.rstrip()[:-2]
    raw = raw.replace("\\\\_", "_").replace("\\\\n", "\n").replace('\\\\"', '"')
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\_", "_")
    return raw


def is_separator_line(ln: str) -> bool:
    cells = [c.strip() for c in ln.strip("|").split("|")]
    return all(c in (":-:", "---", ":---", "---:", "") for c in cells if c is not None)


def parse_table(lines: list[str], start_idx: int) -> tuple[list[str], list[list[str]], int]:
    """指定行から始まる markdown table を parse. (header, data_rows, next_idx)."""
    if start_idx >= len(lines):
        return [], [], start_idx
    header_cells = [c.strip() for c in lines[start_idx].strip("|").split("|")]
    rows: list[list[str]] = []
    i = start_idx + 1
    while i < len(lines) and is_separator_line(lines[i]):
        i += 1
    while i < len(lines):
        ln = lines[i]
        if not ln.strip().startswith("|"):
            break
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # 次のヘッダー行か判定 (= 時刻ではなく時間帯列を含む)
        if cells and "時間帯" in cells[0] and not re.match(r"^\d{1,2}:\d{2}$", cells[0]):
            break
        if is_separator_line(ln):
            i += 1
            continue
        rows.append(cells)
        i += 1
    return header_cells, rows, i


def detect_pair_pattern(cell: str) -> list[str]:
    """セルに区切り文字 (中点・スラッシュ等) があれば split して候補リスト返す.

    例: "牧・河野" → ["牧", "河野"]
    例: "佐藤 / 田中" → ["佐藤", "田中"]
    単一氏名なら [cell] を返す.
    """
    for sep in SEPARATORS:
        if sep in cell:
            parts = [p.strip() for p in cell.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts
    return [cell]


def build_patient_lookup(motodata_rows: list[list[str]]) -> tuple[dict[str, str], dict[str, str]]:
    """元データシート rows から name → code map を構築.

    - exact_match: 「青栁　あい」(全角空白込み完全一致) → P001
    - partial_match: 「青栁」「あい」「青栁あい」など部分・無空白一致 → P001
    """
    exact: dict[str, str] = {}
    partial: dict[str, str] = {}
    for row in motodata_rows:
        if len(row) < 2:
            continue
        code = row[0].strip()
        name = row[1].strip() if len(row) > 1 else ""
        if not code or not name:
            continue
        exact[name] = code
        # 各種正規化版を partial に
        for variant in (
            name.replace("　", " "),
            name.replace(" ", "　"),
            name.replace("　", ""),
            name.replace(" ", ""),
        ):
            if variant != name:
                partial[variant] = code
        # 苗字単独 (空白で split した先頭) も partial に登録
        space_split = re.split(r"[　 ]+", name)
        if space_split and space_split[0]:
            sur = space_split[0]
            # 同苗字が複数いる場合は最後にマッチしたものを保持 (= ambiguous risk あり)
            partial[sur] = code
    return exact, partial


def resolve_name(
    name: str, exact: dict[str, str], partial: dict[str, str]
) -> tuple[str | None, str]:
    """name を resolve. 戻り値 (patient_code or None, status string)."""
    if not name or name in ("空", "0", "-", "x", "×"):
        return None, "skip(placeholder)"
    # exact
    if name in exact:
        return exact[name], "exact"
    # normalize variants
    for variant in (
        name.replace("　", " "),
        name.replace(" ", "　"),
        name.replace("　", ""),
        name.replace(" ", ""),
    ):
        if variant in exact:
            return exact[variant], "exact(normalized)"
    # partial
    if name in partial:
        return partial[name], "partial(surname?)"
    return None, "unresolved"


def main() -> None:
    raw = unwrap_json(SOURCE_FILE.read_text(encoding="utf-8"))
    lines = raw.split("\n")
    print(f"Total lines: {len(lines)}")

    # 1) 元データシート (L0-L77) を parse → patient lookup
    motodata_header, motodata_rows, _ = parse_table(lines, 0)
    print(f"Motodata: {len(motodata_rows)} rows")
    exact_lookup, partial_lookup = build_patient_lookup(motodata_rows)
    print(f"Name lookup: exact={len(exact_lookup)}, partial={len(partial_lookup)}")

    # 2) L335 以降のスケジュール枠組み (仮) をスキャン
    findings: list[dict] = []
    i = 335
    table_count = 0
    while i < min(len(lines), 461):
        if i >= len(lines):
            break
        ln = lines[i]
        if not ln.strip().startswith("|") or is_separator_line(ln):
            i += 1
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # ヘッダー候補?
        if "時間帯" in cells[0] and not re.match(r"^\d{1,2}:\d{2}$", cells[0]):
            # ヘッダー行 + その下の data
            header_cells, data_rows, next_i = parse_table(lines, i)
            table_count += 1
            # col_meta: col_idx → (weekday, course, field) or None
            col_meta: list[tuple[str, str, str] | None] = []
            for c in header_cells:
                parts = c.split("/")
                if len(parts) == 3 and parts[0] in WEEKDAY_JP_MAP:
                    col_meta.append((parts[0], parts[1], parts[2]))
                else:
                    col_meta.append(None)
            # 各データ行 (= 時刻行)
            for data_row in data_rows:
                if not data_row:
                    continue
                # col 0 が時刻でない (= 別の table の header かも) は skip
                if not re.match(r"^\d{1,2}:\d{2}$", data_row[0]):
                    continue
                time_str = data_row[0]
                # 氏名列スキャン
                for col_idx, meta in enumerate(col_meta):
                    if meta is None:
                        continue
                    wd_jp, course, field = meta
                    if field != "氏名":
                        continue
                    if col_idx >= len(data_row):
                        continue
                    raw_cell = data_row[col_idx].strip()
                    if not raw_cell:
                        continue
                    # 区切り文字パターン検出
                    candidates = detect_pair_pattern(raw_cell)
                    is_pair = len(candidates) > 1
                    for cand in candidates:
                        pcode, status = resolve_name(cand, exact_lookup, partial_lookup)
                        if status == "skip(placeholder)":
                            continue
                        if status == "exact":
                            continue  # 正常解決、記録不要
                        # 部分一致や未解決のみ記録
                        findings.append(
                            {
                                "weekday": wd_jp,
                                "course": course,
                                "time": time_str,
                                "cell_raw": raw_cell,
                                "candidate": cand,
                                "is_pair_split": is_pair,
                                "status": status,
                                "resolved_code": pcode or "?",
                            }
                        )
            i = next_i
        else:
            i += 1

    print(f"Tables scanned: {table_count}")
    print(f"Findings (partial/unresolved): {len(findings)}")

    # 3) Markdown report 出力
    with OUTPUT_MD.open("w", encoding="utf-8") as f:
        f.write("# Phase F-1: 抽出失敗パターン リスト\n\n")
        f.write("User シート 「スケジュール枠組み (仮)」 の全氏名セルから ")
        f.write("解決失敗 or 注意要 のみを抽出.\n\n")
        f.write(f"- スキャン table 数: {table_count}\n")
        f.write(f"- 元データシート 患者数: {len(motodata_rows)}\n")
        f.write(f"- 抽出失敗 / 注意要 件数: **{len(findings)}**\n\n")

        # status 別集計
        f.write("## status 別件数\n\n")
        status_count: dict[str, int] = {}
        for fi in findings:
            status_count[fi["status"]] = status_count.get(fi["status"], 0) + 1
        for status, cnt in sorted(status_count.items(), key=lambda x: -x[1]):
            f.write(f"- `{status}`: {cnt} 件\n")
        f.write("\n")

        # 中点ペア (= 1 cell に 2 patient) 一覧
        pair_findings = [fi for fi in findings if fi["is_pair_split"]]
        if pair_findings:
            f.write("## 中点ペア表記 (1 cell に 2 patient 疑い)\n\n")
            f.write("| 曜日 | コース | 時刻 | セル原文 | split 後 | 解決状態 | 推測 code |\n")
            f.write("|------|--------|------|---------|---------|---------|----------|\n")
            # 重複排除して cell_raw 別に grouping
            seen_pairs: dict[str, list[dict]] = {}
            for fi in pair_findings:
                key = fi["cell_raw"]
                seen_pairs.setdefault(key, []).append(fi)
            for cell_raw, items in sorted(seen_pairs.items()):
                first = items[0]
                f.write(
                    f"| {first['weekday']} | {first['course']} | {first['time']} | "
                    f"`{cell_raw}` | `{first['candidate']}` | "
                    f"{first['status']} | {first['resolved_code']} |\n"
                )
                # 同セルから split された他候補も
                others = [fi for fi in items if fi["candidate"] != first["candidate"]]
                for fi in others:
                    f.write(
                        f"| | | | | `{fi['candidate']}` | "
                        f"{fi['status']} | {fi['resolved_code']} |\n"
                    )
            f.write("\n")

        # 単一未解決 (= 中点なしで unresolved/partial)
        single_findings = [fi for fi in findings if not fi["is_pair_split"]]
        if single_findings:
            f.write("## 単一氏名 (未解決 or 部分一致)\n\n")
            f.write("| 曜日 | コース | 時刻 | セル原文 | 解決状態 | 推測 code |\n")
            f.write("|------|--------|------|---------|---------|----------|\n")
            seen_singles: dict[tuple[str, str], dict] = {}
            for fi in single_findings:
                key = (fi["cell_raw"], fi["status"])
                if key not in seen_singles:
                    seen_singles[key] = fi
            for fi in sorted(
                seen_singles.values(), key=lambda x: (x["weekday"], x["course"], x["time"])
            ):
                f.write(
                    f"| {fi['weekday']} | {fi['course']} | {fi['time']} | "
                    f"`{fi['cell_raw']}` | {fi['status']} | {fi['resolved_code']} |\n"
                )

    print("\n=== 出力 ===")
    print(f"{OUTPUT_MD}")


if __name__ == "__main__":
    main()
