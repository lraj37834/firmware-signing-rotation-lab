#!/bin/bash
set -u

LOG_DIR="/logs/verifier"
if [ ! -d "/logs" ]; then
    LOG_DIR="$(pwd)/logs/verifier"
fi
mkdir -p "$LOG_DIR"

# Ensure reward.txt is always written even on unexpected script failure.
trap 'echo 0 > "$LOG_DIR/reward.txt"' EXIT

TEST_FILE="/tests/test_outputs.py"
if [ ! -f "$TEST_FILE" ]; then
    TEST_FILE="$(pwd)/tests/test_outputs.py"
fi

PYTHON_CMD="python"
if ! command -v python &> /dev/null; then
    PYTHON_CMD="python3"
fi

# Run pytest; use --ctrf only if the plugin is available.
CTRF_ARGS=""
if $PYTHON_CMD -c "import pytest_ctrf" 2>/dev/null; then
    CTRF_ARGS="--ctrf $LOG_DIR/ctrf.json"
fi

# shellcheck disable=SC2086
$PYTHON_CMD -m pytest $CTRF_ARGS "$TEST_FILE" -rA
code=$?

echo "pytest exit code: ${code}"

# Write reward file and override the EXIT trap.
if [ "$code" -eq 0 ]; then
  echo 1 > "$LOG_DIR/reward.txt"
else
  echo 0 > "$LOG_DIR/reward.txt"
fi

trap - EXIT
exit $code
