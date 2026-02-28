"""
Bulk import from Open Food Facts JSONL data dump.

Downloads the full OFF dump (~7 GB gzip) and streams it line by line
to keep memory usage low. Processes in batches for DB performance.

Usage:
    # Full import (all ~3M products)
    python manage.py import_off_dump

    # Limit to N products (for testing)
    python manage.py import_off_dump --limit 10000

    # Skip download if file already exists
    python manage.py import_off_dump --skip-download

    # Use existing file
    python manage.py import_off_dump --file /path/to/dump.jsonl.gz
"""

import gzip
import json
import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.models import (
    FoodItem,
    FoodNutrientValue,
    FoodText,
    ImportedRecord,
    Nutrient,
    ValidationEvent,
)

OFF_DUMP_URL = "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
DUMP_DIR = Path("/tmp/nutrition-imports")
DUMP_FILE = DUMP_DIR / "openfoodfacts-products.jsonl.gz"

BATCH_SIZE = 500


class Command(BaseCommand):
    help = "Bulk import from Open Food Facts JSONL dump"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nutrient_cache = {}

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0, help="Limit number of products (0=all)"
        )
        parser.add_argument(
            "--skip-download", action="store_true", help="Skip download, use cached file"
        )
        parser.add_argument(
            "--file", type=str, default="", help="Path to existing JSONL.gz file"
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
                "No nutrients in DB. Run 'python manage.py seed_nutrients' first."
            )

        # Download dump if needed
        if not options["skip_download"] and not options["file"]:
            self._download_dump(file_path)
        elif not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        self.stdout.write(f"Importing from {file_path} ...")
        if limit:
            self.stdout.write(f"Limiting to {limit} products.")

        # Stream and process
        stats = {
            "processed": 0,
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "accepted": 0,
            "rejected": 0,
        }
        batch = []
        t0 = time.time()

        for line in self._stream_lines(file_path):
            if limit and stats["processed"] >= limit:
                break

            stats["processed"] += 1

            try:
                product_data = json.loads(line)
            except json.JSONDecodeError:
                stats["errors"] += 1
                continue

            barcode = product_data.get("code", "").strip()
            if not barcode or len(barcode) < 4:
                stats["skipped"] += 1
                continue

            batch.append((barcode, product_data))

            if len(batch) >= batch_size:
                result = self._process_batch(batch)
                stats["imported"] += result["imported"]
                stats["skipped"] += result["skipped"]
                stats["accepted"] += result["accepted"]
                stats["rejected"] += result["rejected"]
                batch = []

                if stats["processed"] % 10000 == 0:
                    elapsed = time.time() - t0
                    rate = stats["processed"] / elapsed
                    self.stdout.write(
                        f"  {stats['processed']:>8,} processed | "
                        f"{stats['imported']:>8,} imported | "
                        f"{rate:.0f}/s"
                    )

        # Final batch
        if batch:
            result = self._process_batch(batch)
            stats["imported"] += result["imported"]
            stats["skipped"] += result["skipped"]
            stats["accepted"] += result["accepted"]
            stats["rejected"] += result["rejected"]

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

    def _download_dump(self, file_path: Path):
        """Download OFF dump with progress."""
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "NutritionCoreDB/1.0 (bulk import)"}

        # Check if already downloaded (must be > 1 MB to be valid)
        if file_path.exists() and file_path.stat().st_size > 1_000_000:
            local_size = file_path.stat().st_size
            try:
                resp = requests.head(OFF_DUMP_URL, headers=headers, timeout=15, allow_redirects=True)
                remote_size = int(resp.headers.get("content-length", 0))
                if local_size == remote_size and remote_size > 0:
                    self.stdout.write(f"Dump already downloaded ({local_size / 1e9:.1f} GB)")
                    return
            except Exception:
                self.stdout.write(f"Using existing file ({local_size / 1e9:.1f} GB)")
                return
        elif file_path.exists():
            # Remove broken/empty file
            file_path.unlink()

        self.stdout.write(f"Downloading OFF dump to {file_path} ...")
        self.stdout.write(f"URL: {OFF_DUMP_URL}")
        self.stdout.write("This is ~7 GB and may take a while.")

        resp = requests.get(
            OFF_DUMP_URL, stream=True, headers=headers, timeout=60, allow_redirects=True
        )
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))

        self.stdout.write(f"Response status: {resp.status_code}, Content-Length: {total}")

        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    self.stdout.write(
                        f"\r  {downloaded / 1e9:.1f} / {total / 1e9:.1f} GB "
                        f"({pct:.0f}%)",
                        ending="",
                    )
                else:
                    self.stdout.write(
                        f"\r  {downloaded / 1e6:.0f} MB downloaded ...",
                        ending="",
                    )

        self.stdout.write(f"\nDownload complete: {downloaded / 1e9:.1f} GB")

        if downloaded < 1_000_000:
            file_path.unlink()
            raise CommandError(
                f"Download too small ({downloaded} bytes). "
                "OFF may be blocking the request. Try downloading manually:\n"
                f"  wget -O {file_path} '{OFF_DUMP_URL}'"
            )

    def _stream_lines(self, file_path: Path):
        """Stream JSONL.gz line by line, low memory."""
        with gzip.open(file_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line

    def _load_nutrient_cache(self):
        """Cache off_key -> Nutrient for fast lookups."""
        for n in Nutrient.objects.exclude(off_key__isnull=True).exclude(off_key=""):
            self._nutrient_cache[n.off_key] = n

    @transaction.atomic
    def _process_batch(self, batch):
        """Process a batch of (barcode, product_data) tuples."""
        result = {"imported": 0, "skipped": 0, "accepted": 0, "rejected": 0}

        # Check which barcodes already exist
        barcodes = [b for b, _ in batch]
        existing_keys = set(
            FoodItem.objects.filter(
                canonical_key__in=[f"off:{b}" for b in barcodes]
            ).values_list("canonical_key", flat=True)
        )

        for barcode, product_data in batch:
            canonical_key = f"off:{barcode}"
            if canonical_key in existing_keys:
                result["skipped"] += 1
                continue

            try:
                accepted = self._import_product(barcode, product_data)
                result["imported"] += 1
                if accepted:
                    result["accepted"] += 1
                else:
                    result["rejected"] += 1
            except Exception:
                result["skipped"] += 1

        return result

    def _import_product(self, barcode, product_data):
        """Import a single product. Returns True if accepted."""
        product = product_data if "product_name" in product_data else product_data.get("product", product_data)

        product_name = (product.get("product_name") or "").strip()
        brand = (product.get("brands") or "").strip() or None
        ingredients = (product.get("ingredients_text") or "").strip() or None
        lang = (product.get("lang") or "en").strip()[:10]

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

        # ImportedRecord (store minimal raw data to save disk)
        record = ImportedRecord.objects.create(
            source="OFF",
            external_id=barcode,
            raw_json={
                "code": barcode,
                "product_name": product_name,
                "brands": brand,
                "lang": lang,
                "nutriments": product.get("nutriments", {}),
            },
            food_item=food,
        )

        # Nutrients
        nutriments = product.get("nutriments") or {}
        nutrient_values = []
        for off_key, nutrient in self._nutrient_cache.items():
            raw_val = nutriments.get(off_key)
            if raw_val is None:
                continue
            try:
                amount = Decimal(str(raw_val))
            except (InvalidOperation, ValueError, TypeError):
                continue

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
        energy = nutriments.get("energy-kcal_100g")
        if not product_name:
            reasons.append(("empty_name", "Product name is empty"))
        if energy is not None:
            try:
                if float(energy) > 900:
                    reasons.append(("energy_too_high", f"energy-kcal={energy} > 900"))
            except (ValueError, TypeError):
                pass

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
