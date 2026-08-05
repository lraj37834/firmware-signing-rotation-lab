#!/bin/bash
set -e

TARGET_DIR="/app/publisher"
if [ ! -d "/app" ]; then
    TARGET_DIR="$(pwd)/publisher"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$TARGET_DIR"

cp "$SCRIPT_DIR/release-publisher.mjs" "$TARGET_DIR/release-publisher.mjs"
chmod +x "$TARGET_DIR/release-publisher.mjs" 2>/dev/null || true

echo "Solution successfully published to $TARGET_DIR"
