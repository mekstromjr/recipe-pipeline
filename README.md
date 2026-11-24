# recipe-pipeline

Turns scanned handwritten recipe cards and newspaper clippings into structured recipes in a
self-hosted [Mealie](https://mealie.io) instance, using local OCR and a local language
model, with a human review step before anything is published and the original scan kept
alongside every recipe.

![The Mealie recipe library: a grid of recipe cards with photos, titles and category tags such as Brunch, Vegetables and No Added Sugar](docs/screenshots/recipes-mealie.webp)

**Status:** in use, running in a Kubernetes cluster as two services (a FastAPI server and
an OCR worker). Recipes reach Mealie when a person approves them in the hosted review UI;
nothing is uploaded automatically. Developed on a self-hosted GitLab; this GitHub repo is a
read-only mirror.

## How it works

```mermaid
flowchart LR
    U[Upload PDFs or ZIP<br/>web UI] --> S[(Postgres jobs<br/>+ S3 scans)]
    S -->|claim with lease| W[worker]
    W --> O[glm-ocr<br/>CPU Ollama]
    O -->|failed card| C[cloud OCR<br/>OpenRouter]
    O & C --> T[qwen3.5:4b<br/>structure + enrich]
    T --> G[review gate]
    G -->|result| S
    S --> R[human review UI<br/>edit, approve, reject]
    R -->|approve| M[Mealie<br/>recipe + image + original PDF]
```

1. Scans are uploaded through the server's web UI (one PDF per recipe, multi-page is fine,
   or a ZIP of them). Each file becomes a job in Postgres, and the PDF is stored in
   S3-compatible storage. Re-uploading identical content is reported as a duplicate.
2. The worker claims a job over an outbound HTTP API, renders the pages, and transcribes
   them with `glm-ocr` on a CPU-only Ollama host. A card that fails locally gets one retry
   with a cloud vision model via OpenRouter; that is the only path by which a scan is sent
   to a third-party service.
3. `qwen3.5:4b` structures the transcription into recipe fields (title, ingredients,
   steps, times, servings) and drops unrelated clipping text. A separate enrichment pass
   adds cuisine, course and a short dish history using a SearXNG search on the dish name.
4. A quality gate checks for faint or empty scans, `[UNCLEAR]` spans, missing structure
   and model repetition, and attaches its reasons to the recipe. Failed extractions are
   filed as failed; everything else lands in the review queue.
5. A person reviews each recipe next to its scan, edits if needed, and approves or
   rejects it. Approval creates the recipe in Mealie with the upright page image and the
   original PDF attached as an asset.

## Engineering decisions

- **OCR transcribes; the language model never overrides it.** A vision LLM asked to read
  ambiguous handwriting tends to produce a plausible word rather than flag doubt, and a
  silent `1 tsp` to `1 tbsp` would survive review because it looks right. An early local
  test confirmed this: a vision model invented an entire recipe from a rotated scan. So
  the text model only structures and annotates the OCR output, and the verbatim
  transcription is kept on every recipe (`original_transcription`).
- **Card content and added content are kept apart.** Every field carries provenance
  (from the card, enriched, or estimated). Estimated times and servings are listed as
  such, and dish history, OCR confidence and off-recipe curiosities go into Mealie's
  `extras` rather than the canonical fields. Only the dish name is sent to web search,
  never the card's quantities.
- **Human approval is the gate, not a confidence score.** On a clean card `glm-ocr`
  matched the previous OCR engine line for line, but it makes plausible substitutions on
  hard cards (one read "Worchestershire
  sauce" as "shredded cheese"), and no automated check catches those. So every
  non-failed recipe goes to the review queue.
- **Guarding the OCR model's failure modes.** Uncapped, `glm-ocr` generated without end
  on a faint page; with a 2,048-token cap it terminates in about three minutes. The
  review gate also detects a transcript that repeats itself.
- **No rotation step.** `glm-ocr` reads 180-degree pages in correct line order, and
  pre-rotating sideways pages made results worse, so the pipeline OCRs the raw scan and
  only deskews the display image.
- **A job state machine shared by both sides.** The worker and server import one
  `shared/` package for the recipe model and the job lifecycle (`queued`, `processing`,
  `needs_review`, `approved`, `uploaded`, plus `failed` and `rejected`), so an illegal
  transition is a programming error rather than silent corruption. A claimed job holds a
  lease; if the worker dies mid-job, the lease expires and the job returns to the queue.
- **The worker holds one credential and makes only outbound calls.** It never touches the
  database, S3 or Mealie directly; it claims jobs, downloads the source, and posts results
  (idempotently, so retries are safe).
- **Mealie's ingredient parser, with a confidence floor.** Parsed ingredients make
  amounts and units structured in Mealie, but the parser rewrites wording on OCR'd
  handwriting, so a parse below 0.90 confidence falls back to the verbatim line. Details
  in [docs/mealie-api.md](docs/mealie-api.md).

History: [docs/redesign-v2-plan.md](docs/redesign-v2-plan.md) is the plan for the move
from an on-device Apple Vision OCR pipeline on a Mac to the current in-cluster design. It
is kept as a historical record and differs from what shipped in places.

## Layout

| Path | Role |
|---|---|
| `shared/` | `Recipe` model with per-field provenance, review report, job state machine, Mealie payload builder |
| `server/` | FastAPI control plane: uploads, worker API, review API and UI, Mealie injection, Alembic migrations |
| `worker/` | OCR and structuring pipeline, the `recipe-pipeline` CLI, and the pull loop (`recipe-pipeline work`) |
| `scripts/promptcheck.py` | Ad-hoc check of the structuring prompt against a live model |
| `Dockerfile`, `Dockerfile.worker` | Server and worker images; each installs only its own workspace member |

## Running it locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and an
[Ollama](https://ollama.com) server with `glm-ocr` and `qwen3.5:4b` pulled. Enrichment
queries a SearXNG instance; if it is unreachable, recipes come back unenriched.

```bash
git clone https://github.com/mekstromjr/recipe-pipeline.git
cd recipe-pipeline
uv sync                       # installs shared, worker and server into one venv
cp .env.example .env          # set RECIPE_OLLAMA_HOST and friends
uv run recipe-pipeline --help
```

The worker CLI can process a folder of scans without the server:

```bash
uv run recipe-pipeline extract --limit 5   # inbox/*.pdf -> output/<slug>/ (recipe JSON, pages, review)
uv run recipe-pipeline review              # local review UI on http://127.0.0.1:8765
uv run recipe-pipeline upload              # push ok and approved recipes to Mealie
```

The deployed path is the server plus `recipe-pipeline work`, which needs
`RECIPE_SERVER_URL` and `RECIPE_WORKER_API_KEY`. The server runs with
`uvicorn --factory recipe_pipeline_server.app:create_app`, applies its Alembic migrations
on startup, and expects Postgres, S3-compatible storage and forward-auth in front of the
human-facing routes (it reads the `X-Authentik-Username` header).

## Testing and CI

```bash
uv run ruff check .
uv run pytest -q
```

Without a database, 85 tests pass and 25 API tests skip. To run those, point
`RECIPE_TEST_DB_URL` at a scratch Postgres (the schema is migrated on startup and tables
are truncated per test):

```bash
docker run -d --rm --name recipe-test-db -p 127.0.0.1:5433:5432 \
  -e POSTGRES_USER=recipes -e POSTGRES_PASSWORD=ci-only -e POSTGRES_DB=recipes_test \
  postgres:16.6-alpine
RECIPE_TEST_DB_URL=postgresql+psycopg://recipes:ci-only@127.0.0.1:5433/recipes_test \
  uv run pytest -q
```

Ollama, Mealie and S3 are faked in tests. The GitLab pipeline runs secret detection, ruff,
the worker and shared tests, and the server tests against a Postgres service container,
then builds the server and worker images with Kaniko from the same commit on `main` and
on semver tags.

## Configuration

All settings use the `RECIPE_` prefix and can also be read from `.env`. See
[.env.example](.env.example),
[worker/src/recipe_pipeline/config.py](worker/src/recipe_pipeline/config.py) and
[server/src/recipe_pipeline_server/config.py](server/src/recipe_pipeline_server/config.py).

| Variable | Used by | Purpose |
|---|---|---|
| `RECIPE_OLLAMA_HOST` | worker | Ollama server running the OCR and text models |
| `RECIPE_OCR_MODEL` / `RECIPE_TEXT_MODEL` | worker | `glm-ocr:latest` and `qwen3.5:4b` by default |
| `RECIPE_NUM_THREAD` | worker | Must equal the Ollama host's CPU allocation (llama.cpp ignores the cgroup limit) |
| `RECIPE_OCR_NUM_PREDICT` | worker | OCR token cap, the guard against runaway generation (2048) |
| `RECIPE_OPENROUTER_API_KEY` | worker | Enables cloud OCR escalation for cards that fail locally; unset disables it |
| `RECIPE_SEARXNG_URL` | worker | SearXNG for enrichment |
| `RECIPE_SERVER_URL` | worker | Server address for the `work` pull loop |
| `RECIPE_WORKER_API_KEY` | both | The worker's one credential; the server checks it on the worker API |
| `RECIPE_DB_URL` | server | Postgres DSN (`postgresql+psycopg://...`) |
| `RECIPE_S3_ENDPOINT`, `RECIPE_S3_BUCKET`, `RECIPE_S3_ACCESS_KEY`, `RECIPE_S3_SECRET_KEY` | server | Storage for source scans and page images |
| `RECIPE_MEALIE_URL` / `RECIPE_MEALIE_API_TOKEN` | both | Mealie target for approved recipes |
| `RECIPE_GROUP_TAGS` | both | Tags added to every uploaded recipe |

## License

[MIT](LICENSE)
