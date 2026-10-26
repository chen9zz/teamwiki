from tests.conftest import register, workspace


async def test_unauthenticated_requests_are_rejected(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/workspaces")).status_code == 401


async def test_register_login_logout_revokes_replayed_cookie(client):
    user = await register(client)
    assert (await client.get("/api/auth/me")).json()["id"] == user["id"]
    cookie = client.cookies.get("teamwiki_session")
    assert cookie
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (
        await client.get(
            "/api/auth/me",
            headers={
                "Cookie": f"teamwiki_session={cookie}",
            },
        )
    ).status_code == 401
    response = await client.post(
        "/api/auth/login",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    assert (await client.get("/api/auth/me")).json()["email"] == "owner@example.com"


async def test_wrong_password_and_duplicate_email_are_rejected(client):
    await register(client)
    assert (
        await client.post(
            "/api/auth/login",
            json={
                "email": "owner@example.com",
                "password": "this is wrong",
            },
        )
    ).status_code == 401
    response = await client.post(
        "/api/auth/register",
        json={
            "email": "OWNER@example.com",
            "password": "correct horse battery staple",
            "name": "Duplicate",
        },
    )
    assert response.status_code == 409


async def test_mutating_requests_reject_untrusted_origin(client):
    await register(client)
    response = await client.post(
        "/api/workspaces", json={"name": "Stolen"}, headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 403
    response = await client.post(
        "/api/workspaces", json={"name": "Safe"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 201


async def test_invitation_is_email_bound_single_use_and_no_privilege_escalation(client):
    owner = await register(client)
    ws = await workspace(client)
    wid = ws["id"]
    assert ws["role"] == "owner"
    response = await client.post(
        f"/api/workspaces/{wid}/invitations",
        json={
            "email": "editor@example.com",
            "role": "editor",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["token"]
    await client.post("/api/auth/logout")
    await register(client, "intruder@example.com", "Intruder")
    assert (await client.post("/api/invitations/accept", json={"token": token})).status_code == 403
    assert (await client.get(f"/api/workspaces/{wid}/members")).status_code == 404
    await client.post("/api/auth/logout")
    editor = await register(client, "editor@example.com", "Editor")
    accepted = await client.post("/api/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["id"] == wid
    assert (await client.post("/api/invitations/accept", json={"token": token})).status_code == 400
    assert (
        await client.patch(f"/api/workspaces/{wid}/members/{editor['id']}", json={"role": "admin"})
    ).status_code == 403
    assert (await client.delete(f"/api/workspaces/{wid}/members/{owner['id']}")).status_code == 403


async def test_admin_cannot_create_admin_or_change_owner_and_removed_member_loses_access(client):
    owner = await register(client)
    wid = (await workspace(client))["id"]
    invitation = await client.post(
        f"/api/workspaces/{wid}/invitations",
        json={
            "email": "admin@example.com",
            "role": "admin",
        },
    )
    await client.post("/api/auth/logout")
    admin = await register(client, "admin@example.com", "Admin")
    await client.post("/api/invitations/accept", json={"token": invitation.json()["token"]})
    assert (
        await client.post(
            f"/api/workspaces/{wid}/invitations",
            json={
                "email": "other@example.com",
                "role": "admin",
            },
        )
    ).status_code == 403
    assert (
        await client.patch(f"/api/workspaces/{wid}/members/{owner['id']}", json={"role": "viewer"})
    ).status_code == 403
    assert (await client.delete(f"/api/workspaces/{wid}/members/{owner['id']}")).status_code == 403
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"email": owner["email"], "password": "correct horse battery staple"}
    )
    assert (await client.delete(f"/api/workspaces/{wid}/members/{admin['id']}")).status_code == 204
    await client.post("/api/auth/logout")
    await client.post(
        "/api/auth/login", json={"email": admin["email"], "password": "correct horse battery staple"}
    )
    assert (await client.get(f"/api/workspaces/{wid}/members")).status_code == 404
    assert (await client.get("/api/workspaces")).json() == []


async def test_commit_failure_is_reported_before_success_response(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    await register(client)

    async def rejected_commit(self):
        raise IntegrityError("commit", {}, Exception("commit rejected"))

    monkeypatch.setattr(AsyncSession, "commit", rejected_commit)
    response = await client.post("/api/workspaces", json={"name": "Must not report success"})
    assert response.status_code == 409
