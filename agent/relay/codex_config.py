"""Codex（ChatGPT）の起こし方の決まり — 中継（direct）と箱の runner が同じものを使う（1 か所）。

標準ライブラリだけ（runner はこのファイルを /srv/agent/relay/codex_config.py からパスで読み込む）。

決めたこと（2026-10-12・段 3-5。根拠は docs/plans/agent-ui/phase3-integration-status-2026-10-12.md §6）
- つなぎ方: ``codex app-server``（stdio の JSON-RPC）。``thread/start|resume``・``turn/start|steer|interrupt``・
  承認の問い合わせ・``model/list``・``account/read``・``config/read``・``experimentalFeature/list``。
- sandbox: ``danger-full-access``（箱＝コンテナが砂場）。【実測】codex 0.162.1 の Linux の砂場（bwrap）は Docker の
  既定の seccomp では動かない（"No permissions to create a new namespace"）。read-only / workspace-write にすると
  すべてのコマンドが失敗する。
- 承認: ``approval_policy=on-request``・``approvals_reviewer=user``（承認をモデルに任せる guardian は切る）。
  PO 2026-10-12 改め「箱の中の計算は自由・書き込みだけを関所で」: シェル・python・その場の script は聞かずに動く。
  コマンドの決まり（execpolicy・``agent/deploy/runner/rakusuke.rules``）は rk を allow、外へ出る道具・パッケージ・
  別の CLI を forbidden にするだけ。問い合わせが来たら中継が ``command_decision`` で答える（秘密・外へ・内側口の
  直打ち・agent/ の書き換えだけ断る）。**守りは承認に頼らない**（モデルは聞かずに動く・code mode の教訓）:
  書き込みは中継の承認番号だけ、網は箱の内側と出口の許可リストだけ。走ったものは ``relay.watch`` が見張る。
- 締め付け（-c・毎回）: Web 検索・画像・ブラウザー・コンピューター操作・サブエージェント・アプリ・プラグイン・
  フック・記憶・MCP なし・ChatGPT ログインだけ・OpenAI の provider だけ。始める前に ``config/read`` と
  ``experimentalFeature/list`` で効いているかを確かめ、効いていなければ会話を始めない（``lockdown_problems``）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

APPROVAL_POLICY = "on-request"
SANDBOX_MODE = "danger-full-access"
APPROVALS_REVIEWER = "user"
RULES_NAME = "rakusuke.rules"

#: 毎回 -c で渡す締め付け（key, TOML の値）。config.toml（モデルが書き換えうる）より -c が勝つ。
#: 知らない機能の名前は codex が警告するだけ（版の違いに強い）。
LOCKDOWN: list[tuple[str, str]] = [
    # ChatGPT のログインと OpenAI の provider だけ（API キー・別のつなぎ先を使わせない）
    ("forced_login_method", '"chatgpt"'),
    ("model_provider", '"openai"'),
    ("sandbox_mode", f'"{SANDBOX_MODE}"'),
    ("approval_policy", f'"{APPROVAL_POLICY}"'),
    ("approvals_reviewer", f'"{APPROVALS_REVIEWER}"'),
    ("mcp_servers", "{}"),
    ("notify", "[]"),
    ("web_search", '"disabled"'),
    ("history.persistence", '"none"'),
    ("check_for_update_on_startup", "false"),
    ("otel.exporter", '"none"'),
    ("otel.trace_exporter", '"none"'),
    ("otel.metrics_exporter", '"none"'),
    ("memories.generate_memories", "false"),
    ("memories.use_memories", "false"),
    # rk には RK_RELAY_TOKEN が要る（名前に TOKEN を含むので既定の除外で消える）。環境は runner が許可リストで作るので
    # ほかに秘密は無い
    ("shell_environment_policy.ignore_default_excludes", "true"),
    # 質問カード（request_user_input）を既定のモードでも使えるように
    ("features.default_mode_request_user_input", "true"),
]

#: 切る機能（features.<名前>=false）。承認をモデルに任せる・外へ出る・道具を増やす・設定を混ぜる・記憶。
FEATURES_OFF: tuple[str, ...] = (
    "guardian_approval", "guardian_reuse_parent_compaction", "guardianv2",
    "apps", "enable_mcp_apps", "plugins", "plugin_hooks", "plugin_sharing", "remote_plugin", "recommended_plugins",
    "hooks", "tool_suggest", "skill_mcp_dependency_install", "external_agent_memory_import", "memories", "chronicle",
    "in_app_updates",
    "web_search_request", "web_search_cached", "standalone_web_search", "image_generation",
    "browser_use", "browser_use_external", "browser_use_full_cdp_access", "browser_annotation_api", "in_app_browser",
    "computer_use", "in_app_local_automation", "in_app_chat", "in_app_dictation", "realtime_conversation",
    "multi_agent", "multi_agent_v2", "view_image", "request_permissions_tool", "goals", "artifact", "worktrees",
    "workspace_dependencies", "agent_message_board", "send_message_to_user_async", "sleep_tool",
)

#: 始める前に「off」であることを experimentalFeature/list で確かめるもの（一覧に名前が無いものは版に無い＝可）。
MUST_BE_OFF: tuple[str, ...] = (
    "guardian_approval", "guardian_reuse_parent_compaction", "apps", "enable_mcp_apps", "plugins", "hooks",
    "memories", "image_generation", "browser_use", "computer_use", "multi_agent", "multi_agent_v2",
    "in_app_browser", "standalone_web_search",
)

#: config/read の実効値で確かめるもの（key → 許す値）。
MUST_CONFIG: dict[str, Any] = {
    "approval_policy": APPROVAL_POLICY,
    "sandbox_mode": SANDBOX_MODE,
    "approvals_reviewer": APPROVALS_REVIEWER,
    "forced_login_method": "chatgpt",
    "model_provider": "openai",
    "web_search": "disabled",
}


def config_args() -> list[str]:
    """app-server に付ける -c の並び（サブコマンドの前）。"""
    out: list[str] = []
    for k, v in LOCKDOWN:
        out += ["-c", f"{k}={v}"]
    for name in FEATURES_OFF:
        out += ["-c", f"features.{name}=false"]
    return out


def codex_argv(codex_bin: str = "codex") -> list[str]:
    """``codex -c … app-server``。モデルは thread/start・turn/start で渡す（model/list で選んでから）。"""
    return [codex_bin, *config_args(), "app-server"]


# ---------- 始める前の確かめ（config/read・experimentalFeature/list） ----------
def _get(cfg: dict, dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def lockdown_problems(config: dict | None, features: Iterable[dict] | None) -> list[str]:
    """締め付けが効いていない所の一覧（英語の短い印・ログ用）。空なら効いている。

    config = config/read の ``config``、features = experimentalFeature/list の ``data`` を全ページ分。
    答えが無い・形が違うときも「効いていない」とする（閉じる側に倒す）。
    """
    probs: list[str] = []
    if not isinstance(config, dict):
        probs.append("config/read: no answer")
    else:
        for key, want in MUST_CONFIG.items():
            got = _get(config, key)
            if got != want:
                probs.append(f"config {key}={got!r}")
        mcp = config.get("mcp_servers")
        if isinstance(mcp, dict) and any(isinstance(v, dict) and v.get("enabled", True) for v in mcp.values()):
            probs.append("config mcp_servers not empty")
    if features is None:
        probs.append("experimentalFeature/list: no answer")
    else:
        on = {f.get("name") for f in features if isinstance(f, dict) and f.get("enabled")}
        for name in MUST_BE_OFF:
            if name in on:
                probs.append(f"feature {name} on")
    return probs


# ---------- 承認の問い合わせへの答え（中継が決める） ----------
# PO 2026-10-12 改め: 箱の中の計算（シェル・python・その場の script）は自由。断るのは本当の危うさだけ:
# 秘密らしい所を読む・外へ出る道具・中継の内側口を直接たたく・読み取り専用の agent/ を書き換える。
# （sandbox は danger-full-access なので、ふだんは問い合わせ自体が来ない。来たときだけここで答える。）

#: 秘密らしい所（CLI のログイン情報・環境・他のプロセス・鍵）。
SECRET_PATH = re.compile(r"(\.codex/(?:auth\.json|config\.toml|rules)|\.claude/\.credentials|\.credentials\.json|"
                         r"auth\.json|/proc/\d+|/proc/self/environ|/root\b|/etc/shadow|/run/agent|\.ssh/|id_rsa|"
                         r"id_ed25519|(?:^|[\s/'\"])\.env\b|/home/agent/\.codex|/home/agent/\.claude)", re.I)
#: 外へ出る道具（出口の許可リストでも止まるが、使わせない）。
NET_TOOLS = re.compile(r"(?:^|[\s;&|(/])(curl|wget|nc|ncat|netcat|socat|ssh|scp|sftp|telnet|ftp|openssl\s+s_client)"
                       r"(?=\s|$)")
#: python などからの直接の通信（中継の内側口・外への HTTP）。rk を通さない書き込みの抜け道を見つける用。
RAW_HTTP = re.compile(r"(urllib\.request|http\.client|requests\.(?:get|post|put|patch|delete|request)|httpx|aiohttp|"
                      r"socket\.(?:create_connection|socket))", re.I)
RELAY_INNER = re.compile(r"(agent-relay|carelink-agent)(?::8091)?|:8091\b|RK_RELAY_TOKEN", re.I)
#: 読み取り専用の agent/ の写し（スキル・rk・SQL）を書き換えようとする形。
AGENT_DIR_WRITE = re.compile(r"(?:>|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b|\bchmod\b|\btouch\b|"
                             r"write_text|open\([^)]*['\"][wa])[^\n]*/srv/agent\b")

RK_HEAD = re.compile(r"^(?:(?:[A-Z_][A-Z0-9_]*=\S*)\s+)*(?:(?:\./|/srv/agent/)?bin/rk|rk|python3?(?:\.\d+)?\s+-m\s+rk)"
                     r"(?:\s|$)")


def unwrap_shell(cmd: str) -> str:
    """``bash -lc '…'`` / ``/bin/bash -c "…"`` の中身を取り出す（取り出せなければそのまま）。"""
    m = re.match(r"""^\s*(?:/usr)?(?:/bin/)?(?:ba|da|z)?sh\s+-l?c\s+(['"])(.*)\1\s*$""", cmd or "", re.S)
    return m.group(2) if m else (cmd or "")


def is_rk_command(command: str | None) -> bool:
    """コマンドが rk で始まるか（bash -lc の包みは外して見る）。"""
    inner = unwrap_shell(command or "").strip()
    inner = re.sub(r"^cd\s+\S+\s*&&\s*", "", inner)
    return bool(RK_HEAD.match(inner))


def command_risk(command: str | None) -> str | None:
    """コマンドの本当の危うさ（印）。無ければ None。watch.py と承認の答えが同じ物差しを使う。"""
    c = unwrap_shell(command or "")
    if SECRET_PATH.search(c):
        return "secret_read"
    if NET_TOOLS.search(c):
        return "net_tool"
    if RELAY_INNER.search(c) and not is_rk_command(c):
        return "relay_direct"
    if RAW_HTTP.search(c) and not is_rk_command(c):
        return "raw_http"
    if AGENT_DIR_WRITE.search(c):
        return "agent_dir_write"
    return None


def command_decision(command: str | None) -> tuple[str, str]:
    """コマンドの承認の問い合わせへの答え: ("accept"|"decline", 理由の印)。危うさが無ければ accept。"""
    risk = command_risk(command)
    return ("decline", risk) if risk else ("accept", "ok")


def file_change_decision(paths: Iterable[str], writable_roots: Iterable[str]) -> tuple[str, str]:
    """apply_patch の承認: すべて作業フォルダー（とその scratch）の中なら accept。"""
    roots = [r.rstrip("/") + "/" for r in writable_roots if r]
    ps = [p for p in paths if isinstance(p, str)]
    if not ps:
        return "decline", "no_paths"
    for p in ps:
        if ".." in p.replace("\\", "/").split("/") or not any(p.startswith(r) for r in roots):
            return "decline", "outside_work_dir"
    return "accept", "work_dir"


def rules_text() -> str:
    """rakusuke.rules の中身（runner が CODEX_HOME/rules/ に書く）。"""
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "deploy" / "runner" / RULES_NAME
    return p.read_text(encoding="utf-8")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
