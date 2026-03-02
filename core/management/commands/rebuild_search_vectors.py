"""
Backfill search_vector for all existing FoodText rows.

This command updates search_vector using the same logic as the DB trigger,
but does it in batches via raw SQL for maximum speed.

Usage:
    python manage.py rebuild_search_vectors
    python manage.py rebuild_search_vectors --batch-size 10000
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
    help = "Backfill search_vector for all FoodText rows (batch SQL)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5000,
            help="Number of rows to update per batch (default: 5000)",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]

        with connection.cursor() as cursor:
            # Count total rows
            cursor.execute("SELECT COUNT(*) FROM core_foodtext")
            total = cursor.fetchone()[0]
            self.stdout.write(f"Total FoodText rows: {total:,}")

            if total == 0:
                self.stdout.write(self.style.WARNING("No rows to update."))
                return

            # Count rows with NULL search_vector
            cursor.execute(
                "SELECT COUNT(*) FROM core_foodtext WHERE search_vector IS NULL"
            )
            null_count = cursor.fetchone()[0]
            self.stdout.write(f"Rows with NULL search_vector: {null_count:,}")

        t0 = time.time()
        updated = 0
        offset = 0

        while offset < total:
            with connection.cursor() as cursor:
                # Update batch using CTE for efficiency
                sql = f"""
                    UPDATE core_foodtext ft
                    SET search_vector =
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.name, '')), 'A') ||
                        setweight(to_tsvector({LANG_MAP_SQL}, COALESCE(ft.brand, '')), 'B')
                    WHERE ft.id IN (
                        SELECT id FROM core_foodtext
                        ORDER BY id
                        LIMIT %s OFFSET %s
                    )
                """
                cursor.execute(sql, [batch_size, offset])
                rows_affected = cursor.rowcount
                updated += rows_affected

            offset += batch_size
            elapsed = time.time() - t0
            rate = updated / elapsed if elapsed > 0 else 0
            self.stdout.write(
                f"  {updated:>10,} / {total:,} updated "
                f"({updated * 100 / total:.1f}%) | "
                f"{rate:,.0f}/s | "
                f"{elapsed:.0f}s"
            )

        elapsed = time.time() - t0
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Updated {updated:,} search vectors in {elapsed:.0f}s"
            )
        )
