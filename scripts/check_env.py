#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys

REQUIRED = [
    "numpy",
    "pandas",
    "sklearn",
    "tensorflow",
    "ccxt",
]

missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]

if missing:
    print("[ERROR] 缺少依赖:", ", ".join(missing))
    print("请执行: bash scripts/setup_env.sh")
    sys.exit(1)

print("[OK] 运行环境完整")
