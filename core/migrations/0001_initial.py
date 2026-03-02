"""
Initial migration — creates all base models.

FoodItem, FoodText (without search_vector), Nutrient, FoodNutrientValue,
ImportedRecord, ValidationEvent.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        # ---------------------------------------------------------------
        # FoodItem
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="FoodItem",
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
                ("canonical_key", models.CharField(max_length=255, unique=True)),
                (
                    "food_type",
                    models.CharField(
                        choices=[
                            ("raw", "Raw"),
                            ("branded", "Branded"),
                            ("supplement", "Supplement"),
                            ("recipe", "Recipe"),
                        ],
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-updated_at"],
            },
        ),
        # ---------------------------------------------------------------
        # Nutrient
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="Nutrient",
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
                ("canonical_code", models.CharField(max_length=100, unique=True)),
                ("unit", models.CharField(max_length=20)),
                ("category", models.CharField(blank=True, default="", max_length=100)),
                ("usda_nutrient_id", models.IntegerField(blank=True, null=True)),
                ("off_key", models.CharField(blank=True, max_length=100, null=True)),
            ],
            options={
                "ordering": ["canonical_code"],
            },
        ),
        # ---------------------------------------------------------------
        # FoodText
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="FoodText",
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
                (
                    "food_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="texts",
                        to="core.fooditem",
                    ),
                ),
                (
                    "lang",
                    models.CharField(
                        help_text="BCP 47 language tag, e.g. 'de'",
                        max_length=10,
                    ),
                ),
                ("name", models.CharField(max_length=500)),
                ("brand", models.CharField(blank=True, max_length=255, null=True)),
                ("ingredients", models.TextField(blank=True, null=True)),
            ],
            options={
                "ordering": ["lang"],
                "unique_together": {("food_item", "lang")},
            },
        ),
        # ---------------------------------------------------------------
        # FoodNutrientValue
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="FoodNutrientValue",
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
                (
                    "food_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nutrient_values",
                        to="core.fooditem",
                    ),
                ),
                (
                    "nutrient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="food_values",
                        to="core.nutrient",
                    ),
                ),
                (
                    "basis",
                    models.CharField(
                        choices=[
                            ("per_100g", "Per 100 g"),
                            ("per_serving", "Per Serving"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(decimal_places=4, max_digits=12),
                ),
                ("unit", models.CharField(max_length=20)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["nutrient__canonical_code"],
                "unique_together": {("food_item", "nutrient", "basis")},
            },
        ),
        # ---------------------------------------------------------------
        # ImportedRecord
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="ImportedRecord",
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
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("USDA", "USDA"),
                            ("OFF", "Open Food Facts"),
                            ("USER_REQ", "User Request"),
                        ],
                        max_length=10,
                    ),
                ),
                ("external_id", models.CharField(max_length=255)),
                ("raw_json", models.JSONField()),
                (
                    "food_item",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="imported_records",
                        to="core.fooditem",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        # ---------------------------------------------------------------
        # ValidationEvent
        # ---------------------------------------------------------------
        migrations.CreateModel(
            name="ValidationEvent",
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
                (
                    "imported_record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="validations",
                        to="core.importedrecord",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                            ("needs_review", "Needs Review"),
                        ],
                        max_length=20,
                    ),
                ),
                ("reason_code", models.CharField(max_length=100)),
                ("reason_text", models.TextField(blank=True, default="")),
                ("ai_confidence", models.FloatField(default=0.0)),
                ("suggested_patch", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
