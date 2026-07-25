#!/usr/bin/env bash
set -euo pipefail

# Build standalone Elidia CLI binary for the current platform.
#
# Prerequisites:
#   pip install pyinstaller
#
# Usage:
#   ./packaging/build.sh
#
# Output:
#   dist/elidia  (or dist/elidia.exe on Windows)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Elidia CLI Build ==="
echo "Platform: $(uname -s)-$(uname -m)"
echo "Python: $(python3 --version)"
echo ""

cd "$PROJECT_ROOT"

if ! command -v pyinstaller &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller
fi

echo "Building standalone binary..."
pyinstaller packaging/elidia.spec --clean --noconfirm

BINARY="dist/elidia"
if [[ "$(uname -s)" == *MINGW* ]] || [[ "$(uname -s)" == *MSYS* ]] || [[ "$(uname -s)" == *CYGWIN* ]]; then
    BINARY="dist/elidia.exe"
fi

if [ -f "$BINARY" ]; then
    SIZE=$(du -h "$BINARY" | cut -f1)
    echo ""
    echo "Build successful!"
    echo "  Binary: $BINARY"
    echo "  Size: $SIZE"
    echo ""
    echo "Test: $BINARY --version"
    "$BINARY" --version || true
else
    echo "Build failed — binary not found at $BINARY"
    exit 1
fi
