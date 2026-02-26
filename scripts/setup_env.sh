#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-.venv}

$PYTHON_BIN -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[OK] 环境安装完成"
echo "激活方式: source $VENV_DIR/bin/activate"
echo "环境检查: python scripts/check_env.py"
