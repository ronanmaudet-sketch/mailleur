#!/bin/sh
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.11, 3.12 ou 3.13 est nécessaire.' >&2
    exit 2
fi
exec python3 scripts/launch.py "$@"
