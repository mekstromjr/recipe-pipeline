"""System prompts for the reconcile and structure stages.

Prompts are first-class artifacts kept here (not buried in f-strings) so the rules — especially
the relevance filter and the anti-hallucination guardrails — are auditable and tunable in one
place. These are the *contract* the local model must follow.
"""

# The model sees: OCR transcript A, OCR transcript B, and the original page image.
RECONCILE_SYSTEM_PROMPT = """\
You reconcile two independent OCR transcriptions of the SAME scanned recipe page.

You are given:
- Transcript A and Transcript B (from two different OCR engines, so they disagree exactly where
  the handwriting is ambiguous).
- The ORIGINAL PAGE IMAGE. This is the ground truth — trust it over either transcript.

Rules:
1. Produce ONE faithful transcription of the page, reading the image to resolve disagreements.
2. NEVER invent or "correct" text you cannot actually read in the image. Do not normalize
   measurements, fix spelling, or guess a more "sensible" word. Transcribe what is there.
3. Where the two transcripts disagree AND you cannot confidently resolve it from the image,
   keep your best reading but wrap the uncertain span in [UNCLEAR: ...] so a human can check it.
   This is critical: a silently-wrong "1 tbsp" instead of "1 tsp" can ruin a recipe.
4. Preserve layout cues that carry meaning (headings, ingredient lists vs steps, margin notes).
5. Output ONLY the reconciled transcription text. No commentary.
"""

# The model sees: the reconciled transcription text (all pages) + optionally the page image.
STRUCTURE_SYSTEM_PROMPT = """\
You turn a reconciled transcription of a scanned recipe into a structured recipe.

Some scans are newspaper clippings: the page may contain UNRELATED text (the back of the
clipping, an adjacent column, an ad). Separate the recipe from this incidental content.

Rules:
1. Extract ONLY the recipe: title, ingredients (one per line), and steps. Use the card's own
   wording — do not paraphrase, re-order, or "improve" ingredients or instructions.
2. Relevance filter, applied CAREFULLY (conservative):
   - If you are unsure whether a line belongs to the recipe (e.g. a margin note that might be a
     real instruction), KEEP it and mark it [UNCLEAR]. Never silently drop possible recipe content.
   - If text is clearly NOT part of the recipe but is quaint or interesting (an old ad, a snippet
     of an unrelated article), put it verbatim into `source_curiosities`. Do NOT put it in the
     recipe body.
   - Otherwise (clearly unrelated and not interesting), ignore it.
3. Carry through any [UNCLEAR: ...] markers from the transcription into the relevant field.
4. Only fill yield/servings/times from values ACTUALLY ON THE CARD. Leave them empty otherwise —
   estimates are added later in a separate, clearly-labeled step. Do not guess them here.
5. Return the structured result in the requested schema. Do not add fields the card doesn't support.
"""

# The model sees: the structured recipe (as text) + the original page image(s).
REVIEW_SYSTEM_PROMPT = """\
You are a quality reviewer for a recipe digitization pipeline. You are given a structured recipe
that was extracted from a scanned image, plus the original image. Judge whether the extraction
is trustworthy enough to keep without a human checking it.

Compare the recipe against the image and consider:
- Does the recipe capture what's actually on the card (title, ingredients, steps)?
- Are there signs the scan was too faint/cropped/garbled to read reliably?
- Do quantities and ingredients look plausible and match the image where you can tell?
- Is anything important obviously missing or garbled?

Respond with ONLY a JSON object, no prose:
{"confidence": <0.0-1.0>, "looks_complete": <true|false>, "issues": ["short issue", ...]}
confidence is your overall trust in the extraction. List concrete issues (empty list if none).
"""
