# recipe-pipeline v2 — plan (<pve-host> Ollama, Apple Vision removed)

Status: APPROVED-PENDING (Q1-Q3 answered). Supersedes the Mac-bound v1 flow.

## Goal

Free the Mac and simplify. All inference moves to the CPU Ollama on **<pve-host>**
(`http://ollama.meklab.net:11434`, <ct>). Apple Vision is removed; **glm-ocr** becomes the
OCR engine and **qwen3.5:4b** does structuring and QA. Speed is explicitly not a constraint.

## Decisions taken

1. **Follow the scribe pattern** — containerize the worker, run it in-cluster, point it at
   `http://ollama.meklab.net:11434` via Deployment env.
2. **Keep the LLM confidence score.**
3. **Skip OpenRouter escalation for now** — leave `htr/openrouter.py` in place and inert;
   file a follow-up ticket to revisit if quality proves inadequate.

## scribe already solved most of this

`scribe` runs glm-ocr + qwen3.5:4b against this exact Ollama in production today, and its
`src/scribe/ollama.py` says it was *adapted from recipe-pipeline's own client*. The code comes
home. Reuse rather than rewrite:

| From scribe | Why it matters here |
|---|---|
| `generate_with_image()` | `/api/generate`, `temperature: 0`, client-side timing |
| **`normalize_latex()`** | glm-ocr renders `1/2` as `$\frac{1}{2}$`. Recipes are *made* of fractions — without this every quantity is corrupt |
| **`num_thread: 6`** per request | llama.cpp sizes threads from host cores and ignores the cgroup quota. Measured **+73% generation**. No server-side env var exists — every client must send it |
| `chat_structured()` | Resolves the open question below: qwen3.5:4b **does** honor Ollama JSON-schema `format` in production. Also sets `think: False` — qwen3.5 is a thinking model and its trace is generated at CPU speed for output we discard |
| `downscale(max_edge=1500)`, render DPI 150 | 200 DPI cost ~50% more wall clock for byte-identical output |
| `health()` | Identity check — the dev Mac runs its own ollama and a misconfigured host silently answers with different models |

Also inherited: **glm-ocr reports zero for every timing field** it returns, so wall clock must
be measured client-side, and **local port 11434 is ambiguous** on the Mac (kubectl binds `::1`,
native ollama binds `127.0.0.1`) — use 11500.

Note: scribe's *code default* still points at `ollama-mini.infra.svc.cluster.local`, which no
longer exists — the live Deployment overrides it by env. Worth a small follow-up on scribe.

## Measured on <pve-host> (against `samples/` ground truth)

| Finding | Evidence |
|---|---|
| glm-ocr is not a chat model | `ollama show --template glm-ocr` = bare `{{ .Prompt }}`; `/api/chat` emitted a 4m42s run of `-` |
| Needs a non-empty instruction | `prompt: ""` returned `''` |
| Clean cursive: excellent | `01-jello` matched ground truth line for line; one surname wrong |
| Harder card: real errors | `02-cheese-rotated`: `Worchestershire sauce` -> `shredded cheese`, `Pecan nuts` -> `puffy beacon nuts`, `mold` -> `milk` |
| **Orientation-invariant** | The 180-rotated page transcribed correctly *and in upright line order*. Pre-rotating to +90/+270 made it worse |
| **Repetition looping** | 4 of 5 outputs repeated the whole transcript 2-3x verbatim |
| **Runaway on low-signal input** | `05-faint` never returned: HTTP 000 after 550s |

### Honest quality note

`Worchestershire sauce` -> `shredded cheese` is exactly the silent-plausible-substitution the
v1 README warned about, and no automated gate will catch it. This is acceptable because hosted
manual review is now the real gate — but it raises the volume through that queue, and it is why
the cheap deterministic checks stay.

## Plan

1. **Client + config.** Port scribe's `ollama.py` back into the worker. Replace
   `vision_model`/`enrich_model` with `ocr_model: glm-ocr:latest` / `text_model: qwen3.5:4b`;
   add `num_thread: 6`, `ocr_max_edge: 1500`, explicit `num_ctx: 32768`. Add a **`num_predict`
   cap** — scribe's 1800s timeout bounds the damage but does not prevent the faint-page hang.

2. **New OCR backend.** `htr/glmocr.py` behind the existing `HTRBackend` interface, using
   scribe's prompt and `normalize_latex()`. Delete `htr/applevision.py`, the `apple` extra in
   `worker/pyproject.toml`, and the `applevision` defaults in `config.py:55` / `cli.py:61`.

3. **Gut `preprocess.py`.** `detect_rotation`, `_is_upside_down` and `_ORIENT_AB_PROMPT` all
   delete — glm-ocr reads the raw scan correctly at any orientation, and pre-rotating hurts.
   `make_upright` survives as fine-deskew for the thumbnail only. Removes an LLM round-trip and
   3 OCR passes per card.

4. **Two new gates in `review.py`** for failure modes glm-ocr actually exhibits:
   - *repetition*: de-duplicate clean whole-transcript repeats; flag partial ones.
   - *degenerate output*: alphanumeric ratio / distinct-character count, to catch the `- - - -`
     mode that currently passes as hundreds of valid characters.

5. **Re-validate thresholds.** `FAINT_DARK_FRACTION`, `FAINT_STD`, `EMPTY_OCR_CHARS` were tuned
   against Apple Vision. Re-check against glm-ocr. Keep the LLM confidence pass (decision 2),
   repointed at qwen3.5:4b via `chat_structured`.

6. **Batch by stage.** <pve-host> runs `OLLAMA_MAX_LOADED_MODELS: "1"`; alternating models per card
   costs a ~30s swap twice per recipe. OCR the whole batch, then structure the whole batch.
   Raising that setting is not an option — it caused the 2026-08-19 <k3s-server> outage.

7. **Containerize + CI.** Extend the Dockerfile with a worker image, enable `test:worker` in
   `.gitlab-ci.yml` (currently skipped: "worker is macOS-only"), and add the k8s Deployment with
   `RECIPE_OLLAMA_HOST=http://ollama.meklab.net:11434` and `RECIPE_NUM_THREAD=6`. Retire the
   `launchd` agent and `sudo pmset repeat wake`.

8. **Docs.** Rewrite the README flow and revise — not delete — the "why not just ask GPT to read
   it" section, using the measured results above.

## Dev-time connectivity

Containerizing dissolves the firewall question in production (k3s nodes are already permitted).
For laptop development <pve-host> is still unreachable, and scribe's documented port-forward no
longer applies since <pve-host> is not a k8s Service. Use an SSH tunnel on **local port 11500**:

```bash
ssh -N -L 11500:<lan-ip>:11434 root@<lan-ip>
```

## Acceptance check

Re-run all three `samples/` cards and diff against ground truth via `bakeoff.py`. Expect jello
to match, cheese-ball to show the known errors, faint to be **held rather than to hang**.

## Sequencing

Steps 1-3 land together (the pipeline is broken in between), then 4-6, then the acceptance
check, then 7-8.
