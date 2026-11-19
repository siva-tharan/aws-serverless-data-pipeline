#!/usr/bin/env bash
# Builds the Lambda deployment folder (build/package):
#   - function code from src/
#   - PyMySQL (pure Python, so it runs on the arm64 Lambda runtime as-is)
#   - the Amazon RDS CA bundle, used to verify the database's TLS certificate
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGE_DIR="$ROOT/build/package"

# Pick a working Python (on Windows "python3" may be a Microsoft Store placeholder).
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3 was not found. Install it or set PYTHON=/path/to/python." >&2
  exit 1
fi

echo "Running unit tests"
(cd "$ROOT" && "$PYTHON" -m unittest discover -s tests)

echo "Building $PACKAGE_DIR"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

"$PYTHON" -m pip install --quiet --disable-pip-version-check \
  --requirement "$ROOT/src/requirements.txt" --target "$PACKAGE_DIR"
cp "$ROOT"/src/*.py "$PACKAGE_DIR/"
curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
  -o "$PACKAGE_DIR/rds-global-bundle.pem"

echo "Build ready: $PACKAGE_DIR"
