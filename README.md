# Nutrition Core Database

Django + PostgreSQL MVP for managing a normalized nutrition database with ETL from Open Food Facts.

## Quick Start (Docker)

```bash
# 1. Start everything
docker compose up --build

# 2. Done! Services:
#    - Django Admin: http://localhost:8000/admin/
#    - REST API:     http://localhost:8000/api/
```

Default admin credentials (from `.env`):
- **User:** `admin`
- **Password:** `admin`

## Import a Product (Open Food Facts)

```bash
# Nutella
docker compose exec web python manage.py import_off_barcode 3017620422003

# Coca-Cola
docker compose exec web python manage.py import_off_barcode 5449000000996
```

This will:
1. Fetch the product from OFF API v2
2. Create an `ImportedRecord` with the raw JSON
3. Normalize into `FoodItem`, `FoodText`, `FoodNutrientValue`
4. Run validation heuristics and create a `ValidationEvent`

## REST API

### Search Foods

```bash
# Search by name (German)
curl "http://localhost:8000/api/foods/search?q=nutella&lang=de"

# Search by name (any language)
curl "http://localhost:8000/api/foods/search?q=coca"
```

### Get Food Details

```bash
curl "http://localhost:8000/api/foods/<uuid>?lang=de"
```

## Django Admin Features

### Rejected Queue

1. Go to **Admin > Core > Validation Events**
2. Use the **Review Status** filter → select **Rejected / Needs Review**
3. Detail view shows the raw JSON and suggested patch

### Admin Actions

- **Force Accept**: Select events → Action dropdown → "Force Accept selected events"
- **Link to FoodItem**: Auto-links ImportedRecord to matching FoodItem by canonical key

## Local Development (without Docker)

```bash
# 1. Create venv
python -m venv .venv && source .venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Set env vars (adjust for local Postgres)
export POSTGRES_HOST=localhost
export POSTGRES_DB=nutrition_db
export POSTGRES_USER=nutrition
export POSTGRES_PASSWORD=nutrition
export DJANGO_DEBUG=True

# 4. Run migrations
python manage.py migrate

# 5. Create superuser
python manage.py createsuperuser

# 6. Run dev server
python manage.py runserver
```

## Project Structure

```
├── config/              # Django project settings, urls, wsgi
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── core/                # Main app: models, admin, ETL
│   ├── models.py        # FoodItem, FoodText, Nutrient, etc.
│   ├── admin.py         # Admin config with Rejected Queue
│   └── management/
│       └── commands/
│           └── import_off_barcode.py
├── api/                 # REST API (DRF)
│   ├── serializers.py
│   ├── views.py
│   └── urls.py
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
└── .env
```

## Data Models

| Model              | Purpose                                      |
|---------------------|----------------------------------------------|
| `FoodItem`          | Central food entity (UUID PK, canonical key) |
| `FoodText`          | Multilingual names, brand, ingredients       |
| `Nutrient`          | Nutrient definitions (energy, protein, etc.) |
| `FoodNutrientValue` | Nutrient amounts per food (per 100g/serving) |
| `ImportedRecord`    | Raw imports from USDA/OFF with JSON          |
| `ValidationEvent`   | QA status: accepted/rejected/needs_review    |

## Environment Variables

| Variable                     | Default          | Description                    |
|------------------------------|------------------|--------------------------------|
| `POSTGRES_DB`                | `nutrition_db`   | Database name                  |
| `POSTGRES_USER`              | `nutrition`      | Database user                  |
| `POSTGRES_PASSWORD`          | `nutrition`      | Database password              |
| `POSTGRES_HOST`              | `db`             | Database host                  |
| `POSTGRES_PORT`              | `5432`           | Database port                  |
| `DJANGO_SECRET_KEY`          | (insecure default) | Django secret key            |
| `DJANGO_DEBUG`               | `True`           | Debug mode                     |
| `ALLOWED_HOSTS`              | `localhost,127.0.0.1` | Comma-separated hosts     |
| `DJANGO_SUPERUSER_USERNAME`  | —                | Auto-create superuser          |
| `DJANGO_SUPERUSER_PASSWORD`  | —                | Auto-create superuser          |
| `DJANGO_SUPERUSER_EMAIL`     | —                | Auto-create superuser          |
