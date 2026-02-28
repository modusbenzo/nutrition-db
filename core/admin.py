import json

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    FoodItem,
    FoodNutrientValue,
    FoodText,
    ImportedRecord,
    Nutrient,
    ValidationEvent,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class FoodTextInline(admin.TabularInline):
    model = FoodText
    extra = 0


class FoodNutrientValueInline(admin.TabularInline):
    model = FoodNutrientValue
    extra = 0


# ---------------------------------------------------------------------------
# FoodItem
# ---------------------------------------------------------------------------
@admin.register(FoodItem)
class FoodItemAdmin(admin.ModelAdmin):
    list_display = ("canonical_key", "food_type", "updated_at")
    list_filter = ("food_type",)
    search_fields = ("canonical_key", "texts__name")
    inlines = [FoodTextInline, FoodNutrientValueInline]
    readonly_fields = ("id", "created_at", "updated_at")


# ---------------------------------------------------------------------------
# FoodText
# ---------------------------------------------------------------------------
@admin.register(FoodText)
class FoodTextAdmin(admin.ModelAdmin):
    list_display = ("name", "lang", "brand", "food_item")
    list_filter = ("lang",)
    search_fields = ("name", "brand")


# ---------------------------------------------------------------------------
# Nutrient
# ---------------------------------------------------------------------------
@admin.register(Nutrient)
class NutrientAdmin(admin.ModelAdmin):
    list_display = ("canonical_code", "unit", "category", "usda_nutrient_id", "off_key")
    search_fields = ("canonical_code",)


# ---------------------------------------------------------------------------
# FoodNutrientValue
# ---------------------------------------------------------------------------
@admin.register(FoodNutrientValue)
class FoodNutrientValueAdmin(admin.ModelAdmin):
    list_display = ("food_item", "nutrient", "basis", "amount", "unit")
    list_filter = ("basis",)
    search_fields = ("food_item__canonical_key", "nutrient__canonical_code")


# ---------------------------------------------------------------------------
# ImportedRecord
# ---------------------------------------------------------------------------
@admin.register(ImportedRecord)
class ImportedRecordAdmin(admin.ModelAdmin):
    list_display = ("source", "external_id", "food_item", "created_at")
    list_filter = ("source",)
    search_fields = ("external_id",)
    readonly_fields = ("id", "created_at", "raw_json_pretty")

    def raw_json_pretty(self, obj):
        pretty = json.dumps(obj.raw_json, indent=2, ensure_ascii=False)
        return format_html("<pre style='max-height:400px;overflow:auto'>{}</pre>", pretty)

    raw_json_pretty.short_description = "Raw JSON"


# ---------------------------------------------------------------------------
# ValidationEvent  – "Rejected Queue"
# ---------------------------------------------------------------------------
class ValidationStatusFilter(admin.SimpleListFilter):
    title = "Review Status"
    parameter_name = "review_status"

    def lookups(self, request, model_admin):
        return [
            ("rejected_or_review", "Rejected / Needs Review"),
            ("rejected", "Rejected"),
            ("needs_review", "Needs Review"),
            ("accepted", "Accepted"),
        ]

    def queryset(self, request, queryset):
        val = self.value()
        if val == "rejected_or_review":
            return queryset.filter(status__in=["rejected", "needs_review"])
        if val in ("rejected", "needs_review", "accepted"):
            return queryset.filter(status=val)
        return queryset


@admin.register(ValidationEvent)
class ValidationEventAdmin(admin.ModelAdmin):
    list_display = (
        "imported_record",
        "status",
        "reason_code",
        "ai_confidence",
        "created_at",
    )
    list_filter = (ValidationStatusFilter, "status")
    search_fields = ("imported_record__external_id", "reason_code")
    readonly_fields = (
        "id",
        "created_at",
        "raw_json_display",
        "suggested_patch_display",
    )
    actions = ["force_accept", "link_to_food_item"]

    fieldsets = (
        (None, {"fields": ("id", "imported_record", "status", "reason_code", "reason_text")}),
        ("AI", {"fields": ("ai_confidence",)}),
        (
            "Data",
            {
                "fields": ("raw_json_display", "suggested_patch_display"),
                "classes": ("collapse",),
            },
        ),
        ("Meta", {"fields": ("created_at",)}),
    )

    # -- pretty displays --
    def raw_json_display(self, obj):
        pretty = json.dumps(
            obj.imported_record.raw_json, indent=2, ensure_ascii=False
        )
        return format_html("<pre style='max-height:400px;overflow:auto'>{}</pre>", pretty)

    raw_json_display.short_description = "Imported Raw JSON"

    def suggested_patch_display(self, obj):
        pretty = json.dumps(obj.suggested_patch, indent=2, ensure_ascii=False)
        return format_html("<pre style='max-height:400px;overflow:auto'>{}</pre>", pretty)

    suggested_patch_display.short_description = "Suggested Patch"

    # -- admin actions --
    @admin.action(description="Force Accept selected events")
    def force_accept(self, request, queryset):
        updated = queryset.update(status="accepted")
        self.message_user(request, f"{updated} event(s) set to accepted.")

    @admin.action(description="Link to FoodItem (sets first selected food_item on record)")
    def link_to_food_item(self, request, queryset):
        """
        Links the ImportedRecord of each selected ValidationEvent to an existing
        FoodItem. Uses the food_item already referenced on the ImportedRecord if
        present, otherwise tries to match by external_id as canonical_key.
        In a production UI this would be a custom form – for the MVP we auto-match.
        """
        linked = 0
        for event in queryset.select_related("imported_record"):
            record = event.imported_record
            if record.food_item_id:
                continue
            food = FoodItem.objects.filter(
                canonical_key=f"off:{record.external_id}"
            ).first()
            if food:
                record.food_item = food
                record.save(update_fields=["food_item"])
                linked += 1
        self.message_user(request, f"{linked} record(s) linked to FoodItem.")
