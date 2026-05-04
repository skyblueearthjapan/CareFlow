"""Export the live FastAPI OpenAPI schema as JSON.

Usage:
    python scripts/export_openapi.py [output_path]

Defaults output to `openapi.json` at the backend root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    # Ensure backend/ is on sys.path so `app` is importable when running
    # this script directly (no package install required).
    backend_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(backend_root))

    from app.main import create_app  # imported lazily after path tweak

    output = Path(argv[1]) if len(argv) > 1 else backend_root / "openapi.json"
    app = create_app()
    schema = app.openapi()

    output.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OpenAPI schema written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
