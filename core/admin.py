import json

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models import (
    FoodItem,
    FoodNutrientValue,
    FoodText,
    ImportedRecord,
    Nutrient,
    ValidationEvent,
)


def rejected_count(request):
    """Badge callback for sidebar — shows number of items needing review."""
    count = ValidationEvent.objects.filter(
        status__in=["rejected", "needs_review"]
    ).count()
    return count if count > 0 else None


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class FoodTextInline(TabularInline):
    model = FoodText
    extra = 0
    fields = ("lang", "name", "brand", "ingredients")
    tab = True


class FoodNutrientValueInline(TabularInline):
    model = FoodNutrientValue
    extra = 0
    fields = ("nutrient", "basis", "amount", "unit")
    autocomplete_fields = ("nutrient",)
    tab = True


class ValidationEventInline(TabularInline):
    model = ValidationEvent
    extra = 0
    fields = ("status", "reason_code", "reason_text", "ai_confidence")
    readonly_fields = ("status", "reason_code", "reason_text", "ai_confidence")
    tab = True


# ---------------------------------------------------------------------------
# FoodItem
# ---------------------------------------------------------------------------
@admin.register(FoodItem)
class FoodItemAdmin(ModelAdmin):
    list_display = (
        "canonical_key",
        "show_name",
        "show_brand",
        "food_type_badge",
        "show_nutrient_count",
        "updated_at",
    )
    list_filter = ("food_type",)
    search_fields = ("canonical_key", "texts__name", "texts__brand")
    list_per_page = 30
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [FoodTextInline, FoodNutrientValueInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related("texts")
            .annotate(nutrient_count=Count("nutrient_values"))
        )

    @display(description="Name", ordering="texts__name")
    def show_name(self, obj):
        text = obj.texts.first()
        return text.name if text else "-"

    @display(description="Marke")
    def show_brand(self, obj):
        text = obj.texts.first()
        return text.brand if text and text.brand else "-"

    @display(
        description="Typ",
        label={
            "raw": "success",
            "branded": "info",
            "supplement": "warning",
            "recipe": "primary",
        },
    )
    def food_type_badge(self, obj):
        return obj.food_type

    @display(description="Nahrstoffe")
    def show_nutrient_count(self, obj):
        return obj.nutrient_count


# ---------------------------------------------------------------------------
# FoodText
# ---------------------------------------------------------------------------
@admin.register(FoodText)
class FoodTextAdmin(ModelAdmin):
    list_display = ("name", "lang", "brand", "food_item")
    list_filter = ("lang",)
    search_fields = ("name", "brand")
    list_per_page = 30
    autocomplete_fields = ("food_item",)


# ---------------------------------------------------------------------------
# Nutrient
# ---------------------------------------------------------------------------
@admin.register(Nutrient)
class NutrientAdmin(ModelAdmin):
    list_display = (
        "canonical_code",
        "unit",
        "category_badge",
        "usda_nutrient_id",
        "off_key",
    )
    list_filter = ("category",)
    search_fields = ("canonical_code", "off_key")
    list_per_page = 50

    @display(
        description="Kategorie",
        label={
            "energy": "warning",
            "macronutrient": "success",
            "mineral": "info",
            "vitamin": "primary",
            "other": "secondary",
        },
    )
    def category_badge(self, obj):
        return obj.category


# ---------------------------------------------------------------------------
# FoodNutrientValue
# ---------------------------------------------------------------------------
@admin.register(FoodNutrientValue)
class FoodNutrientValueAdmin(ModelAdmin):
    list_display = ("food_item", "nutrient", "basis", "amount", "unit")
    list_filter = ("basis", "nutrient__category")
    search_fields = ("food_item__canonical_key", "nutrient__canonical_code")
    autocomplete_fields = ("food_item", "nutrient")
    list_per_page = 30


# ---------------------------------------------------------------------------
# ImportedRecord
# ---------------------------------------------------------------------------
@admin.register(ImportedRecord)
class ImportedRecordAdmin(ModelAdmin):
    list_display = (
        "source_badge",
        "external_id",
        "show_linked",
        "show_validation_count",
        "created_at",
    )
    list_filter = ("source",)
    search_fields = ("external_id",)
    readonly_fields = ("id", "created_at", "raw_json_pretty")
    autocomplete_fields = ("food_item",)
    list_per_page = 30
    inlines = [ValidationEventInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("food_item")
            .annotate(validation_count=Count("validations"))
        )

    @display(
        description="Quelle",
        label={"USDA": "info", "OFF": "success"},
    )
    def source_badge(self, obj):
        return obj.source

    @display(description="Verlinkt")
    def show_linked(self, obj):
        if obj.food_item:
            return format_html(
                '<span style="color:green">&#10003;</span> {}',
                obj.food_item.canonical_key[:40],
            )
        return format_html('<span style="color:red">&#10007;</span>')

    @display(description="Validierungen")
    def show_validation_count(self, obj):
        return obj.validation_count

    def raw_json_pretty(self, obj):
        pretty = json.dumps(obj.raw_json, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f8f9fa;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    raw_json_pretty.short_description = "Raw JSON"


# ---------------------------------------------------------------------------
# ValidationEvent — "Rejected Queue"
# ---------------------------------------------------------------------------
@admin.register(ValidationEvent)
class ValidationEventAdmin(ModelAdmin):
    list_display = (
        "show_source",
        "show_external_id",
        "status_badge",
        "reason_code",
        "ai_confidence_bar",
        "created_at",
    )
    list_filter = ("status", "reason_code")
    search_fields = ("imported_record__external_id", "reason_code", "reason_text")
    readonly_fields = (
        "id",
        "created_at",
        "raw_json_display",
        "suggested_patch_display",
    )
    list_per_page = 30
    actions = ["force_accept_bulk", "link_to_food_item"]

    fieldsets = (
        (
            "Status",
            {"fields": ("imported_record", "status", "reason_code", "reason_text")},
        ),
        ("KI-Bewertung", {"fields": ("ai_confidence",)}),
        (
            "Daten",
            {
                "fields": ("raw_json_display", "suggested_patch_display"),
                "classes": ("collapse",),
            },
        ),
        ("Meta", {"fields": ("id", "created_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("imported_record", "imported_record__food_item")
        )

    @display(description="Quelle")
    def show_source(self, obj):
        return obj.imported_record.source

    @display(description="External ID")
    def show_external_id(self, obj):
        return obj.imported_record.external_id

    @display(
        description="Status",
        label={
            "accepted": "success",
            "rejected": "danger",
            "needs_review": "warning",
        },
    )
    def status_badge(self, obj):
        return obj.status

    @display(description="Konfidenz")
    def ai_confidence_bar(self, obj):
        pct = int(obj.ai_confidence * 100)
        color = "#22c55e" if pct >= 80 else "#f59e0b" if pct >= 50 else "#ef4444"
        return format_html(
            '<div style="width:80px;background:#e5e7eb;border-radius:4px;overflow:hidden">'
            '<div style="width:{}%;height:8px;background:{};border-radius:4px"></div>'
            "</div>"
            '<span style="font-size:11px;color:#6b7280">{}%</span>',
            pct,
            color,
            pct,
        )

    def raw_json_display(self, obj):
        pretty = json.dumps(
            obj.imported_record.raw_json, indent=2, ensure_ascii=False
        )
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f8f9fa;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    raw_json_display.short_description = "Importierte Rohdaten (JSON)"

    def suggested_patch_display(self, obj):
        pretty = json.dumps(obj.suggested_patch, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f0fdf4;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    suggested_patch_display.short_description = "Vorgeschlagene Korrektur"

    # -- Admin Actions --
    @action(description="Akzeptieren (Force Accept)")
    def force_accept_bulk(self, request, queryset):
        updated = queryset.update(status="accepted")
        self.message_user(request, f"{updated} Eintrag/Eintraege akzeptiert.")

    @action(description="Mit FoodItem verlinken")
    def link_to_food_item(self, request, queryset):
        linked = 0
        for event in queryset.select_related("imported_record"):
            record = event.imported_record
            if record.food_item_id:
                continue
            food = FoodItem.objects.filter(
                canonical_key=f"off:{record.external_id}"
            ).first()
            if not food:
                food = FoodItem.objects.filter(
                    canonical_key=f"usda:{record.external_id}"
                ).first()
            if food:
                record.food_item = food
                record.save(update_fields=["food_item"])
                linked += 1
        self.message_user(request, f"{linked} Datensatz/Datensaetze verlinkt.")
