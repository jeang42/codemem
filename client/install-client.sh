#!/usr/bin/env bash
# Install the codemem client on Linux/macOS. All the work is in install_client.py (stdlib only).
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install_client.py"
