"""GitHub Repo Explorer – Flask-застосунок на основі публічного GitHub REST API."""
import math
import os
import sqlite3

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, url_for)

import database as db
import github_client as gh

PER_PAGE = 10


# ---------- Допоміжні функції валідації ----------

def parse_int(value, default, minimum=None):
    """М'яке перетворення для вебінтерфейсу: при помилці повертає default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and number < minimum:
        return default
    return number


def strict_int(value, field, errors, minimum=0):
    """Суворе перетворення для REST API: помилка потрапляє у errors."""
    if isinstance(value, bool) or value is None:
        errors[field] = "має бути цілим числом"
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = "має бути цілим числом"
        return None
    if number < minimum:
        errors[field] = f"має бути не менше {minimum}"
        return None
    return number


def validate_repo(data, partial=False):
    """Перевіряє дані репозиторію. Повертає (clean, errors)."""
    errors, clean = {}, {}
    text_limits = {"description": 1000, "language": 50, "notes": 2000,
                   "avatar_url": 500, "updated_at": 40}

    if not partial or "github_id" in data:
        value = strict_int(data.get("github_id"), "github_id", errors, minimum=1)
        if value is not None:
            clean["github_id"] = value
    if not partial or "full_name" in data:
        name = data.get("full_name")
        if not isinstance(name, str) or "/" not in name.strip() or len(name) > 200:
            errors["full_name"] = "обов'язкове поле у форматі owner/name"
        else:
            clean["full_name"] = name.strip()
    if not partial or "html_url" in data:
        url = data.get("html_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            errors["html_url"] = "обов'язкове поле, має починатися з http(s)://"
        else:
            clean["html_url"] = url
    for field in ("stars", "forks"):
        if field in data:
            value = strict_int(data[field], field, errors)
            if value is not None:
                clean[field] = value
    for field, limit in text_limits.items():
        if field in data:
            value = data[field]
            if value is not None and not isinstance(value, str):
                errors[field] = "має бути рядком"
            elif value is not None and len(value) > limit:
                errors[field] = f"занадто довге (максимум {limit} символів)"
            else:
                value = value.strip() if value else None
                clean[field] = value or None
    return clean, errors


def paginate(total, page, per_page=PER_PAGE):
    pages = max(1, math.ceil(total / per_page))
    return {"page": page, "pages": pages, "total": total,
            "has_prev": page > 1, "has_next": page < pages}


def row_to_dict(row):
    return dict(row)


# ---------- Фабрика застосунку ----------

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.path.join(os.path.dirname(os.path.abspath(__file__)), "repos.db"),
    )
    if test_config:
        app.config.update(test_config)
    db.init_db(app.config["DATABASE"])

    def get_db():
        if "db" not in g:
            g.db = db.get_connection(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.template_filter("date")
    def date_filter(value):
        return value[:10] if value else "—"

    @app.template_filter("num")
    def num_filter(value):
        return f"{value:,}".replace(",", " ") if isinstance(value, int) else value

    def safe_next(target, fallback):
        if target and target.startswith("/") and not target.startswith("//"):
            return target
        return fallback

    # ======================= ВЕБІНТЕРФЕЙС =======================

    @app.get("/")
    def index():
        return render_template("index.html", recent=db.get_recent_queries(get_db()),
                               form={"q": "", "language": "", "sort": "stars"})

    @app.get("/search")
    def search():
        form = {
            "q": request.args.get("q", "").strip(),
            "language": request.args.get("language", "").strip(),
            "sort": request.args.get("sort", "stars"),
        }
        page = parse_int(request.args.get("page"), 1, minimum=1)
        conn = get_db()

        def fail(message, status):
            flash(message, "danger" if status >= 500 else "warning")
            return render_template("index.html", form=form,
                                   recent=db.get_recent_queries(conn)), status

        if not form["q"]:
            return fail("Введіть пошуковий запит.", 400)
        try:
            result = gh.search_repositories(form["q"], form["language"],
                                            form["sort"], page, PER_PAGE)
        except gh.GitHubError as exc:
            return fail(exc.message, exc.http_status)

        if page == 1:
            db.add_history(conn, form["q"], form["language"], result["total"])
        saved_ids = db.existing_github_ids(conn, [r["github_id"] for r in result["items"]])
        qargs = {k: v for k, v in form.items() if v}
        return render_template("results.html", form=form, result=result,
                               saved_ids=saved_ids, qargs=qargs,
                               pg=paginate(result["total"], page))

    @app.get("/repo/<owner>/<name>")
    def repo_detail(owner, name):
        full_name = f"{owner}/{name}"
        try:
            repo = gh.get_repository(full_name)
        except gh.GitHubError as exc:
            flash(exc.message, "warning" if exc.http_status < 500 else "danger")
            return redirect(url_for("index")), 302
        saved = repo["github_id"] in db.existing_github_ids(get_db(), [repo["github_id"]])
        return render_template("repo_detail.html", repo=repo, saved=saved)

    @app.post("/save")
    def save():
        clean, errors = validate_repo(request.form)
        target = safe_next(request.form.get("next"), url_for("saved"))
        if errors:
            flash("Не вдалося зберегти: " + "; ".join(f"{k} {v}" for k, v in errors.items()),
                  "danger")
            return redirect(target)
        try:
            db.insert_repo(get_db(), clean)
            flash(f"Репозиторій «{clean['full_name']}» збережено.", "success")
        except sqlite3.IntegrityError:
            flash("Цей репозиторій уже є в локальній базі.", "info")
        return redirect(target)

    @app.get("/saved")
    def saved():
        conn = get_db()
        form = {
            "q": request.args.get("q", "").strip(),
            "language": request.args.get("language", "").strip(),
            "min_stars": request.args.get("min_stars", "").strip(),
            "sort": request.args.get("sort", "saved"),
            "order": request.args.get("order", "desc"),
        }
        page = parse_int(request.args.get("page"), 1, minimum=1)
        min_stars = parse_int(form["min_stars"], None, minimum=0)
        rows, total = db.list_repos(conn, form["q"] or None, form["language"] or None,
                                    min_stars, form["sort"], form["order"], page, PER_PAGE)
        qargs = {k: v for k, v in form.items() if v}
        return render_template("saved.html", repos=rows, form=form, qargs=qargs,
                               languages=db.get_languages(conn),
                               pg=paginate(total, page), sorts=db.SORT_COLUMNS)

    @app.get("/saved/<int:repo_id>")
    def saved_detail(repo_id):
        repo = db.get_repo(get_db(), repo_id)
        if repo is None:
            return render_template("404.html", message="Запис не знайдено в локальній базі."), 404
        return render_template("saved_detail.html", repo=repo)

    @app.route("/saved/<int:repo_id>/edit", methods=["GET", "POST"])
    def saved_edit(repo_id):
        conn = get_db()
        repo = db.get_repo(conn, repo_id)
        if repo is None:
            return render_template("404.html", message="Запис не знайдено в локальній базі."), 404
        if request.method == "POST":
            fields = {k: request.form.get(k, "") for k in ("description", "language", "notes")}
            clean, errors = validate_repo(fields, partial=True)
            if errors:
                for key, msg in errors.items():
                    flash(f"{key}: {msg}", "danger")
                return render_template("edit.html", repo=repo, values=fields), 400
            db.update_repo(conn, repo_id, clean)
            flash("Зміни збережено.", "success")
            return redirect(url_for("saved_detail", repo_id=repo_id))
        return render_template("edit.html", repo=repo, values=repo)

    @app.post("/saved/<int:repo_id>/delete")
    def saved_delete(repo_id):
        if db.delete_repo(get_db(), repo_id):
            flash("Запис видалено.", "success")
            return redirect(url_for("saved"))
        return render_template("404.html", message="Запис не знайдено в локальній базі."), 404

    @app.get("/history")
    def history():
        return render_template("history.html", items=db.get_history(get_db()))

    @app.post("/history/clear")
    def history_clear():
        db.clear_history(get_db())
        flash("Історію пошуку очищено.", "success")
        return redirect(url_for("history"))

    @app.get("/stats")
    def stats():
        return render_template("stats.html", stats=db.get_stats(get_db()))

    # ======================= REST API =======================

    def api_error(message, status, details=None):
        body = {"error": message}
        if details:
            body["details"] = details
        return jsonify(body), status

    @app.get("/api/repos")
    def api_list():
        errors = {}
        page = strict_int(request.args.get("page", 1), "page", errors, minimum=1)
        per_page = strict_int(request.args.get("per_page", PER_PAGE), "per_page", errors, minimum=1)
        min_stars = None
        if request.args.get("min_stars") not in (None, ""):
            min_stars = strict_int(request.args["min_stars"], "min_stars", errors)
        sort = request.args.get("sort", "saved")
        order = request.args.get("order", "desc")
        if sort not in db.SORT_COLUMNS:
            errors["sort"] = "допустимі значення: " + ", ".join(db.SORT_COLUMNS)
        if order not in ("asc", "desc"):
            errors["order"] = "допустимі значення: asc, desc"
        if per_page is not None and per_page > 100:
            errors["per_page"] = "максимум 100"
        if errors:
            return api_error("Некоректні параметри запиту", 400, errors)
        rows, total = db.list_repos(get_db(), request.args.get("q") or None,
                                    request.args.get("language") or None,
                                    min_stars, sort, order, page, per_page)
        return jsonify({"items": [row_to_dict(r) for r in rows],
                        "page": page, "per_page": per_page, "total": total}), 200

    @app.get("/api/repos/<int:repo_id>")
    def api_get(repo_id):
        row = db.get_repo(get_db(), repo_id)
        if row is None:
            return api_error("Репозиторій не знайдено", 404)
        return jsonify(row_to_dict(row)), 200

    @app.post("/api/repos")
    def api_create():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return api_error("Тіло запиту має бути JSON-об'єктом", 400)
        clean, errors = validate_repo(data)
        if errors:
            return api_error("Некоректні дані", 400, errors)
        conn = get_db()
        try:
            new_id = db.insert_repo(conn, clean)
        except sqlite3.IntegrityError:
            return api_error("Репозиторій із таким github_id уже існує", 409)
        return jsonify(row_to_dict(db.get_repo(conn, new_id))), 201

    @app.patch("/api/repos/<int:repo_id>")
    def api_patch(repo_id):
        conn = get_db()
        if db.get_repo(conn, repo_id) is None:
            return api_error("Репозиторій не знайдено", 404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not data:
            return api_error("Тіло запиту має бути непорожнім JSON-об'єктом", 400)
        unknown = [k for k in data if k not in db.EDITABLE_FIELDS]
        if unknown:
            return api_error("Ці поля змінювати не можна", 400,
                             {k: "не підтримується" for k in unknown})
        clean, errors = validate_repo(data, partial=True)
        if errors:
            return api_error("Некоректні дані", 400, errors)
        db.update_repo(conn, repo_id, clean)
        return jsonify(row_to_dict(db.get_repo(conn, repo_id))), 200

    @app.delete("/api/repos/<int:repo_id>")
    def api_delete(repo_id):
        if not db.delete_repo(get_db(), repo_id):
            return api_error("Репозиторій не знайдено", 404)
        return jsonify({"message": "Репозиторій видалено", "id": repo_id}), 200

    @app.get("/api/stats")
    def api_stats():
        return jsonify(db.get_stats(get_db())), 200

    # ---------- Обробники помилок (JSON для /api, HTML для решти) ----------

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return api_error("Ресурс не знайдено", 404)
        return render_template("404.html", message="Сторінку не знайдено."), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        if request.path.startswith("/api/"):
            return api_error("Метод не підтримується", 405)
        return render_template("404.html", message="Метод не підтримується."), 405

    @app.errorhandler(500)
    def server_error(_e):
        if request.path.startswith("/api/"):
            return api_error("Внутрішня помилка сервера", 500)
        return render_template("404.html", message="Внутрішня помилка сервера."), 500

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
