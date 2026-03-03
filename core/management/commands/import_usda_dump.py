"""
Bulk import from USDA FoodData Central CSV dump.

Processes in chunks of 200k foods to keep RAM usage low.
Streams CSV files from ZIP instead of loading into memory.

Usage:
    python manage.py import_usda_dump
    python manage.py import_usda_dump --limit 10000
    python manage.py import_usda_dump --skip-download
    python manage.py import_usda_dump --file /path/to/FoodData_Central_csv.zip
"""

import csv
import io
import time
import zipfile
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

USDA_DUMP_URL = "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_csv_2025-12-18.zip"
DUMP_DIR = Path("/tmp/nutrition-imports")
DUMP_FILE = DUMP_DIR / "FoodData_Central_csv.zip"

BATCH_SIZE = 500
CHUNK_SIZE = 100000  # Process 100k foods at a time to limit RAM

RELEVANT_DATA_TYPES = {"foundation_food", "sr_legacy_food", "branded_food", "survey_fndds_food"}

DATA_TYPE_MAP = {
    "foundation_food": "raw",
    "sr_legacy_food": "raw",
    "branded_food": "branded",
    "survey_fndds_food": "raw",
}


class Command(BaseCommand):
    help = "Bulk import from USDA FoodData Central CSV dump (chunked, low-RAM)"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nutrient_cache = {}

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
        parser.add_argument(
            "--chunk-size", type=int, default=CHUNK_SIZE,
            help="Foods per chunk (lower = less RAM, default: 100000)"
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        batch_size = options["batch_size"]
        chunk_size = options["chunk_size"]
        file_path = Path(options["file"]) if options["file"] else DUMP_FILE

        self._load_nutrient_cache()
        if not self._nutrient_cache:
            raise CommandError(
                "No nutrients with usda_nutrient_id in DB. "
                "Run 'python manage.py seed_nutrients' first."
            )
        self.stdout.write(f"Loaded {len(self._nutrient_cache)} USDA nutrient mappings.")

        if not options["skip_download"] and not options["file"]:
            self._download_dump(options["url"], file_path)
        elif not file_path.exists():
            raise CommandError(f"File not found: {file_path}")

        self.stdout.write(f"Reading ZIP: {file_path}")

        with zipfile.ZipFile(file_path, "r") as zf:
            # Phase 1: Get ALL relevant fdc_ids (just IDs + minimal data as tuples)
            self.stdout.write("  Phase 1: Scanning food.csv for relevant IDs ...")
            all_foods = self._scan_food_ids(zf, limit)
            total_foods = len(all_foods)
            self.stdout.write(f"  Found {total_foods:,} relevant foods.")

            # Phase 2: Process in chunks
            all_fdc_ids = list(all_foods.keys())
            num_chunks = (total_foods + chunk_size - 1) // chunk_size
            self.stdout.write(
                f"  Processing in {num_chunks} chunks of {chunk_size:,} ..."
            )

            stats = {
                "imported": 0,
                "skipped": 0,
                "accepted": 0,
                "rejected": 0,
                "dedup_linked": 0,
            }
            t0 = time.time()

            for chunk_idx in range(num_chunks):
                start = chunk_idx * chunk_size
                end = min(start + chunk_size, total_foods)
                chunk_ids = all_fdc_ids[start:end]
                chunk_id_set = set(chunk_ids)

                self.stdout.write(
                    f"\n  --- Chunk {chunk_idx + 1}/{num_chunks} "
                    f"({len(chunk_ids):,} foods) ---"
                )

                # Extract foods data for this chunk
                foods = {fid: all_foods[fid] for fid in chunk_ids}

                # Stream branded_food.csv for this chunk
                self.stdout.write("    Streaming branded_food.csv ...")
                brands = self._stream_brands(zf, chunk_id_set)
                self.stdout.write(f"    Loaded {len(brands):,} brand entries.")

                # Stream food_nutrient.csv for this chunk
                self.stdout.write("    Streaming food_nutrient.csv ...")
                nutrients_data = self._stream_food_nutrients(zf, chunk_id_set)
                self.stdout.write(
                    f"    Loaded nutrients for {len(nutrients_data):,} foods."
                )

                # Disable search_vector trigger during bulk import (massive speedup)
                with connection.cursor() as cur:
                    cur.execute(
                        "ALTER TABLE core_foodtext DISABLE TRIGGER "
                        "foodtext_search_vector_update"
                    )

                # Import this chunk in DB batches
                chunk_imported = 0
                for i in range(0, len(chunk_ids), batch_size):
                    batch_fdc_ids = chunk_ids[i : i + batch_size]
                    result = self._import_batch(
                        batch_fdc_ids, foods, brands, nutrients_data
                    )
                    stats["imported"] += result["imported"]
                    stats["skipped"] += result["skipped"]
                    stats["accepted"] += result["accepted"]
                    stats["rejected"] += result["rejected"]
                    stats["dedup_linked"] += result["dedup_linked"]
                    chunk_imported += result["imported"] + result["skipped"] + result["dedup_linked"]

                    # Progress every 10 batches
                    if (i // batch_size) % 10 == 9:
                        elapsed = time.time() - t0
                        total_done = start + chunk_imported
                        rate = total_done / elapsed if elapsed > 0 else 0
                        self.stdout.write(
                            f"    {total_done:>10,} / {total_foods:,} "
                            f"| imp: {stats['imported']:,} "
                            f"| skip: {stats['skipped']:,} "
                            f"| dedup: {stats['dedup_linked']:,} "
                            f"| {rate:,.0f}/s"
                        )

                # Re-enable trigger
                with connection.cursor() as cur:
                    cur.execute(
                        "ALTER TABLE core_foodtext ENABLE TRIGGER "
                        "foodtext_search_vector_update"
                    )

                done = end
                elapsed = time.time() - t0
                self.stdout.write(
                    f"    Chunk done: {done:,} / {total_foods:,} "
                    f"| imported: {stats['imported']:,} "
                    f"| dedup: {stats['dedup_linked']:,} "
                    f"| {elapsed:.0f}s"
                )

                # Free chunk memory
                del foods, brands, nutrients_data

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone in {elapsed:.0f}s:\n"
                f"  Imported:     {stats['imported']:,}\n"
                f"  Skipped:      {stats['skipped']:,}\n"
                f"  Dedup linked: {stats['dedup_linked']:,}\n"
                f"  Accepted:     {stats['accepted']:,}\n"
                f"  Rejected:     {stats['rejected']:,}"
            )
        )

    def _download_dump(self, url: str, file_path: Path):
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
        for n in Nutrient.objects.exclude(usda_nutrient_id__isnull=True):
            self._nutrient_cache[n.usda_nutrient_id] = n

    def _find_csv_in_zip(self, zf, filename):
        matching = [n for n in zf.namelist() if n.endswith(f"/{filename}") or n == filename]
        return matching[0] if matching else None

    def _scan_food_ids(self, zf, limit):
        """
        Stream food.csv -> {fdc_id: (description, data_type)} using tuples.
        Only keeps relevant data_types. Minimal memory footprint.
        """
        entry = self._find_csv_in_zip(zf, "food.csv")
        if not entry:
            raise CommandError("food.csv not found in ZIP")

        foods = {}
        with zf.open(entry) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                data_type = row.get("data_type", "").strip()
                if data_type not in RELEVANT_DATA_TYPES:
                    continue

                fdc_id = row.get("fdc_id", "").strip()
                if not fdc_id:
                    continue

                # Tuple instead of dict: (description, data_type)
                foods[fdc_id] = (
                    row.get("description", "").strip(),
                    data_type,
                )

                if limit and len(foods) >= limit:
                    break

        return foods

    def _stream_brands(self, zf, fdc_ids):
        """Stream branded_food.csv -> {fdc_id: (brand_owner, brand_name, ingredients, gtin_upc)}."""
        entry = self._find_csv_in_zip(zf, "branded_food.csv")
        if not entry:
            return {}

        brands = {}
        with zf.open(entry) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                fdc_id = row.get("fdc_id", "").strip()
                if fdc_id not in fdc_ids:
                    continue
                # Tuple instead of dict
                brands[fdc_id] = (
                    (row.get("brand_owner") or "").strip() or None,
                    (row.get("brand_name") or "").strip() or None,
                    (row.get("ingredients") or "").strip() or None,
                    (row.get("gtin_upc") or "").strip() or None,
                )
        return brands

    def _stream_food_nutrients(self, zf, fdc_ids):
        """Stream food_nutrient.csv -> {fdc_id: [(nutrient_id, amount), ...]}."""
        entry = self._find_csv_in_zip(zf, "food_nutrient.csv")
        if not entry:
            raise CommandError("food_nutrient.csv not found in ZIP")

        data = {}
        with zf.open(entry) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
            for row in reader:
                fdc_id = row.get("fdc_id", "").strip()
                if fdc_id not in fdc_ids:
                    continue

                nutrient_id_str = row.get("nutrient_id", "").strip()
                amount_str = row.get("amount", "").strip()
                if not nutrient_id_str or not amount_str:
                    continue

                try:
                    nutrient_id = int(nutrient_id_str)
                except ValueError:
                    continue

                if nutrient_id not in self._nutrient_cache:
                    continue

                try:
                    amount = Decimal(amount_str)
                except InvalidOperation:
                    continue

                if fdc_id not in data:
                    data[fdc_id] = []
                data[fdc_id].append((nutrient_id, amount))

        return data

    @transaction.atomic
    def _import_batch(self, batch_ids, foods, brands, nutrients_data):
        result = {
            "imported": 0,
            "skipped": 0,
            "accepted": 0,
            "rejected": 0,
            "dedup_linked": 0,
        }

        canonical_keys = [f"usda:{fdc_id}" for fdc_id in batch_ids]
        existing = set(
            FoodItem.objects.filter(
                canonical_key__in=canonical_keys
            ).values_list("canonical_key", flat=True)
        )

        barcodes_to_check = {}
        for fdc_id in batch_ids:
            brand_tuple = brands.get(fdc_id)
            if brand_tuple:
                gtin = brand_tuple[3]  # gtin_upc is index 3
                if gtin and len(gtin) >= 4:
                    barcodes_to_check[fdc_id] = gtin

        off_by_barcode = {}
        if barcodes_to_check:
            off_keys = [f"off:{bc}" for bc in barcodes_to_check.values()]
            for food in FoodItem.objects.filter(canonical_key__in=off_keys):
                bc = food.canonical_key[4:]
                off_by_barcode[bc] = food

        for fdc_id in batch_ids:
            canonical_key = f"usda:{fdc_id}"
            if canonical_key in existing:
                result["skipped"] += 1
                continue

            food_tuple = foods[fdc_id]  # (description, data_type)
            brand_tuple = brands.get(fdc_id)  # (brand_owner, brand_name, ingredients, gtin_upc) or None
            nutrient_list = nutrients_data.get(fdc_id, [])

            gtin = brand_tuple[3] if brand_tuple else None
            if gtin and gtin in off_by_barcode:
                existing_food = off_by_barcode[gtin]
                try:
                    with transaction.atomic():
                        ImportedRecord.objects.create(
                            source="USDA",
                            external_id=str(fdc_id),
                            raw_json={
                                "fdc_id": fdc_id,
                                "description": food_tuple[0],
                                "data_type": food_tuple[1],
                                "gtin_upc": gtin,
                                "nutrient_count": len(nutrient_list),
                                "dedup_note": f"Linked to existing OFF product off:{gtin}",
                            },
                            food_item=existing_food,
                        )
                    result["dedup_linked"] += 1
                except Exception:
                    result["skipped"] += 1
                continue

            try:
                with transaction.atomic():
                    accepted = self._import_food(
                        fdc_id, food_tuple, brand_tuple, nutrient_list
                    )
                result["imported"] += 1
                if accepted:
                    result["accepted"] += 1
                else:
                    result["rejected"] += 1
            except Exception:
                result["skipped"] += 1

        return result

    def _import_food(self, fdc_id, food_tuple, brand_tuple, nutrient_list):
        """Import a single USDA food. food_tuple = (description, data_type)."""
        description, data_type = food_tuple
        food_type = DATA_TYPE_MAP.get(data_type, "raw")

        food = FoodItem.objects.create(
            canonical_key=f"usda:{fdc_id}",
            food_type=food_type,
        )

        brand_name = None
        ingredients = None
        if brand_tuple:
            brand_name = brand_tuple[0] or brand_tuple[1]  # brand_owner or brand_name
            ingredients = brand_tuple[2]

        FoodText.objects.create(
            food_item=food,
            lang="en",
            name=description or f"USDA {fdc_id}",
            brand=brand_name,
            ingredients=ingredients,
        )

        record = ImportedRecord.objects.create(
            source="USDA",
            external_id=str(fdc_id),
            raw_json={
                "fdc_id": fdc_id,
                "description": description,
                "data_type": data_type,
                "nutrient_count": len(nutrient_list),
            },
            food_item=food,
        )

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
