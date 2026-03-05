"""
API views for Nutrition Core Database.

Search is powered by Meilisearch for instant (<50ms) results.
Falls back to PostgreSQL FTS if Meilisearch is unavailable.

FoodRequest allows apps to submit missing food data for auto-creation.
"""

import hashlib
import logging

from django.conf import settings as django_settings
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

logger = logging.getLogger(__name__)


def index_food_in_meilisearch(food_item, food_text, sources=None):
    """
    Index a single food item into Meilisearch (live sync).
    Called after auto-creation from FoodRequest.
    Nutrients are NOT stored in Meili — loaded from PG at query time.
    Silently fails if Meilisearch is unavailable.
    """
    meili = _get_meili()
    if not meili:
        return

    try:
        doc = {
            "id": str(food_text.id),
            "food_item_id": str(food_item.id),
            "canonical_key": food_item.canonical_key,
            "food_type": food_item.food_type,
            "name": food_text.name or "",
            "brand": food_text.brand or "",
            "lang": food_text.lang,
            "source": list(sources) if sources else ["USER_REQ"],
        }
        meili.index("foods").add_documents([doc])
    except Exception as e:
        logger.warning("Failed to index food %s in Meilisearch: %s", food_item.id, e)

# Map BCP 47 lang codes to PostgreSQL text search config names (fallback)
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

# ---------------------------------------------------------------------------
# Meilisearch client (lazy singleton)
# ---------------------------------------------------------------------------
_meili_client = None


def _get_meili():
    """Get or create Meilisearch client. Returns None if unavailable."""
    global _meili_client
    if _meili_client is not None:
        return _meili_client
    try:
        import meilisearch
        _meili_client = meilisearch.Client(
            django_settings.MEILISEARCH_URL,
            django_settings.MEILISEARCH_MASTER_KEY,
        )
        # Quick health check
        _meili_client.health()
        return _meili_client
    except Exception as e:
        logger.warning("Meilisearch unavailable, falling back to PostgreSQL: %s", e)
        _meili_client = None
        return None


class FoodSearchView(generics.ListAPIView):
    """
    GET /api/foods/search?q=Banane&lang=de&limit=25&offset=0

    Primary: Meilisearch (instant, typo-tolerant, ranked)
    Fallback: PostgreSQL FTS + trigram (if Meilisearch is down)

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

        # Parse pagination params
        limit = int(request.query_params.get("limit", 25))
        limit = min(limit, 100)
        offset = int(request.query_params.get("offset", 0))

        # Try Meilisearch first
        meili = _get_meili()
        if meili:
            try:
                response_data = self._search_meilisearch(
                    meili, q, lang, food_type_param, source_param,
                    limit, offset, request,
                )
                cache.set(cache_key, response_data, CACHE_TTL)
                return Response(response_data)
            except Exception as e:
                logger.warning("Meilisearch search failed, falling back: %s", e)

        # Fallback to PostgreSQL
        response_data = self._search_postgres(
            q, lang, food_type_param, source_param, limit, offset, request,
        )
        cache.set(cache_key, response_data, CACHE_TTL)
        return Response(response_data)

    # ------------------------------------------------------------------
    # Meilisearch search
    # ------------------------------------------------------------------
    def _search_meilisearch(
        self, client, q, lang, food_type_param, source_param,
        limit, offset, request,
    ):
        """Search via Meilisearch — typically <50ms."""
        index = client.index("foods")

        # Build filter expressions
        filters = []
        if lang:
            filters.append(f'lang = "{lang}"')

        if food_type_param:
            valid_types = {"raw", "branded", "supplement"}
            types = [t.strip() for t in food_type_param.split(",") if t.strip() in valid_types]
            if types:
                if len(types) == 1:
                    filters.append(f'food_type = "{types[0]}"')
                else:
                    or_parts = " OR ".join(f'food_type = "{t}"' for t in types)
                    filters.append(f"({or_parts})")

        if source_param:
            sources = [s.strip() for s in source_param.split(",") if s.strip()]
            if sources:
                if len(sources) == 1:
                    filters.append(f'source = "{sources[0]}"')
                else:
                    or_parts = " OR ".join(f'source = "{s}"' for s in sources)
                    filters.append(f"({or_parts})")

        search_params = {
            "limit": limit,
            "offset": offset,
            "showRankingScore": True,
            "attributesToRetrieve": [
                "id", "food_item_id", "canonical_key", "food_type",
                "name", "brand", "lang",
            ],
        }
        if filters:
            search_params["filter"] = " AND ".join(filters)

        result = index.search(q, search_params)

        # Load nutrients from PostgreSQL for the hits
        food_item_ids = [hit["food_item_id"] for hit in result["hits"]]
        nutrients_map = {}
        if food_item_ids:
            from core.models import FoodNutrientValue
            for nv in (
                FoodNutrientValue.objects.filter(
                    food_item_id__in=food_item_ids,
                    basis="per_100g",
                ).select_related("nutrient")
            ):
                nutrients_map.setdefault(str(nv.food_item_id), {})[
                    nv.nutrient.canonical_code
                ] = float(nv.amount)

        # Build response matching our API format
        results = []
        for hit in result["hits"]:
            results.append({
                "id": hit["food_item_id"],
                "canonical_key": hit["canonical_key"],
                "food_type": hit["food_type"],
                "name": hit["name"],
                "brand": hit.get("brand") or None,
                "lang": hit["lang"],
                "nutrients": nutrients_map.get(hit["food_item_id"], {}),
                "score": round(hit.get("_rankingScore", 0), 4) if "_rankingScore" in hit else 0,
            })

        total_count = result.get("estimatedTotalHits", len(results))

        # Build pagination URLs
        base_url = request.build_absolute_uri(request.path)
        params = request.query_params.copy()

        next_url = None
        if offset + limit < total_count:
            params["offset"] = offset + limit
            params["limit"] = limit
            next_url = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        prev_url = None
        if offset > 0:
            params["offset"] = max(0, offset - limit)
            params["limit"] = limit
            prev_url = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        return {
            "count": total_count,
            "next": next_url,
            "previous": prev_url,
            "results": results,
        }

    # ------------------------------------------------------------------
    # PostgreSQL fallback (same as before)
    # ------------------------------------------------------------------
    def _search_postgres(
        self, q, lang, food_type_param, source_param,
        limit, offset, request,
    ):
        """Fallback: PostgreSQL FTS-first with trigram fallback."""
        pg_config = PG_LANG_MAP.get(lang, "simple")
        search_query = SearchQuery(q, config=pg_config, search_type="plain")

        qs = FoodText.objects.select_related("food_item")

        if lang:
            qs = qs.filter(lang=lang)

        if food_type_param:
            valid_types = {"raw", "branded", "supplement"}
            types = [t.strip() for t in food_type_param.split(",") if t.strip() in valid_types]
            if types:
                qs = qs.filter(food_item__food_type__in=types)

        if source_param:
            sources = [s.strip() for s in source_param.split(",")]
            qs = qs.filter(food_item__imported_records__source__in=sources)

        # FTS first
        fts_qs = qs.filter(search_vector=search_query).annotate(
            fts_rank=SearchRank(F("search_vector"), search_query),
            prefix_bonus=Case(
                When(name__istartswith=q, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
            score=SearchRank(F("search_vector"), search_query)
            * Value(2.0, output_field=FloatField())
            + Case(
                When(name__istartswith=q, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            ),
        ).order_by("-score", "name")

        needed = offset + limit
        fts_results = list(fts_qs[:needed + 1])
        has_more_fts = len(fts_results) > needed

        if len(fts_results) >= needed or has_more_fts:
            page = fts_results[offset:offset + limit]
            total_count = offset + len(fts_results)
        else:
            trigram_qs = qs.filter(
                Q(name__trigram_similar=q) | Q(name__istartswith=q)
            ).annotate(
                trigram_sim=TrigramSimilarity("name", q),
                prefix_bonus=Case(
                    When(name__istartswith=q, then=Value(1.0)),
                    default=Value(0.0),
                    output_field=FloatField(),
                ),
                score=Greatest(
                    TrigramSimilarity("name", q),
                    Value(0.0),
                    output_field=FloatField(),
                ) + Case(
                    When(name__istartswith=q, then=Value(1.0)),
                    default=Value(0.0),
                    output_field=FloatField(),
                ),
            ).order_by("-score", "name")

            fts_ids = {ft.id for ft in fts_results}
            trigram_results = [
                ft for ft in trigram_qs[:needed]
                if ft.id not in fts_ids
            ]
            combined = fts_results + trigram_results
            page = combined[offset:offset + limit]
            total_count = len(combined)

        # Batch load nutrients
        food_item_ids = [ft.food_item_id for ft in page]
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

        results = []
        for ft in page:
            results.append({
                "id": str(ft.food_item.id),
                "canonical_key": ft.food_item.canonical_key,
                "food_type": ft.food_item.food_type,
                "name": ft.name,
                "brand": ft.brand,
                "lang": ft.lang,
                "nutrients": nutrients_map.get(ft.food_item_id, {}),
                "score": round(float(ft.score), 4) if hasattr(ft, "score") else 0,
            })

        base_url = request.build_absolute_uri(request.path)
        params = request.query_params.copy()

        next_url = None
        if offset + limit < total_count:
            params["offset"] = offset + limit
            params["limit"] = limit
            next_url = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        prev_url = None
        if offset > 0:
            params["offset"] = max(0, offset - limit)
            params["limit"] = limit
            prev_url = f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

        return {
            "count": total_count,
            "next": next_url,
            "previous": prev_url,
            "results": results,
        }


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

            # Live-index into Meilisearch
            food_text = FoodText.objects.filter(food_item=food).first()
            if food_text:
                index_food_in_meilisearch(
                    food_item=food,
                    food_text=food_text,
                    sources=["USER_REQ"],
                )

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
