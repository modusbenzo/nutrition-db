# Nutrition Core Database — API Docs

**Base URL:** `https://nutritionapi.uk/api`

---

## 1. Search Foods

```
GET /api/foods/search
```

### Query Parameters

| Parameter   | Required | Default | Description |
|-------------|----------|---------|-------------|
| `q`         | Yes      | —       | Search query (min 2 chars) |
| `lang`      | No       | all     | Language filter: `de`, `en`, `fr`, `es`, `it`, `pt`, `nl`, `sv`, `ru`, `tr`, `da`, `fi`, `hu`, `no`, `ro` |
| `food_type` | No       | all     | Filter by type: `raw`, `branded`, `supplement` (comma-separated) |
| `source`    | No       | all     | Filter by data source: `USDA`, `OFF`, `USER_REQ` (comma-separated) |
| `limit`     | No       | 25      | Results per page (max 100) |
| `offset`    | No       | 0       | Pagination offset |

### Search Ranking (Meilisearch)

1. **Words** — all query terms present
2. **Typo tolerance** — fuzzy matching ("Bananna" → "Banana", "brocoli" → "Broccoli")
3. **Proximity** — how close query terms appear
4. **Attribute** — name matches weighted higher than brand
5. **Exactness** — exact matches rank higher
6. **Name length** — shorter/simpler names rank higher when relevance is equal

Fallback: PostgreSQL FTS + Trigram (if Meilisearch is down)

### Example Requests

```bash
# Raw food search (replaces direct USDA API call)
GET /api/foods/search?q=banana&lang=en&food_type=raw

# German branded product
GET /api/foods/search?q=Nutella&lang=de&food_type=branded

# Raw foods only from USDA
GET /api/foods/search?q=chicken breast&lang=en&food_type=raw&source=USDA

# All types (default behavior)
GET /api/foods/search?q=Banane&lang=de
```

### Response

```json
{
  "count": 42,
  "next": "https://nutritionapi.uk/api/foods/search?q=banana&lang=en&food_type=raw&limit=25&offset=25",
  "previous": null,
  "results": [
    {
      "id": "a1b2c3d4-...",
      "canonical_key": "usda:167746",
      "food_type": "raw",
      "name": "Banana, raw",
      "brand": null,
      "lang": "en",
      "nutrients": {
        "energy_kcal": 89.0,
        "proteins": 1.09,
        "fat": 0.33,
        "carbohydrates": 22.84,
        "sugars": 12.23,
        "fiber": 2.6,
        "potassium": 358.0,
        "vitamin_c": 8.7,
        "vitamin_b6": 0.367
      },
      "score": 2.1234
    }
  ]
}
```

### `food_type` Values

| Value        | Description | Sources |
|--------------|-------------|---------|
| `raw`        | Unprocessed/generic foods (Foundation, SR Legacy, Survey) | USDA, OFF |
| `branded`    | Branded/packaged products with barcode | OFF, USDA |
| `supplement` | Dietary supplements | — |

### Mapping from USDA dataType

| USDA `dataType`      | Our `food_type` |
|----------------------|-----------------|
| Foundation           | `raw`           |
| SR Legacy            | `raw`           |
| Survey (FNDDS)       | `raw`           |
| Branded Food         | `branded`       |

---

## 2. Food Detail

```
GET /api/foods/{id}?lang=de
```

### Path Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `id`      | Yes      | Food item UUID |

### Query Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `lang`    | No       | `en`    | Preferred language for texts |

### Response

```json
{
  "id": "a1b2c3d4-...",
  "canonical_key": "usda:167746",
  "food_type": "raw",
  "texts": [
    {
      "lang": "en",
      "name": "Banana, raw",
      "brand": null,
      "ingredients": null
    }
  ],
  "nutrients": [
    {
      "nutrient": {
        "canonical_code": "energy_kcal",
        "unit": "kcal",
        "category": "energy"
      },
      "basis": "per_100g",
      "amount": "89.00",
      "unit": "kcal"
    }
  ],
  "all_nutrients": [...],
  "created_at": "2026-03-01T12:00:00Z",
  "updated_at": "2026-03-01T12:00:00Z"
}
```

---

## 3. Submit Food Request (Learning Loop)

When a user searches and the result is missing or wrong, the app submits a FoodRequest. The system learns from these requests and auto-creates foods when confidence is high enough.

```
POST /api/foods/request
Content-Type: application/json
```

### Request Body

```json
{
  "original_query": "Alnatura Bio Hafermilch",
  "lang": "de",
  "submitted_name": "Haferdrink Natur",
  "submitted_brand": "Alnatura",
  "submitted_barcode": "4104420205925",
  "submitted_nutrients": {
    "energy_kcal": 42,
    "fat": 1.5,
    "carbohydrates": 6.5,
    "sugars": 4.0,
    "proteins": 0.3,
    "fiber": 0.8,
    "salt": 0.1
  },
  "submitted_source_url": "https://www.alnatura.de/haferdrink",
  "submitted_raw_data": {}
}
```

### Fields

| Field                 | Required | Type   | Description |
|-----------------------|----------|--------|-------------|
| `original_query`      | Yes      | string | What the user originally searched for |
| `lang`                | No       | string | Language (default: `de`) |
| `submitted_name`      | No       | string | Correct product name |
| `submitted_brand`     | No       | string | Brand name |
| `submitted_barcode`   | No       | string | EAN/UPC barcode |
| `submitted_nutrients` | No       | object | Nutrient values per 100g (see keys below) |
| `submitted_source_url`| No       | string | URL where data was found |
| `submitted_raw_data`  | No       | object | Any additional raw data |

### Nutrient Keys (for `submitted_nutrients`)

```
energy_kcal, energy_kj, proteins, fat, carbohydrates, sugars, fiber,
saturated_fat, monounsaturated_fat, polyunsaturated_fat, trans_fat,
cholesterol, salt, sodium, calcium, iron, magnesium, phosphorus,
potassium, zinc, copper, manganese, selenium, iodine,
vitamin_a, vitamin_b1, vitamin_b2, vitamin_b3, vitamin_b5,
vitamin_b6, vitamin_b9, vitamin_b12, vitamin_c, vitamin_d,
vitamin_e, vitamin_k, alcohol, caffeine, water
```

### Auto-Creation Logic

The system computes a confidence score (0.0 - 1.0):

| Data provided      | Score boost |
|--------------------|-------------|
| Has name           | +0.3        |
| Has barcode        | +0.2        |
| Has energy_kcal    | +0.2        |
| Energy 0-900 kcal  | +0.1        |
| Has 3+ nutrients   | +0.1        |
| Has source URL     | +0.1        |

**Confidence >= 0.8** → FoodItem auto-created immediately
**Confidence < 0.8** → Queued for admin review

### Deduplication

- Same barcode already requested → counter incremented, data enriched
- Same query+lang already requested → counter incremented, data enriched
- Frequently requested items (count >= 5) get a confidence bonus

### Response (201 Created — new request)

```json
{
  "id": "e5f6g7h8-...",
  "original_query": "Alnatura Bio Hafermilch",
  "lang": "de",
  "submitted_name": "Haferdrink Natur",
  "submitted_brand": "Alnatura",
  "submitted_barcode": "4104420205925",
  "status": "auto_created",
  "food_item": "a1b2c3d4-...",
  "ai_confidence": 0.9,
  "request_count": 1,
  "created_at": "2026-03-02T10:00:00Z",
  "updated_at": "2026-03-02T10:00:00Z"
}
```

### Response (200 OK — duplicate request, counter incremented)

```json
{
  "id": "e5f6g7h8-...",
  "status": "pending",
  "ai_confidence": 0.5,
  "request_count": 3,
  ...
}
```

### Status Values

| Status         | Description |
|----------------|-------------|
| `pending`      | Waiting for admin review or more data |
| `auto_created` | FoodItem auto-created (high confidence) |
| `approved`     | Admin manually approved and created |
| `rejected`     | Admin rejected |

---

## 4. Check Food Request Status

```
GET /api/foods/request/{id}
```

Returns same response as the POST endpoint.

---

## App Integration Flow

### Search Flow (replaces USDA API)

```
1. User types "Banana"
2. App calls: GET /api/foods/search?q=banana&lang=en&food_type=raw
3. App shows results with nutrients
4. User selects "Banana, raw" → App calls: GET /api/foods/{id}
```

### Missing Food Flow (Learning Loop)

```
1. User searches "Alnatura Hafermilch" → no good result
2. User scans barcode or enters data manually
3. App calls: POST /api/foods/request with all available data
4. If confidence >= 0.8: food is instantly available via search
5. If confidence < 0.8: admin reviews in dashboard, approves/rejects
6. Next user searching same thing finds the food
```

### Barcode Scan Flow

```
1. User scans barcode "4104420205925"
2. App searches: GET /api/foods/search?q=4104420205925&food_type=branded
3. If found: show product
4. If not found: POST /api/foods/request with barcode + any data from label
```
