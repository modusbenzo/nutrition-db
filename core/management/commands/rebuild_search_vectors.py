"""
Backfill search_vector for all existing FoodText rows.

Drops the GIN index first, does bulk UPDATE, then recreates the index.
This is 10-50x faster than updating with the index in place.

Usage:
    python manage.py rebuild_search_vectors
    python manage.py rebuild_search_vectors --batch-size 50000
    python manage.py rebuild_search_vectors --only-null
"""

import time

from django.core.management.base import BaseCommand
from django.db import connection


LANG_MAP_SQL = """
CASE ft.lang
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
END
"""


class Command(BaseCommand):
    help = "Backfill search_vector (drops GIN index, bulk update, recreates index)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50000,
            help="Rows per batch (default: 50000)",
        )
        parser.add_argument(
            "--only-null",
            action="store_true",
            help="Only update rows where search_vector IS NULL",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        only_null = options["only_null"]

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM core_foodtext")
            total = cursor.fetchone()[0]
            self.stdout.write(f"Total FoodText rows: {total:,}")

            cursor.execute(
                "SELECT COUNT(*) FROM core_foodtext WHERE search_vector IS NULL"
            )
            null_count = cursor.fetchone()[0]
            self.stdout.write(f"Rows with NULL search_vector: {null_count:,}")

        target = null_count if only_null else total
        if target == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to update."))
            return

        null_filter = "WHERE search_vector IS NULL" if only_null else ""

        # Step 1: Drop GIN index (makes UPDATE 10-50x faster)
        self.stdout.write("Dropping GIN index on search_vector ...")
        with connection.cursor() as cursor:
            cursor.execute("DROP INDEX IF EXISTS foodtext_search_gin;")
        self.stdout.write("  Index dropped.")

        # Step 2: Bulk UPDATE in large batches
        t0 = time.time()
        updated = 0
        last_id = "00000000-0000-0000-0000-000000000000"

        self.stdout.write(f"Updating search_vector in batches of {batch_size:,} ...")

        while True:
            with connection.cursor() as cursor:
                # Get next batch of IDs
                cursor.execute(
                    f"SELECT id FROM core_foodtext "
                    f"WHERE id > %s "
                    f"{'AND search_vector IS NULL' if only_null else ''} "
                    f"ORDER BY id LIMIT %s",
                    [last_id, batch_size],
                )
                ids = cursor.fetchall()

                if not ids:
                    break

                last_id = str(ids[-1][0])
                id_list = [str(r[0]) for r in ids]

                # Bulk UPDATE
                placeholders = ",".join(["%s"] * len(id_list))
                sql = f"""
                    UPDATE core_foodtext ft
                    SET search_vector =
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.name, '')), 'A') ||
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.brand, '')), 'B')
                    WHERE ft.id IN ({placeholders})
                """
                cursor.execute(sql, id_list)
                updated += cursor.rowcount

            elapsed = time.time() - t0
            rate = updated / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {updated:>10,} / {target:,} "
                f"({updated * 100 / target:.1f}%) | "
                f"{rate:,.0f}/s | "
                f"{elapsed:.0f}s"
            )

        elapsed_update = time.time() - t0
        self.stdout.write(f"\nUpdate done in {elapsed_update:.0f}s.")

        # Step 3: Recreate GIN index
        self.stdout.write("Recreating GIN index on search_vector (this may take a few minutes) ...")
        t1 = time.time()
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE INDEX CONCURRENTLY foodtext_search_gin "
                "ON core_foodtext USING gin (search_vector);"
            )
        elapsed_index = time.time() - t1
        self.stdout.write(f"  Index rebuilt in {elapsed_index:.0f}s.")

        total_elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Updated {updated:,} rows in {total_elapsed:.0f}s total "
                f"(update: {elapsed_update:.0f}s, index: {elapsed_index:.0f}s)"
            )
        )
