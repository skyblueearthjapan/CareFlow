"""使うモデルの決まり（Claude = いつも最新の Opus・Codex = いつも最新の Sol）。"""

import pytest

from relay import models
from relay.runner import claude_argv


def test_claude_always_opus_alias(tmp_path):
    files = {"mcp": tmp_path / "m.json", "isolation": tmp_path / "i.json", "prompt": tmp_path / "p.md"}
    for resume in (None, "abc-123"):
        argv = claude_argv("claude", files, resume)
        assert argv[argv.index("--model") + 1] == "opus"  # --resume の時も毎回
    assert "--resume" in claude_argv("claude", files, "abc-123")


@pytest.mark.parametrize("resolved,ok", [
    ("claude-opus-5-5", True), ("claude-opus-6", True), ("claude-sonnet-5-5", False),
    ("claude-fable-5", False), ("default", False), (None, True),
])
def test_claude_init_check(resolved, ok):
    assert (models.claude_init_problem(resolved) is None) is ok


def test_display_name():
    assert models.display_name("claude-opus-5-5") == "Opus 5.5"
    assert models.display_name("claude-opus-6") == "Opus 6"
    assert models.display_name("gpt-6.1-sol") == "Sol 6.1"


def test_newest_sol_by_numeric_version():
    ids = ["gpt-5.6-sol", "gpt-6-sol", "gpt-6.1-sol", "gpt-6.1-sol-preview", "gpt-7-astra", "gpt-6.2-luna",
           "gpt-10-sol-mini", "best", "default"]
    assert models.newest_sol(ids) == "gpt-6.1-sol"
    assert models.newest_sol(["gpt-5.10-sol", "gpt-5.9-sol"]) == "gpt-5.10-sol"  # 文字列ではなく数字で
    assert models.newest_sol(["gpt-7-sol", "gpt-6.1-sol"]) == "gpt-7-sol"


def test_sol_baseline_when_unknown():
    assert models.newest_sol(None) == "gpt-6.1-sol"
    assert models.newest_sol([]) == "gpt-6.1-sol"
    assert models.newest_sol(["gpt-6-luna", "claude-opus-5-5"]) == "gpt-6.1-sol"


def test_sol_fallback_on_unsupported():
    picker = models.SolPicker(["gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol"])
    assert picker.current == "gpt-6.1-sol"
    assert picker.on_error("some other error") is None
    msg = "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."
    assert picker.on_error(msg) == "gpt-6-sol"
    assert picker.on_error(msg) == "gpt-5.6-sol"
    assert picker.on_error(msg) is None  # これより下は無い
    assert [f["to"] for f in picker.fallbacks] == ["gpt-6-sol", "gpt-5.6-sol"]


@pytest.mark.parametrize("name", ["best", "default", "BEST", "claude-fable-5", "gpt-7-astra", "", None])
def test_forbidden_names(name):
    assert models.is_forbidden(name)


def test_sol_version_rejects_suffixes():
    assert models.sol_version("gpt-6.1-sol") == (6, 1, 0)
    assert models.sol_version("gpt-6.1-sol-high") is None
    assert models.sol_version("gpt-6.1.2.3-sol") is None
    assert models.sol_version("o4-sol") is None
