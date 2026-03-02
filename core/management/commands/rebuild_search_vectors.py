"""
Backfill search_vector for all existing FoodText rows.

Uses cursor-based batching: SELECT IDs first, then UPDATE.
No RETURNING, no OFFSET — constant speed regardless of table size.

Usage:
    python manage.py rebuild_search_vectors
    python manage.py rebuild_search_vectors --batch-size 5000
    python manage.py rebuild_search_vectors --only-null
"""

import time

from django.core.management.base import BaseCommand
from django.db import connection


# Same language mapping as the DB trigger
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
    help = "Backfill search_vector for all FoodText rows (cursor-based batching)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of rows to update per batch (default: 5000)",
        )
        parser.add_argument(
            "--only-null",
            action="store_true",
            help="Only update rows where search_vector IS NULL",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        only_null = options["only_null"]

        null_filter = "AND search_vector IS NULL" if only_null else ""

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM core_foodtext")
            total = cursor.fetchone()[0]
            self.stdout.write(f"Total FoodText rows: {total:,}")

            if total == 0:
                self.stdout.write(self.style.WARNING("No rows to update."))
                return

            cursor.execute(
                "SELECT COUNT(*) FROM core_foodtext WHERE search_vector IS NULL"
            )
            null_count = cursor.fetchone()[0]
            self.stdout.write(f"Rows with NULL search_vector: {null_count:,}")

        target = null_count if only_null else total
        if target == 0:
            self.stdout.write(self.style.SUCCESS("All rows already have search_vector."))
            return

        t0 = time.time()
        updated = 0
        last_id = "00000000-0000-0000-0000-000000000000"

        while True:
            with connection.cursor() as cursor:
                # Step 1: SELECT next batch of IDs
                cursor.execute(
                    f"SELECT id FROM core_foodtext "
                    f"WHERE id > %s {null_filter} "
                    f"ORDER BY id LIMIT %s",
                    [last_id, batch_size],
                )
                ids = [row[0] for row in cursor.fetchall()]

                if not ids:
                    break

                last_id = str(ids[-1])

                # Step 2: UPDATE those specific IDs
                placeholders = ",".join(["%s"] * len(ids))
                sql = f"""
                    UPDATE core_foodtext ft
                    SET search_vector =
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.name, '')), 'A') ||
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.brand, '')), 'B')
                    WHERE ft.id IN ({placeholders})
                """
                cursor.execute(sql, [str(i) for i in ids])
                updated += cursor.rowcount

            elapsed = time.time() - t0
            rate = updated / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {updated:>10,} / {target:,} updated "
                f"({updated * 100 / target:.1f}%) | "
                f"{rate:,.0f}/s | "
                f"{elapsed:.0f}s"
            )

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Updated {updated:,} search vectors in {elapsed:.0f}s"
            )
        )
