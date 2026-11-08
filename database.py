"""Робота з локальною базою даних SQLite.

Усі значення, що надходять від користувача, передаються до SQL
ВИКЛЮЧНО через параметри (?), без f-рядків.
Єдині рядки, що підставляються в SQL, – це імена стовпців/напрямок
сортування, які беруться зі «білих списків» нижче, а не з введення.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id    INTEGER NOT NULL UNIQUE,
    full_name    TEXT    NOT NULL CHECK (length(trim(full_name)) > 0),
    description  TEXT,
    language     TEXT,
    stars        INTEGER NOT NULL DEFAULT 0 CHECK (stars >= 0),
    forks        INTEGER NOT NULL DEFAULT 0 CHECK (forks >= 0),
    html_url     TEXT    NOT NULL,
    avatar_url   TEXT,
    updated_at   TEXT,
    notes        TEXT,
    saved_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_repos_language ON repos(language);
CREATE INDEX IF NOT EXISTS idx_repos_stars    ON repos(stars);

CREATE TABLE IF NOT EXISTS search_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT    NOT NULL,
    language      TEXT,
    results_count INTEGER NOT NULL DEFAULT 0,
    searched_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# Білі списки для сортування
SORT_COLUMNS = {
    "saved": "saved_at",
    "stars": "stars",
    "forks": "forks",
    "name": "full_name COLLATE NOCASE",
    "updated": "updated_at",
}
EDITABLE_FIELDS = ("description", "language", "notes", "stars", "forks")


def get_connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path):
    conn = get_connection(path)
    with conn:
        conn.executescript(SCHEMA)
    conn.close()


def _like(value):
    """Екранування спецсимволів LIKE (%, _, \\)."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------- CRUD для repos ----------

def insert_repo(conn, data):
    """Додає запис. Кидає sqlite3.IntegrityError, якщо github_id вже є."""
    with conn:
        cur = conn.execute(
            """INSERT INTO repos
               (github_id, full_name, description, language, stars, forks,
                html_url, avatar_url, updated_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["github_id"], data["full_name"], data.get("description"),
                data.get("language"), data.get("stars", 0), data.get("forks", 0),
                data["html_url"], data.get("avatar_url"), data.get("updated_at"),
                data.get("notes"),
            ),
        )
    return cur.lastrowid


def get_repo(conn, repo_id):
    return conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,)).fetchone()


def list_repos(conn, q=None, language=None, min_stars=None,
               sort="saved", order="desc", page=1, per_page=10):
    """Пошук/фільтрація/сортування/пагінація виконуються засобами SQL."""
    where, params = [], []
    if q:
        pattern = _like(q)
        where.append(
            "(full_name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
            "OR notes LIKE ? ESCAPE '\\')"
        )
        params += [pattern, pattern, pattern]
    if language:
        where.append("language = ? COLLATE NOCASE")
        params.append(language)
    if min_stars is not None:
        where.append("stars >= ?")
        params.append(min_stars)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sort_col = SORT_COLUMNS.get(sort, SORT_COLUMNS["saved"])
    direction = "ASC" if order == "asc" else "DESC"

    total = conn.execute(
        "SELECT COUNT(*) FROM repos" + where_sql, params
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM repos" + where_sql
        + f" ORDER BY {sort_col} {direction}, id DESC LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    return rows, total


def update_repo(conn, repo_id, fields):
    """Оновлює лише дозволені поля. Повертає True, якщо запис існував."""
    keys = [k for k in fields if k in EDITABLE_FIELDS]
    if not keys:
        return get_repo(conn, repo_id) is not None
    set_sql = ", ".join(f"{k} = ?" for k in keys)  # імена з білого списку
    with conn:
        cur = conn.execute(
            f"UPDATE repos SET {set_sql} WHERE id = ?",
            [fields[k] for k in keys] + [repo_id],
        )
    return cur.rowcount > 0


def delete_repo(conn, repo_id):
    with conn:
        cur = conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
    return cur.rowcount > 0


def existing_github_ids(conn, github_ids):
    """Які з переданих github_id уже збережені (для позначки на сторінці пошуку)."""
    ids = list(github_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT github_id FROM repos WHERE github_id IN ({placeholders})", ids
    ).fetchall()
    return {r["github_id"] for r in rows}


def get_languages(conn):
    rows = conn.execute(
        "SELECT DISTINCT language FROM repos WHERE language IS NOT NULL "
        "ORDER BY language COLLATE NOCASE"
    ).fetchall()
    return [r["language"] for r in rows]


# ---------- Статистика ----------

def get_stats(conn):
    summary = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(stars),0) AS total_stars, "
        "COALESCE(ROUND(AVG(stars),1),0) AS avg_stars, "
        "COALESCE(SUM(forks),0) AS total_forks FROM repos"
    ).fetchone()
    by_language = conn.execute(
        "SELECT COALESCE(language,'Не вказано') AS language, COUNT(*) AS repos, "
        "SUM(stars) AS stars FROM repos GROUP BY COALESCE(language,'Не вказано') "
        "ORDER BY repos DESC, stars DESC"
    ).fetchall()
    top = conn.execute(
        "SELECT * FROM repos ORDER BY stars DESC, id DESC LIMIT 1"
    ).fetchone()
    return {
        "summary": dict(summary),
        "by_language": [dict(r) for r in by_language],
        "top_repo": dict(top) if top else None,
    }


# ---------- Історія пошуку ----------

def add_history(conn, query, language, results_count):
    with conn:
        conn.execute(
            "INSERT INTO search_history (query, language, results_count) VALUES (?, ?, ?)",
            (query, language or None, results_count),
        )


def get_history(conn, limit=50):
    return conn.execute(
        "SELECT * FROM search_history ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_recent_queries(conn, limit=6):
    return conn.execute(
        "SELECT query, language, MAX(id) AS last_id FROM search_history "
        "GROUP BY query, language ORDER BY last_id DESC LIMIT ?", (limit,)
    ).fetchall()


def clear_history(conn):
    with conn:
        conn.execute("DELETE FROM search_history")
