#!/usr/bin/env python3
"""Same-origin container smoke: seed first, then exercise the public gateway.

Usage: python scripts/seed.py --url http://127.0.0.1:8080
       python scripts/docker_smoke.py --url http://127.0.0.1:8080
Uses only Python's standard library. The asynchronous import deliberately waits
for beat to dispatch the transactional outbox and a real worker to consume it.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from seed import Client


def http_status(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def denied(client, path):
    try:
        client.request("GET", path)
    except RuntimeError as exc:
        assert "HTTP 403" in str(exc) or "HTTP 404" in str(exc), str(exc)
    else:
        raise AssertionError(f"Cross-tenant access allowed: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    status, body = http_status(base + "/")
    assert status == 200 and b'<div id="root">' in body, "Frontend did not serve its built app"
    status, body = http_status(base + "/api/ready")
    assert status == 200, body.decode()
    readiness = json.loads(body)
    for path in ("/internal/metrics", "/internal/anything"):
        assert http_status(base + path)[0] == 404, f"Internal route publicly exposed: {path}"

    owner = Client(base)
    password = os.getenv("SEED_PASSWORD", "DemoPassword123!")
    owner.sign_in("owner@teamwiki.dev", "团队负责人", password)
    seed = next(w for w in owner.request("GET", "/workspaces") if w["name"] == "TeamWiki 演示空间")
    assert len(owner.request("GET", f"/workspaces/{seed['id']}/pages")) >= 4

    token = uuid.uuid4().hex
    ws = owner.request("POST", "/workspaces", {"name": f"Container smoke {token[:8]}"})
    wid = ws["id"]
    prefix = f"/workspaces/{wid}"
    page = owner.request("POST", prefix + "/pages", {"title": "Version smoke", "content": "initial"})
    updated = owner.request(
        "PATCH", prefix + f"/pages/{page['id']}", {"version": page["version"], "content": "saved"}
    )
    assert updated["version"] == page["version"] + 1
    try:
        owner.request(
            "PATCH", prefix + f"/pages/{page['id']}", {"version": page["version"], "content": "stale"}
        )
    except RuntimeError as exc:
        assert "HTTP 409" in str(exc), str(exc)
    else:
        raise AssertionError("A stale version overwrote the page")
    revisions = owner.request("GET", prefix + f"/pages/{page['id']}/revisions")
    assert {r["version"] for r in revisions} >= {page["version"], updated["version"]}

    stranger = Client(base)
    stranger.sign_in(f"smoke-{token}@example.com", "Separate tenant", password)
    stranger.request("POST", "/workspaces", {"name": "Private tenant"})
    denied(stranger, prefix + "/pages")
    denied(stranger, prefix + f"/pages/{page['id']}")

    boundary = "teamwiki-" + token
    title = "Worker smoke " + token[:8]
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{title}.md"\r\n'
        f"Content-Type: text/markdown\r\n\r\n# {title}\nImported by a real Celery worker.\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    request = urllib.request.Request(
        base + "/api" + prefix + "/imports",
        data=payload,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Idempotency-Key": token},
    )
    with owner.opener.open(request, timeout=15) as response:
        assert response.status == 202
        job = json.load(response)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        job = owner.request("GET", prefix + f"/imports/{job['id']}")
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(1)
    assert job["status"] == "completed", job
    assert job["processed"] == 1, job
    assert any(p["title"] == title for p in owner.request("GET", prefix + "/pages")), job
    print(
        json.dumps(
            {
                "result": "passed",
                "frontend": True,
                "readiness": readiness,
                "versions": True,
                "tenant_isolation": True,
                "worker_and_beat_import": True,
                "workspace_id": wid,
                "job_id": job["id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
