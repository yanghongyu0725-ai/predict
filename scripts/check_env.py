#!/usr/bin/env python3
from __future__ import annotations

import importlib
import importlib.util
import platform
import sys

REQUIRED_SPECS = [
    "numpy",
    "pandas",
    "sklearn",
    "tensorflow",
    "ccxt",
    "plotly",
    "flask",
    "dateutil",
]

# Import checks catch broken transitive dependencies (for example pandas without python-dateutil).
REQUIRED_IMPORTS = [
    "numpy",
    "pandas",
    "sklearn",
    "tensorflow",
    "ccxt",
    "plotly",
    "flask",
    "dateutil",
]

missing_specs = [name for name in REQUIRED_SPECS if importlib.util.find_spec(name) is None]
import_errors: list[str] = []

for name in REQUIRED_IMPORTS:
    try:
        importlib.import_module(name)
    except Exception as exc:
        import_errors.append(f"{name}: {exc}")

if missing_specs or import_errors:
    if missing_specs:
        print("[ERROR] 缺少依赖:", ", ".join(missing_specs))
    if import_errors:
        print("[ERROR] 依赖导入失败:")
        for item in import_errors:
            print(" -", item)
    if platform.system().lower().startswith("win"):
        print("Windows CMD: scripts\\setup_env.bat")
        print("Windows PowerShell: powershell -ExecutionPolicy Bypass -File scripts/setup_env.ps1")
        print("建议补装: python -m pip install --upgrade --force-reinstall python-dateutil pandas")
    else:
        print("Linux/macOS: bash scripts/setup_env.sh")
        print("建议补装: python -m pip install --upgrade --force-reinstall python-dateutil pandas")
    sys.exit(1)

print("[OK] 运行环境完整")
