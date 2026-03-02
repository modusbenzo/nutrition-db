"""
API views for Nutrition Core Database.

Search uses 3-layer PostgreSQL ranking:
  1. Full-Text Search (tsvector + SearchQuery) — stemming-aware, weighted
  2. Trigram Similarity (pg_trgm) — fuzzy/typo-tolerant
  3. Exact Prefix Bonus — "Banane" ranks higher than "Banana Bread"

FoodRequest allows apps to submit missing food data for auto-creation.
"""

import hashlib

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.core.cache import cache
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.functions import Greatest
from rest_framework import generics, status
from rest_framework.response import Response

from core.models import FoodItem, FoodNutrientValue, FoodRequest, FoodText

from .pagination import SearchPagination
from .serializers import (
    FoodItemDetailSerializer,
    FoodItemListSerializer,
    FoodItemSearchSerializer,
    FoodRequestCreateSerializer,
    FoodRequestResponseSerializer,
)


# Map BCP 47 lang codes to PostgreSQL text search config names
PG_LANG_MAP = {
    "de": "german",
    "en": "english",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
    "sv": "swedish",
    "ru": "russian",
    "tr": "turkish",
    "da": "danish",
    "fi": "finnish",
    "hu": "hungarian",
    "no": "norwegian",
    "ro": "romanian",
}

CACHE_TTL = 300  # 5 minutes


class FoodSearchView(generics.ListAPIView):
    """
    GET /api/foods/search?q=Banane&lang=de&limit=25&offset=0

    3-layer search:
      1. FTS (SearchRank) — weighted by name (A) and brand (B)
      2. Trigram similarity — fuzzy matching for typos
      3. Prefix bonus — exact prefix gets +1.0

    Returns flat JSON for maximum speed.
    """

    serializer_class = FoodItemSearchSerializer
    pagination_class = SearchPagination

    def list(self, request, *args, **kwargs):
        q = request.query_params.get("q", "").strip()
        lang = request.query_params.get("lang", "")

        if not q or len(q) < 2:
            return Response({"count": 0, "results": []})

        # Check cache (include all filter params in key)
        food_type_param = request.query_params.get("food_type", "")
        source_param = request.query_params.get("source", "")
        cache_key = f"search:{hashlib.md5(f'{q}:{lang}:{food_type_param}:{source_param}'.encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        # Determine PostgreSQL text search config
        pg_config = PG_LANG_MAP.get(lang, "simple")
        search_query = SearchQuery(q, config=pg_config, search_type="plain")

        # Build queryset on FoodText
        qs = FoodText.objects.select_related("food_item")

        if lang:
            qs = qs.filter(lang=lang)

        # food_type filter (raw, branded, supplement)
        food_type = request.query_params.get("food_type", "")
        if food_type:
            valid_types = {"raw", "branded", "supplement"}
            types = [t.strip() for t in food_type.split(",") if t.strip() in valid_types]
            if types:
                qs = qs.filter(food_item__food_type__in=types)

        # source filter (USDA, OFF, USER_REQ)
        source = request.query_params.get("source", "")
        if source:
            sources = [s.strip() for s in source.split(",")]
            qs = qs.filter(food_item__imported_records__source__in=sources)

        # Three layers of matching
        fts_filter = Q(search_vector=search_query)
        trigram_filter = Q(name__trigram_similar=q)
        prefix_filter = Q(name__istartswith=q)

        qs = qs.filter(fts_filter | trigram_filter | prefix_filter)

        # Annotate scores
        qs = qs.annotate(
            fts_rank=SearchRank(F("search_vector"), search_query),
            trigram_sim=TrigramSimilarity("name", q),
            prefix_bonus=Case(
                When(name__istartswith=q, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
        )

        # Combined score: MAX(fts_rank * 2, trigram_sim) + prefix_bonus
        qs = qs.annotate(
            score=Greatest(
                F("fts_rank") * Value(2.0, output_field=FloatField()),
                F("trigram_sim"),
                output_field=FloatField(),
            )
            + F("prefix_bonus"),
        )

        qs = qs.order_by("-score", "name")

        # Paginate
        page = self.paginate_queryset(qs)
        if page is None:
            page = list(qs[:25])

        # Collect food_item IDs for batch nutrient loading
        food_item_ids = [ft.food_item_id for ft in page]

        # Batch load nutrients (avoid N+1)
        nutrients_map = {}
        if food_item_ids:
            nutrient_values = (
                FoodNutrientValue.objects.filter(
                    food_item_id__in=food_item_ids,
                    basis="per_100g",
                )
                .select_related("nutrient")
            )
            for nv in nutrient_values:
                nutrients_map.setdefault(nv.food_item_id, {})[
                    nv.nutrient.canonical_code
                ] = float(nv.amount)

        # Build flat results
        results = []
        for ft in page:
            results.append(
                {
                    "id": str(ft.food_item.id),
                    "canonical_key": ft.food_item.canonical_key,
                    "food_type": ft.food_item.food_type,
                    "name": ft.name,
                    "brand": ft.brand,
                    "lang": ft.lang,
                    "nutrients": nutrients_map.get(ft.food_item_id, {}),
                    "score": round(float(ft.score), 4) if hasattr(ft, "score") else 0,
                }
            )

        response_data = self.get_paginated_response(results).data

        # Cache the response (5 min TTL)
        cache.set(cache_key, response_data, CACHE_TTL)

        return Response(response_data)


class FoodDetailView(generics.RetrieveAPIView):
    """
    GET /api/foods/{id}?lang=de
    """

    serializer_class = FoodItemDetailSerializer
    queryset = FoodItem.objects.prefetch_related(
        "texts", "nutrient_values__nutrient"
    )
    lookup_field = "id"


# ---------------------------------------------------------------------------
# FoodRequest endpoints
# ---------------------------------------------------------------------------
class FoodRequestCreateView(generics.CreateAPIView):
    """
    POST /api/foods/request

    App submits missing food data. Server deduplicates by barcode or query,
    and either auto-creates the food item or queues it for admin review.
    """

    serializer_class = FoodRequestCreateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        barcode = data.get("submitted_barcode", "").strip()
        query = data["original_query"].strip()
        lang = data.get("lang", "de")

        # Deduplication: check if same barcode or same query+lang already requested
        existing = None
        if barcode:
            existing = FoodRequest.objects.filter(
                submitted_barcode=barcode
            ).first()

        if not existing:
            existing = FoodRequest.objects.filter(
                original_query__iexact=query,
                lang=lang,
            ).first()

        if existing:
            # Increment counter and update data if richer
            existing.request_count += 1
            update_fields = ["request_count", "updated_at"]

            if data.get("submitted_name") and not existing.submitted_name:
                existing.submitted_name = data["submitted_name"]
                update_fields.append("submitted_name")
            if data.get("submitted_brand") and not existing.submitted_brand:
                existing.submitted_brand = data["submitted_brand"]
                update_fields.append("submitted_brand")
            if data.get("submitted_nutrients") and not existing.submitted_nutrients:
                existing.submitted_nutrients = data["submitted_nutrients"]
                update_fields.append("submitted_nutrients")
            if data.get("submitted_source_url") and not existing.submitted_source_url:
                existing.submitted_source_url = data["submitted_source_url"]
                update_fields.append("submitted_source_url")

            existing.save(update_fields=update_fields)

            response_serializer = FoodRequestResponseSerializer(existing)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        # Create new request
        food_request = serializer.save()

        # Quick auto-validation heuristic
        confidence = self._compute_confidence(food_request)
        food_request.ai_confidence = confidence

        if confidence >= 0.8:
            food_item = self._auto_create_food(food_request)
            if food_item:
                food_request.food_item = food_item
                food_request.status = "auto_created"
                food_request.ai_review_notes = "Auto-created: high confidence"
            else:
                food_request.ai_review_notes = "Auto-creation failed, queued for review"

        food_request.save()

        response_serializer = FoodRequestResponseSerializer(food_request)
        return Response(
            response_serializer.data,
            status=status.HTTP_201_CREATED,
        )

    def _compute_confidence(self, req):
        """Simple heuristic confidence score (0.0 - 1.0)."""
        score = 0.0

        if req.submitted_name:
            score += 0.3

        if req.submitted_barcode:
            score += 0.2

        nutrients = req.submitted_nutrients or {}
        if nutrients.get("energy_kcal") is not None:
            score += 0.2
            try:
                kcal = float(nutrients["energy_kcal"])
                if 0 < kcal <= 900:
                    score += 0.1
            except (ValueError, TypeError):
                pass

        if len(nutrients) >= 3:
            score += 0.1

        if req.submitted_source_url:
            score += 0.1

        return min(score, 1.0)

    def _auto_create_food(self, req):
        """Auto-create a FoodItem from the request data."""
        from decimal import Decimal, InvalidOperation

        from core.models import (
            FoodItem,
            FoodNutrientValue,
            FoodText,
            ImportedRecord,
            Nutrient,
        )

        name = req.submitted_name or req.original_query
        barcode = req.submitted_barcode
        canonical_key = f"req:{barcode}" if barcode else f"req:{req.id}"

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
                },
                food_item=food,
            )

            nutrients = req.submitted_nutrients or {}
            if nutrients:
                nutrient_objs = {
                    n.canonical_code: n
                    for n in Nutrient.objects.filter(
                        canonical_code__in=list(nutrients.keys())
                    )
                }
                values_to_create = []
                for code, amount in nutrients.items():
                    nutrient = nutrient_objs.get(code)
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

        except Exception:
            return None


class FoodRequestDetailView(generics.RetrieveAPIView):
    """
    GET /api/foods/request/{id}

    Check status of a food request.
    """

    serializer_class = FoodRequestResponseSerializer
    queryset = FoodRequest.objects.all()
    lookup_field = "id"
