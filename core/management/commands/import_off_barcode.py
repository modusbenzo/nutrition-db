"""
Management command to import a product from Open Food Facts by barcode.

Usage:
    python manage.py import_off_barcode 3017620422003
"""

from decimal import Decimal, InvalidOperation

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    FoodItem,
    FoodNutrientValue,
    FoodText,
    ImportedRecord,
    Nutrient,
    ValidationEvent,
)

# Maps OFF nutrient keys -> (canonical_code, unit, category)
NUTRIENT_MAP = {
    "energy-kcal_100g": ("energy_kcal", "kcal", "energy"),
    "proteins_100g": ("proteins", "g", "macronutrient"),
    "fat_100g": ("fat", "g", "macronutrient"),
    "carbohydrates_100g": ("carbohydrates", "g", "macronutrient"),
    "sugars_100g": ("sugars", "g", "macronutrient"),
    "fiber_100g": ("fiber", "g", "macronutrient"),
    "salt_100g": ("salt", "g", "mineral"),
}

OFF_API_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"


class Command(BaseCommand):
    help = "Import a product from Open Food Facts by barcode"

    def add_arguments(self, parser):
        parser.add_argument("barcode", type=str, help="EAN / barcode of the product")

    def handle(self, *args, **options):
        barcode = options["barcode"].strip()
        self.stdout.write(f"Fetching barcode {barcode} from Open Food Facts ...")

        resp = requests.get(
            OFF_API_URL.format(barcode=barcode),
            headers={"User-Agent": "NutritionCoreDB/1.0"},
            timeout=15,
        )
        if resp.status_code != 200:
            raise CommandError(f"OFF API returned HTTP {resp.status_code}")

        data = resp.json()
        if data.get("status") != 1:
            raise CommandError(f"Product not found: {barcode}")

        product = data.get("product", {})

        with transaction.atomic():
            self._import(barcode, product, data)

        self.stdout.write(self.style.SUCCESS(f"Done: {barcode}"))

    def _import(self, barcode: str, product: dict, raw_data: dict):
        # 1) ImportedRecord
        record = ImportedRecord.objects.create(
            source="OFF",
            external_id=barcode,
            raw_json=raw_data,
        )
        self.stdout.write(f"  ImportedRecord created: {record.id}")

        # 2) Extract names / lang
        product_name = (product.get("product_name") or "").strip()
        brand = (product.get("brands") or "").strip() or None
        ingredients = (product.get("ingredients_text") or "").strip() or None
        lang = (product.get("lang") or "en").strip()[:10]

        # 3) FoodItem
        canonical_key = f"off:{barcode}"
        food, created = FoodItem.objects.get_or_create(
            canonical_key=canonical_key,
            defaults={"food_type": "branded"},
        )
        if created:
            self.stdout.write(f"  FoodItem created: {food.id}")
        else:
            self.stdout.write(f"  FoodItem exists: {food.id}")

        record.food_item = food
        record.save(update_fields=["food_item"])

        # 4) FoodText
        FoodText.objects.update_or_create(
            food_item=food,
            lang=lang,
            defaults={
                "name": product_name or f"Unknown ({barcode})",
                "brand": brand,
                "ingredients": ingredients,
            },
        )

        # 5) Nutrients
        nutriments = product.get("nutriments") or {}
        for off_key, (code, unit, category) in NUTRIENT_MAP.items():
            raw_val = nutriments.get(off_key)
            if raw_val is None:
                continue
            try:
                amount = Decimal(str(raw_val))
            except (InvalidOperation, ValueError):
                continue

            nutrient, _ = Nutrient.objects.get_or_create(
                canonical_code=code,
                defaults={"unit": unit, "category": category, "off_key": off_key},
            )
            FoodNutrientValue.objects.update_or_create(
                food_item=food,
                nutrient=nutrient,
                basis="per_100g",
                defaults={"amount": amount, "unit": unit},
            )

        # 6) Validation heuristic
        reasons = []
        energy = nutriments.get("energy-kcal_100g")
        if not product_name:
            reasons.append(("empty_name", "Product name is empty"))
        if energy is not None:
            try:
                if float(energy) > 900:
                    reasons.append(("energy_too_high", f"energy-kcal={energy} > 900 per 100 g"))
            except (ValueError, TypeError):
                pass

        if reasons:
            status_val = "rejected"
            reason_code = reasons[0][0]
            reason_text = "; ".join(r[1] for r in reasons)
            confidence = 0.6
        else:
            status_val = "accepted"
            reason_code = "auto_accepted"
            reason_text = "Passed basic heuristics"
            confidence = 0.9

        ValidationEvent.objects.create(
            imported_record=record,
            status=status_val,
            reason_code=reason_code,
            reason_text=reason_text,
            ai_confidence=confidence,
            suggested_patch={},
        )
        self.stdout.write(f"  ValidationEvent: {status_val} ({reason_code})")
