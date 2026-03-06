# App Fixes — Offene Punkte aus Testing

**Stand:** 06.03.2026

---

## Fix 1: `isPreparedFood` Flag im Parser

### Problem
Die App nutzt `englishName.contains("raw")` um zu entscheiden ob `food_type=raw` gesucht wird.
Das schlägt fehl bei Grundzutaten ohne "raw" im Namen (z.B. "salad", "oats", "butter").
→ Suche ohne Filter → Branded-Müll dominiert (5 Mio OFF-Produkte).

### Lösung
Neues Feld `isPreparedFood` im Parser-Output. GPT entscheidet — kein Hardcoding.

```json
{
  "name": "Döner",
  "englishName": "doner kebab",
  "isBranded": false,
  "isPreparedFood": true,
  "estimatedGrams": 380
}
```

### Parser-Prompt Ergänzung

```
Neues Feld "isPreparedFood":
- true bei ZUBEREITETEN/ZUSAMMENGESETZTEN Gerichten:
  Döner, Pizza, Burger, Schnitzel, Lasagne, Sushi, Currywurst,
  Wrap, Sandwich, Pommes/Fries, Croissant, Kuchen, Tiramisu
- false bei EINZELNEN GRUNDZUTATEN:
  Banane, Ei, Milch, Haferflocken, Reis, Salat, Butter,
  Hähnchenbrust, Lachs, Kartoffel, Apfel, Brot, Toast
```

### Resolver-Logik

```swift
// food_type Bestimmung — KEIN Hardcoding, GPT entscheidet
let foodType: String
if ingredient.isBranded {
    foodType = "branded"
} else if ingredient.isPreparedFood {
    foodType = ""       // kein Filter → alle Typen
} else {
    foodType = "raw"    // Grundzutaten → nur raw (filtert OFF-Branded weg)
}
```

### Beispiele

| Eingabe | `isPreparedFood` | `food_type` | Warum |
|---------|-------------------|-------------|-------|
| Banane | `false` | `raw` | Grundzutat → USDA "Banana, raw" #1 |
| Salat | `false` | `raw` | Grundzutat → USDA "Salad" statt Branded-Salatkit |
| Haferflocken | `false` | `raw` | Grundzutat → "Oats, raw" #1 |
| Butter | `false` | `raw` | Grundzutat |
| Döner | `true` | (leer) | Fertiggericht → alle Typen durchsuchen |
| Pizza | `true` | (leer) | Fertiggericht |
| Pommes | `true` | (leer) | Fertiggericht (frittiert/zubereitet) |
| Big Mac | `true` | (leer) | Fertiggericht |
| ESN Protein | — | `branded` | `isBranded=true` hat Vorrang |

### Bisherige (fehlerhafte) Logik entfernen

```swift
// ❌ ALT — entfernen:
let lower = englishName.lowercased()
foodType = (lower.contains("raw") || lower.contains("whole")) ? "raw" : ""

// ✅ NEU — ersetzen mit:
if ingredient.isBranded {
    foodType = "branded"
} else if ingredient.isPreparedFood {
    foodType = ""
} else {
    foodType = "raw"
}
```

---

## Fix 2: GPT-Auswahl — Name VOR Kalorien

### Problem
GPT wählt "Cereal, frosted oats with marshmallows" (372 kcal) statt "Oats, raw" (379 kcal),
weil 372 näher an der Schätzung von 370 liegt. Aber Frosted Oats ≠ Haferflocken!

### Lösung
GPT-Prompt im `selectBestNutriCandidate` ändern.
Name-Match ist der **primäre Filter**, Kalorien nur **Tiebreaker**.

### Aktueller Prompt (falsch)

```
Schritt 3: Wähle den Kandidaten dessen NAME passt UND dessen kcal am nächsten
an deiner Schätzung liegt.
```

### Neuer Prompt

```
User sucht: "{originalName}" (englisch: "{englishQuery}")

Kandidaten:
{candidateList}

REIHENFOLGE (wichtig!):
1. NAME zuerst: Welche Kandidaten beschreiben DASSELBE Lebensmittel?
   Filtere alle raus, die etwas ANDERES sind.
   Beispiel: "Oats, raw" passt zu Haferflocken,
   aber "Cereal, frosted oats with marshmallows" ist ein ANDERES Produkt → rausfiltern.

2. KALORIEN als Tiebreaker: Wenn mehrere Kandidaten namentlich passen,
   schätze die erwarteten kcal/100g und wähle den plausibelsten.

Format (2 Zeilen):
Schätzung: [X] kcal/100g
Bester: [Nummer] oder "none"
```

### Beispiel: Haferflocken

```
Kandidaten:
[0] "Oats, raw" — 379 kcal/100g (raw)
[1] "Oats, whole grain, steel cut" — 0 kcal/100g (raw)
[2] "Cereal, frosted oats with marshmallows" — 372 kcal/100g (raw)
[3] "Oats (USDA FDP)" — 389 kcal/100g (raw)

Schritt 1 (Name):
  #0 "Oats, raw" → Haferflocken ✅
  #1 0 kcal → skip
  #2 "Cereal, frosted oats with marshmallows" → ANDERES Produkt ❌
  #3 "Oats" → Haferflocken ✅

Schritt 2 (Tiebreaker): #0 (379) vs #3 (389) → beide plausibel, #0 näher
→ Antwort: "0"
```
