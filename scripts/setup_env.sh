#!/usr/bin/env bash
set -eu

PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-.venv}

"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "[OK] 环境安装完成 (Linux/macOS/Git-Bash)"
echo "激活方式: . $VENV_DIR/bin/activate"
echo "环境检查: python scripts/check_env.py"
