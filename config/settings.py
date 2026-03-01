"""Django settings for Nutrition Core Database."""

import os
from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-change-me-in-production-abc123",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "nutrition_db"),
        "USER": os.environ.get("POSTGRES_USER", "nutrition"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "nutrition"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# ---------------------------------------------------------------------------
# Unfold Admin Theme
# ---------------------------------------------------------------------------
UNFOLD = {
    "SITE_TITLE": "Nutrition DB",
    "SITE_HEADER": "Nutrition Core Database",
    "SITE_SYMBOL": "nutrition",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Lebensmittel",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Lebensmittel",
                        "icon": "restaurant",
                        "link": reverse_lazy("admin:core_fooditem_changelist"),
                    },
                    {
                        "title": "Texte / Sprachen",
                        "icon": "translate",
                        "link": reverse_lazy("admin:core_foodtext_changelist"),
                    },
                    {
                        "title": "Nahrstoffe",
                        "icon": "science",
                        "link": reverse_lazy("admin:core_nutrient_changelist"),
                    },
                    {
                        "title": "Nahrwerte",
                        "icon": "monitoring",
                        "link": reverse_lazy("admin:core_foodnutrientvalue_changelist"),
                    },
                ],
            },
            {
                "title": "Import & QA",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Importierte Datensatze",
                        "icon": "cloud_download",
                        "link": reverse_lazy("admin:core_importedrecord_changelist"),
                    },
                    {
                        "title": "Rejected Queue",
                        "icon": "flag",
                        "link": reverse_lazy("admin:core_validationevent_changelist"),
                        "badge": "core.admin.rejected_count",
                    },
                ],
            },
            {
                "title": "System",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Benutzer",
                        "icon": "people",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Gruppen",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
        ],
    },
}
