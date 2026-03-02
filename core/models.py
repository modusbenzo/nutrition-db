import uuid

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models


class FoodType(models.TextChoices):
    RAW = "raw", "Raw"
    BRANDED = "branded", "Branded"
    SUPPLEMENT = "supplement", "Supplement"
    RECIPE = "recipe", "Recipe"


class NutrientBasis(models.TextChoices):
    PER_100G = "per_100g", "Per 100 g"
    PER_SERVING = "per_serving", "Per Serving"


class ImportSource(models.TextChoices):
    USDA = "USDA", "USDA"
    OFF = "OFF", "Open Food Facts"
    USER_REQUEST = "USER_REQ", "User Request"


class ValidationStatus(models.TextChoices):
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    NEEDS_REVIEW = "needs_review", "Needs Review"


class FoodRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTO_CREATED = "auto_created", "Auto-Created"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


# ---------------------------------------------------------------------------
# FoodItem
# ---------------------------------------------------------------------------
class FoodItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_key = models.CharField(max_length=255, unique=True)
    food_type = models.CharField(max_length=20, choices=FoodType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.canonical_key


# ---------------------------------------------------------------------------
# FoodText
# ---------------------------------------------------------------------------
class FoodText(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    food_item = models.ForeignKey(
        FoodItem, on_delete=models.CASCADE, related_name="texts"
    )
    lang = models.CharField(max_length=10, help_text="BCP 47 language tag, e.g. 'de'")
    name = models.CharField(max_length=500)
    brand = models.CharField(max_length=255, blank=True, null=True)
    ingredients = models.TextField(blank=True, null=True)
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        unique_together = [("food_item", "lang")]
        ordering = ["lang"]
        indexes = [
            GinIndex(fields=["search_vector"], name="foodtext_search_gin"),
            GinIndex(
                name="foodtext_name_trgm",
                fields=["name"],
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                name="foodtext_brand_trgm",
                fields=["brand"],
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.lang})"


# ---------------------------------------------------------------------------
# Nutrient
# ---------------------------------------------------------------------------
class Nutrient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_code = models.CharField(max_length=100, unique=True)
    unit = models.CharField(max_length=20)
    category = models.CharField(max_length=100, blank=True, default="")
    usda_nutrient_id = models.IntegerField(blank=True, null=True)
    off_key = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        ordering = ["canonical_code"]

    def __str__(self):
        return f"{self.canonical_code} ({self.unit})"


# ---------------------------------------------------------------------------
# FoodNutrientValue
# ---------------------------------------------------------------------------
class FoodNutrientValue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    food_item = models.ForeignKey(
        FoodItem, on_delete=models.CASCADE, related_name="nutrient_values"
    )
    nutrient = models.ForeignKey(
        Nutrient, on_delete=models.CASCADE, related_name="food_values"
    )
    basis = models.CharField(max_length=20, choices=NutrientBasis.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=4)
    unit = models.CharField(max_length=20)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("food_item", "nutrient", "basis")]
        ordering = ["nutrient__canonical_code"]

    def __str__(self):
        return f"{self.food_item} / {self.nutrient} = {self.amount} {self.unit}"


# ---------------------------------------------------------------------------
# ImportedRecord
# ---------------------------------------------------------------------------
class ImportedRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=10, choices=ImportSource.choices)
    external_id = models.CharField(max_length=255)
    raw_json = models.JSONField()
    food_item = models.ForeignKey(
        FoodItem,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="imported_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source", "external_id"], name="importrec_source_extid"),
        ]

    def __str__(self):
        return f"{self.source}:{self.external_id}"


# ---------------------------------------------------------------------------
# ValidationEvent
# ---------------------------------------------------------------------------
class ValidationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    imported_record = models.ForeignKey(
        ImportedRecord, on_delete=models.CASCADE, related_name="validations"
    )
    status = models.CharField(max_length=20, choices=ValidationStatus.choices)
    reason_code = models.CharField(max_length=100)
    reason_text = models.TextField(blank=True, default="")
    ai_confidence = models.FloatField(default=0.0)
    suggested_patch = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.imported_record} -> {self.status}"


# ---------------------------------------------------------------------------
# FoodRequest — Learning loop for missing foods
# ---------------------------------------------------------------------------
class FoodRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # What the user searched for
    original_query = models.CharField(max_length=500)
    lang = models.CharField(max_length=10, default="de")

    # Data submitted by the app (from web search, barcode scan, etc.)
    submitted_name = models.CharField(max_length=500, blank=True, default="")
    submitted_brand = models.CharField(max_length=255, blank=True, default="")
    submitted_barcode = models.CharField(max_length=50, blank=True, default="")
    submitted_nutrients = models.JSONField(
        default=dict, blank=True,
        help_text="Dict of canonical_code -> amount per 100g",
    )
    submitted_source_url = models.URLField(max_length=1000, blank=True, default="")
    submitted_raw_data = models.JSONField(default=dict, blank=True)

    # Processing
    status = models.CharField(
        max_length=20,
        choices=FoodRequestStatus.choices,
        default=FoodRequestStatus.PENDING,
        db_index=True,
    )
    food_item = models.ForeignKey(
        FoodItem,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="food_requests",
    )

    # AI review
    ai_confidence = models.FloatField(default=0.0)
    ai_review_notes = models.TextField(blank=True, default="")

    # Metadata
    request_count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-request_count", "-created_at"]
        indexes = [
            models.Index(fields=["status", "-request_count"], name="foodreq_status_count"),
            models.Index(fields=["submitted_barcode"], name="foodreq_barcode"),
        ]

    def __str__(self):
        return f"FoodRequest: {self.original_query} ({self.status})"
