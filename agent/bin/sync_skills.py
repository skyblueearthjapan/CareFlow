"""スキルの正典 .agents/skills/（Codex）を .claude/skills/（Claude Code）へそのまま写す。

    python agent/bin/sync_skills.py          # 写す（.claude/skills の余分なファイルは消す）
    python agent/bin/sync_skills.py --check  # 違いを出すだけ（違いがあれば終了コード 1）

スキルを直すのは .agents/skills/ だけ。直したら必ずこれを流す（tests/test_skills_sync.py が違いを見張る）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
SRC = AGENT_DIR / ".agents" / "skills"
DST = AGENT_DIR / ".claude" / "skills"
IGNORE = {"__pycache__", ".DS_Store"}


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and not any(part in IGNORE for part in p.relative_to(root).parts)}


def diff(src: Path = SRC, dst: Path = DST) -> list[str]:
    """違いの一覧（空なら同じ）。"""
    a, b = _files(src), _files(dst)
    out = [f"足りない: {k}" for k in sorted(a.keys() - b.keys())]
    out += [f"余分: {k}" for k in sorted(b.keys() - a.keys())]
    out += [f"中身が違う: {k}" for k in sorted(a.keys() & b.keys()) if a[k].read_bytes() != b[k].read_bytes()]
    return out


def sync(src: Path = SRC, dst: Path = DST) -> list[str]:
    if not src.is_dir():
        raise SystemExit(f"スキルの正典がありません: {src}")
    changes = diff(src, dst)
    a, b = _files(src), _files(dst)
    for k in b.keys() - a.keys():
        b[k].unlink()
    for k, p in a.items():
        q = dst / k
        if not q.exists() or q.read_bytes() != p.read_bytes():
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, q)
    # 空になったフォルダを消す
    if dst.is_dir():
        for d in sorted((d for d in dst.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
    return changes


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="写さずに違いだけ出す")
    args = ap.parse_args(argv)
    if args.check:
        d = diff()
        for line in d:
            print(line)
        print("同じです" if not d else f"違いが {len(d)} 件あります（python agent/bin/sync_skills.py で写す）")
        return 1 if d else 0
    changes = sync()
    for line in changes:
        print(line)
    print(f"写しました: {SRC} → {DST}（{len(changes)} 件の違いを合わせた）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
