# recipe-pipeline v2 — plan (<pve-host> Ollama, Apple Vision removed)

Status: PROPOSED, awaiting approval. Supersedes the Mac-bound v1 flow.

## Goal

Free the Mac and simplify. All inference moves to the CPU Ollama on **<pve-host>**
(`http://ollama.meklab.net:11434`, <ct>). Apple Vision is removed entirely; **glm-ocr**
becomes the OCR engine and **qwen3.5:4b** does structuring plus light QA. Speed is
explicitly not a constraint — recipes arrive in large batches and may take weeks to process.

## Measured on <pve-host> before writing this plan

All against the existing ground-truth files in `samples/`.

| Finding | Evidence |
|---|---|
| glm-ocr is NOT a chat model | `ollama show --template glm-ocr` = bare `{{ .Prompt }}`. `/api/chat` emitted a 4m42s run of `-`. Must use `/api/generate` |
| It needs a non-empty instruction | `prompt: ""` returned `''`; `"Recognize the text in the image."` worked |
| Clean cursive: excellent | `01-jello` matched `samples/jello-cabbage-salad.txt` line for line; sole error was a surname (`Dae` vs `Doe`) |
| Harder card: real ingredient errors | `02-cheese-rotated` vs `samples/cheese-ball.txt` — `Worchestershire sauce` read as `shredded cheese`, `Pecan nuts` as `puffy beacon nuts`, `mold` as `milk`, `Blue Roka` as `Blue Ale` |
| **Orientation-invariant, like Apple Vision** | The 180-rotated jello page transcribed correctly AND in upright line order. Sideways read fine as-scanned; pre-rotating to +90/+270 made it *worse* |
| **Repetition looping is systemic** | 4 of 5 outputs repeated the entire transcript 2-3x verbatim |
| **Low-signal input runs away** | `05-faint` never returned: HTTP 000 after 550s |
| Both models are vision-capable | `ollama show`: glm-ocr 1.1B ctx 131072; qwen3.5:4b 4.7B ctx 262144 |
| <pve-host> is unreachable from the Mac | Mac is on the LAN (`en5`, 192.168.88.x), so the `DOCKER-USER` rule limiting 11434 to k3s nodes is the blocker — not routing |

### What this means

Two of these are load-bearing and were not anticipated:

**Orientation detection mostly disappears.** glm-ocr turns out to share Apple Vision's
orientation invariance, and unlike Vision it returns a 180 page in *correct* line order — so
even the line-reversal step goes. We OCR the raw scan, full stop. Rotation only matters for
the display thumbnail now.

**Two new failure modes need gates.** Repetition looping and runaway generation are both
things glm-ocr does on this corpus, and neither is caught by anything in `review.py` today.
A page of repeated text sails past `EMPTY_OCR_CHARS = 12` as a clean result.

### Honest quality note

On clean cursive glm-ocr matches Apple Vision. On the harder card it does not: `Worchestershire
sauce` -> `shredded cheese` is exactly the silent-plausible-substitution the v1 README warned
about, and it would pass an automated gate. This is survivable because hosted manual review is
now the real gate — but it raises the volume flowing through that queue, and it is the reason
the cheap deterministic checks should stay.

## The structural unlock

Apple Vision (`pyobjc`) is the worker's **only** macOS dependency. Removing it means the worker
can be containerized and run in-cluster, worker tests can finally run in CI (`.gitlab-ci.yml`
currently skips them: "worker is macOS-only"), and the `launchd` + `sudo pmset repeat wake`
batch machinery disappears.

## Plan

1. **Config + client.** Repoint `ollama_host` at <pve-host>. Replace `vision_model`/`enrich_model`
   with `ocr_model: glm-ocr` and `text_model: qwen3.5:4b`. Add `ollama_client.generate()`
   hitting `/api/generate`, with the not-a-chat-model and non-empty-prompt findings recorded as
   comments — both are invisible from config and cost hours to rediscover. Keep `num_ctx`
   explicit at **32768** (matches what was tested; do not silently inherit <pve-host>'s compose env).
   Add a **`num_predict` cap** and a client-side timeout — non-negotiable given the faint-page
   hang.

2. **New OCR backend.** Add `htr/glmocr.py` behind the existing `HTRBackend` interface,
   downscaling pages to ~1280px. Delete `htr/applevision.py`, the `apple` extra in
   `worker/pyproject.toml`, and the `applevision` defaults in `config.py:55` and `cli.py:61`.

3. **Gut `preprocess.py`.** `detect_rotation`, `_is_upside_down`, `_ORIENT_AB_PROMPT` and the
   char-count margin all delete — glm-ocr reads the raw scan in correct order at any
   orientation, and pre-rotating measurably hurts. Keep `make_upright` for the thumbnail only;
   with no angle detection left it reduces to fine-deskew. This removes an LLM round-trip and
   3 extra OCR passes per card.

4. **Add the two gates glm-ocr actually needs** to `review.py`:
   - *repetition*: detect a transcript whose content repeats (observed in 4 of 5 test pages).
     De-duplicate where it is a clean whole-transcript repeat, flag where it is not.
   - *degenerate output*: alphanumeric ratio / distinct-character count, to catch the
     `- - - -` mode that currently passes as hundreds of valid characters.

5. **Re-validate the existing thresholds.** `FAINT_DARK_FRACTION`, `FAINT_STD`,
   `EMPTY_OCR_CHARS` were tuned against Apple Vision output. Re-check against glm-ocr even if
   the answer is "unchanged". Keep the cheap deterministic signals; see open question 2 for the
   LLM confidence pass.

6. **Batch by stage, not by recipe.** <pve-host> runs `OLLAMA_MAX_LOADED_MODELS: "1"`, so
   alternating glm-ocr -> qwen3.5:4b per card costs a ~30s swap twice per recipe. OCR the whole
   batch, then structure the whole batch: 2 swaps per run instead of 2N. Raising that setting is
   not an option — the compose header records that doing so caused the 2026-08-19
   <k3s-server> outage.

7. **Verify structured output.** `structure.py` relies on Ollama JSON-schema `format`.
   qwen3.5:4b advertises no structured-output guarantee, and this assumption already broke once
   with the gemma4 MLX tags. Test one schema-constrained call before building on it; fall back
   to prompt-and-parse if it fails.

8. **Keep `bakeoff.py`** as the ground-truth harness. (Earlier draft deleted it — wrong: it is
   what runs the samples-vs-ground-truth acceptance check below, and with OpenRouter staying
   there is still more than one engine.)

9. **Docs.** Rewrite the README flow diagram and the "why not just ask GPT to read it" section.
   The hallucination argument was about a general vision LLM, not a dedicated OCR model — but
   the cheese-ball errors above mean the section should be *revised honestly*, not deleted.

## Open questions

1. **Connectivity and containerization are one decision, not two.** A containerized worker runs
   on a k3s node, and k3s nodes are exactly what `DOCKER-USER` already allows. So:
   - *Defer containerization* -> the Mac needs dev-time access to <pve-host>: SSH tunnel (no infra
     change, matches the existing SearXNG/Mealie port-forward idiom) or open the firewall to the
     Mac. Ollama has no authentication, so opening it is a deliberate security call.
   - *Containerize now* -> the question dissolves, at the cost of a bigger change to review.

   Recommend: **SSH tunnel now, containerize as a follow-up.** Keeps this change reviewable and
   proves the model swap end-to-end before moving where it runs.

2. **The LLM review pass** (`review.py` step 5, `vision_model`). Delete it, or keep it warn-only
   as a hint into the hosted queue? Given the cheese-ball error class, an LLM confidence score is
   unlikely to catch the substitutions that matter — leaning delete.

3. **OpenRouter cloud escalation** (`htr/openrouter.py`, the `escalate` command). Recommend
   keeping: with a 1.1B OCR model the hard-cursive tail gets *bigger*, so escalation is more
   valuable now, not less. But it is the one stage that sends scans off-network.

## Acceptance check

Re-run all three `samples/` cards through the new pipeline and diff against their ground-truth
`.txt` files. Expect jello to match, cheese-ball to show known errors, faint to be *held* rather
than to hang.

## Sequencing

Steps 1-3 land together (the pipeline is broken in between). Then 4-7, then the acceptance
check, then 9. Containerization by the answer to Q1.
