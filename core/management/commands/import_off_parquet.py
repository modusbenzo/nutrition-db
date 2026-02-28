"""
Bulk import from Open Food Facts Parquet dump (Hugging Face).

Reads the Parquet file in batches to keep memory low (~100 MB).
Much faster than JSONL and not blocked by OFF.

Usage:
    # Full import (all ~3M products)
    python manage.py import_off_parquet

    # Limit to N products (for testing)
    python manage.py import_off_parquet --limit 10000

    # Skip download, use already downloaded file
    python manage.py import_off_parquet --skip-download

    # Use specific file
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

# Map OFF parquet column names -> (canonical_code, off_key for nutrient lookup)
# The parquet columns use the same keys as OFF nutriments but without _100g suffix
NUTRIENT_COLUMNS = {
    "energy-kcal_100g": "energy_kcal",
    "energy-kj_100g": "energy_kj",
    "proteins_100g": "proteins",
    "fat_100g": "fat",
    "carbohydrates_100g": "carbohydrates",
    "sugars_100g": "sugars",
    "fiber_100g": "fiber",
    "saturated-fat_100g": "saturated_fat",
    "monounsaturated-fat_100g": "monounsaturated_fat",
    "polyunsaturated-fat_100g": "polyunsaturated_fat",
    "trans-fat_100g": "trans_fat",
    "cholesterol_100g": "cholesterol",
    "salt_100g": "salt",
    "sodium_100g": "sodium",
    "calcium_100g": "calcium",
    "iron_100g": "iron",
    "magnesium_100g": "magnesium",
    "phosphorus_100g": "phosphorus",
    "potassium_100g": "potassium",
    "zinc_100g": "zinc",
    "copper_100g": "copper",
    "manganese_100g": "manganese",
    "selenium_100g": "selenium",
    "vitamin-a_100g": "vitamin_a",
    "vitamin-c_100g": "vitamin_c",
    "vitamin-d_100g": "vitamin_d",
    "vitamin-e_100g": "vitamin_e",
    "vitamin-k_100g": "vitamin_k",
    "vitamin-b1_100g": "vitamin_b1",
    "vitamin-b2_100g": "vitamin_b2",
    "vitamin-pp_100g": "vitamin_b3",
    "pantothenic-acid_100g": "vitamin_b5",
    "vitamin-b6_100g": "vitamin_b6",
    "vitamin-b9_100g": "vitamin_b9",
    "vitamin-b12_100g": "vitamin_b12",
    "alcohol_100g": "alcohol",
    "caffeine_100g": "caffeine",
}

PARQUET_BATCH_SIZE = 10_000  # rows per parquet read batch
DB_BATCH_SIZE = 500  # rows per DB transaction


class Command(BaseCommand):
    help = "Bulk import from Open Food Facts Parquet dump (Hugging Face)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nutrient_cache = {}  # canonical_code -> Nutrient

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0, help="Limit number of products (0=all)"
        )
        parser.add_argument(
            "--skip-download", action="store_true", help="Skip download"
        )
        parser.add_argument(
            "--file", type=str, default="", help="Path to existing parquet file"
        )
        parser.add_argument(
            "--batch-size", type=int, default=DB_BATCH_SIZE, help="DB batch size"
        )

    def handle(self, *args, **options):
        try:
            import pyarrow.parquet as pq
        except ImportError:
            raise CommandError("pyarrow is required. Install with: pip install pyarrow")

        limit = options["limit"]
        batch_size = options["batch_size"]
        file_path = Path(options["file"]) if options["file"] else DUMP_FILE

        # Load nutrient cache
        self._load_nutrient_cache()
        if not self._nutrient_cache:
            raise CommandError(
                "No nutrients in DB. Run 'python manage.py seed_nutrients' first."
            )
        self.stdout.write(f"Loaded {len(self._nutrient_cache)} nutrient definitions.")

        # Download if needed
        if not options["skip_download"] and not options["file"]:
            self._download(file_path)
        elif not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size
        self.stdout.write(f"Parquet file: {file_path} ({file_size / 1e9:.2f} GB)")

        # Determine which columns exist in the parquet file
        pf = pq.ParquetFile(file_path)
        available_cols = set(pf.schema.names)
        self.stdout.write(f"Total rows in file: {pf.metadata.num_rows:,}")

        # Core columns we need
        core_cols = ["code", "product_name", "brands", "ingredients_text", "lang"]
        read_cols = [c for c in core_cols if c in available_cols]

        # Nutrient columns that exist in the file
        nutrient_cols = [c for c in NUTRIENT_COLUMNS.keys() if c in available_cols]
        read_cols.extend(nutrient_cols)

        self.stdout.write(f"Reading {len(read_cols)} columns, {len(nutrient_cols)} nutrient columns.")

        # Process in batches
        stats = {
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "accepted": 0,
            "rejected": 0,
        }
        t0 = time.time()

        for batch in pf.iter_batches(batch_size=PARQUET_BATCH_SIZE, columns=read_cols):
            table = batch.to_pydict()
            num_rows = len(table.get("code", []))

            # Process in smaller DB batches
            for i in range(0, num_rows, batch_size):
                if limit and stats["processed"] >= limit:
                    break

                end = min(i + batch_size, num_rows)
                chunk = {k: v[i:end] for k, v in table.items()}
                result = self._process_chunk(chunk, nutrient_cols)

                stats["processed"] += end - i
                stats["imported"] += result["imported"]
                stats["skipped"] += result["skipped"]
                stats["errors"] += result["errors"]
                stats["accepted"] += result["accepted"]
                stats["rejected"] += result["rejected"]

            if limit and stats["processed"] >= limit:
                break

            # Progress
            elapsed = time.time() - t0
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {stats['processed']:>10,} processed | "
                f"{stats['imported']:>9,} imported | "
                f"{stats['skipped']:>9,} skipped | "
                f"{rate:,.0f}/s"
            )

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone in {elapsed:.0f}s:\n"
                f"  Processed: {stats['processed']:,}\n"
                f"  Imported:  {stats['imported']:,}\n"
                f"  Skipped:   {stats['skipped']:,}\n"
                f"  Errors:    {stats['errors']:,}\n"
                f"  Accepted:  {stats['accepted']:,}\n"
                f"  Rejected:  {stats['rejected']:,}"
            )
        )

    def _download(self, file_path: Path):
        """Download parquet from Hugging Face."""
        DUMP_DIR.mkdir(parents=True, exist_ok=True)

        if file_path.exists() and file_path.stat().st_size > 1_000_000:
            self.stdout.write(
                f"File already exists ({file_path.stat().st_size / 1e9:.2f} GB). "
                "Use --skip-download or delete to re-download."
            )
            return

        if file_path.exists():
            file_path.unlink()

        self.stdout.write(f"Downloading from Hugging Face to {file_path} ...")
        resp = requests.get(HF_PARQUET_URL, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))

        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    self.stdout.write(
                        f"\r  {downloaded / 1e6:.0f} / {total / 1e6:.0f} MB ({pct:.0f}%)",
                        ending="",
                    )
                else:
                    self.stdout.write(
                        f"\r  {downloaded / 1e6:.0f} MB ...", ending=""
                    )

        self.stdout.write(f"\nDownload complete: {downloaded / 1e6:.0f} MB")

    def _load_nutrient_cache(self):
        """Cache canonical_code -> Nutrient."""
        for n in Nutrient.objects.all():
            self._nutrient_cache[n.canonical_code] = n

    @transaction.atomic
    def _process_chunk(self, chunk, nutrient_cols):
        """Process a chunk of rows from the parquet file."""
        result = {"imported": 0, "skipped": 0, "errors": 0, "accepted": 0, "rejected": 0}

        codes = chunk.get("code", [])
        num_rows = len(codes)

        # Filter out empty barcodes and check existing
        valid_indices = []
        barcodes = []
        for idx in range(num_rows):
            barcode = str(codes[idx] or "").strip()
            if barcode and len(barcode) >= 4:
                valid_indices.append(idx)
                barcodes.append(barcode)

        if not barcodes:
            result["skipped"] = num_rows
            return result

        existing_keys = set(
            FoodItem.objects.filter(
                canonical_key__in=[f"off:{b}" for b in barcodes]
            ).values_list("canonical_key", flat=True)
        )

        for list_pos, idx in enumerate(valid_indices):
            barcode = barcodes[list_pos]
            canonical_key = f"off:{barcode}"

            if canonical_key in existing_keys:
                result["skipped"] += 1
                continue

            try:
                accepted = self._import_row(barcode, chunk, idx, nutrient_cols)
                result["imported"] += 1
                if accepted:
                    result["accepted"] += 1
                else:
                    result["rejected"] += 1
            except Exception:
                result["errors"] += 1

        result["skipped"] += num_rows - len(valid_indices)
        return result

    def _import_row(self, barcode, chunk, idx, nutrient_cols):
        """Import a single row. Returns True if accepted."""
        product_name = str(chunk.get("product_name", [None])[idx] or "").strip()
        brand = str(chunk.get("brands", [None])[idx] or "").strip() or None
        ingredients = str(chunk.get("ingredients_text", [None])[idx] or "").strip() or None
        lang = str(chunk.get("lang", [None])[idx] or "en").strip()[:10] or "en"

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

        # ImportedRecord (compact)
        raw = {"code": barcode, "product_name": product_name, "brands": brand, "lang": lang}
        record = ImportedRecord.objects.create(
            source="OFF",
            external_id=barcode,
            raw_json=raw,
            food_item=food,
        )

        # Nutrients
        nutrient_values = []
        energy_kcal = None
        for col in nutrient_cols:
            canonical_code = NUTRIENT_COLUMNS[col]
            nutrient = self._nutrient_cache.get(canonical_code)
            if not nutrient:
                continue

            raw_val = chunk[col][idx]
            if raw_val is None:
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
