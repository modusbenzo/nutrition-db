from rest_framework import serializers

from core.models import FoodItem, FoodNutrientValue, FoodRequest, FoodText, Nutrient


# ---------------------------------------------------------------------------
# Existing serializers (Detail view)
# ---------------------------------------------------------------------------
class NutrientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Nutrient
        fields = ("canonical_code", "unit", "category")


class FoodNutrientValueSerializer(serializers.ModelSerializer):
    nutrient = NutrientSerializer(read_only=True)

    class Meta:
        model = FoodNutrientValue
        fields = ("nutrient", "basis", "amount", "unit")


class FoodTextSerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodText
        fields = ("lang", "name", "brand", "ingredients")


class FoodItemListSerializer(serializers.ModelSerializer):
    """Compact serializer for search results."""

    texts = serializers.SerializerMethodField()
    nutrients = serializers.SerializerMethodField()

    class Meta:
        model = FoodItem
        fields = ("id", "canonical_key", "food_type", "texts", "nutrients")

    def _get_lang(self):
        request = self.context.get("request")
        return request.query_params.get("lang", "en") if request else "en"

    def get_texts(self, obj):
        lang = self._get_lang()
        texts = getattr(obj, "_prefetched_texts", None)
        if texts is None:
            qs = obj.texts.filter(lang=lang)
            if not qs.exists():
                qs = obj.texts.all()[:1]
            return FoodTextSerializer(qs, many=True).data
        # Use prefetched data
        filtered = [t for t in texts if t.lang == lang]
        if not filtered:
            filtered = texts[:1]
        return FoodTextSerializer(filtered, many=True).data

    def get_nutrients(self, obj):
        values = getattr(obj, "_prefetched_nutrients", None)
        if values is None:
            qs = obj.nutrient_values.filter(basis="per_100g").select_related("nutrient")
            return FoodNutrientValueSerializer(qs, many=True).data
        filtered = [v for v in values if v.basis == "per_100g"]
        return FoodNutrientValueSerializer(filtered, many=True).data


class FoodItemDetailSerializer(FoodItemListSerializer):
    """Full detail serializer -- includes all bases."""

    all_nutrients = serializers.SerializerMethodField()

    class Meta(FoodItemListSerializer.Meta):
        fields = FoodItemListSerializer.Meta.fields + ("all_nutrients", "created_at", "updated_at")

    def get_all_nutrients(self, obj):
        qs = obj.nutrient_values.select_related("nutrient")
        return FoodNutrientValueSerializer(qs, many=True).data


# ---------------------------------------------------------------------------
# Flat Search Serializer (fast, no nesting)
# ---------------------------------------------------------------------------
class FoodItemSearchSerializer(serializers.Serializer):
    """
    Flat serializer for search results — minimal overhead, no N+1.
    Built from annotated queryset, not from model traversal.
    """

    id = serializers.UUIDField()
    canonical_key = serializers.CharField()
    food_type = serializers.CharField()
    name = serializers.CharField()
    brand = serializers.CharField(allow_null=True)
    lang = serializers.CharField()
    nutrients = serializers.DictField()
    score = serializers.FloatField(required=False)


# ---------------------------------------------------------------------------
# FoodRequest Serializers
# ---------------------------------------------------------------------------
class FoodRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for POST /api/foods/request — app submits missing food data."""

    class Meta:
        model = FoodRequest
        fields = (
            "original_query",
            "lang",
            "submitted_name",
            "submitted_brand",
            "submitted_barcode",
            "submitted_nutrients",
            "submitted_source_url",
            "submitted_raw_data",
        )

    def validate_original_query(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("original_query must not be empty.")
        return value.strip()

    def validate_submitted_nutrients(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("submitted_nutrients must be a dict.")
        return value


class FoodRequestResponseSerializer(serializers.ModelSerializer):
    """Serializer for reading FoodRequest status."""

    class Meta:
        model = FoodRequest
        fields = (
            "id",
            "original_query",
            "lang",
            "submitted_name",
            "submitted_brand",
            "submitted_barcode",
            "status",
            "food_item",
            "ai_confidence",
            "request_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
