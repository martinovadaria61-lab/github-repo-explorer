"""Модуль для отримання та перетворення даних із GitHub REST API.

Документація: https://docs.github.com/en/rest
Ендпоїнти, що використовуються:
  GET /search/repositories  – пошук репозиторіїв
  GET /repos/{owner}/{repo} – детальна інформація про репозиторій
"""
import os
import re
import time

import requests

API_URL = "https://api.github.com"
TIMEOUT = 10            # секунд
CACHE_TTL = 300         # секунд: кешування відповідей (щоб економити ліміти API)
SEARCH_LIMIT = 1000     # GitHub віддає лише перші 1000 результатів пошуку
SORTS = {"best-match": None, "stars": "stars", "forks": "forks", "updated": "updated"}

_cache = {}


class GitHubError(Exception):
    http_status = 502

    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.message = message
        if http_status:
            self.http_status = http_status


class InvalidQuery(GitHubError):
    http_status = 400


class NotFound(GitHubError):
    http_status = 404


class RateLimited(GitHubError):
    http_status = 429


class Unavailable(GitHubError):
    http_status = 503


def clear_cache():
    _cache.clear()


def _request(path, params=None):
    """Єдина точка виходу в мережу (її підміняють у тестах)."""
    key = (path, tuple(sorted((params or {}).items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "repo-explorer-student-app",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(API_URL + path, params=params,
                            headers=headers, timeout=TIMEOUT)
    except requests.Timeout:
        raise Unavailable("GitHub API не відповідає (перевищено час очікування).")
    except requests.RequestException:
        raise Unavailable("Не вдалося з'єднатися з GitHub API. Перевірте інтернет-з'єднання.")

    if resp.status_code == 200:
        data = resp.json()
        _cache[key] = (time.time(), data)
        return data
    if resp.status_code in (403, 429):
        if resp.status_code == 429 or resp.headers.get("X-RateLimit-Remaining") == "0" \
                or "rate limit" in resp.text.lower():
            raise RateLimited("Перевищено ліміт запитів до GitHub API. "
                              "Спробуйте пізніше або задайте GITHUB_TOKEN.")
        raise GitHubError("GitHub API відмовив у доступі.", 502)
    if resp.status_code == 404:
        raise NotFound("Репозиторій не знайдено.")
    if resp.status_code == 422:
        raise InvalidQuery("GitHub не зміг обробити запит. Перевірте параметри пошуку.")
    raise GitHubError(f"GitHub API повернув помилку {resp.status_code}.")


def normalize_repo(item):
    """Перетворює повну JSON-відповідь у компактну внутрішню структуру."""
    license_info = item.get("license") or {}
    owner = item.get("owner") or {}
    return {
        "github_id": item["id"],
        "full_name": item["full_name"],
        "owner": owner.get("login", ""),
        "description": item.get("description"),
        "language": item.get("language"),
        "stars": item.get("stargazers_count", 0),
        "forks": item.get("forks_count", 0),
        "open_issues": item.get("open_issues_count", 0),
        "watchers": item.get("subscribers_count"),   # є лише в детальній відповіді
        "license": license_info.get("spdx_id") or license_info.get("name"),
        "topics": item.get("topics") or [],
        "html_url": item["html_url"],
        "avatar_url": owner.get("avatar_url"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _clean_language(language):
    # Залишаємо лише безпечні символи, щоб не додавати власні qualifier-и до запиту
    return re.sub(r"[^\w+#. \-]", "", language or "").strip()


def search_repositories(query, language="", sort="stars", page=1, per_page=10):
    query = (query or "").strip()
    if not query:
        raise InvalidQuery("Пошуковий запит не може бути порожнім.")
    if sort not in SORTS:
        raise InvalidQuery("Невідомий параметр сортування.")
    if (page - 1) * per_page >= SEARCH_LIMIT:
        raise InvalidQuery("GitHub надає лише перші 1000 результатів пошуку.")

    q = query
    lang = _clean_language(language)
    if lang:
        q += f' language:"{lang}"'
    params = {"q": q, "per_page": per_page, "page": page}
    if SORTS[sort]:
        params["sort"] = SORTS[sort]
        params["order"] = "desc"

    data = _request("/search/repositories", params)
    total = min(data.get("total_count", 0), SEARCH_LIMIT)
    return {
        "items": [normalize_repo(i) for i in data.get("items", [])],
        "total": total,
        "pages": max(1, -(-total // per_page)),
        "page": page,
    }


def get_repository(full_name):
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", full_name or ""):
        raise InvalidQuery("Некоректна назва репозиторію (очікується owner/name).")
    return normalize_repo(_request(f"/repos/{full_name}"))
