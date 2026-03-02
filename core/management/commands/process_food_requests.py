"""
Process pending FoodRequests with heuristic validation.

High-confidence requests get auto-created as FoodItems.
Low-confidence requests remain pending for admin review.

Usage:
    python manage.py process_food_requests
    python manage.py process_food_requests --threshold 0.7
    python manage.py process_food_requests --dry-run

Can be run as a cronjob every 5 minutes:
    */5 * * * * cd /app && python manage.py process_food_requests
"""

from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand

from core.models import (
    FoodItem,
    FoodNutrientValue,
    FoodRequest,
    FoodText,
    ImportedRecord,
    Nutrient,
)


class Command(BaseCommand):
    help = "Process pending FoodRequests — auto-create high-confidence items"

    def add_arguments(self, parser):
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.8,
            help="Minimum confidence to auto-create (default: 0.8)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show what would be done, don't modify anything",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Max number of requests to process (default: 100)",
        )

    def handle(self, *args, **options):
        threshold = options["threshold"]
        dry_run = options["dry_run"]
        limit = options["limit"]

        pending = FoodRequest.objects.filter(status="pending").order_by(
            "-request_count", "-created_at"
        )[:limit]

        total = pending.count()
        if total == 0:
            self.stdout.write("No pending requests.")
            return

        self.stdout.write(f"Processing {total} pending requests (threshold={threshold})...")

        # Pre-load nutrient definitions
        nutrient_cache = {n.canonical_code: n for n in Nutrient.objects.all()}

        stats = {"auto_created": 0, "kept_pending": 0, "errors": 0}

        for req in pending:
            confidence = self._compute_confidence(req)
            req.ai_confidence = confidence

            if dry_run:
                action = "AUTO-CREATE" if confidence >= threshold else "KEEP PENDING"
                self.stdout.write(
                    f"  [{action}] {req.original_query} "
                    f"(barcode={req.submitted_barcode or '-'}, "
                    f"confidence={confidence:.2f}, "
                    f"count={req.request_count})"
                )
                continue

            if confidence >= threshold:
                food_item = self._auto_create_food(req, nutrient_cache)
                if food_item:
                    req.food_item = food_item
                    req.status = "auto_created"
                    req.ai_review_notes = (
                        f"Auto-created by process_food_requests "
                        f"(confidence={confidence:.2f})"
                    )
                    req.save()
                    stats["auto_created"] += 1
                else:
                    req.ai_review_notes = (
                        f"Auto-creation failed (confidence={confidence:.2f})"
                    )
                    req.save(update_fields=["ai_confidence", "ai_review_notes"])
                    stats["errors"] += 1
            else:
                req.save(update_fields=["ai_confidence"])
                stats["kept_pending"] += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run — no changes made."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone:\n"
                    f"  Auto-created: {stats['auto_created']}\n"
                    f"  Kept pending: {stats['kept_pending']}\n"
                    f"  Errors:       {stats['errors']}"
                )
            )

    def _compute_confidence(self, req):
        """Heuristic confidence score (0.0 - 1.0)."""
        score = 0.0

        # Has a name
        if req.submitted_name:
            score += 0.3

        # Has a barcode
        if req.submitted_barcode:
            score += 0.2

        # Has nutrients with energy
        nutrients = req.submitted_nutrients or {}
        if nutrients.get("energy_kcal") is not None:
            score += 0.2
            try:
                kcal = float(nutrients["energy_kcal"])
                if 0 < kcal <= 900:
                    score += 0.1
            except (ValueError, TypeError):
                pass

        # Has multiple nutrients
        if len(nutrients) >= 3:
            score += 0.1

        # Has source URL
        if req.submitted_source_url:
            score += 0.1

        # Frequently requested (bonus for popularity)
        if req.request_count >= 5:
            score += 0.1
        elif req.request_count >= 3:
            score += 0.05

        return min(score, 1.0)

    def _auto_create_food(self, req, nutrient_cache):
        """Create FoodItem + FoodText + nutrients from the request."""
        name = req.submitted_name or req.original_query
        barcode = req.submitted_barcode
        canonical_key = f"req:{barcode}" if barcode else f"req:{req.id}"

        # Avoid duplicates
        if FoodItem.objects.filter(canonical_key=canonical_key).exists():
            return FoodItem.objects.get(canonical_key=canonical_key)

        try:
            food = FoodItem.objects.create(
                canonical_key=canonical_key,
                food_type="branded" if barcode else "raw",
            )

            FoodText.objects.create(
                food_item=food,
                lang=req.lang,
                name=name,
                brand=req.submitted_brand or None,
            )

            ImportedRecord.objects.create(
                source="USER_REQ",
                external_id=str(req.id),
                raw_json={
                    "original_query": req.original_query,
                    "submitted_name": req.submitted_name,
                    "submitted_brand": req.submitted_brand,
                    "submitted_barcode": barcode,
                    "source_url": req.submitted_source_url,
                    "auto_processed": True,
                },
                food_item=food,
            )

            # Nutrients
            nutrients = req.submitted_nutrients or {}
            if nutrients:
                values_to_create = []
                for code, amount in nutrients.items():
                    nutrient = nutrient_cache.get(code)
                    if not nutrient:
                        continue
                    try:
                        values_to_create.append(
                            FoodNutrientValue(
                                food_item=food,
                                nutrient=nutrient,
                                basis="per_100g",
                                amount=Decimal(str(amount)),
                                unit=nutrient.unit,
                            )
                        )
                    except (InvalidOperation, ValueError):
                        continue

                if values_to_create:
                    FoodNutrientValue.objects.bulk_create(values_to_create)

            return food

        except Exception as e:
            self.stderr.write(f"  Error creating food for '{req.original_query}': {e}")
            return None
