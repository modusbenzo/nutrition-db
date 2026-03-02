"""
Add SearchVectorField to FoodText with GIN indexes (FTS + trigram),
auto-update DB trigger for search_vector, FoodRequest model,
and ImportedRecord compound index.
"""

import uuid

import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.deletion
from django.db import migrations, models


# -----------------------------------------------------------------------
# DB trigger: auto-update search_vector on INSERT/UPDATE of FoodText
# -----------------------------------------------------------------------
TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION foodtext_search_vector_update() RETURNS trigger AS $$
DECLARE
    pg_lang regconfig;
BEGIN
    pg_lang := CASE NEW.lang
        WHEN 'de' THEN 'german'::regconfig
        WHEN 'en' THEN 'english'::regconfig
        WHEN 'fr' THEN 'french'::regconfig
        WHEN 'es' THEN 'spanish'::regconfig
        WHEN 'it' THEN 'italian'::regconfig
        WHEN 'pt' THEN 'portuguese'::regconfig
        WHEN 'nl' THEN 'dutch'::regconfig
        WHEN 'sv' THEN 'swedish'::regconfig
        WHEN 'ru' THEN 'russian'::regconfig
        WHEN 'tr' THEN 'turkish'::regconfig
        WHEN 'da' THEN 'danish'::regconfig
        WHEN 'fi' THEN 'finnish'::regconfig
        WHEN 'hu' THEN 'hungarian'::regconfig
        WHEN 'no' THEN 'norwegian'::regconfig
        WHEN 'ro' THEN 'romanian'::regconfig
        ELSE 'simple'::regconfig
    END;

    NEW.search_vector :=
        setweight(to_tsvector(pg_lang, COALESCE(NEW.name, '')), 'A') ||
        setweight(to_tsvector(pg_lang, COALESCE(NEW.brand, '')), 'B');

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER_SQL = """
DROP TRIGGER IF EXISTS foodtext_search_vector_trigger ON core_foodtext;
CREATE TRIGGER foodtext_search_vector_trigger
    BEFORE INSERT OR UPDATE OF name, brand, lang
    ON core_foodtext
    FOR EACH ROW
    EXECUTE FUNCTION foodtext_search_vector_update();
"""

DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS foodtext_search_vector_trigger ON core_foodtext;
DROP FUNCTION IF EXISTS foodtext_search_vector_update();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_search_extensions"),
    ]

    operations = [
        # ---------------------------------------------------------------
        # FoodText: add search_vector column (IF NOT EXISTS for idempotency)
        # ---------------------------------------------------------------
        migrations.RunSQL(
            sql="ALTER TABLE core_foodtext ADD COLUMN IF NOT EXISTS search_vector tsvector;",
            reverse_sql="ALTER TABLE core_foodtext DROP COLUMN IF EXISTS search_vector;",
            state_operations=[
                migrations.AddField(
                    model_name="foodtext",
                    name="search_vector",
                    field=django.contrib.postgres.search.SearchVectorField(
                        blank=True, null=True
                    ),
                ),
            ],
        ),
        # ---------------------------------------------------------------
        # FoodText: GIN indexes for FTS + trigram fuzzy search
        # ---------------------------------------------------------------
        migrations.AddIndex(
            model_name="foodtext",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"],
                name="foodtext_search_gin",
            ),
        ),
        migrations.AddIndex(
            model_name="foodtext",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["name"],
                name="foodtext_name_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.AddIndex(
            model_name="foodtext",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["brand"],
                name="foodtext_brand_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        # ---------------------------------------------------------------
        # DB Trigger for auto-updating search_vector
        # ---------------------------------------------------------------
        migrations.RunSQL(
            sql=TRIGGER_FUNCTION_SQL + TRIGGER_SQL,
            reverse_sql=DROP_TRIGGER_SQL,
        ),
        # ---------------------------------------------------------------
        # FoodRequest model
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="FoodRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("original_query", models.CharField(max_length=500)),
                ("lang", models.CharField(default="de", max_length=10)),
                (
                    "submitted_name",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                (
                    "submitted_brand",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "submitted_barcode",
                    models.CharField(blank=True, default="", max_length=50),
                ),
                (
                    "submitted_nutrients",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Dict of canonical_code -> amount per 100g",
                    ),
                ),
                (
                    "submitted_source_url",
                    models.URLField(blank=True, default="", max_length=1000),
                ),
                (
                    "submitted_raw_data",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("auto_created", "Auto-Created"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                (
                    "food_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="food_requests",
                        to="core.fooditem",
                    ),
                ),
                ("ai_confidence", models.FloatField(default=0.0)),
                ("ai_review_notes", models.TextField(blank=True, default="")),
                ("request_count", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-request_count", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="foodrequest",
            index=models.Index(
                fields=["status", "-request_count"],
                name="foodreq_status_count",
            ),
        ),
        migrations.AddIndex(
            model_name="foodrequest",
            index=models.Index(
                fields=["submitted_barcode"],
                name="foodreq_barcode",
            ),
        ),
        # ---------------------------------------------------------------
        # ImportedRecord: compound index on (source, external_id)
        # ---------------------------------------------------------------
        migrations.AddIndex(
            model_name="importedrecord",
            index=models.Index(
                fields=["source", "external_id"],
                name="importrec_source_extid",
            ),
        ),
    ]
