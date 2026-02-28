"""
Seed the Nutrient table with standard nutrient definitions.
Maps both OFF and USDA keys to canonical codes.

Usage:
    python manage.py seed_nutrients
"""

from django.core.management.base import BaseCommand

from core.models import Nutrient

# (canonical_code, unit, category, usda_nutrient_id, off_key)
NUTRIENTS = [
    # Energy
    ("energy_kcal", "kcal", "energy", 1008, "energy-kcal_100g"),
    ("energy_kj", "kJ", "energy", 1062, "energy-kj_100g"),
    # Macronutrients
    ("proteins", "g", "macronutrient", 1003, "proteins_100g"),
    ("fat", "g", "macronutrient", 1004, "fat_100g"),
    ("carbohydrates", "g", "macronutrient", 1005, "carbohydrates_100g"),
    ("sugars", "g", "macronutrient", 2000, "sugars_100g"),
    ("fiber", "g", "macronutrient", 1079, "fiber_100g"),
    ("saturated_fat", "g", "macronutrient", 1258, "saturated-fat_100g"),
    ("monounsaturated_fat", "g", "macronutrient", 1292, "monounsaturated-fat_100g"),
    ("polyunsaturated_fat", "g", "macronutrient", 1293, "polyunsaturated-fat_100g"),
    ("trans_fat", "g", "macronutrient", 1257, "trans-fat_100g"),
    ("cholesterol", "mg", "macronutrient", 1253, "cholesterol_100g"),
    # Minerals
    ("salt", "g", "mineral", None, "salt_100g"),
    ("sodium", "mg", "mineral", 1093, "sodium_100g"),
    ("calcium", "mg", "mineral", 1087, "calcium_100g"),
    ("iron", "mg", "mineral", 1089, "iron_100g"),
    ("magnesium", "mg", "mineral", 1090, "magnesium_100g"),
    ("phosphorus", "mg", "mineral", 1091, "phosphorus_100g"),
    ("potassium", "mg", "mineral", 1092, "potassium_100g"),
    ("zinc", "mg", "mineral", 1095, "zinc_100g"),
    ("copper", "mg", "mineral", 1098, "copper_100g"),
    ("manganese", "mg", "mineral", 1101, "manganese_100g"),
    ("selenium", "µg", "mineral", 1103, "selenium_100g"),
    ("iodine", "µg", "mineral", 1100, "iodine_100g"),
    # Vitamins
    ("vitamin_a", "µg", "vitamin", 1106, "vitamin-a_100g"),
    ("vitamin_b1", "mg", "vitamin", 1165, "vitamin-b1_100g"),
    ("vitamin_b2", "mg", "vitamin", 1166, "vitamin-b2_100g"),
    ("vitamin_b3", "mg", "vitamin", 1167, "vitamin-pp_100g"),
    ("vitamin_b5", "mg", "vitamin", 1170, "pantothenic-acid_100g"),
    ("vitamin_b6", "mg", "vitamin", 1175, "vitamin-b6_100g"),
    ("vitamin_b9", "µg", "vitamin", 1177, "vitamin-b9_100g"),
    ("vitamin_b12", "µg", "vitamin", 1178, "vitamin-b12_100g"),
    ("vitamin_c", "mg", "vitamin", 1162, "vitamin-c_100g"),
    ("vitamin_d", "µg", "vitamin", 1114, "vitamin-d_100g"),
    ("vitamin_e", "mg", "vitamin", 1109, "vitamin-e_100g"),
    ("vitamin_k", "µg", "vitamin", 1185, "vitamin-k_100g"),
    # Other
    ("alcohol", "g", "other", 1018, "alcohol_100g"),
    ("caffeine", "mg", "other", 1057, "caffeine_100g"),
    ("water", "g", "other", 1051, None),
]


class Command(BaseCommand):
    help = "Seed the Nutrient table with standard definitions"

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for code, unit, category, usda_id, off_key in NUTRIENTS:
            _, created = Nutrient.objects.update_or_create(
                canonical_code=code,
                defaults={
                    "unit": unit,
                    "category": category,
                    "usda_nutrient_id": usda_id,
                    "off_key": off_key,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated, "
                f"{len(NUTRIENTS)} total nutrients."
            )
        )
