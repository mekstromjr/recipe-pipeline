# Mealie API mapping

How an approved recipe becomes a Mealie recipe. Verified against Mealie v3.19.1. The
payload is built in one place,
[`shared/src/recipe_pipeline_shared/mealie_payload.py`](../shared/src/recipe_pipeline_shared/mealie_payload.py),
and used by both the server's injection client
([`server/src/recipe_pipeline_server/mealie.py`](../server/src/recipe_pipeline_server/mealie.py))
and the worker's local `upload` command.

## Call sequence

1. Get-or-create every tag and category (`GET`/`POST /api/organizers/{tags|categories}`).
   Organizer references need `id`, `name` and `slug`.
2. `POST /api/recipes` with `{"name": ...}` returns the slug.
3. Ingredients go through Mealie's own parser (`POST /api/parser/ingredients`, `nlp`).
   A parsed food is resolved to a real food row first, because `food: {"id": null}` makes
   Mealie return HTTP 500.
4. `PATCH /api/recipes/{slug}` with the full body. It must not re-send `name` (a
   duplicate name returns 400).
5. `PUT /api/recipes/{slug}/image` with the upright page-1 image.
6. `POST /api/recipes/{slug}/assets` with the original scanned PDF, kept as a permanent
   record of the source.

## Field mapping

| Recipe field | Mealie field | Notes |
|---|---|---|
| name | `name` | set at create only |
| description / dish history | `description` | dish history is used when the card has no description |
| ingredients | `recipeIngredient[]` | parsed objects; `originalText` always holds the card's verbatim line |
| instructions | `recipeInstructions[]` | full step shape (`text`, `title`, `summary`, `ingredientReferences`); bare `{text}` returns 500 |
| servings | `recipeYield` | estimates are listed in `extras.ai_estimated_fields` |
| times | `prepTime` / `cookTime` / `totalTime` / `performTime` | same |
| categories | `recipeCategory[]` | meal or course |
| tags | `tags[]` | cuisine, the models that processed the recipe (e.g. `glm-ocr`, `qwen3.5:4b`), and `RECIPE_GROUP_TAGS` |
| source, import note | `notes[]` | `{title, text}` |
| provenance, OCR confidence, `[UNCLEAR]` spans, dish history, off-recipe curiosities, verbatim transcription | `extras` | flat string values only; lists return 500 |

## Ingredient parsing threshold

Mealie's parser normalises wording, which is risky on OCR'd handwriting: "20 large
marshmellows" parsed to the food "marshmallow" (spelling silently corrected), and "1 8 oz
pkg. Pecan nuts" parsed to the food "pkg." with "Pecan nuts" pushed into the note. A
low-scoring parse is worse than no parse, so below
`RECIPE_INGREDIENT_PARSE_MIN_CONFIDENCE` (default 0.90, a judgment call rather than a
measured optimum) the row falls back to a plain note holding the whole string.
`settings.disableAmount` is set only when nothing was parsed, so structured amounts render
whenever they exist.

## Access

The Mealie Service is reached directly inside the cluster (`RECIPE_MEALIE_URL`) with a
long-lived API token (`RECIPE_MEALIE_API_TOKEN`, minted in the Mealie UI under the user
menu). For local use, port-forward the Service and point the URL at the forward.
