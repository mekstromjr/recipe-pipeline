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
uv run recipe-pipeline escalate           # re-OCR the FAILED tail with cloud vision (hard cursive)
uv run recipe-pipeline review             # local UI: work the needs_review queue (scan + edit + approve)
# ...then upload (ok recipes auto-upload; needs_review upload once approved in the review UI)
```

### Nightly batches (avoid one 12-hour run)

A whole-corpus run is long, so process it ~50/night via a `launchd` agent (macOS). The pipeline
is resumable (already-done recipes are skipped), so each run just advances the next batch.

```bash
# Point input at your scans folder (process in place), then install the agent:
#   RECIPE_INPUT_DIR=/path/to/scansDocuments   (in .env)
cp scripts/net.meklab.recipe-pipeline.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/net.meklab.recipe-pipeline.plist   # runs 01:00 nightly
# So it runs even if the Mac is asleep (needs sudo + plugged in):
sudo pmset repeat wake MTWRFSU 00:55:00
```

`scripts/nightly-batch.sh` runs `extract --limit 50` then `escalate --limit 50` under
`caffeinate` (no idle-sleep mid-batch); progress logs to `output/nightly.log`. Requires Ollama.
Disable with `launchctl unload …` and `sudo pmset repeat cancel`.

**Upload runs separately, during the day.** The LAN route to the k3s API is unreliable right
after the overnight sleep/wake (the Mac is dual-homed wired+wireless on the subnet and Tailscale
also routes it), so uploading at 1am fails. Instead a second agent
(`net.meklab.recipe-pipeline-upload`, `scripts/upload-batch.sh`) runs at 9/12/15/18/21h: it
checks the cluster is reachable, and if so uploads the **high-confidence (`ok`)** recipes
(`needs_review` stays manual). It's a clean no-op when unreachable, and idempotent (per-recipe
`.uploaded` markers), so the backlog self-heals on whichever slot the route is healthy.

```bash
cp scripts/net.meklab.recipe-pipeline-upload.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/net.meklab.recipe-pipeline-upload.plist
```

**Cloud escalation (hard cursive).** Apple Vision can't reliably read difficult handwriting (and
a local VLM *fabricates* on it — dangerous). So failed cards escalate to a strong cloud vision
model (`escalate`), which reads cursive far better. This is the only stage that sends a scan
off-device, and only for the failed tail; results still pass the review gate. Set
`RECIPE_OPENROUTER_API_KEY` (copy from `.env`). On a sample, escalating the failed tail
roughly doubled the usable rate.

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

Restructured into a **uv workspace** (recipe-pipeline #2): the worker (MacBook OCR) and the
server (k8s control plane) share one `Recipe` model via `shared/`, so they can never drift.
`uv sync` builds all three members into one dev venv; the server image (#12) builds only the
server member, so it never pulls the worker's Apple Vision deps. Full design: repo wiki
`architecture/recipe-service` (milestone *meklab: Recipe Service*).

| Path | Role |
|---|---|
| `shared/` (`recipe-pipeline-shared`) | the Recipe model + slug — imported by worker AND server |
| `shared/src/recipe_pipeline_shared/schema.py` | `Recipe` model + per-field provenance + review report |
| `shared/src/recipe_pipeline_shared/slug.py` | `slugify` (job slugs + Mealie organizer slugs) |
| `worker/` (`recipe-pipeline-worker`) | the MacBook OCR pipeline + the `recipe-pipeline` CLI |
| `worker/src/recipe_pipeline/config.py` | settings (pydantic-settings, `RECIPE_` env prefix) |
| `worker/src/recipe_pipeline/prompts.py` | structure/review system prompts incl. relevance rules |
| `worker/src/recipe_pipeline/pdf.py` | render PDF pages → images (pypdfium2) |
| `worker/src/recipe_pipeline/ingest.py` | discover `inbox/*.pdf` → jobs |
| `worker/src/recipe_pipeline/preprocess.py` | orientation detection + upright image (for review/display) |
| `worker/src/recipe_pipeline/htr/` | OCR backends: `applevision` (local) + `openrouter` (cloud escalation) |
| `worker/src/recipe_pipeline/structure.py` | OCR text → recipe fields + relevance filter |
| `worker/src/recipe_pipeline/enrich.py` | SearXNG-backed additive metadata enrichment |
| `worker/src/recipe_pipeline/review.py` | quality gate (faint/empty/[UNCLEAR]/structure/LLM review) |
| `worker/src/recipe_pipeline/render.py` | recipe → markdown for human review |
| `worker/src/recipe_pipeline/bakeoff.py` | CER/WER OCR comparison harness |
| `worker/src/recipe_pipeline/extract.py` | end-to-end batch orchestration (resumable; manifest) |
| `worker/src/recipe_pipeline/review_server.py` | current local review UI (superseded by the hosted server in #9) |
| `worker/src/recipe_pipeline/mealie.py` | Mealie API client (migrates server-side in #7) |
| `worker/src/recipe_pipeline/cli.py` | `extract` / `escalate` / `review` / `upload` / `bakeoff` commands |
| `server/` (`recipe-pipeline-server`) | FastAPI control plane (scaffold; built in #4–#8) — NO Apple Vision deps |
