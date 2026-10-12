"""中継を起こす: PYTHONPATH=agent python -m relay

公開口（:8090）と内側口（:8091）とサーバーどうしの口（:8092・backend → カルテの下書き）を 1 つのプロセス・
1 つの event loop で動かす（会話の状態を共有するため）。
"""

from __future__ import annotations

import asyncio

import uvicorn

from relay.auth import BackendAuthChecker, office_flag_gate
from relay.checkpoint import create_checkpoint_app
from relay.config import RelayConfig
from relay.execs import PgDumpBackup, PsqlExecutor, rk_config
from relay.karte import KarteDrafter
from relay.public import create_public_app
from relay.service import RelayService
from relay.service_port import create_service_app
from relay.store import SqlStore


def build(cfg: RelayConfig | None = None):
    cfg = cfg or RelayConfig.from_env()
    store = SqlStore(cfg.db_url)
    sql = PsqlExecutor(cfg.ro, row_limit=cfg.sql_row_limit)
    svc = RelayService(cfg, store, sql=sql, backup=PgDumpBackup(cfg.dump, cfg.backup_dir, rk_config(cfg)))
    drafter = KarteDrafter(svc)
    public = create_public_app(svc, BackendAuthChecker(cfg.backend_url), office_flag_gate(sql.run), karte=drafter)
    internal = create_checkpoint_app(svc)
    service = create_service_app(svc, drafter)
    return cfg, public, internal, service


async def main() -> None:
    cfg, public, internal, service = build()
    # 本文は出さない。起こし方と口だけ（秘密は出さない）
    print(f"[relay] launch={cfg.launch} public=:{cfg.public_port} internal=:{cfg.internal_port} "
          f"service=:{cfg.service_port}({'on' if cfg.service_token else 'off'}) "
          f"enabled={int(cfg.enabled)} sandbox_subnet={cfg.sandbox_subnet or '-'}", flush=True)
    servers = [
        uvicorn.Server(uvicorn.Config(public, host=cfg.host, port=cfg.public_port, log_level="info",
                                      access_log=False, proxy_headers=True)),
        uvicorn.Server(uvicorn.Config(internal, host=cfg.host, port=cfg.internal_port, log_level="warning",
                                      access_log=False)),
        uvicorn.Server(uvicorn.Config(service, host=cfg.host, port=cfg.service_port, log_level="warning",
                                      access_log=False)),
    ]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
