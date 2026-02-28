"""
Bulk import from Open Food Facts Parquet dump (Hugging Face).

The HF parquet has nested structs:
  - nutriments: list<struct<name, value, 100g, serving, unit>>
  - product_name: list<struct<lang, text>>
  - ingredients_text: list<struct<lang, text>>

Usage:
    python manage.py import_off_parquet --limit 10000
    python manage.py import_off_parquet --skip-download
    python manage.py import_off_parquet --file /path/to/food.parquet
"""

import time
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

HF_PARQUET_URL = (
    "https://huggingface.co/datasets/openfoodfacts/product-database"
    "/resolve/main/food.parquet?download=true"
)
DUMP_DIR = Path("/tmp/nutrition-imports")
DUMP_FILE = DUMP_DIR / "food.parquet"

# OFF nutriment name -> our canonical_code
NUTRIENT_NAME_MAP = {
    "energy-kcal": "energy_kcal",
    "energy-kj": "energy_kj",
    "proteins": "proteins",
    "fat": "fat",
    "carbohydrates": "carbohydrates",
    "sugars": "sugars",
    "fiber": "fiber",
    "saturated-fat": "saturated_fat",
    "monounsaturated-fat": "monounsaturated_fat",
    "polyunsaturated-fat": "polyunsaturated_fat",
    "trans-fat": "trans_fat",
    "cholesterol": "cholesterol",
    "salt": "salt",
    "sodium": "sodium",
    "calcium": "calcium",
    "iron": "iron",
    "magnesium": "magnesium",
    "phosphorus": "phosphorus",
    "potassium": "potassium",
    "zinc": "zinc",
    "copper": "copper",
    "manganese": "manganese",
    "selenium": "selenium",
    "vitamin-a": "vitamin_a",
    "vitamin-c": "vitamin_c",
    "vitamin-d": "vitamin_d",
    "vitamin-e": "vitamin_e",
    "vitamin-k": "vitamin_k",
    "vitamin-b1": "vitamin_b1",
    "vitamin-b2": "vitamin_b2",
    "vitamin-pp": "vitamin_b3",
    "pantothenic-acid": "vitamin_b5",
    "vitamin-b6": "vitamin_b6",
    "vitamin-b9": "vitamin_b9",
    "vitamin-b12": "vitamin_b12",
    "alcohol": "alcohol",
    "caffeine": "caffeine",
}

PARQUET_BATCH_SIZE = 5_000
DB_BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Bulk import from Open Food Facts Parquet dump (Hugging Face)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nutrient_cache = {}  # canonical_code -> Nutrient

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--skip-download", action="store_true")
        parser.add_argument("--file", type=str, default="")
        parser.add_argument("--batch-size", type=int, default=DB_BATCH_SIZE)

    def handle(self, *args, **options):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise CommandError("pyarrow required: pip install pyarrow")

        limit = options["limit"]
        batch_size = options["batch_size"]
        file_path = Path(options["file"]) if options["file"] else DUMP_FILE

        self._load_nutrient_cache()
        if not self._nutrient_cache:
            raise CommandError("No nutrients in DB. Run 'python manage.py seed_nutrients' first.")
        self.stdout.write(f"Loaded {len(self._nutrient_cache)} nutrient definitions.")

        if not options["skip_download"] and not options["file"]:
            self._download(file_path)
        elif not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        pf = pq.ParquetFile(file_path)
        total_rows = pf.metadata.num_rows
        self.stdout.write(f"Parquet: {file_size / 1e9:.2f} GB, {total_rows:,} rows")

        # Read columns we need
        read_cols = ["code", "product_name", "brands", "ingredients_text", "lang", "nutriments"]
        available = set(pf.schema.names)
        read_cols = [c for c in read_cols if c in available]
        self.stdout.write(f"Reading columns: {read_cols}")

        stats = {"processed": 0, "imported": 0, "skipped": 0, "errors": 0, "accepted": 0, "rejected": 0}
        t0 = time.time()

        for batch in pf.iter_batches(batch_size=PARQUET_BATCH_SIZE, columns=read_cols):
            rows = batch.to_pydict()
            num_rows = len(rows.get("code", []))

            for i in range(0, num_rows, batch_size):
                if limit and stats["processed"] >= limit:
                    break

                end = min(i + batch_size, num_rows)
                chunk = {k: v[i:end] for k, v in rows.items()}
                result = self._process_chunk(chunk)

                stats["processed"] += end - i
                stats["imported"] += result["imported"]
                stats["skipped"] += result["skipped"]
                stats["errors"] += result["errors"]
                stats["accepted"] += result["accepted"]
                stats["rejected"] += result["rejected"]

            if limit and stats["processed"] >= limit:
                break

            elapsed = time.time() - t0
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {stats['processed']:>10,} processed | "
                f"{stats['imported']:>9,} imported | "
                f"{stats['errors']:>6,} errors | "
                f"{rate:,.0f}/s"
            )

        elapsed = time.time() - t0
        self.stdout.write(self.style.SUCCESS(
            f"\nDone in {elapsed:.0f}s:\n"
            f"  Processed: {stats['processed']:,}\n"
            f"  Imported:  {stats['imported']:,}\n"
            f"  Skipped:   {stats['skipped']:,}\n"
            f"  Errors:    {stats['errors']:,}\n"
            f"  Accepted:  {stats['accepted']:,}\n"
            f"  Rejected:  {stats['rejected']:,}"
        ))

    def _download(self, file_path: Path):
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        if file_path.exists() and file_path.stat().st_size > 1_000_000:
            self.stdout.write(f"Already exists ({file_path.stat().st_size / 1e9:.2f} GB)")
            return
        if file_path.exists():
            file_path.unlink()

        self.stdout.write("Downloading from Hugging Face ...")
        resp = requests.get(HF_PARQUET_URL, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    self.stdout.write(f"\r  {downloaded / 1e6:.0f}/{total / 1e6:.0f} MB", ending="")
        self.stdout.write(f"\nDownload complete: {downloaded / 1e6:.0f} MB")

    def _load_nutrient_cache(self):
        for n in Nutrient.objects.all():
            self._nutrient_cache[n.canonical_code] = n

    def _extract_lang_text(self, lang_text_list, preferred_lang=None):
        """Extract text from list<struct<lang, text>>."""
        if not lang_text_list:
            return "", ""
        # Try preferred lang first
        if preferred_lang:
            for item in lang_text_list:
                if isinstance(item, dict) and item.get("lang") == preferred_lang:
                    return item.get("text", ""), preferred_lang
        # Fall back to first non-empty entry
        for item in lang_text_list:
            if isinstance(item, dict):
                text = (item.get("text") or "").strip()
                if text:
                    return text, item.get("lang", "en")
        return "", "en"

    def _extract_nutriments(self, nutriments_list):
        """Extract nutriments from list<struct<name, value, 100g, serving, unit>>."""
        result = {}
        if not nutriments_list:
            return result
        for item in nutriments_list:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            val_100g = item.get("100g")
            if name and val_100g is not None:
                canonical = NUTRIENT_NAME_MAP.get(name)
                if canonical:
                    result[canonical] = val_100g
        return result

    @transaction.atomic
    def _process_chunk(self, chunk):
        result = {"imported": 0, "skipped": 0, "errors": 0, "accepted": 0, "rejected": 0}

        codes = chunk.get("code", [])
        num_rows = len(codes)

        # Filter valid barcodes
        valid = []
        for idx in range(num_rows):
            barcode = str(codes[idx] or "").strip()
            if barcode and len(barcode) >= 4:
                valid.append((idx, barcode))
            else:
                result["skipped"] += 1

        if not valid:
            return result

        # Check existing
        existing = set(
            FoodItem.objects.filter(
                canonical_key__in=[f"off:{b}" for _, b in valid]
            ).values_list("canonical_key", flat=True)
        )

        for idx, barcode in valid:
            if f"off:{barcode}" in existing:
                result["skipped"] += 1
                continue
            try:
                accepted = self._import_row(barcode, chunk, idx)
                result["imported"] += 1
                if accepted:
                    result["accepted"] += 1
                else:
                    result["rejected"] += 1
            except Exception as e:
                result["errors"] += 1

        return result

    def _import_row(self, barcode, chunk, idx):
        lang_hint = str(chunk.get("lang", [None])[idx] or "en").strip()[:10] or "en"

        # Product name (nested)
        product_name_list = chunk.get("product_name", [None])[idx]
        product_name, name_lang = self._extract_lang_text(product_name_list, lang_hint)
        product_name = product_name.strip()

        # Brand (simple string)
        brand = str(chunk.get("brands", [None])[idx] or "").strip() or None

        # Ingredients (nested)
        ingredients_list = chunk.get("ingredients_text", [None])[idx]
        ingredients, _ = self._extract_lang_text(ingredients_list, lang_hint)
        ingredients = ingredients.strip() or None

        lang = name_lang or lang_hint

        # Nutriments (nested)
        nutriments_list = chunk.get("nutriments", [None])[idx]
        nutriments = self._extract_nutriments(nutriments_list)

        # FoodItem
        food = FoodItem.objects.create(
            canonical_key=f"off:{barcode}",
            food_type="branded",
        )

        # FoodText
        FoodText.objects.create(
            food_item=food,
            lang=lang,
            name=product_name or f"Unknown ({barcode})",
            brand=brand,
            ingredients=ingredients,
        )

        # ImportedRecord
        record = ImportedRecord.objects.create(
            source="OFF",
            external_id=barcode,
            raw_json={
                "code": barcode,
                "product_name": product_name,
                "brands": brand,
                "lang": lang,
                "nutriment_count": len(nutriments),
            },
            food_item=food,
        )

        # Nutrients
        nutrient_values = []
        energy_kcal = None
        for canonical_code, raw_val in nutriments.items():
            nutrient = self._nutrient_cache.get(canonical_code)
            if not nutrient:
                continue
            try:
                amount = Decimal(str(raw_val))
            except (InvalidOperation, ValueError, TypeError):
                continue

            if canonical_code == "energy_kcal":
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
        if not product_name:
            reasons.append(("empty_name", "Product name is empty"))
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
