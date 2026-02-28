from rest_framework import serializers

from core.models import FoodItem, FoodNutrientValue, FoodText, Nutrient


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
        qs = obj.texts.filter(lang=lang)
        if not qs.exists():
            qs = obj.texts.all()[:1]
        return FoodTextSerializer(qs, many=True).data

    def get_nutrients(self, obj):
        qs = obj.nutrient_values.filter(basis="per_100g").select_related("nutrient")
        return FoodNutrientValueSerializer(qs, many=True).data


class FoodItemDetailSerializer(FoodItemListSerializer):
    """Full detail serializer – includes all bases."""

    all_nutrients = serializers.SerializerMethodField()

    class Meta(FoodItemListSerializer.Meta):
        fields = FoodItemListSerializer.Meta.fields + ("all_nutrients", "created_at", "updated_at")

    def get_all_nutrients(self, obj):
        qs = obj.nutrient_values.select_related("nutrient")
        return FoodNutrientValueSerializer(qs, many=True).data
