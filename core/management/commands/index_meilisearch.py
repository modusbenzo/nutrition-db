"""
Management command to index FoodText + nutrients into Meilisearch.

Usage:
    python manage.py index_meilisearch              # full reindex
    python manage.py index_meilisearch --chunk=50000 # custom chunk size
    python manage.py index_meilisearch --clear       # delete index first
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

import meilisearch


INDEX_NAME = "foods"

# Searchable and filterable attributes for optimal search
SETTINGS = {
    "searchableAttributes": ["name", "brand"],
    "filterableAttributes": ["lang", "food_type", "source"],
    "sortableAttributes": ["name"],
    "rankingRules": [
        "words",
        "typo",
        "proximity",
        "attribute",
        "sort",
        "exactness",
    ],
    # Typo tolerance tuning — be lenient for long food names
    "typoTolerance": {
        "enabled": True,
        "minWordSizeForTypos": {
            "oneTypo": 4,
            "twoTypos": 8,
        },
    },
    "pagination": {
        "maxTotalHits": 10000,
    },
}


class Command(BaseCommand):
    help = "Index all FoodText data into Meilisearch for instant search."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chunk",
            type=int,
            default=25_000,
            help="Batch size for indexing (default: 25000)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete the index before re-indexing",
        )

    def handle(self, *args, **options):
        chunk_size = options["chunk"]

        client = meilisearch.Client(
            settings.MEILISEARCH_URL,
            settings.MEILISEARCH_MASTER_KEY,
        )

        # Health check
        try:
            health = client.health()
            self.stdout.write(f"Meilisearch status: {health['status']}")
        except Exception as e:
            self.stderr.write(f"Cannot connect to Meilisearch: {e}")
            return

        # Clear index if requested
        if options["clear"]:
            self.stdout.write("Deleting existing index...")
            try:
                client.delete_index(INDEX_NAME)
                time.sleep(2)
            except meilisearch.errors.MeilisearchApiError:
                pass
            self.stdout.write("Index deleted.")

        # Create or get index
        try:
            client.create_index(INDEX_NAME, {"primaryKey": "id"})
            self.stdout.write(f"Created index '{INDEX_NAME}'")
        except meilisearch.errors.MeilisearchApiError:
            self.stdout.write(f"Index '{INDEX_NAME}' already exists")

        index = client.index(INDEX_NAME)

        # Apply settings
        self.stdout.write("Applying index settings...")
        task = index.update_settings(SETTINGS)
        self._wait_for_task(client, task)
        self.stdout.write("Settings applied.")

        # Stream FoodText in chunks — load sources + nutrients per chunk
        # to avoid OOM from loading everything at once
        from core.models import FoodNutrientValue, FoodText, ImportedRecord

        total = FoodText.objects.count()
        self.stdout.write(
            f"\nIndexing {total:,} FoodText rows in chunks of {chunk_size:,}..."
        )
        self.stdout.write(
            "  (sources + nutrients loaded per chunk to save memory)\n"
        )

        t0 = time.time()
        indexed = 0
        last_id = None

        while True:
            qs = FoodText.objects.select_related("food_item").order_by("id")
            if last_id:
                qs = qs.filter(id__gt=last_id)

            batch = list(qs[:chunk_size])
            if not batch:
                break

            # Collect food_item IDs for this chunk
            food_item_ids = list({ft.food_item_id for ft in batch})

            # Load sources for this chunk only
            source_map = {}
            for fid, src in (
                ImportedRecord.objects.filter(food_item_id__in=food_item_ids)
                .values_list("food_item_id", "source")
            ):
                source_map.setdefault(fid, set()).add(src)

            # Load nutrients for this chunk only
            nutrients_map = {}
            for nv in (
                FoodNutrientValue.objects.filter(
                    food_item_id__in=food_item_ids,
                    basis="per_100g",
                )
                .select_related("nutrient")
                .only("food_item_id", "nutrient__canonical_code", "amount")
            ):
                nutrients_map.setdefault(nv.food_item_id, {})[
                    nv.nutrient.canonical_code
                ] = float(nv.amount)

            # Build documents
            documents = []
            for ft in batch:
                food_item = ft.food_item
                doc = {
                    "id": str(ft.id),
                    "food_item_id": str(food_item.id),
                    "canonical_key": food_item.canonical_key,
                    "food_type": food_item.food_type,
                    "name": ft.name or "",
                    "brand": ft.brand or "",
                    "lang": ft.lang,
                    "source": list(source_map.get(food_item.id, [])),
                    "nutrients": nutrients_map.get(food_item.id, {}),
                }
                documents.append(doc)

            # Send to Meilisearch
            task = index.add_documents(documents)
            self._wait_for_task(client, task)

            # Free memory
            del source_map, nutrients_map, documents, food_item_ids

            last_id = batch[-1].id
            indexed += len(batch)
            elapsed = time.time() - t0
            rate = indexed / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {indexed:,}/{total:,} indexed "
                f"({indexed * 100 / total:.1f}%) "
                f"[{rate:.0f} docs/s]"
            )

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {indexed:,} documents indexed in {elapsed:.0f}s"
            )
        )

        # Show index stats
        stats = index.get_stats()
        self.stdout.write(f"Index stats: {stats['numberOfDocuments']:,} documents")

    def _wait_for_task(self, client, task_info):
        """Wait for a Meilisearch task to complete."""
        task_uid = task_info.task_uid
        while True:
            task = client.get_task(task_uid)
            if task.status in ("succeeded", "failed"):
                if task.status == "failed":
                    self.stderr.write(f"Task {task_uid} failed: {task.error}")
                return task
            time.sleep(0.5)
