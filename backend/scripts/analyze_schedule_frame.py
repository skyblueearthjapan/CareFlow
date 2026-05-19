"""新スプレッドシート「スケジュール枠組み (仮)」シートの構造解析.

入力: tool-results/mcp-claude_ai_Google_Drive-read_file_content-1779169466214.txt
出力: コンソールに構造情報 (sheet 数 / header / 列マッピング / データサンプル)
"""

from __future__ import annotations

from pathlib import Path

FILE = Path(
    r"C:\Users\imaizumi.LINEWORKS-NET\.claude\projects\C--Users-imaizumi-LINEWORKS-NET-Documents-CareFlow\8175abfe-2af3-49cd-9fe8-2d8008ae0777\tool-results\mcp-claude_ai_Google_Drive-read_file_content-1779169466214.txt"
)


def main() -> None:
    raw = FILE.read_text(encoding="utf-8")
    # BOM
    if raw.startswith("﻿"):
        raw = raw[1:]
    # JSON wrapper
    if raw.startswith('{"fileContent":"'):
        raw = raw[len('{"fileContent":"') :]
        if raw.rstrip().endswith('"}'):
            raw = raw.rstrip()[:-2]
    # Escape
    raw = raw.replace("\\\\_", "_").replace("\\\\n", "\n").replace('\\\\"', '"')
    raw = raw.replace("\\n", "\n").replace('\\"', '"').replace("\\_", "_")

    lines = raw.split("\n")
    print(f"Total lines: {len(lines)}")

    # Find table headers (= unique first-col patterns)
    table_starts: list[tuple[int, str]] = []
    in_table = False
    prev_was_separator = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            is_separator = all(c in (":-:", "---", ":---", "---:", "") for c in cells)
            if is_separator:
                prev_was_separator = True
                continue
            if not in_table:
                # Possible header
                if not prev_was_separator:
                    table_starts.append((i, ln[:200]))
                    in_table = True
            prev_was_separator = False
        else:
            in_table = False
            prev_was_separator = False

    print("\n=== TABLE STARTS (header lines) ===")
    for idx, (line_no, content) in enumerate(table_starts):
        print(f"#{idx + 1} L{line_no}: {content[:150]}...")

    # Focus on L335 schedule frame
    print("\n=== L335 周辺 (スケジュール枠組み 仮) ===")
    if len(lines) > 335:
        header = lines[335]
        print(f"Header: {header[:300]}")
        cells = [c.strip() for c in header.strip("|").split("|")]
        print(f"Columns: {len(cells)}")
        print("\nUnique column patterns (parse 曜日/コース):")
        for j, c in enumerate(cells):
            # 「月曜日/A/氏名」「4/都賀/時間帯」等
            print(f"  Col {j}: '{c}'")
            if j > 30:
                print(f"  ... ({len(cells) - j} more)")
                break


if __name__ == "__main__":
    main()
