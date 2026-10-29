import io
import os
import zipfile

import pytest

from tests.conftest import register, workspace
from tests.test_pages import create_page


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"), reason="Real PG FTS")
async def test_vector_representation_limit_returns_413_without_partial_page(client):
    await register(client)
    wid = (await workspace(client))["id"]
    body = " ".join(f"w{index:06d}" for index in range(125_000))
    assert len(body) == 999_999
    response = await client.post(
        f"/api/workspaces/{wid}/pages", json={"title": "Large vocabulary", "content": body}
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "search_index_limit"
    assert (await client.get(f"/api/workspaces/{wid}/pages")).json() == []


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"), reason="Real PG FTS")
async def test_chinese_search_api_finds_a_two_character_term_inside_a_sentence(client):
    await register(client)
    wid = (await workspace(client))["id"]
    page = await create_page(client, wid, title="TeamWiki", content="团队知识管理支持中文搜索")
    result = await client.get(f"/api/workspaces/{wid}/search", params={"q": "知识"})
    assert result.status_code == 200
    assert [item["id"] for item in result.json()] == [page["id"]]
    assert result.json()[0]["search_index_truncated"] is False


async def test_export_route_selects_portable_format_and_preserves_legacy_default(client):
    await register(client)
    wid = (await workspace(client))["id"]
    await create_page(client, wid, title="Export me", content="Content")
    portable = await client.get(f"/api/workspaces/{wid}/export?format=teamwiki")
    assert portable.status_code == 200
    with zipfile.ZipFile(io.BytesIO(portable.content)) as archive:
        assert "teamwiki.json" in archive.namelist()
    legacy = await client.get(f"/api/workspaces/{wid}/export")
    assert legacy.status_code == 200
    with zipfile.ZipFile(io.BytesIO(legacy.content)) as archive:
        assert len(archive.namelist()) == 1
        assert "teamwiki.json" not in archive.namelist()
    assert (await client.get(f"/api/workspaces/{wid}/export?format=unknown")).status_code == 422
