"""中継の設定（環境変数）。秘密（DB のパスワード・エージェント用の管理者の資格情報）は環境変数でだけ受ける。

  AGENT_RELAY_ENABLED        1 で有効（全体の止め。既定 0）
  AGENT_ALLOWED_USER_IDS     試験の間だけ許す管理者の user id（カンマ区切り・空なら管理者全員）
  AGENT_RELAY_ALLOWED_ORIGINS  POST を受ける Origin（カンマ区切り・既定 https://carelink.kaipoke-api.net）
  AGENT_RELAY_BACKEND_URL    backend の /api/v1（既定 http://carelink-backend:8000/api/v1）
  AGENT_RELAY_DB_URL         agent スキーマへの接続（SQLAlchemy の URL・既定 sqlite の data/relay.db）
  AGENT_RELAY_DATA_DIR       作業の置き場所（既定 /data）: sessions/<id>/・runs/・rakusuke/（トークン）
  AGENT_RELAY_AGENT_DIR      agent/ の写し（CLI の cwd・既定 このリポジトリの agent/）
  AGENT_RELAY_INTERNAL_URL   箱の rk から見た内側口（RK_RELAY_URL に渡す・既定 http://carelink-agent:8091）
  AGENT_RELAY_PUBLIC_PORT / AGENT_RELAY_INTERNAL_PORT / AGENT_RELAY_HOST（8090 / 8091 / 0.0.0.0）
  AGENT_MAX_CLI              同時に動かす CLI の数（既定 1・上限 2）
  AGENT_IDLE_SECONDS         使わない CLI を終えるまでの秒（既定 1200）
  AGENT_APPROVAL_TTL         承認番号の寿命（秒・既定 900）
  AGENT_MIN_MEM_MB           会話を始める前に要る空きメモリ（既定 900）
  AGENT_SENDS_PER_HOUR       1 人 1 時間の送信の上限（既定 30）
  AGENT_CLAUDE_BIN           claude の実行ファイル（既定 claude）
  AGENT_DEFAULT_PROVIDER     新しい相談の AI（codex = ChatGPT〔既定・PO 2026-10-12〕／claude）。画面から選ぶこともできる
  AGENT_PROVIDERS            使ってよい AI（カンマ区切り・既定 codex,claude）
  AGENT_CODEX_BIN            codex の実行ファイル（direct のとき・既定 codex）
  AGENT_RUNNER_SESSIONS_DIR  箱の runner の会話の置き場所（Codex の作業フォルダーの元・既定 /data/sessions）
  AGENT_RUNNER_AGENT_DIR     箱の中の agent/ の写し（Codex の thread の cwd・既定 /srv/agent）
  AGENT_HTTPS_PROXY          箱の CLI に渡す出口プロキシ（既定 http://carelink-agent:3128）
  AGENT_ALLOWED_KEY_SOURCES  init の apiKeySource として許すもの（既定 none）
  AGENT_RO_PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD    読み取り専用 SQL（agent_ro）
  AGENT_DUMP_PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD  バックアップ（agent_dump）
  AGENT_BACKUP_DIR           バックアップの置き場所（既定 /opt/carelink/backups/agent）
  RK_IDENTIFIER / RK_PASSWORD  エージェント専用の管理者ユーザー（中継だけが持つ書き込みの鍵）

起こし方（CLI をどう起こすか）:
  AGENT_RUNNER_SOCKET        箱の runner の unix ソケット（例 /run/agent/runner.sock）。あれば「runner」で起こす
  AGENT_RELAY_LAUNCH         direct / runner（既定: AGENT_RUNNER_SOCKET があれば runner、無ければ direct）。
                             direct = 中継のプロセスから claude を直接起こす（手元の開発・試験だけ）
  AGENT_SANDBOX_SUBNET       箱の網（例 172.31.250.0/24）。内側口はこの範囲からだけ・公開口はこの範囲以外からだけ受ける
  AGENT_URL_SIGNING_KEY      印刷用の署名つき URL の鍵（無ければ起動ごとに作る＝再起動で古い URL は無効）
  AGENT_LEGACY_OWNER_USER_ID 人ごとの席にする前の共有のログイン（箱の /home/agent/.codex・.claude）の持ち主の user id。
                             あれば最初にその人の席へ移す（席が空のときだけ・1 回きり）。無ければ agent.settings の
                             legacy_owner_user_id を見る。どちらも無ければ移さない（コードに user id は書かない）

agent/deploy（docker-compose.agent.yml・set-role-passwords.sh）の名前も受ける（左が無ければ右）:
  AGENT_RELAY_BACKEND_URL <- AGENT_BACKEND_URL、AGENT_RELAY_ALLOWED_ORIGINS <- AGENT_PUBLIC_ORIGIN、
  AGENT_RELAY_PUBLIC_PORT <- AGENT_PUBLIC_PORT、AGENT_RELAY_INTERNAL_PORT <- AGENT_INNER_PORT、
  AGENT_MAX_CLI <- AGENT_MAX_PROCS、AGENT_MIN_MEM_MB <- AGENT_MIN_MEM_AVAILABLE_MB、
  AGENT_RELAY_DB_URL <- AGENT_RELAY_DSN（postgresql://…）、AGENT_RO_PG* <- AGENT_RO_DSN、AGENT_DUMP_PG* <- AGENT_DUMP_DSN、
  RK_IDENTIFIER / RK_PASSWORD <- AGENT_BACKEND_IDENTIFIER / AGENT_BACKEND_PASSWORD、PGHOST・PGDATABASE の既定 <- AGENT_PG_HOST・AGENT_PG_DB

apiKeySource（init）は段 3 では "none"（サブスクのログイン）だけを許す。AGENT_ALLOWED_KEY_SOURCES に他の値を
書いても無視する（API キーで動かす判断は段 6 の前に PO が決める・設計 §2-4）。
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

AGENT_DIR = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env2(name: str, alt: str, default: str) -> str:
    """name が無ければ alt（agent/deploy の名前）、どちらも無ければ default。"""
    return _env(name, _env(alt, default))


def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


#: 選べる AI（PO 2026-10-12: お客様は ChatGPT の契約が多い → 既定は Codex。Claude も選べる）。
PROVIDERS = ("codex", "claude")

#: 段 3 で許す apiKeySource（サブスクのログインだけ）。
SUBSCRIPTION_KEY_SOURCES = frozenset({"none"})


def sqlalchemy_url(dsn: str) -> str:
    """postgresql://… を SQLAlchemy の psycopg 3 の URL に。sqlite などはそのまま。"""
    for head in ("postgresql://", "postgres://"):
        if dsn.startswith(head):
            return "postgresql+psycopg://" + dsn[len(head):]
    return dsn


@dataclass
class PgConn:
    host: str = "carelink-postgres"
    port: str = "5432"
    user: str = ""
    database: str = "carelink"
    password: str = ""

    @classmethod
    def from_dsn(cls, dsn: str) -> "PgConn":
        u = urlsplit(dsn)
        return cls(host=u.hostname or "carelink-postgres", port=str(u.port or 5432), user=unquote(u.username or ""),
                   database=(u.path or "/carelink").lstrip("/") or "carelink", password=unquote(u.password or ""))

    @classmethod
    def from_env(cls, prefix: str, user: str) -> "PgConn":
        dsn = _env(f"{prefix}DSN", "")
        if dsn:
            return cls.from_dsn(dsn)
        return cls(host=_env(f"{prefix}PGHOST", _env("AGENT_PG_HOST", "carelink-postgres")),
                   port=_env(f"{prefix}PGPORT", "5432"), user=_env(f"{prefix}PGUSER", user),
                   database=_env(f"{prefix}PGDATABASE", _env("AGENT_PG_DB", "carelink")),
                   password=os.environ.get(f"{prefix}PGPASSWORD", ""))


@dataclass
class RelayConfig:
    enabled: bool = False
    allowed_user_ids: set[str] = field(default_factory=set)
    allowed_origins: list[str] = field(default_factory=lambda: ["https://carelink.kaipoke-api.net"])
    backend_url: str = "http://carelink-backend:8000/api/v1"
    db_url: str = ""
    data_dir: Path = Path("/data")
    agent_dir: Path = AGENT_DIR
    internal_url: str = "http://carelink-agent:8091"
    host: str = "0.0.0.0"
    public_port: int = 8090
    internal_port: int = 8091
    max_cli: int = 1
    idle_seconds: int = 1200
    approval_ttl: int = 900
    min_mem_mb: int = 900
    sends_per_hour: int = 30
    turn_timeout: int = 900
    claude_bin: str = "claude"
    codex_bin: str = "codex"
    default_provider: str = "codex"
    providers: tuple[str, ...] = ("codex", "claude")
    runner_sessions_dir: str = "/data/sessions"
    runner_agent_dir: str = "/srv/agent"
    https_proxy: str = "http://carelink-agent:3128"
    allowed_key_sources: set[str] = field(default_factory=lambda: set(SUBSCRIPTION_KEY_SOURCES))
    ro: PgConn = field(default_factory=lambda: PgConn(user="agent_ro"))
    dump: PgConn = field(default_factory=lambda: PgConn(user="agent_dump"))
    backup_dir: Path = Path("/opt/carelink/backups/agent")
    sql_row_limit: int = 5000
    launch: str = "direct"                 # direct（手元・試験）/ runner（箱の runner へ unix ソケット）
    runner_socket: str = ""
    sandbox_subnet: str = ""
    url_signing_key: str = field(default_factory=lambda: secrets.token_hex(32))
    cli_home: str = "/home/agent"
    legacy_owner_user_id: str = ""

    def in_sandbox(self, host: str | None) -> bool:
        """接続元が箱の網か（AGENT_SANDBOX_SUBNET が無ければ判定しない＝False）。"""
        if not self.sandbox_subnet or not host:
            return False
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(self.sandbox_subnet, strict=False)
        except ValueError:
            return False

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @classmethod
    def from_env(cls) -> "RelayConfig":
        data = Path(_env("AGENT_RELAY_DATA_DIR", "/data"))
        # agent/deploy の秘密ファイルの名前 → rk の名前（エージェント専用の管理者・中継の中の rk が使う）
        for rk_name, deploy_name in (("RK_IDENTIFIER", "AGENT_BACKEND_IDENTIFIER"),
                                     ("RK_PASSWORD", "AGENT_BACKEND_PASSWORD")):
            if not os.environ.get(rk_name) and os.environ.get(deploy_name):
                os.environ[rk_name] = os.environ[deploy_name]
        dsn = _env("AGENT_RELAY_DSN", "")
        socket_path = _env("AGENT_RUNNER_SOCKET", "")
        launch = _env("AGENT_RELAY_LAUNCH", "runner" if socket_path else "direct")
        if launch not in ("direct", "runner"):
            raise ValueError("AGENT_RELAY_LAUNCH は direct か runner です")
        if launch == "runner" and not socket_path:
            raise ValueError("AGENT_RELAY_LAUNCH=runner には AGENT_RUNNER_SOCKET が要ります")
        signing = _env("AGENT_URL_SIGNING_KEY", "")
        origins = _env2("AGENT_RELAY_ALLOWED_ORIGINS", "AGENT_PUBLIC_ORIGIN", "https://carelink.kaipoke-api.net")
        key_sources = set(_csv("AGENT_ALLOWED_KEY_SOURCES", "none")) & SUBSCRIPTION_KEY_SOURCES
        providers = tuple(p for p in _csv("AGENT_PROVIDERS", "codex,claude") if p in PROVIDERS) or ("codex",)
        default_provider = _env("AGENT_DEFAULT_PROVIDER", "codex")
        if default_provider not in providers:
            raise ValueError(f"AGENT_DEFAULT_PROVIDER={default_provider} は AGENT_PROVIDERS にありません")
        return cls(
            enabled=_env("AGENT_RELAY_ENABLED", "0") == "1",
            allowed_user_ids=set(_csv("AGENT_ALLOWED_USER_IDS")),
            allowed_origins=[x.strip() for x in origins.split(",") if x.strip()],
            backend_url=_env2("AGENT_RELAY_BACKEND_URL", "AGENT_BACKEND_URL", "http://carelink-backend:8000/api/v1"),
            db_url=_env("AGENT_RELAY_DB_URL",
                        sqlalchemy_url(dsn) if dsn else f"sqlite:///{(data / 'relay.db').as_posix()}"),
            data_dir=data,
            agent_dir=Path(_env("AGENT_RELAY_AGENT_DIR", str(AGENT_DIR))),
            internal_url=_env("AGENT_RELAY_INTERNAL_URL", "http://carelink-agent:8091"),
            host=_env("AGENT_RELAY_HOST", "0.0.0.0"),
            public_port=int(_env2("AGENT_RELAY_PUBLIC_PORT", "AGENT_PUBLIC_PORT", "8090")),
            internal_port=int(_env2("AGENT_RELAY_INTERNAL_PORT", "AGENT_INNER_PORT", "8091")),
            max_cli=max(1, min(2, int(_env2("AGENT_MAX_CLI", "AGENT_MAX_PROCS", "1")))),
            idle_seconds=int(_env("AGENT_IDLE_SECONDS", "1200")),
            approval_ttl=int(_env("AGENT_APPROVAL_TTL", "900")),
            min_mem_mb=int(_env2("AGENT_MIN_MEM_MB", "AGENT_MIN_MEM_AVAILABLE_MB", "900")),
            sends_per_hour=int(_env("AGENT_SENDS_PER_HOUR", "30")),
            claude_bin=_env("AGENT_CLAUDE_BIN", "claude"),
            codex_bin=_env("AGENT_CODEX_BIN", "codex"),
            default_provider=default_provider,
            providers=providers,
            runner_sessions_dir=_env("AGENT_RUNNER_SESSIONS_DIR", "/data/sessions"),
            runner_agent_dir=_env("AGENT_RUNNER_AGENT_DIR", "/srv/agent"),
            https_proxy=_env("AGENT_HTTPS_PROXY", "http://carelink-agent:3128"),
            allowed_key_sources=key_sources or set(SUBSCRIPTION_KEY_SOURCES),  # サブスクだけ
            ro=PgConn.from_env("AGENT_RO_", "agent_ro"),
            dump=PgConn.from_env("AGENT_DUMP_", "agent_dump"),
            backup_dir=Path(_env("AGENT_BACKUP_DIR", "/opt/carelink/backups/agent")),
            launch=launch,
            runner_socket=socket_path,
            sandbox_subnet=_env("AGENT_SANDBOX_SUBNET", ""),
            url_signing_key=signing or secrets.token_hex(32),
            cli_home=_env("AGENT_CLI_HOME", "/home/agent"),
            legacy_owner_user_id=_env("AGENT_LEGACY_OWNER_USER_ID", ""),
        )
