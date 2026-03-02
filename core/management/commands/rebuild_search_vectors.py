"""
Backfill search_vector for all existing FoodText rows.

Uses cursor-based batching (WHERE id > last_id) instead of OFFSET
for constant-speed performance regardless of table size.

Usage:
    python manage.py rebuild_search_vectors
    python manage.py rebuild_search_vectors --batch-size 10000
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
            default=10000,
            help="Number of rows to update per batch (default: 10000)",
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

            if total == 0:
                self.stdout.write(self.style.WARNING("No rows to update."))
                return

            cursor.execute(
                "SELECT COUNT(*) FROM core_foodtext WHERE search_vector IS NULL"
            )
            null_count = cursor.fetchone()[0]
            self.stdout.write(f"Rows with NULL search_vector: {null_count:,}")

        t0 = time.time()
        updated = 0
        last_id = "00000000-0000-0000-0000-000000000000"

        null_filter = "AND ft.search_vector IS NULL" if only_null else ""
        target = null_count if only_null else total

        while True:
            with connection.cursor() as cursor:
                # Cursor-based batching: WHERE id > last_id ORDER BY id LIMIT N
                # This is O(1) per batch regardless of position in the table
                sql = f"""
                    UPDATE core_foodtext ft
                    SET search_vector =
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.name, '')), 'A') ||
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.brand, '')), 'B')
                    WHERE ft.id IN (
                        SELECT id FROM core_foodtext
                        WHERE id > %s {null_filter}
                        ORDER BY id
                        LIMIT %s
                    )
                    RETURNING ft.id
                """
                cursor.execute(sql, [last_id, batch_size])
                rows = cursor.fetchall()
                rows_affected = len(rows)

                if rows_affected == 0:
                    break

                # last_id = max ID in this batch
                last_id = str(rows[-1][0])
                updated += rows_affected

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
