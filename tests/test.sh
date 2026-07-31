#!/bin/bash
set -u

LOG_DIR="/logs/verifier"
if [ ! -d "/logs" ]; then
    LOG_DIR="$(pwd)/logs/verifier"
fi
mkdir -p "$LOG_DIR"

TEST_FILE="/tests/test_outputs.py"
if [ ! -f "$TEST_FILE" ]; then
    TEST_FILE="$(pwd)/tests/test_outputs.py"
fi

PYTHON_CMD="python"
if ! command -v python &> /dev/null; then
    PYTHON_CMD="python3"
fi

$PYTHON_CMD -m pytest --ctrf "$LOG_DIR/ctrf.json" "$TEST_FILE" -rA
code=$?

echo "pytest exit code: ${code}"

if [ "$code" -eq 0 ]; then
  echo 1 > "$LOG_DIR/reward.txt"
else
  echo 0 > "$LOG_DIR/reward.txt"
fi

exit $code
