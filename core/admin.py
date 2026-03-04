import json
from decimal import Decimal, InvalidOperation

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from .models import (
    FoodItem,
    FoodNutrientValue,
    FoodRequest,
    FoodText,
    ImportedRecord,
    Nutrient,
    ValidationEvent,
)


def rejected_count(request):
    """Badge callback for sidebar -- shows number of items needing review."""
    try:
        count = ValidationEvent.objects.filter(
            status__in=["rejected", "needs_review"]
        ).count()
        return count if count > 0 else None
    except Exception:
        return None


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
        "updated_at",
    )
    list_filter = ("food_type",)
    search_fields = ("canonical_key",)
    list_per_page = 30
    show_full_result_count = False
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [FoodTextInline, FoodNutrientValueInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("texts")

    @display(description="Name")
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
        },
    )
    def food_type_badge(self, obj):
        return obj.food_type


# ---------------------------------------------------------------------------
# FoodText
# ---------------------------------------------------------------------------
@admin.register(FoodText)
class FoodTextAdmin(ModelAdmin):
    list_display = ("name", "lang", "brand", "food_item")
    list_filter = ("lang",)
    search_fields = ("name",)
    list_per_page = 30
    show_full_result_count = False
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
    search_fields = ("food_item__canonical_key",)
    autocomplete_fields = ("food_item", "nutrient")
    list_per_page = 30
    show_full_result_count = False


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
    show_full_result_count = False
    inlines = [ValidationEventInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("food_item")

    @display(
        description="Quelle",
        label={"USDA": "info", "OFF": "success", "USER_REQ": "warning"},
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

    def raw_json_pretty(self, obj):
        pretty = json.dumps(obj.raw_json, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:400px;overflow:auto;background:#f8f9fa;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    raw_json_pretty.short_description = "Raw JSON"


# ---------------------------------------------------------------------------
# ValidationEvent -- "Rejected Queue"
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
    search_fields = ("imported_record__external_id",)
    show_full_result_count = False
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


# ---------------------------------------------------------------------------
# FoodRequest -- "Learning Loop"
# ---------------------------------------------------------------------------
@admin.register(FoodRequest)
class FoodRequestAdmin(ModelAdmin):
    list_display = (
        "original_query",
        "show_submitted_name",
        "show_barcode",
        "status_badge",
        "request_count_display",
        "ai_confidence_bar",
        "show_linked",
        "created_at",
    )
    list_filter = ("status", "lang")
    search_fields = ("original_query", "submitted_name", "submitted_barcode")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "request_count",
        "ai_confidence",
        "ai_review_notes",
        "nutrients_display",
        "raw_data_display",
    )
    list_per_page = 30
    autocomplete_fields = ("food_item",)
    actions = ["approve_and_create", "reject_requests"]

    fieldsets = (
        (
            "Anfrage",
            {
                "fields": (
                    "original_query",
                    "lang",
                    "submitted_name",
                    "submitted_brand",
                    "submitted_barcode",
                    "submitted_source_url",
                ),
            },
        ),
        (
            "Nahrwerte",
            {
                "fields": ("nutrients_display",),
                "classes": ("collapse",),
            },
        ),
        (
            "Rohdaten",
            {
                "fields": ("raw_data_display",),
                "classes": ("collapse",),
            },
        ),
        (
            "Verarbeitung",
            {
                "fields": (
                    "status",
                    "food_item",
                    "ai_confidence",
                    "ai_review_notes",
                    "request_count",
                ),
            },
        ),
        ("Meta", {"fields": ("id", "created_at", "updated_at")}),
    )

    @display(description="Name")
    def show_submitted_name(self, obj):
        return obj.submitted_name or "-"

    @display(description="Barcode")
    def show_barcode(self, obj):
        return obj.submitted_barcode or "-"

    @display(
        description="Status",
        label={
            "pending": "warning",
            "auto_created": "info",
            "approved": "success",
            "rejected": "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    @display(description="Anfragen")
    def request_count_display(self, obj):
        count = obj.request_count
        if count >= 10:
            return format_html(
                '<span style="color:#ef4444;font-weight:bold">{}</span>', count
            )
        elif count >= 5:
            return format_html(
                '<span style="color:#f59e0b;font-weight:bold">{}</span>', count
            )
        return count

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

    @display(description="Verlinkt")
    def show_linked(self, obj):
        if obj.food_item:
            return format_html(
                '<span style="color:green">&#10003;</span> {}',
                obj.food_item.canonical_key[:40],
            )
        return format_html('<span style="color:#9ca3af">-</span>')

    def nutrients_display(self, obj):
        if not obj.submitted_nutrients:
            return "-"
        pretty = json.dumps(obj.submitted_nutrients, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:300px;overflow:auto;background:#f8f9fa;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    nutrients_display.short_description = "Eingereichte Nahrwerte"

    def raw_data_display(self, obj):
        if not obj.submitted_raw_data:
            return "-"
        pretty = json.dumps(obj.submitted_raw_data, indent=2, ensure_ascii=False)
        return format_html(
            '<pre style="max-height:300px;overflow:auto;background:#f8f9fa;'
            'padding:12px;border-radius:8px;font-size:12px">{}</pre>',
            pretty,
        )

    raw_data_display.short_description = "Rohdaten"

    # -- Admin Actions --
    @action(description="Genehmigen & FoodItem erstellen")
    def approve_and_create(self, request, queryset):
        created = 0
        for req in queryset.filter(status__in=["pending", "auto_created"]):
            food_item = self._create_food_from_request(req)
            if food_item:
                req.status = "approved"
                req.food_item = food_item
                req.ai_review_notes = f"Approved by admin: {request.user.username}"
                req.save(update_fields=["status", "food_item", "ai_review_notes", "updated_at"])
                created += 1
        self.message_user(
            request,
            f"{created} Lebensmittel erstellt und genehmigt.",
        )

    @action(description="Ablehnen")
    def reject_requests(self, request, queryset):
        updated = queryset.filter(status="pending").update(
            status="rejected",
            ai_review_notes=f"Rejected by admin: {request.user.username}",
        )
        self.message_user(request, f"{updated} Anfrage(n) abgelehnt.")

    def _create_food_from_request(self, req):
        """Create a FoodItem from an approved FoodRequest."""
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
                },
                food_item=food,
            )

            # Nutrients
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
