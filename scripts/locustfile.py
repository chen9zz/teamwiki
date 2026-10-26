"""Exploratory mixed load against an explicitly chosen disposable workspace.

Set LOAD_EMAIL, LOAD_PASSWORD and LOAD_WORKSPACE_ID. Every virtual user creates
its own page; stale-write 409 responses have a separate request name and are
expected. Use benchmark_concurrency.py for repeatable before/after evidence.
"""

import os
import random
from uuid import uuid4

from locust import HttpUser, between, task
from locust.exception import StopUser


class WikiUser(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self):
        email = os.getenv("LOAD_EMAIL")
        password = os.getenv("LOAD_PASSWORD")
        workspace_id = os.getenv("LOAD_WORKSPACE_ID")
        if not all((email, password, workspace_id)):
            raise StopUser("Set LOAD_EMAIL, LOAD_PASSWORD and LOAD_WORKSPACE_ID for a disposable workspace")
        response = self.client.post(
            "/api/auth/login",
            json={
                "email": email,
                "password": password,
            },
            name="auth/login",
        )
        if response.status_code != 200:
            raise StopUser("Benchmark account could not sign in")
        workspaces = self.client.get("/api/workspaces", name="workspaces/list").json()
        if workspace_id not in {workspace["id"] for workspace in workspaces}:
            raise StopUser("LOAD_WORKSPACE_ID is not accessible to the benchmark account")
        self.wid = workspace_id
        self.prefix = f"/api/workspaces/{self.wid}"
        self.pages = self.client.get(self.prefix + "/pages", name="pages/list").json()
        response = self.client.post(
            self.prefix + "/pages",
            json={
                "title": f"Load test {uuid4().hex[:12]}",
                "content": "# Load test\n\nInitial content.",
                "tags": ["load-test"],
            },
            name="pages/create",
        )
        if response.status_code != 201:
            raise StopUser("Unable to create load test page")
        self.own_page = response.json()
        self.pages.append(self.own_page)

    @task(5)
    def read_page(self):
        page = random.choice(self.pages)
        self.client.get(self.prefix + f"/pages/{page['id']}", name="pages/read")

    @task(2)
    def search(self):
        self.client.get(self.prefix + "/search", params={"q": "TeamWiki"}, name="search")

    @task(1)
    def graph(self):
        self.client.get(self.prefix + "/graph", name="graph")

    @task(1)
    def save_and_conflict(self):
        page = self.own_page
        path = self.prefix + f"/pages/{page['id']}"
        payload = {
            "version": page["version"],
            "content": f"# Load test\n\nEdit {uuid4().hex}",
        }
        response = self.client.patch(path, json=payload, name="pages/save")
        if response.status_code == 200:
            self.own_page = response.json()
            with self.client.patch(path, json=payload, name="pages/stale-save", catch_response=True) as stale:
                if stale.status_code == 409:
                    stale.success()
                else:
                    stale.failure(f"Expected version conflict, got {stale.status_code}")
