from django.db.models import Q
from rest_framework import generics, status
from rest_framework.response import Response

from core.models import FoodItem, FoodText

from .serializers import FoodItemDetailSerializer, FoodItemListSerializer


class FoodSearchView(generics.ListAPIView):
    """
    GET /api/foods/search?q=...&lang=de
    Searches FoodText.name and brand, returns FoodItems with per_100g nutrients.
    """

    serializer_class = FoodItemListSerializer

    def get_queryset(self):
        q = self.request.query_params.get("q", "").strip()
        lang = self.request.query_params.get("lang", "")

        if not q:
            return FoodItem.objects.none()

        filters = Q(texts__name__icontains=q) | Q(texts__brand__icontains=q)
        if lang:
            filters &= Q(texts__lang=lang)

        return (
            FoodItem.objects.filter(filters)
            .distinct()
            .prefetch_related("texts", "nutrient_values__nutrient")
        )


class FoodDetailView(generics.RetrieveAPIView):
    """
    GET /api/foods/{id}?lang=de
    """

    serializer_class = FoodItemDetailSerializer
    queryset = FoodItem.objects.prefetch_related(
        "texts", "nutrient_values__nutrient"
    )
    lookup_field = "id"
