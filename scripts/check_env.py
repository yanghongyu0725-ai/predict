#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import importlib.util
import platform
import sys

COMMON_MODULES = [
    "numpy",
    "pandas",
    "ccxt",
    "plotly",
    "flask",
    "dateutil",
]

FULL_ONLY_MODULES = [
    "sklearn",
    "tensorflow",
]


def run_checks(modules: list[str]) -> tuple[list[str], list[str]]:
    missing_specs = [name for name in modules if importlib.util.find_spec(name) is None]
    import_errors: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            import_errors.append(f"{name}: {exc}")
    return missing_specs, import_errors


def print_fix_hints() -> None:
    if platform.system().lower().startswith("win"):
        print("Windows CMD: scripts\\setup_env.bat")
        print("Windows PowerShell: powershell -ExecutionPolicy Bypass -File scripts/setup_env.ps1")
    else:
        print("Linux/macOS: bash scripts/setup_env.sh")
    print("建议补装(UI): python -m pip install --upgrade --force-reinstall python-dateutil pandas flask ccxt plotly")
    print("建议补装(全量): python -m pip install --upgrade --force-reinstall -r requirements.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description="Environment dependency checker")
    parser.add_argument("--mode", choices=["ui", "full"], default="full", help="ui=UI启动依赖，full=策略全量依赖")
    args = parser.parse_args()

    modules = COMMON_MODULES if args.mode == "ui" else COMMON_MODULES + FULL_ONLY_MODULES
    missing_specs, import_errors = run_checks(modules)

    if missing_specs or import_errors:
        print(f"[ERROR] 模式={args.mode} 依赖检查失败")
        if missing_specs:
            print("[ERROR] 缺少依赖:", ", ".join(missing_specs))
        if import_errors:
            print("[ERROR] 依赖导入失败:")
            for item in import_errors:
                print(" -", item)
        print_fix_hints()
        return 1

    print(f"[OK] 模式={args.mode} 运行环境完整")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
