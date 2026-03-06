# Nutrition Database — Anweisungen App-Team

**Stand:** 06.03.2026
**Base URL:** `http://164.92.207.35/api`

---

## Was fertig ist

Die Datenbank ist live und enthält **6.2 Millionen Lebensmittel** aus USDA + OpenFoodFacts.
Suche läuft über Meilisearch mit **<100ms Antwortzeit**, Typo-Toleranz inklusive.

Ihr müsst **KEIN eigenes Schema bauen** — die DB existiert bereits.
Unten steht genau, wie ihr die API ansprecht.

---

## 1. Resolver-Kette (NEU)

```
User-Input
    │
    ▼
GPT-4o-mini (Parser) → ParsedIngredient[]
    │
    ▼ Pro Zutat:

Stufe 1: 📚 Lokale History (wie bisher, lokal)
    │
    ▼ kein Treffer

Stufe 2: 🗄️ EIGENE DATENBANK
    │  GET /api/foods/search?q={name}&lang=en&food_type=raw&limit=5
    │  (bei branded: food_type=branded)
    │
    │  → 5 Kandidaten an GPT → GPT wählt besten oder "none"
    │  → Kosten: 1 API Call + ~0.1ct GPT-Auswahl
    │
    ▼ kein Treffer ("none")

Stufe 3: 🥬 USDA API (wie bisher)
    │  → Ergebnis per POST /api/foods/request in DB speichern
    │
    ▼ kein Treffer

Stufe 4: 🌐 GPT Web-Search (wie bisher)
    │  → Ergebnis per POST /api/foods/request in DB speichern
    │
    ▼ kein Treffer

Stufe 5: 🧠 GPT Schätzung (NICHT in DB speichern)
```

---

## 2. Search Endpoint

```
GET /api/foods/search?q=banana&lang=en&food_type=raw&limit=5
```

### Query Parameters

| Parameter   | Required | Default | Beschreibung |
|-------------|----------|---------|--------------|
| `q`         | Ja       | —       | Suchbegriff (min 2 Zeichen) |
| `lang`      | Nein     | alle    | Sprache: `en`, `de`, `fr`, ... |
| `food_type` | Nein     | alle    | `raw`, `branded`, `supplement` (kommasepariert) |
| `source`    | Nein     | alle    | `USDA`, `OFF`, `USER_REQ` (kommasepariert) |
| `limit`     | Nein     | 25      | Ergebnisse pro Seite (max 100) |
| `offset`    | Nein     | 0       | Pagination Offset |

### Response

```json
{
  "count": 74,
  "next": "...&offset=5",
  "previous": null,
  "results": [
    {
      "id": "89c1f472-08af-4207-940b-629baff2cbc9",
      "canonical_key": "usda:2709224",
      "food_type": "raw",
      "name": "Banana, raw",
      "brand": null,
      "lang": "en",
      "nutrients": {
        "energy_kcal": 97.0,
        "proteins": 0.74,
        "fat": 0.28,
        "carbohydrates": 22.71,
        "sugars": 15.8,
        "fiber": 1.7,
        "saturated_fat": 0.112,
        "monounsaturated_fat": 0.032,
        "polyunsaturated_fat": 0.073,
        "cholesterol": 0.0,
        "sodium": 0.0,
        "potassium": 326.0,
        "calcium": 5.0,
        "iron": 0.0,
        "magnesium": 28.0,
        "phosphorus": 22.0,
        "zinc": 0.16,
        "copper": 0.101,
        "selenium": 0.0,
        "vitamin_a": 1.0,
        "vitamin_b1": 0.054,
        "vitamin_b2": 0.0,
        "vitamin_b3": 0.653,
        "vitamin_b6": 0.212,
        "vitamin_b9": 15.0,
        "vitamin_b12": 0.0,
        "vitamin_c": 12.0,
        "vitamin_d": 0.0,
        "vitamin_e": 0.1,
        "vitamin_k": 0.1,
        "alcohol": 0.0,
        "caffeine": 0.0,
        "water": 75.6
      },
      "score": 0.9876
    }
  ]
}
```

### Wie die App das nutzt

```
1. App sucht: GET /api/foods/search?q=oats+raw&lang=en&food_type=raw&limit=5
2. Bekommt 5 Kandidaten zurück
3. Schickt die 5 an GPT-4o-mini: "Welcher passt am besten zu 'Haferflocken'?"
4. GPT antwortet mit ID oder "none"
5. Wenn ID → Nährwerte aus nutrients übernehmen
6. Wenn "none" → weiter zu USDA/Web-Search
```

---

## 3. Feld-Mapping (WICHTIG)

Die API gibt Nährwerte **pro 100g** als verschachteltes `nutrients`-Objekt zurück.
Ihr müsst die Felder auf euer App-Model mappen:

| Euer Swift-Feld    | Unser API-Feld              |
|---------------------|-----------------------------|
| `calories`          | `nutrients.energy_kcal`     |
| `protein`           | `nutrients.proteins`        |
| `carbs`             | `nutrients.carbohydrates`   |
| `fat`               | `nutrients.fat`             |
| `fiber`             | `nutrients.fiber`           |
| `sugar`             | `nutrients.sugars`          |
| `saturatedFat`      | `nutrients.saturated_fat`   |
| `sodium`            | `nutrients.sodium`          |
| `potassium`         | `nutrients.potassium`       |
| `calcium`           | `nutrients.calcium`         |
| `iron`              | `nutrients.iron`            |
| `vitaminA`          | `nutrients.vitamin_a`       |
| `vitaminC`          | `nutrients.vitamin_c`       |
| `vitaminD`          | `nutrients.vitamin_d`       |
| ...                 | ...                         |

**Alle Werte sind pro 100g.** Die App rechnet dann: `wert * (grammGegessen / 100)`.

### Alle verfügbaren Nutrient Keys

```
energy_kcal, energy_kj, proteins, fat, carbohydrates, sugars, fiber,
saturated_fat, monounsaturated_fat, polyunsaturated_fat, trans_fat,
cholesterol, salt, sodium, calcium, iron, magnesium, phosphorus,
potassium, zinc, copper, manganese, selenium, iodine,
vitamin_a, vitamin_b1, vitamin_b2, vitamin_b3, vitamin_b5,
vitamin_b6, vitamin_b9, vitamin_b12, vitamin_c, vitamin_d,
vitamin_e, vitamin_k, alcohol, caffeine, water
```

---

## 4. Neues Lebensmittel speichern (Learning Loop)

Wenn die App ein Lebensmittel über USDA oder Web-Search findet, **speichert es das in unserer DB**
damit es beim nächsten Mal sofort da ist.

```
POST /api/foods/request
Content-Type: application/json
```

### Request Body

```json
{
  "original_query": "ESN Designer Whey Vanilla",
  "lang": "de",
  "submitted_name": "ESN Designer Whey Protein Vanilla",
  "submitted_brand": "ESN",
  "submitted_barcode": "4260432554123",
  "submitted_nutrients": {
    "energy_kcal": 374,
    "proteins": 78.0,
    "carbohydrates": 5.2,
    "fat": 4.8,
    "sugars": 3.5,
    "fiber": 0.0,
    "saturated_fat": 2.1,
    "sodium": 0.3
  },
  "submitted_source_url": "https://www.esn.com/whey-vanilla"
}
```

### Felder

| Feld                  | Pflicht | Beschreibung |
|-----------------------|---------|--------------|
| `original_query`      | Ja      | Ursprüngliche Suche des Users |
| `lang`                | Nein    | Sprache (default: `de`) |
| `submitted_name`      | Nein    | Korrekter Produktname |
| `submitted_brand`     | Nein    | Markenname |
| `submitted_barcode`   | Nein    | EAN/UPC Barcode |
| `submitted_nutrients` | Nein    | Nährwerte pro 100g (gleiche Keys wie oben) |
| `submitted_source_url`| Nein    | URL der Quelle |

### Was passiert

- **Genug Daten (Name + Barcode + Kalorien + 3 Nährstoffe):** Lebensmittel wird sofort erstellt und ist direkt über Search findbar
- **Wenig Daten:** Wird für Admin-Review eingereiht
- **Duplikat (gleicher Barcode oder Query):** Zähler wird erhöht, Daten ergänzt

### Response

```json
{
  "id": "e5f6g7h8-...",
  "status": "auto_created",
  "food_item": "a1b2c3d4-...",
  "ai_confidence": 0.9,
  "request_count": 1
}
```

Status-Werte: `pending`, `auto_created`, `approved`, `rejected`

---

## 5. Suchstrategie pro Zutat-Typ

### Generische Lebensmittel (isBranded=false)

```
GET /api/foods/search?q={englishName}&lang=en&food_type=raw&limit=5
```

Beispiel: User sagt "Haferflocken" → Parser gibt `englishName: "oats raw"` →
Suche: `?q=oats+raw&lang=en&food_type=raw&limit=5`

### Markenprodukte (isBranded=true)

```
GET /api/foods/search?q={brandQuery}+{name}&food_type=branded&limit=5
```

Beispiel: User sagt "ESN Protein" → Suche: `?q=ESN+Protein&food_type=branded&limit=5`

### Fertiggerichte (Döner, Pizza, etc.)

Für Fertiggerichte wie "Döner", "Big Mac", "Pizza Margherita":
**Die Grammzahl IMMER über GPT-Schätzung bestimmen, NICHT aus der DB.**

Die DB liefert Nährwerte pro 100g. Die App muss wissen wie schwer ein Döner ist.
→ GPT schätzen lassen: "Wie schwer ist ein durchschnittlicher Döner Kebab?" (~350g)
→ Dann: `nährwert * (350 / 100)`

---

## 6. Swift Integration

### SearchResult Model

```swift
struct NutritionSearchResult: Codable {
    let id: String
    let canonicalKey: String
    let foodType: String
    let name: String
    let brand: String?
    let lang: String
    let nutrients: [String: Double]  // "energy_kcal": 97.0, etc.
    let score: Double

    // Convenience Accessors
    var calories100g: Double { nutrients["energy_kcal"] ?? 0 }
    var protein100g: Double { nutrients["proteins"] ?? 0 }
    var carbs100g: Double { nutrients["carbohydrates"] ?? 0 }
    var fat100g: Double { nutrients["fat"] ?? 0 }
    var fiber100g: Double { nutrients["fiber"] ?? 0 }
}

struct SearchResponse: Codable {
    let count: Int
    let next: String?
    let previous: String?
    let results: [NutritionSearchResult]
}
```

### Service

```swift
final class NutritionDatabaseService {
    static let shared = NutritionDatabaseService()

    private let baseURL = "http://164.92.207.35/api"

    /// Sucht in eigener DB, gibt Top 5 zurück
    func search(
        query: String,
        foodType: String = "raw",
        lang: String = "en",
        limit: Int = 5
    ) async throws -> [NutritionSearchResult] {
        let q = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query
        let url = URL(string: "\(baseURL)/foods/search?q=\(q)&lang=\(lang)&food_type=\(foodType)&limit=\(limit)")!

        let (data, _) = try await URLSession.shared.data(from: url)
        let response = try JSONDecoder().decode(SearchResponse.self, from: data)
        return response.results
    }

    /// Neuen Eintrag speichern (nach USDA/Web-Search Fund)
    func saveFood(
        originalQuery: String,
        name: String,
        brand: String? = nil,
        barcode: String? = nil,
        nutrients: [String: Double],
        sourceUrl: String? = nil,
        lang: String = "de"
    ) async throws {
        let url = URL(string: "\(baseURL)/foods/request")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: Any] = [
            "original_query": originalQuery,
            "lang": lang,
            "submitted_nutrients": nutrients
        ]
        if !name.isEmpty { body["submitted_name"] = name }
        if let brand = brand { body["submitted_brand"] = brand }
        if let barcode = barcode { body["submitted_barcode"] = barcode }
        if let url = sourceUrl { body["submitted_source_url"] = url }

        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let _ = try await URLSession.shared.data(for: request)
    }
}
```

### Geänderter Resolver

```swift
private func resolveIngredient(_ ingredient: ParsedIngredient) async throws -> MealItemNutrition {
    let grams = gramsFor(ingredient: ingredient)
    let name = ingredient.name
    let englishName = ingredient.englishName ?? name

    // 1. Lokale History (wie bisher)
    if let historyResult = await resolveFromHistory(...) { return historyResult }

    // 2. EIGENE DATENBANK
    let foodType = ingredient.isBranded ? "branded" : "raw"
    let query = ingredient.isBranded
        ? "\(ingredient.brandQuery ?? "") \(name)"
        : englishName

    let candidates = try? await NutritionDatabaseService.shared.search(
        query: query,
        foodType: foodType,
        limit: 5
    )

    if let candidates = candidates, !candidates.isEmpty {
        if let bestMatch = await selectBestMatch(candidates: candidates, originalQuery: name) {
            return MealItemNutrition(
                name: name,
                grams: grams,
                calories: Int(bestMatch.calories100g * grams / 100),
                protein: bestMatch.protein100g * grams / 100,
                carbs: bestMatch.carbs100g * grams / 100,
                fat: bestMatch.fat100g * grams / 100,
                source: .database
            )
        }
    }

    // 3. USDA (wie bisher) → danach in DB speichern
    if let usdaResult = try? await resolveFromUSDA(...) {
        try? await NutritionDatabaseService.shared.saveFood(
            originalQuery: name,
            name: usdaResult.name,
            nutrients: ["energy_kcal": usdaResult.calories, "proteins": usdaResult.protein, ...],
            lang: "en"
        )
        return usdaResult
    }

    // 4. Web-Search (wie bisher) → danach in DB speichern
    if let webResult = await searchNutritionWithGPT(...) {
        try? await NutritionDatabaseService.shared.saveFood(
            originalQuery: name,
            name: webResult.name,
            brand: webResult.brand,
            nutrients: [...],
            sourceUrl: webResult.sourceUrl
        )
        return webResult
    }

    // 5. GPT Schätzung (NICHT in DB speichern)
    if let estimated = await estimateWithGPTKnowledge(...) { return estimated }

    // 6. Fallback
    return MealItemNutrition(name: name, grams: grams, calories: 0, ...)
}
```

---

## 7. Was ihr NICHT bauen müsst

| Feature aus altem Spec | Warum nicht nötig |
|------------------------|-------------------|
| Eigenes DB-Schema (`nutrition_items` Tabelle) | Existiert bereits, anderes Schema |
| `usage_count` / Usage-Tracking | Nicht nötig |
| `confidence` / `verified` Felder | Wird Backend-seitig gehandelt |
| `aliases` Array | Meilisearch hat Typo-Toleranz eingebaut |
| `search_log` Tabelle | Nicht nötig für MVP |
| Full-Text-Search Indizes | Meilisearch macht das |
| `default_portion_g` | App nutzt GPT-Schätzung für Mengen |

---

## 8. Kostenvergleich

### Vorher (pro Mahlzeit, 3 Zutaten)

| Call | Kosten |
|------|--------|
| Parser (gpt-4o-mini) | ~0.3ct |
| Branded Web-Search (gpt-4o-search) | ~3-5ct pro Zutat |
| **Typisch** | **~5.5ct** |

### Mit eigener Datenbank

| Call | Kosten |
|------|--------|
| Parser (gpt-4o-mini) | ~0.3ct |
| DB Search | 0ct |
| GPT Auswahl (gpt-4o-mini) | ~0.1ct pro Zutat |
| Web-Search Fallback | ~3-5ct (nur bei DB-Miss) |
| **Typisch (nach 1 Monat)** | **~0.6ct (90% Reduktion)** |
