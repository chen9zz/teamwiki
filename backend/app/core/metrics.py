"""Small metrics with fixed, tenant-independent label sets.

The API container sets PROMETHEUS_MULTIPROC_DIR before Python starts, sharing
counters across its Uvicorn workers. Without that environment setting this is a
single-process diagnostic endpoint. Containers need separate scrape targets.
"""

import os
from pathlib import Path

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "teamwiki_http_requests_total", "Completed API requests", ("method", "route", "status"), registry=REGISTRY
)
HTTP_DURATION = Histogram(
    "teamwiki_http_request_duration_seconds",
    "API request duration",
    ("method", "route", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
    registry=REGISTRY,
)
CACHE_COMMANDS = Counter(
    "teamwiki_cache_commands_total", "Cache command outcomes", ("command", "outcome"), registry=REGISTRY
)
DB_FAILURES = Counter(
    "teamwiki_database_failures_total", "Retryable database capacity failures", ("reason",), registry=REGISTRY
)
HTTP_INFLIGHT = Gauge(
    "teamwiki_http_inflight",
    "Requests admitted in API workers",
    registry=REGISTRY,
    multiprocess_mode="livesum",
)
HTTP_REJECTED = Counter("teamwiki_http_rejected_total", "Requests rejected at admission", registry=REGISTRY)


def render_metrics() -> tuple[bytes, str]:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        # Uvicorn has no child_exit hook for SIGKILL. Remove only dead live-gauge
        # files in this API container's PID namespace; counter history is kept.
        for file in Path(os.environ["PROMETHEUS_MULTIPROC_DIR"]).glob("gauge_live*_*.db"):
            pid = int(file.stem.rsplit("_", 1)[1])
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                multiprocess.mark_process_dead(pid)
            except PermissionError:
                pass
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return generate_latest(registry), CONTENT_TYPE_LATEST


def close_metrics():
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(os.getpid())
