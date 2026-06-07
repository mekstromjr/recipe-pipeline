"""OCR bake-off harness — answers the cursive question empirically.

No published benchmark covers hard cursive recipe cards, so we measure on the real thing:
run each candidate OCR backend over sample PDFs in ``samples/`` that have a hand-typed
ground-truth ``.txt``, score CER/WER (jiwer), and report a ranking. The winner(s) become
OCR_A/OCR_B; if zero-shot cursive is too weak, the escalation is to fine-tune Kraken.

Sample layout:  samples/<name>.pdf  +  samples/<name>.txt  (ground truth)
Per-backend transcriptions are written to output/bakeoff/<backend>/<name>.txt for eyeballing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from recipe_pipeline import pdf
from recipe_pipeline.htr import get_backend

if TYPE_CHECKING:
    from recipe_pipeline.config import Settings
    from recipe_pipeline.htr import HTRBackend


@dataclass
class Sample:
    name: str
    pdf_path: Path
    ground_truth: str


def collect_samples(samples_dir: Path) -> list[Sample]:
    """Find ``<name>.pdf`` files in ``samples_dir`` that have a sibling ``<name>.txt``."""
    if not samples_dir.exists():
        return []
    out: list[Sample] = []
    for pdf_path in sorted(samples_dir.glob("*.pdf"), key=lambda p: p.name.lower()):
        gt = pdf_path.with_suffix(".txt")
        if gt.exists():
            out.append(
                Sample(name=pdf_path.stem, pdf_path=pdf_path, ground_truth=gt.read_text())
            )
    return out


def normalize(text: str) -> str:
    """Lowercase + collapse whitespace, so scores reflect content not formatting/case."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _transcribe_pdf(backend: HTRBackend, pdf_path: Path) -> str:
    """Run a backend over every page of a PDF and join the page transcriptions."""
    pages = pdf.render_pages(pdf_path)
    return "\n".join(backend.transcribe_page(img).text for img in pages)


def run_bakeoff(settings: Settings, *, backends: list[str]) -> None:
    samples = collect_samples(settings.samples)
    if not samples:
        print(
            f"No samples found in {settings.samples}. Add <name>.pdf + <name>.txt "
            "(ground truth) pairs and retry."
        )
        return

    # Instantiate backends, skipping any that aren't available in this environment.
    live: dict[str, HTRBackend] = {}
    for name in backends:
        try:
            live[name] = get_backend(name)
        except Exception as exc:  # noqa: BLE001 - unavailable backend shouldn't abort the run
            print(f"  skipping backend {name!r}: {exc}")
    if not live:
        print("No usable backends. Nothing to score.")
        return

    out_root = settings.outbox / "bakeoff"
    print(f"\n{len(samples)} sample(s) x {len(live)} backend(s) — CER / WER, lower is better\n")
    header = f"{'sample':<28}" + "".join(f"{n:>22}" for n in live)
    print(header)
    print("-" * len(header))

    totals: dict[str, list[float]] = {n: [] for n in live}
    for s in samples:
        ref = normalize(s.ground_truth)
        cells: list[str] = []
        for name, backend in live.items():
            try:
                hyp_raw = _transcribe_pdf(backend, s.pdf_path)
            except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
                cells.append(f"{'ERROR':>22}")
                print(f"    {name} failed on {s.name}: {exc}")
                continue
            out_file = out_root / name / f"{s.name}.txt"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(hyp_raw)

            import jiwer

            hyp = normalize(hyp_raw)
            cer = jiwer.cer(ref, hyp)
            wer = jiwer.wer(ref, hyp)
            totals[name].append(cer)
            cells.append(f"{cer * 100:>9.1f}% /{wer * 100:>8.1f}%")
        print(f"{s.name[:27]:<28}" + "".join(cells))

    print("-" * len(header))
    avg_cells = "".join(
        f"{(sum(v) / len(v) * 100):>20.1f}%" if v else f"{'n/a':>22}" for v in totals.values()
    )
    print(f"{'mean CER':<28}{avg_cells}")
    print(f"\nPer-backend transcriptions written under {out_root}/ for inspection.")
