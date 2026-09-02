"""Regression-check the structure prompt against every sample card.

    uv run --no-sync python scripts/promptcheck.py                 # the installed prompt
    uv run --no-sync python scripts/promptcheck.py /tmp/draft.txt  # a candidate

Feeds samples/*.txt (Apple Vision ground truth) into structure() rather than live OCR, so it
measures the STRUCTURE stage alone and is not perturbed by OCR run-to-run variance.

Run it BEFORE and AFTER a prompt edit and compare. A prompt change that fixes one card while
quietly regressing another is the failure this exists to catch — the cheese and jello cards
were silently DROPPING their attributions and nobody noticed until this was run.

Needs a reachable ollama; from a laptop:
    ssh -N -L 11500:<lan-ip>:11434 mekadmin@<lan-ip>
    RECIPE_OLLAMA_HOST=http://127.0.0.1:11500 uv run --no-sync python scripts/promptcheck.py
"""
import pathlib
import sys

from recipe_pipeline import prompts
from recipe_pipeline import structure as S
from recipe_pipeline.config import load_settings

prompt_file = sys.argv[1] if len(sys.argv) > 1 else None
if prompt_file:
    text = pathlib.Path(prompt_file).read_text()
    prompts.STRUCTURE_SYSTEM_PROMPT = text
    S.STRUCTURE_SYSTEM_PROMPT = text

s = load_settings()
cards = [
    ("jello",    "samples/jello-cabbage-salad.txt"),
    ("cheese",   "samples/cheese-ball.txt"),
    ("potato",   "samples/mediterranean-potato-salad.txt"),
]
for name, path in cards:
    ocr = pathlib.Path(path).read_text()
    r = S.structure(s, ocr_pages=[ocr], source_pdf="x.pdf")
    print(f"===== {name}", flush=True)
    print(f"  name        : {r.name}")
    print(f"  ingredients : {len(r.ingredients)}")
    print(f"  instructions: {len(r.instructions)}")
    for i in r.instructions:
        print(f"      - {i.text[:72]}")
    print(f"  yield       : {r.recipe_yield.value!r}   servings: {r.servings.value!r}")
    print(f"  curiosities : {r.source_curiosities!r}", flush=True)
