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


def run_import_checks(modules: list[str]) -> tuple[list[str], list[str]]:
    missing_specs = [name for name in modules if importlib.util.find_spec(name) is None]
    import_errors: list[str] = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            import_errors.append(f"{name}: {exc}")
    return missing_specs, import_errors


def run_spec_checks(modules: list[str]) -> list[str]:
    return [name for name in modules if importlib.util.find_spec(name) is None]


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
    parser.add_argument("--strict-full", action="store_true", help="full模式下也执行重依赖导入（默认仅做存在性检查）")
    args = parser.parse_args()

    if args.mode == "ui":
        missing_specs, import_errors = run_import_checks(COMMON_MODULES)
        if missing_specs or import_errors:
            print("[ERROR] 模式=ui 依赖检查失败")
            if missing_specs:
                print("[ERROR] 缺少依赖:", ", ".join(missing_specs))
            if import_errors:
                print("[ERROR] 依赖导入失败:")
                for item in import_errors:
                    print(" -", item)
            print_fix_hints()
            return 1
        print("[OK] 模式=ui 运行环境完整")
        return 0

    # full mode
    missing_common, common_import_errors = run_import_checks(COMMON_MODULES)
    if args.strict_full:
        missing_full, full_import_errors = run_import_checks(FULL_ONLY_MODULES)
    else:
        missing_full = run_spec_checks(FULL_ONLY_MODULES)
        full_import_errors = []

    missing_specs = missing_common + [m for m in missing_full if m not in missing_common]
    import_errors = common_import_errors + full_import_errors

    if missing_specs or import_errors:
        print("[ERROR] 模式=full 依赖检查失败")
        if missing_specs:
            print("[ERROR] 缺少依赖:", ", ".join(missing_specs))
        if import_errors:
            print("[ERROR] 依赖导入失败:")
            for item in import_errors:
                print(" -", item)
        if not args.strict_full:
            print("[INFO] full模式默认不导入重依赖(tensorflow/sklearn)，仅检查是否可发现。")
        print_fix_hints()
        return 1

    if not args.strict_full:
        print("[OK] 模式=full 运行环境完整（重依赖采用存在性检查）")
    else:
        print("[OK] 模式=full 运行环境完整")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
