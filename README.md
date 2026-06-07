# recipe-pipeline

Digitize old family recipes — handwritten cards and newspaper clippings — and upload them to
the self-hosted **Mealie** server, preserving the original scan as a permanent record.

> **Status:** working end-to-end (extract → reviewed output); Mealie upload is manual/bulk.
> Tracking issue: [meklab/home#36](https://gitlab.meklab.net/meklab/home/-/issues/36)
> (milestone *meklab: Recipe Pipeline*).

## What it does

```
inbox/*.pdf                         # one scanned PDF = one recipe (multi-page OK)
  → render pages to images          (pypdfium2, no system deps)
  → OCR                             (Apple Vision, on-device — reads any orientation)
  → detect orientation              (90/270 by text volume; 0-vs-180 by a text-model A/B check);
                                      reverse line order for 180; make an upright copy for review/display
  → structure + relevance filter    (local LLM, JSON-schema: recipe fields; drop unrelated
                                      clipping text; quaint off-recipe bits → extras.source_curiosities)
  → enrich (additive only)          (local model + SearXNG: cuisine, meal, dish history,
                                      estimated times/servings — never alters card content)
  → assemble recipe JSON w/ provenance (from_card vs enriched vs estimated)
  → REVIEW GATE                     (faint/empty/[UNCLEAR]/missing/low-confidence → HELD;
                                      only clean recipes are eligible to upload)
  → (manual) bulk-import to Mealie  (recipe + upright page image + the ORIGINAL PDF as an asset)
```

### Why this design (not "just ask GPT to read it")

Pure vision-LLM transcription is **rejected as the source of truth**: on ambiguous handwriting
LLMs silently emit a plausible-but-wrong word (in one study GPT-4o was wrong 64% of the time on
genuinely unclear characters) rather than flagging doubt — a silent `1 tsp → 1 tbsp` would
survive review *because it looks right*. (We confirmed this locally: the vision model hallucinated
a whole fake recipe from a rotated scan.)

So **Apple Vision does the transcription** — it's accurate on this corpus (~3% CER on legible
cursive in our bake-off), on-device/private, free, and orientation-tolerant. A local LLM only
*structures* Apple Vision's text into recipe fields and *adds* metadata; it never overrides what
the OCR read. (An earlier two-engine + image-grounded-reconcile design was removed: the second
engine, TrOCR, was universally worse here and its disagreement with Apple Vision broke the quality
gate, while feeding the LLM raw images caused rotation hallucinations.)

Everything that *adds* information (dish history, cuisine, estimated cook time) is kept strictly
separate from what was actually on the card, via per-field **provenance** and Mealie's `extras`;
the verbatim OCR text is preserved on `original_transcription`.

## Quickstart

```bash
cd recipe-pipeline
uv sync --extra apple         # Apple Vision OCR backend (macOS); core deps are light
cp .env.example .env          # then edit
uv run recipe-pipeline --help
```

Drop scanned PDFs (one per recipe) into `inbox/`, then:

```bash
uv run recipe-pipeline extract            # all of inbox/ -> reviewed output/<slug>/
uv run recipe-pipeline extract --limit 20 # sample batch (process 20 new recipes)
uv run recipe-pipeline extract --force    # reprocess everything (default: skip already-done)
# ...review output/<slug>/recipe.md + output/_manifest.json...
# then bulk-import the OK recipes to Mealie (manual)
```

## Mealie API mapping (verified against the live v3.19.1 instance)

Create is **two calls**: `POST /api/recipes` with `{"name": "..."}` returns a slug, then
`PATCH /api/recipes/{slug}` with the full `Recipe-Input` body.

| Our field | Mealie `Recipe-Input` | Notes |
|---|---|---|
| title | `name` | |
| summary | `description` | |
| ingredients | `recipeIngredient[]` `[{note, quantity:0}]` | free-text mode + `settings.disableAmount:true` |
| steps | `recipeInstructions[]` `[{text}]` | `text` required |
| servings | `recipeServings` (num) + `recipeYield` (str) | estimates flagged in `extras` |
| times | `prepTime`/`cookTime`/`totalTime`/`performTime` (str) | estimates flagged |
| meal/course | `recipeCategory[]` `[{name, slug}]` | name + slug both required |
| cuisine/genre | `tags[]` `[{name, slug}]` | name + slug both required |
| source / notes | `notes[]` `[{title, text}]` | |
| provenance, dish history, AI-estimate flags, OCR confidence, `[UNCLEAR]` spans, off-recipe curiosities | `extras` (free-form object) | keeps non-card content OUT of canonical fields |
| display image | `PUT /api/recipes/{slug}/image` (multipart) | page-1 rendered JPEG |
| **original scanned PDF** | `POST /api/recipes/{slug}/assets` (multipart) | permanent record of the source |

**Auth:** the `mealie` Service is plain ClusterIP:9000 with no Authentik (that middleware is
only at the Traefik ingress), so `kubectl -n prod port-forward svc/mealie 9000:9000` + a Mealie
API token gives clean API access. SearXNG's JSON API needs the same port-forward trick.

## Layout

| Path | Role |
|---|---|
| `src/recipe_pipeline/config.py` | settings (pydantic-settings, `RECIPE_` env prefix) |
| `src/recipe_pipeline/schema.py` | internal `Recipe` model + per-field provenance + review report |
| `src/recipe_pipeline/prompts.py` | structure/review system prompts incl. relevance rules |
| `src/recipe_pipeline/pdf.py` | render PDF pages → images (pypdfium2) |
| `src/recipe_pipeline/ingest.py` | discover `inbox/*.pdf` → jobs |
| `src/recipe_pipeline/preprocess.py` | orientation detection + upright image (for review/display) |
| `src/recipe_pipeline/htr/` | OCR backend interface + `applevision` (Apple Vision, on-device) |
| `src/recipe_pipeline/structure.py` | OCR text → recipe fields + relevance filter |
| `src/recipe_pipeline/enrich.py` | SearXNG-backed additive metadata enrichment |
| `src/recipe_pipeline/review.py` | quality gate (faint/empty/[UNCLEAR]/structure/LLM review) |
| `src/recipe_pipeline/mealie.py` | Mealie API client (verified mapping; manual bulk import) |
| `src/recipe_pipeline/render.py` | recipe → markdown for human review |
| `src/recipe_pipeline/bakeoff.py` | CER/WER OCR comparison harness |
| `src/recipe_pipeline/extract.py` | end-to-end batch orchestration (resumable; manifest) |
| `src/recipe_pipeline/cli.py` | `extract` / `upload` / `bakeoff` commands |
