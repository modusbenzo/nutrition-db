"""
Bulk import from USDA FoodData Central CSV dump.

Downloads the FoodData Central CSV bundle and imports Foundation Foods,
SR Legacy, and Branded Foods.

Usage:
    # Full import
    python manage.py import_usda_dump

    # Limit to N foods (for testing)
    python manage.py import_usda_dump --limit 10000

    # Skip download
    python manage.py import_usda_dump --skip-download

    # Use specific ZIP file
    python manage.py import_usda_dump --file /path/to/FoodData_Central_csv.zip
"""

import csv
import io
import json
import os
import time
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

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

# USDA releases updates regularly; this is the latest stable URL pattern.
USDA_DUMP_URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_csv_2024-10-31.zip"
DUMP_DIR = Path("/tmp/nutrition-imports")
DUMP_FILE = DUMP_DIR / "FoodData_Central_csv.zip"

BATCH_SIZE = 500

# USDA data_type values we care about
RELEVANT_DATA_TYPES = {"foundation_food", "sr_legacy_food", "branded_food", "survey_fndds_food"}

# Map USDA data_type to our food_type
DATA_TYPE_MAP = {
    "foundation_food": "raw",
    "sr_legacy_food": "raw",
    "branded_food": "branded",
    "survey_fndds_food": "raw",
}


class Command(BaseCommand):
    help = "Bulk import from USDA FoodData Central CSV dump"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nutrient_cache = {}  # usda_nutrient_id -> Nutrient

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0, help="Limit number of foods (0=all)"
        )
        parser.add_argument(
            "--skip-download", action="store_true", help="Skip download, use cached file"
        )
        parser.add_argument(
            "--file", type=str, default="", help="Path to existing ZIP file"
        )
        parser.add_argument(
            "--url", type=str, default=USDA_DUMP_URL, help="URL to USDA CSV ZIP"
        )
        parser.add_argument(
            "--batch-size", type=int, default=BATCH_SIZE, help="DB batch size"
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_size = options["batch_size"]
        file_path = Path(options["file"]) if options["file"] else DUMP_FILE

        # Ensure nutrient definitions exist
        self._load_nutrient_cache()
        if not self._nutrient_cache:
            raise CommandError(
                "No nutrients with usda_nutrient_id in DB. "
                "Run 'python manage.py seed_nutrients' first."
            )
        self.stdout.write(f"Loaded {len(self._nutrient_cache)} USDA nutrient mappings.")

        # Download
        if not options["skip_download"] and not options["file"]:
            self._download_dump(options["url"], file_path)
        elif not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        self.stdout.write(f"Reading ZIP: {file_path}")

        with zipfile.ZipFile(file_path, "r") as zf:
            csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
            self.stdout.write(f"  Found {len(csv_files)} CSV files in archive.")

            # 1) Load food.csv → build food index
            foods = self._read_foods(zf, limit)
            self.stdout.write(f"  Loaded {len(foods)} foods to import.")

            # 2) Load branded_food.csv for brand info
            brands = self._read_brands(zf, set(foods.keys()))
            self.stdout.write(f"  Loaded {len(brands)} brand entries.")

            # 3) Load food_nutrient.csv
            nutrients_data = self._read_food_nutrients(zf, set(foods.keys()))
            self.stdout.write(
                f"  Loaded nutrient data for {len(nutrients_data)} foods."
            )

        # 4) Import in batches
        self.stdout.write("Importing into database ...")
        stats = {"imported": 0, "skipped": 0, "accepted": 0, "rejected": 0}
        t0 = time.time()

        food_ids = list(foods.keys())
        for i in range(0, len(food_ids), batch_size):
            batch_ids = food_ids[i : i + batch_size]
            result = self._import_batch(batch_ids, foods, brands, nutrients_data)
            stats["imported"] += result["imported"]
            stats["skipped"] += result["skipped"]
            stats["accepted"] += result["accepted"]
            stats["rejected"] += result["rejected"]

            if (i + batch_size) % 5000 == 0 or i + batch_size >= len(food_ids):
                elapsed = time.time() - t0
                self.stdout.write(
                    f"  {min(i + batch_size, len(food_ids)):>8,} / {len(food_ids):,} "
                    f"| imported: {stats['imported']:,} "
                    f"| {elapsed:.0f}s"
                )

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone in {elapsed:.0f}s:\n"
                f"  Imported:  {stats['imported']:,}\n"
                f"  Skipped:   {stats['skipped']:,}\n"
                f"  Accepted:  {stats['accepted']:,}\n"
                f"  Rejected:  {stats['rejected']:,}"
            )
        )

    def _download_dump(self, url: str, file_path: Path):
        """Download USDA dump."""
        DUMP_DIR.mkdir(parents=True, exist_ok=True)

        if file_path.exists():
            self.stdout.write(f"Dump already exists: {file_path}")
            return

        self.stdout.write(f"Downloading USDA dump from:\n  {url}")
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))

        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    self.stdout.write(
                        f"\r  {downloaded / 1e6:.0f} / {total / 1e6:.0f} MB ({pct:.0f}%)",
                        ending="",
                    )
        self.stdout.write(f"\nDownload complete: {downloaded / 1e6:.0f} MB")

    def _load_nutrient_cache(self):
        """Cache usda_nutrient_id -> Nutrient."""
        for n in Nutrient.objects.exclude(usda_nutrient_id__isnull=True):
            self._nutrient_cache[n.usda_nutrient_id] = n

    def _read_csv_from_zip(self, zf, filename):
        """Read a CSV from the ZIP, return a csv.DictReader."""
        # Find the file (might be in a subdirectory)
        matching = [n for n in zf.namelist() if n.endswith(f"/{filename}") or n == filename]
        if not matching:
            return None
        with zf.open(matching[0]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8")
            reader = csv.DictReader(text)
            return list(reader)

    def _read_foods(self, zf, limit):
        """Read food.csv → {fdc_id: {description, data_type, ...}}."""
        rows = self._read_csv_from_zip(zf, "food.csv")
        if not rows:
            raise CommandError("food.csv not found in ZIP")

        foods = {}
        for row in rows:
            data_type = row.get("data_type", "").strip()
            if data_type not in RELEVANT_DATA_TYPES:
                continue

            fdc_id = row.get("fdc_id", "").strip()
            if not fdc_id:
                continue

            foods[fdc_id] = {
                "description": row.get("description", "").strip(),
                "data_type": data_type,
                "food_category_id": row.get("food_category_id", "").strip(),
            }

            if limit and len(foods) >= limit:
                break

        return foods

    def _read_brands(self, zf, fdc_ids):
        """Read branded_food.csv → {fdc_id: {brand_owner, ingredients}}."""
        rows = self._read_csv_from_zip(zf, "branded_food.csv")
        if not rows:
            return {}

        brands = {}
        for row in rows:
            fdc_id = row.get("fdc_id", "").strip()
            if fdc_id not in fdc_ids:
                continue
            brands[fdc_id] = {
                "brand_owner": (row.get("brand_owner") or "").strip() or None,
                "brand_name": (row.get("brand_name") or "").strip() or None,
                "ingredients": (row.get("ingredients") or "").strip() or None,
                "gtin_upc": (row.get("gtin_upc") or "").strip() or None,
            }
        return brands

    def _read_food_nutrients(self, zf, fdc_ids):
        """Read food_nutrient.csv → {fdc_id: [(nutrient_id, amount), ...]}."""
        rows = self._read_csv_from_zip(zf, "food_nutrient.csv")
        if not rows:
            raise CommandError("food_nutrient.csv not found in ZIP")

        data = {}
        for row in rows:
            fdc_id = row.get("fdc_id", "").strip()
            if fdc_id not in fdc_ids:
                continue

            nutrient_id_str = row.get("nutrient_id", "").strip()
            amount_str = row.get("amount", "").strip()
            if not nutrient_id_str or not amount_str:
                continue

            try:
                nutrient_id = int(nutrient_id_str)
                amount = Decimal(amount_str)
            except (ValueError, InvalidOperation):
                continue

            # Only keep nutrients we care about
            if nutrient_id not in self._nutrient_cache:
                continue

            if fdc_id not in data:
                data[fdc_id] = []
            data[fdc_id].append((nutrient_id, amount))

        return data

    @transaction.atomic
    def _import_batch(self, batch_ids, foods, brands, nutrients_data):
        """Import a batch of USDA foods."""
        result = {"imported": 0, "skipped": 0, "accepted": 0, "rejected": 0}

        # Check existing
        canonical_keys = [f"usda:{fdc_id}" for fdc_id in batch_ids]
        existing = set(
            FoodItem.objects.filter(
                canonical_key__in=canonical_keys
            ).values_list("canonical_key", flat=True)
        )

        for fdc_id in batch_ids:
            canonical_key = f"usda:{fdc_id}"
            if canonical_key in existing:
                result["skipped"] += 1
                continue

            food_data = foods[fdc_id]
            brand_data = brands.get(fdc_id, {})
            nutrient_list = nutrients_data.get(fdc_id, [])

            try:
                accepted = self._import_food(
                    fdc_id, food_data, brand_data, nutrient_list
                )
                result["imported"] += 1
                if accepted:
                    result["accepted"] += 1
                else:
                    result["rejected"] += 1
            except Exception:
                result["skipped"] += 1

        return result

    def _import_food(self, fdc_id, food_data, brand_data, nutrient_list):
        """Import a single USDA food. Returns True if accepted."""
        description = food_data["description"]
        data_type = food_data["data_type"]
        food_type = DATA_TYPE_MAP.get(data_type, "raw")

        # FoodItem
        food = FoodItem.objects.create(
            canonical_key=f"usda:{fdc_id}",
            food_type=food_type,
        )

        # FoodText (USDA is English)
        brand_name = brand_data.get("brand_owner") or brand_data.get("brand_name")
        FoodText.objects.create(
            food_item=food,
            lang="en",
            name=description or f"USDA {fdc_id}",
            brand=brand_name,
            ingredients=brand_data.get("ingredients"),
        )

        # ImportedRecord
        record = ImportedRecord.objects.create(
            source="USDA",
            external_id=str(fdc_id),
            raw_json={
                "fdc_id": fdc_id,
                "description": description,
                "data_type": data_type,
                "brand": brand_data,
                "nutrient_count": len(nutrient_list),
            },
            food_item=food,
        )

        # Nutrients
        nutrient_values = []
        energy_kcal = None
        for nutrient_id, amount in nutrient_list:
            nutrient = self._nutrient_cache.get(nutrient_id)
            if not nutrient:
                continue
            if nutrient.canonical_code == "energy_kcal":
                energy_kcal = float(amount)
            nutrient_values.append(
                FoodNutrientValue(
                    food_item=food,
                    nutrient=nutrient,
                    basis="per_100g",
                    amount=amount,
                    unit=nutrient.unit,
                )
            )

        if nutrient_values:
            FoodNutrientValue.objects.bulk_create(nutrient_values)

        # Validation
        reasons = []
        if not description:
            reasons.append(("empty_name", "Description is empty"))
        if energy_kcal is not None and energy_kcal > 900:
            reasons.append(("energy_too_high", f"energy-kcal={energy_kcal} > 900"))

        accepted = len(reasons) == 0
        ValidationEvent.objects.create(
            imported_record=record,
            status="accepted" if accepted else "rejected",
            reason_code=reasons[0][0] if reasons else "auto_accepted",
            reason_text="; ".join(r[1] for r in reasons) if reasons else "Passed",
            ai_confidence=0.9 if accepted else 0.6,
            suggested_patch={},
        )

        return accepted
