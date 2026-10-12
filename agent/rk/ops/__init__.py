"""操作の部品（許可リスト）。このフォルダの全モジュールを読み込み、@register された操作だけが反映できる。"""

import importlib
import pkgutil

from rk.opsbase import IMPORT_ERRORS

for _m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    try:
        importlib.import_module(f"{__name__}.{_m.name}")
    except Exception as _e:  # 1 つの部品の誤りで全部が止まらないように（plan check で警告）
        IMPORT_ERRORS.append(f"{_m.name}: {type(_e).__name__}: {_e}")
