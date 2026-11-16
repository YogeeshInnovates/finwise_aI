#!/usr/bin/env sh
set -eu

URL="${1:-http://127.0.0.1:8000/health}"
curl -fsS "$URL"
