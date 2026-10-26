#!/usr/bin/env python3
"""Idempotent demo data through the same public API used by the browser.

Run against a running local API: python scripts/seed.py --url http://localhost:8000
"""

import argparse
import http.cookiejar
import json
import os
import urllib.error
import urllib.request


class Client:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method, path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            self.url + "/api" + path, data=body, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with self.opener.open(req, timeout=30) as response:
                return json.load(response) if response.status != 204 else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {path}: HTTP {exc.code}: {exc.read().decode()}") from exc

    def sign_in(self, email, name, password):
        try:
            return self.request("POST", "/auth/login", {"email": email, "password": password})
        except RuntimeError as exc:
            if "HTTP 401" not in str(exc):
                raise
            return self.request(
                "POST", "/auth/register", {"email": email, "name": name, "password": password}
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()
    password = os.getenv("SEED_PASSWORD", "DemoPassword123!")
    accounts = [
        ("owner", "团队负责人"),
        ("admin", "知识库管理员"),
        ("editor", "内容编辑"),
        ("viewer", "只读访客"),
    ]
    clients = {}
    for role, name in accounts:
        client = Client(args.url)
        client.sign_in(f"{role}@teamwiki.dev", name, password)
        clients[role] = client
    owner = clients["owner"]
    workspaces = owner.request("GET", "/workspaces")
    ws = next((item for item in workspaces if item["name"] == "TeamWiki 演示空间"), None)
    ws = ws or owner.request("POST", "/workspaces", {"name": "TeamWiki 演示空间"})
    wid = ws["id"]
    member_emails = {member["email"] for member in owner.request("GET", f"/workspaces/{wid}/members")}
    for role, _ in accounts[1:]:
        email = f"{role}@teamwiki.dev"
        if email not in member_emails:
            invitation = owner.request(
                "POST", f"/workspaces/{wid}/invitations", {"email": email, "role": role}
            )
            clients[role].request("POST", "/invitations/accept", {"token": invitation["token"]})
    pages = [
        (
            "欢迎使用 TeamWiki",
            "# 团队知识，从这里开始\n\n这里汇集团队的文档、决策与经验。\n\n"
            "- 阅读 [[快速开始]]\n- 了解 [[架构设计]]\n- 查看 [[团队协作规范]]\n\n"
            "试着编辑并保存，随后在历史版本中查看完整快照。",
            ["入门", "推荐"],
        ),
        (
            "快速开始",
            "# 快速开始\n\n1. 在左侧新建文档\n2. 使用 Markdown 编写内容\n"
            "3. 用 `[[文档标题]]` 建立知识链接\n4. 在知识图谱中探索关联\n\n"
            "阅读 [[团队协作规范]]，一起保持知识库清晰。",
            ["入门"],
        ),
        (
            "架构设计",
            "# 架构设计\n\nFastAPI + PostgreSQL + Redis + Celery，React 提供交互界面。\n\n"
            "## 一致性\n\n所有保存携带版本号；竞争写入返回冲突，不会覆盖同事的更新。\n\n"
            "## 数据隔离\n\n每次请求均验证空间成员身份与角色。",
            ["工程", "架构"],
        ),
        (
            "团队协作规范",
            "# 团队协作规范\n\n写下背景、结论与依据；用链接串联相关知识。\n\n"
            "## 编辑流程\n\n保存前确认版本。遇到冲突时先保留草稿，再比较服务器版本。\n\n"
            "## 角色\n\n负责人管理管理员，管理员管理编辑和访客。访客可以阅读与导出。",
            ["团队"],
        ),
    ]
    existing = owner.request("GET", f"/workspaces/{wid}/pages")
    by_title = {page["title"]: page for page in existing}
    created = []
    for title, content, tags in pages:
        if title not in by_title:
            page = owner.request(
                "POST", f"/workspaces/{wid}/pages", {"title": title, "content": content, "tags": tags}
            )
            by_title[title] = page
            created.append(page)
    # Resave new source pages once all targets exist, so title links resolve in either order.
    for page in created:
        owner.request(
            "PATCH",
            f"/workspaces/{wid}/pages/{page['id']}",
            {"version": page["version"], "content": page["content"]},
        )
    print(
        json.dumps(
            {
                "workspace_id": wid,
                "accounts": [f"{r}@teamwiki.dev" for r, _ in accounts],
                "password": password,
                "pages": len(by_title),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
