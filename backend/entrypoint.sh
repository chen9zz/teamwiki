#!/bin/sh
set -eu
# Only the API uses this command. Clean metrics once in the parent, before forks.
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    case "$PROMETHEUS_MULTIPROC_DIR" in
        /tmp/teamwiki-metrics) ;;
        *) echo "PROMETHEUS_MULTIPROC_DIR must be /tmp/teamwiki-metrics" >&2; exit 1 ;;
    esac
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
    rm -f "$PROMETHEUS_MULTIPROC_DIR"/*.db
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --workers "${API_WORKERS:-2}" --timeout-keep-alive 5 --backlog 256
