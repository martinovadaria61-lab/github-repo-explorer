# GitHub Repo Explorer

Вебзастосунок на **Flask** для пошуку репозиторіїв через публічний **GitHub REST API**, локального збереження
вибраних записів у **SQLite** та роботи з ними через вебінтерфейс і власний **REST API**.
Самостійна робота з вебпрограмування, варіант 5.

## Можливості

- Пошук репозиторіїв GitHub за назвою, мовою програмування та способом сортування.
- Для кожного результату: назва, опис, мова, зірки, форки, дата оновлення, аватар власника.
- Сторінка детальної інформації про репозиторій (issues, ліцензія, теми, дати створення/оновлення).
- Збереження в локальну БД; збережені записи доступні без GitHub API.
- Локальні пошук (SQL `LIKE`), фільтр за мовою, мінімальною кількістю зірок, сортування, пагінація, редагування, видалення.
- **Індивідуальні функції:** пагінація результатів, історія пошуку, кешування відповідей API (5 хв), статистика за локальними даними, сторінка деталей.
- Власний REST API (JSON, коди 200/201/400/404).

## Запуск

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                    # http://127.0.0.1:5000
```

Необов'язково: `export GITHUB_TOKEN=...` підвищує ліміт запитів до GitHub API.
Тести (GitHub API підміняється): `python -m pytest`.

## Структура

| Файл | Призначення |
| --- | --- |
| `app.py` | Flask-маршрути (вебінтерфейс + REST API), валідація |
| `github_client.py` | Запити до GitHub API, перетворення JSON у внутрішню структуру, кеш, обробка помилок |
| `database.py` | Схема SQLite та всі операції з БД (тільки параметризовані запити) |
| `templates/` | Jinja2-шаблони (Bootstrap 5) |
| `tests/test_app.py` | Автотести |

## Схема бази даних

```sql
CREATE TABLE repos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id    INTEGER NOT NULL UNIQUE,            -- зовнішній ідентифікатор GitHub
    full_name    TEXT    NOT NULL CHECK (length(trim(full_name)) > 0),
    description  TEXT,
    language     TEXT,
    stars        INTEGER NOT NULL DEFAULT 0 CHECK (stars >= 0),
    forks        INTEGER NOT NULL DEFAULT 0 CHECK (forks >= 0),
    html_url     TEXT    NOT NULL,
    avatar_url   TEXT,
    updated_at   TEXT,
    notes        TEXT,                               -- власні нотатки користувача
    saved_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE search_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    query         TEXT    NOT NULL,
    language      TEXT,
    results_count INTEGER NOT NULL DEFAULT 0,
    searched_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

## REST API

| Метод | Шлях | Опис | Коди |
| --- | --- | --- | --- |
| GET | `/api/repos` | Список. Параметри: `q`, `language`, `min_stars`, `sort` (saved/stars/forks/name/updated), `order` (asc/desc), `page`, `per_page` | 200, 400 |
| GET | `/api/repos/<id>` | Один запис | 200, 404 |
| POST | `/api/repos` | Створення (обов'язкові: `github_id`, `full_name`, `html_url`) | 201, 400, 409 (дубль) |
| PATCH | `/api/repos/<id>` | Часткове оновлення (`description`, `language`, `notes`, `stars`, `forks`) | 200, 400, 404 |
| DELETE | `/api/repos/<id>` | Видалення | 200, 404 |
| GET | `/api/stats` | Статистика | 200 |

Усі відповіді, включно з помилками, — JSON (`{"error": "...", "details": {...}}`).

Приклад для Postman / curl:

```bash
curl "http://127.0.0.1:5000/api/repos?language=Python&min_stars=100&sort=stars"
curl -X POST http://127.0.0.1:5000/api/repos -H "Content-Type: application/json" \
     -d '{"github_id": 1, "full_name": "octo/demo", "html_url": "https://github.com/octo/demo", "stars": 5}'
curl -X PATCH http://127.0.0.1:5000/api/repos/1 -H "Content-Type: application/json" -d '{"notes": "цікавий проєкт"}'
```

## Зовнішній API: обмеження та політика використання

- GitHub REST API: <https://docs.github.com/en/rest>, пошук: `GET https://api.github.com/search/repositories`.
- Без токена: до 60 запитів/год на основне API і **10 запитів/хв на пошук**; з токеном — 30 запитів/хв на пошук.
- Пошук повертає не більше 1000 результатів (застосунок обмежує пагінацію відповідно).
- Обов'язковий заголовок `User-Agent` (використовується). Дані публічні, використання — навчальне.
- Застосунок кешує відповіді на 5 хвилин та показує зрозумілі повідомлення при перевищенні ліміту (429) або недоступності API (503).

## Перевірені помилкові сценарії

Порожній запит (400), неправильне сортування (400), відсутність результатів, недоступність GitHub API (503),
ліміт запитів (429), неіснуючий локальний ресурс (404, у `/api/*` — JSON), некоректний JSON/дані в API (400).
