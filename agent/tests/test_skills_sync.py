"""スキルの正典（.agents/skills）と Claude 用の写し（.claude/skills）が同じか・形が正しいか。"""

import re
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR / "bin"))

import sync_skills  # noqa: E402

SKILLS = AGENT_DIR / ".agents" / "skills"
FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)


def _folders():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


def _frontmatter(text: str) -> dict:
    m = FM_RE.match(text)
    assert m, "先頭に YAML の frontmatter（--- で囲む）がありません"
    out = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        k, sep, v = line.partition(":")
        assert sep, f"frontmatter の行の形が違います: {line!r}"
        out[k.strip()] = v.strip().strip("'\"")
    return out


def test_claude_copy_matches():
    d = sync_skills.diff()
    assert d == [], "スキルの写しが古い → python agent/bin/sync_skills.py を流す:\n" + "\n".join(d)


def test_every_skill_has_frontmatter():
    folders = _folders()
    assert folders
    for f in folders:
        md = f / "SKILL.md"
        assert md.exists(), f"{f.name} に SKILL.md がありません"
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        assert fm.get("name") == f.name, f"{f.name}: name が {fm.get('name')!r}"
        desc = fm.get("description", "")
        assert desc, f"{f.name}: description が空"
        assert len(desc) < 1024, f"{f.name}: description が {len(desc)} 文字（1024 未満に）"


def test_agents_md_lists_every_skill():
    text = (AGENT_DIR / "AGENTS.md").read_text(encoding="utf-8")
    index = text[text.index("## 8."):]
    index = index[:index.index("\n## ", 1)]
    for f in _folders():
        assert f"`{f.name}`" in index, f"AGENTS.md §8 のスキルの一覧に {f.name} がありません"


def test_sync_copies_and_deletes_stale(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "a" / "references").mkdir(parents=True)
    (src / "a" / "SKILL.md").write_text("x", encoding="utf-8")
    (src / "a" / "references" / "q.md").write_text("y", encoding="utf-8")
    (dst / "old").mkdir(parents=True)
    (dst / "old" / "SKILL.md").write_text("stale", encoding="utf-8")
    assert sync_skills.diff(src, dst)
    sync_skills.sync(src, dst)
    assert sync_skills.diff(src, dst) == []
    assert not (dst / "old").exists()
    assert (dst / "a" / "references" / "q.md").read_text(encoding="utf-8") == "y"
