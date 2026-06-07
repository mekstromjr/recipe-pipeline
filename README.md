# recipe-pipeline

Digitize old family recipes — handwritten cards and newspaper clippings — and upload them to
the self-hosted **Mealie** server, preserving the original scan as a permanent record.

> **Status:** scaffold / skeleton. Tracking issue:
> [meklab/home#36](https://gitlab.meklab.net/meklab/home/-/issues/36)
> (milestone *meklab: Recipe Pipeline*). The processing stages are stubbed (`NotImplementedError`)
> pending the OCR bake-off — see [Roadmap](#roadmap).

## What it does

```
inbox/*.pdf                         # one scanned PDF = one recipe (multi-page OK)
  → render pages to images          (pypdfium2, no system deps)
  → orient + deskew                 (fix sideways/upside-down + small tilt) BEFORE OCR
  → segment into text lines
  → OCR ×2                          (two DIFFERENT-architecture local engines)
  → reconcile                       (local vision LLM sees BOTH transcripts + the page image,
                                      resolves disagreements, marks [UNCLEAR] when it can't)
  → structure + relevance filter    (recipe fields; carefully drop unrelated clipping text;
                                      quaint off-recipe bits → extras.source_curiosities)
  → enrich (additive only)          (local model + SearXNG: cuisine, meal, dish history,
                                      estimated times/servings — never alters card content)
  → assemble recipe JSON w/ provenance (from_card vs enriched vs estimated)
  → REVIEW GATE                     (you eyeball output/<slug>/ before anything is uploaded)
  → upload to Mealie                (recipe + page-1 image + the ORIGINAL PDF as an asset)
```

### Why this design (not "just ask GPT to read it")

Pure vision-LLM transcription is **rejected for this use case**: on ambiguous handwriting LLMs
silently emit a plausible-but-wrong word (in one study GPT-4o was wrong 64% of the time on
genuinely unclear characters) rather than flagging doubt — a silent `1 tsp → 1 tbsp` or
`baking soda → baking powder` would survive review *because it looks right*.

Instead, two **different-architecture** OCR engines transcribe independently, and a
**vision** LLM reconciles them **against the original image** (not just the two text strings).
A peer-reviewed 2025 study found this hybrid reaches **CER 1.8% / WER 3.5%** — a 68–72%
improvement over either an LLM or a dedicated OCR engine alone. Where the engines disagree and
the image can't resolve it, the text is marked `[UNCLEAR]` for human review.

Everything that *adds* information (dish history, cuisine, estimated cook time) is kept strictly
separate from what was actually on the card, via per-field **provenance** and Mealie's `extras`.

## Quickstart

```bash
cd recipe-pipeline
uv sync                       # light skeleton; add the OCR/ML stack later with: uv sync --extra ml
cp .env.example .env          # then edit
uv run recipe-pipeline --help
```

Drop one scanned PDF per recipe into `inbox/`, then (once implemented):

```bash
uv run recipe-pipeline extract        # PDF -> reviewed recipe JSON in output/<slug>/
# ...review output/<slug>/recipe.md...
uv run recipe-pipeline upload <slug>  # push the approved recipe to Mealie
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
| `src/recipe_pipeline/schema.py` | internal `Recipe` model + per-field provenance |
| `src/recipe_pipeline/prompts.py` | reconcile/structure system prompts incl. relevance rules |
| `src/recipe_pipeline/pdf.py` | render PDF pages → images (pypdfium2) |
| `src/recipe_pipeline/ingest.py` | discover `inbox/*.pdf` → jobs |
| `src/recipe_pipeline/preprocess.py` | orient + deskew (stub) |
| `src/recipe_pipeline/segment.py` | line/layout segmentation (stub) |
| `src/recipe_pipeline/htr/` | pluggable OCR backends: `trocr`, `kraken`, `applevision` (stubs) |
| `src/recipe_pipeline/reconcile.py` | image-grounded reconcile via Ollama (stub) |
| `src/recipe_pipeline/structure.py` | reconciled text → recipe fields + relevance filter (stub) |
| `src/recipe_pipeline/enrich.py` | SearXNG-backed metadata enrichment (stub) |
| `src/recipe_pipeline/mealie.py` | Mealie API client (stub; verified mapping in docstring) |
| `src/recipe_pipeline/bakeoff.py` | CER/WER OCR comparison harness (stub) |
| `src/recipe_pipeline/extract.py` | end-to-end orchestration (stub glue) |
| `src/recipe_pipeline/cli.py` | `extract` / `upload` / `bakeoff` commands |

## Roadmap

1. **OCR bake-off** — implement the HTR backends + segmentation + `bakeoff.py`, then run on a
   handful of real cursive cards (with hand-typed ground truth in `samples/`) to pick the OCR
   pair empirically. Escalation for a stubborn hand: fine-tune Kraken on ~20–30 samples.
2. **Reconcile** — verify the local vision model accepts images; wire `reconcile.py`.
3. **Enrich + structure** — SearXNG client, structuring + relevance filter, orchestrate `extract`.
4. **Upload** — `mealie.py` against the live API (port-forward + token).
