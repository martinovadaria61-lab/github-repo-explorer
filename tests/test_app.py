import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import github_client as gh
from app import create_app


def fake_item(i=1, **over):
    item = {
        "id": i, "full_name": f"octo/repo{i}", "description": "Demo",
        "language": "Python", "stargazers_count": 100 * i, "forks_count": 5 * i,
        "open_issues_count": 2, "html_url": f"https://github.com/octo/repo{i}",
        "owner": {"login": "octo", "avatar_url": "https://example.com/a.png"},
        "updated_at": "2025-01-02T03:04:05Z", "created_at": "2020-01-01T00:00:00Z",
        "topics": ["flask"], "license": {"spdx_id": "MIT"},
    }
    item.update(over)
    return item


@pytest.fixture
def client(tmp_path, monkeypatch):
    gh.clear_cache()
    monkeypatch.setattr(gh, "_request", lambda path, params=None: (
        {"total_count": 2, "items": [fake_item(1), fake_item(2)]}
        if path == "/search/repositories" else fake_item(1)))
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})
    return app.test_client()


def payload(i=1, **over):
    data = {"github_id": i, "full_name": f"octo/repo{i}",
            "html_url": f"https://github.com/octo/repo{i}",
            "stars": 10 * i, "language": "Python", "description": "d"}
    data.update(over)
    return data


# ---------- вебінтерфейс ----------

def test_search_ok(client):
    r = client.get("/search?q=flask")
    assert r.status_code == 200 and "octo/repo1" in r.get_data(as_text=True)


def test_search_empty_query(client):
    assert client.get("/search?q=").status_code == 400


def test_search_invalid_sort(client):
    assert client.get("/search?q=x&sort=hack").status_code == 400


def test_search_api_unavailable(client, monkeypatch):
    def boom(path, params=None):
        raise gh.Unavailable("down")
    monkeypatch.setattr(gh, "_request", boom)
    assert client.get("/search?q=x").status_code == 503


def test_search_no_results(client, monkeypatch):
    monkeypatch.setattr(gh, "_request", lambda p, params=None: {"total_count": 0, "items": []})
    r = client.get("/search?q=zzzz")
    assert r.status_code == 200 and "нічого не знайдено" in r.get_data(as_text=True)


def test_save_edit_delete_flow(client):
    form = {"github_id": 1, "full_name": "octo/repo1", "html_url": "https://github.com/octo/repo1",
            "stars": 100, "forks": 5, "language": "Python", "description": "Demo"}
    assert client.post("/save", data=form).status_code == 302
    assert "octo/repo1" in client.get("/saved").get_data(as_text=True)
    assert client.post("/saved/1/edit", data={"description": "new", "language": "Go",
                                              "notes": "n"}).status_code == 302
    assert "new" in client.get("/saved/1").get_data(as_text=True)
    assert client.post("/saved/1/delete").status_code == 302
    assert client.get("/saved/1").status_code == 404


def test_local_search_uses_sql_and_is_injection_safe(client):
    client.post("/api/repos", json=payload(1))
    client.post("/api/repos", json=payload(2, language="Go"))
    r = client.get("/api/repos?q=' OR 1=1 --")
    assert r.status_code == 200 and r.get_json()["total"] == 0
    assert client.get("/api/repos?language=go").get_json()["total"] == 1
    assert client.get("/api/repos?min_stars=15").get_json()["total"] == 1


def test_history_and_stats_pages(client):
    client.get("/search?q=flask")
    assert "flask" in client.get("/history").get_data(as_text=True)
    assert client.get("/stats").status_code == 200


# ---------- REST API ----------

def test_api_crud(client):
    r = client.post("/api/repos", json=payload(1))
    assert r.status_code == 201 and r.get_json()["id"] == 1
    assert client.get("/api/repos").status_code == 200
    assert client.get("/api/repos/1").get_json()["full_name"] == "octo/repo1"
    r = client.patch("/api/repos/1", json={"notes": "hi", "stars": 7})
    assert r.status_code == 200 and r.get_json()["stars"] == 7
    assert client.delete("/api/repos/1").status_code == 200
    assert client.get("/api/repos/1").status_code == 404


def test_api_errors_are_json(client):
    assert client.get("/api/repos/999").status_code == 404
    assert client.get("/api/repos/999").is_json
    assert client.get("/api/nope").is_json
    assert client.patch("/api/repos/999", json={"notes": "x"}).status_code == 404
    assert client.delete("/api/repos/999").status_code == 404
    assert client.post("/api/repos", json={"full_name": "x"}).status_code == 400
    assert client.post("/api/repos", data="not json").status_code == 400
    assert client.post("/api/repos", json=payload(1, stars=-5)).status_code == 400
    assert client.get("/api/repos?page=abc").status_code == 400
    assert client.get("/api/repos?sort=hack").status_code == 400


def test_api_duplicate_and_forbidden_patch(client):
    client.post("/api/repos", json=payload(1))
    assert client.post("/api/repos", json=payload(1)).status_code == 409
    assert client.patch("/api/repos/1", json={"github_id": 5}).status_code == 400


# ---------- github_client ----------

def test_normalize_and_cache(monkeypatch):
    gh.clear_cache()
    repo = gh.normalize_repo(fake_item(3))
    assert repo["stars"] == 300 and repo["license"] == "MIT"
    with pytest.raises(gh.InvalidQuery):
        gh.search_repositories("  ")
    with pytest.raises(gh.InvalidQuery):
        gh.get_repository("bad name")
