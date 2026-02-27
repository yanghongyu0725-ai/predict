#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import platform
import sys

REQUIRED = [
    "numpy",
    "pandas",
    "sklearn",
    "tensorflow",
    "ccxt",
    "plotly",
    "flask",
]

missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]

if missing:
    print("[ERROR] 缺少依赖:", ", ".join(missing))
    if platform.system().lower().startswith("win"):
        print("Windows CMD: scripts\\setup_env.bat")
        print("Windows PowerShell: powershell -ExecutionPolicy Bypass -File scripts/setup_env.ps1")
    else:
        print("Linux/macOS: bash scripts/setup_env.sh")
    sys.exit(1)

print("[OK] 运行环境完整")
