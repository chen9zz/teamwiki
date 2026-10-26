"""Repeatable, isolated HTTP concurrency smoke benchmark (requires httpx).

Every invocation registers a fresh owner and workspace through the public API.
No demo credentials, direct SQL writes, Redis flushes, or existing pages are used.
Run against a disposable benchmark database; fixture data intentionally remains
available for investigation. This closed-loop smoke test is not a capacity SLA.
"""

import argparse
import asyncio
import json
import math
import platform
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


def percentile(values, fraction):
    """Linearly interpolate sorted observations, including both endpoints."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def summarize(samples, elapsed, expected_statuses):
    statuses = Counter(str(status) for status, _ in samples)
    accepted = sum(status in expected_statuses for status, _ in samples)
    latencies = [latency for _, latency in samples]
    return {
        "requests": len(samples),
        "elapsed_seconds": round(elapsed, 4),
        "rps": round(len(samples) / elapsed, 3) if elapsed else 0,
        "successful_rps": round(accepted / elapsed, 3) if elapsed else 0,
        "status_counts": dict(sorted(statuses.items())),
        "unexpected_requests": len(samples) - accepted,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else None,
        },
    }


def hot_batch_valid(statuses, users):
    return len(statuses) == users and Counter(statuses) == Counter({200: 1, 409: users - 1})


def document_content(index, edit=0):
    return (
        f"# Benchmark {index:04d}\n\n"
        f"Concurrency benchmark document {index:04d}; sequential edit {edit:08d}.\n\n"
        "This fixed small document exercises authenticated page reads, revisions, "
        "audit writes and link maintenance. The fixture is identical across runs.\n\n"
        "[[Benchmark 0000]]\n"
    )


async def require_json(client, method, path, status=200, **kwargs):
    response = await client.request(method, path, **kwargs)
    if response.status_code != status:
        raise RuntimeError(
            f"{method} {path}: expected {status}, got {response.status_code}: {response.text[:300]}"
        )
    return response.json()


async def seed(client, pages):
    run_id = uuid4().hex
    await require_json(
        client,
        "POST",
        "/api/auth/register",
        201,
        json={
            "email": f"benchmark-{run_id}@example.com",
            "password": uuid4().hex,
            "name": "Benchmark owner",
        },
    )
    workspace = await require_json(
        client,
        "POST",
        "/api/workspaces",
        201,
        json={"name": f"Concurrency benchmark {run_id}"},
    )
    prefix = f"/api/workspaces/{workspace['id']}"
    documents = []
    for index in range(pages):
        documents.append(
            await require_json(
                client,
                "POST",
                prefix + "/pages",
                201,
                json={
                    "title": f"Benchmark {index:04d}",
                    "content": document_content(index),
                    "tags": ["benchmark"],
                },
            )
        )
    return prefix, documents


async def warm_reads(client, prefix, documents, users):
    """Populate only this invocation's versioned keys; auth is outside timing."""
    slots = asyncio.Semaphore(users)

    async def read(page):
        async with slots:
            await require_json(client, "GET", prefix + f"/pages/{page['id']}")

    await asyncio.gather(*(read(page) for page in documents))


async def timed_request(client, method, path, **kwargs):
    started = time.perf_counter()
    try:
        response = await client.request(method, path, **kwargs)
        return response, (response.status_code, (time.perf_counter() - started) * 1000)
    except httpx.HTTPError as error:
        return None, (type(error).__name__, (time.perf_counter() - started) * 1000)


async def verify_history(client, prefix, page, expected_version):
    current = await require_json(client, "GET", prefix + f"/pages/{page['id']}")
    versions = []
    offset = 0
    while True:
        revisions = await require_json(
            client,
            "GET",
            prefix + f"/pages/{page['id']}/revisions",
            params={"limit": 200, "offset": offset},
        )
        versions.extend(revision["version"] for revision in revisions)
        if len(revisions) < 200:
            break
        offset += 200
    valid = current["version"] == expected_version and sorted(versions) == list(
        range(1, expected_version + 1)
    )
    return {
        "page_id": page["id"],
        "expected_version": expected_version,
        "actual_version": current["version"],
        "revision_count": len(versions),
        "contiguous_history": valid,
    }


async def steady_scenario(client, prefix, documents, users, duration, write):
    """One in-flight request per stream; writes own disjoint pages and versions."""
    ready = asyncio.Queue()
    gate = asyncio.Event()
    state = {}
    samples = []
    successes = [0] * users
    error_examples = []

    async def worker(index):
        version = documents[index]["version"]
        sequence = 0
        await ready.put(True)
        await gate.wait()
        while time.perf_counter() < state["deadline"]:
            page = documents[index] if write else documents[(index + sequence * users) % len(documents)]
            path = prefix + f"/pages/{page['id']}"
            if write:
                response, sample = await timed_request(
                    client,
                    "PATCH",
                    path,
                    json={
                        "version": version,
                        "title": page["title"],
                        "content": document_content(index, sequence + 1),
                    },
                )
            else:
                response, sample = await timed_request(client, "GET", path)
            samples.append(sample)
            if response is not None and response.status_code == 200:
                successes[index] += 1
                if write:
                    version = response.json()["version"]
            elif len(error_examples) < 5:
                error_examples.append(
                    {
                        "worker": index,
                        "status": sample[0],
                        "body": response.text[:300] if response is not None else "transport failure",
                    }
                )
            sequence += 1

    tasks = [asyncio.create_task(worker(index)) for index in range(users)]
    for _ in tasks:
        await ready.get()
    started = time.perf_counter()
    state["deadline"] = started + duration
    gate.set()
    await asyncio.gather(*tasks)
    result = summarize(samples, time.perf_counter() - started, {200})
    result["scenario"] = "different_page_writes" if write else "many_page_reads"
    result["error_examples"] = error_examples
    result["verification"] = []
    if write:
        for index in range(users):
            result["verification"].append(
                await verify_history(
                    client,
                    prefix,
                    documents[index],
                    documents[index]["version"] + successes[index],
                )
            )
    result["passed"] = result["unexpected_requests"] == 0 and all(
        item["contiguous_history"] for item in result["verification"]
    )
    return result


async def hot_scenario(client, prefix, page, users, batches):
    samples = []
    outcomes = []
    version = page["version"]
    started = time.perf_counter()
    for batch in range(batches):
        gate = asyncio.Barrier(users)

        async def attempt(index, current_gate=gate, current_version=version, current_batch=batch):
            await current_gate.wait()
            return await timed_request(
                client,
                "PATCH",
                prefix + f"/pages/{page['id']}",
                json={
                    "version": current_version,
                    "content": document_content(9999, current_batch * users + index + 1),
                },
            )

        responses = await asyncio.gather(*(attempt(index) for index in range(users)))
        batch_samples = [sample for _, sample in responses]
        samples.extend(batch_samples)
        valid = hot_batch_valid([status for status, _ in batch_samples], users)
        outcomes.append(
            {
                "submitted_version": version,
                "status_counts": dict(Counter(str(status) for status, _ in batch_samples)),
                "exactly_one_winner": valid,
            }
        )
        # Do not hide a failed batch by resynchronizing and continuing.
        if not valid:
            break
        version += 1
    result = summarize(samples, time.perf_counter() - started, {200, 409})
    result["scenario"] = "hot_page_compare_and_swap"
    result["batches"] = outcomes
    result["verification"] = await verify_history(client, prefix, page, version)
    result["passed"] = (
        len(outcomes) == batches
        and all(item["exactly_one_winner"] for item in outcomes)
        and (result["verification"]["contiguous_history"])
    )
    return result


async def run(args):
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=args.timeout,
        limits=httpx.Limits(max_connections=args.users, max_keepalive_connections=args.users),
        trust_env=False,
    ) as client:
        readiness = await require_json(client, "GET", "/api/ready")
        if readiness.get("database") != "ok" or readiness.get("cache") != "ok":
            raise RuntimeError(f"Warm-cache comparison requires healthy database and cache: {readiness}")
        seed_start = time.perf_counter()
        prefix, documents = await seed(client, args.pages)
        seeded_seconds = time.perf_counter() - seed_start
        results = []
        if args.scenario in {"all", "read"}:
            await warm_reads(client, prefix, documents, args.users)
            results.append(await steady_scenario(client, prefix, documents, args.users, args.duration, False))
        if args.scenario in {"all", "write"}:
            await warm_reads(client, prefix, documents, args.users)
            results.append(await steady_scenario(client, prefix, documents, args.users, args.duration, True))
        if args.scenario in {"all", "hot"}:
            results.append(await hot_scenario(client, prefix, documents[-1], args.users, args.hot_batches))
    return {
        "schema_version": 1,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "label": args.label,
        "base_url": args.base_url,
        "load_generator": {
            "python": platform.python_version(),
            "httpx": httpx.__version__,
            "platform": platform.platform(),
            "mode": "closed_loop_no_think_time",
        },
        "declared_server": {
            "api_workers": args.api_workers,
            "db_pool_size_per_worker": args.db_pool_size,
            "db_max_overflow_per_worker": args.db_max_overflow,
            "notes": args.notes,
        },
        "fixture": {
            "workspace_id": prefix.split("/")[-1],
            "pages": args.pages,
            "document_bytes_initial": len(document_content(0).encode()),
            "links_per_page": 1,
            "created_via_public_api": True,
            "seed_seconds": round(seeded_seconds, 3),
        },
        "parameters": {
            "concurrent_streams": args.users,
            "steady_seconds_per_scenario": args.duration,
            "hot_batches": args.hot_batches,
            "request_timeout_seconds": args.timeout,
            "cache": "reads warmed for every page; writes invalidate their previous version",
            "authentication": "fresh owner session established before timing",
        },
        "results": results,
        "passed": all(result["passed"] for result in results),
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=16, help="Concurrent authenticated request streams")
    parser.add_argument("--duration", type=float, default=10, help="Seconds per read/write scenario")
    parser.add_argument("--pages", type=int, default=100)
    parser.add_argument("--scenario", choices=("all", "read", "write", "hot"), default="all")
    parser.add_argument("--hot-batches", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--label", default="unlabelled")
    parser.add_argument(
        "--api-workers",
        type=int,
        default=2,
        help="Recorded declaration, not discovered",
    )
    parser.add_argument(
        "--db-pool-size",
        type=int,
        default=5,
        help="Recorded declaration, not discovered",
    )
    parser.add_argument(
        "--db-max-overflow",
        type=int,
        default=10,
        help="Recorded declaration, not discovered",
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.users < 2 or args.duration <= 0 or args.timeout <= 0 or args.hot_batches < 1:
        parser.error("users must be >= 2; duration, timeout and hot-batches must be positive")
    if args.pages < args.users + 1:
        parser.error("pages must be >= users + 1 so write streams and the hot page are disjoint")
    return args


def main():
    args = parse_args()
    try:
        report = asyncio.run(run(args))
    except (httpx.HTTPError, RuntimeError) as error:
        print(f"Benchmark setup/verification failed: {error}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for result in report["results"]:
        print(
            f"{result['scenario']}: {result['requests']} requests, {result['rps']} req/s, "
            f"p95 {result['latency_ms']['p95']} ms, statuses {result['status_counts']}, "
            f"passed={result['passed']}"
        )
    print(f"Report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
