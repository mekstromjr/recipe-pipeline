"""Runtime configuration, loaded from environment / `.env` (prefix ``RECIPE_``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = three levels up from this file (worker/src/recipe_pipeline/config.py -> repo
# root). The batch data dirs (inbox/, output/, samples/) live at the REPO root, not worker/ —
# the #2 restructure moved this file a level deeper and parents[2] silently became worker/.
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All tunables. Override any field via a ``RECIPE_<FIELD>`` env var or `.env` entry."""

    model_config = SettingsConfigDict(
        env_prefix="RECIPE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Models: CPU Ollama in <ct>, NOT this Mac ---
    # <ct> has moved hosts twice (<pve-host> -> <pve-host> 2026-08-26 -> <pve-host>). It keeps the static
    # address <lan-ip> and the name `ollama.meklab.net` across moves, so prefer the NAME
    # and re-check the host's specs (cores, memory cap) rather than assuming they carried over.
    # The worker runs in-cluster and reaches it over the LAN, same as scribe. Ollama ships no
    # authentication, so 11434 is restricted to the k3s nodes by iptables INPUT rules inside
    # the CT — a laptop CANNOT reach it, and neither can the PVE host it runs on.
    # For local development, tunnel THROUGH A K3S NODE and override:
    #   ssh -N -L 11500:<lan-ip>:11434 mekadmin@<lan-ip>
    #   RECIPE_OLLAMA_HOST=http://127.0.0.1:11500 uv run recipe-pipeline extract
    # Do NOT tunnel to local port 11434: a dev Mac running its own native ollama binds IPv4
    # 127.0.0.1 while the forward binds IPv6 [::1], so "localhost:11434" resolves to either
    # one unpredictably and requests silently hit the wrong server.
    ollama_host: str = "http://ollama.meklab.net:11434"

    # OCR. glm-ocr is a dedicated 1.1B OCR model — NOT a chat model: its template is a bare
    # `{{ .Prompt }}`, so it must be driven via /api/generate. Calling /api/chat produces
    # degenerate output (a multi-minute run of "-"). It also requires a NON-EMPTY prompt;
    # an empty one returns "".
    ocr_model: str = "glm-ocr:latest"
    # Text stages (structure, enrich, review). Honors Ollama JSON-schema `format`, verified in
    # scribe's production use. It is a *thinking* model — always send think=False, or the
    # discarded reasoning trace is generated at CPU speed.
    text_model: str = "qwen3.5:4b"

    # MEASURED, not guessed (scribe's bake-off on this same host). llama.cpp sizes its thread
    # pool from HOST core count and ignores the container's cgroup quota, so it oversubscribes
    # and the threads fight CFS throttling. Pinning to the real limit measured +73% generation.
    # There is no server-side env var — it is a per-request option, so EVERY call must send it.
    # Must equal the CT's `cores` (3 as of the <pve-host> move; it was also 3 on <pve-host>). llama.cpp
    # sizes its pool from HOST core count and ignores the cgroup, so an unset value
    # oversubscribes. Verify against `pct config <ctid>` after any host move — this is a per-request
    # option with no server-side equivalent, so every caller must send it.
    num_thread: int = 3

    # Longest-edge cap for page images sent to the vision model. Full-resolution renders cost
    # real CPU in the vision encoder for no accuracy gain at this model size.
    ocr_max_edge: int = 1500

    # Hard ceiling on OCR tokens. glm-ocr RUNS AWAY on low-signal input: the faint test page
    # never returned (HTTP 000 after 550s). A recipe card is a few hundred tokens, so this is
    # generous while still bounding a degenerate generation.
    ocr_num_predict: int = 2048

    # Per-request ceiling. Measured on <pve-host> with the host otherwise idle, one page of OCR is
    # ~39s warm; a cold load after a model swap adds tens of seconds more. Under contention
    # with scribe it has been observed at 445-839s (see docs/redesign-v2-plan.md), because
    # ollama serializes across clients. This is a runaway backstop, not an expected duration.
    ollama_timeout_seconds: float = 900.0

    # Context window per request. Must match OLLAMA_CONTEXT_LENGTH on the server (16384).
    #
    # DO NOT RAISE THIS ALONE. The window, the container memory cap, and LLAMA_ARG_CACHE_RAM
    # are sized TOGETHER: a near-full 32k window peaked at 5.9G resident and OOM-killed the
    # server inside a 6G cap (2026-08-26). KV cache scales with tokens actually in the window,
    # so a short-prompt memory measurement does NOT validate a full-window call.
    #
    # Ollama truncates over-length input SILENTLY, dropping system rules or the start of a
    # transcript, so it is set explicitly rather than inherited from the server default.
    ollama_num_ctx: int = 16384

    # --- Metadata enrichment via SearXNG (port-forward svc/searxng; JSON API) ---
    searxng_url: str = "http://localhost:8888"

    # --- Mealie upload target (port-forward svc/mealie; plain ClusterIP, no Authentik) ---
    mealie_url: str = "http://localhost:9000"
    mealie_api_token: str | None = None
    # Fixed tags added to EVERY uploaded recipe, so a pipeline batch is filterable in Mealie.
    # NO PERSON HERE. This defaulted to "a family member" while the corpus was one person's recipe
    # box; that is wrong for anything uploaded since, and a per-recipe attribution belongs with
    # the recipe (the card's own byline lands in source_curiosities), not in a global default.
    # Set RECIPE_GROUP_TAGS to add a batch label when one genuinely applies.
    # The models that processed each recipe are tagged separately (bare names, e.g.
    # "glm-ocr", "qwen3.5:4b"), derived from the recipe itself — see mealie_payload.model_tags.
    group_tags: str = "Scan,recipe-pipeline-v1"

    @property
    def group_tag_list(self) -> list[str]:
        return [t.strip() for t in self.group_tags.split(",") if t.strip()]

    # --- OCR backend (single engine). glm-ocr in <ct>; `openrouter` stays registered for
    # the escalation path but is not used by default.
    ocr_backend: str = "glmocr"

    # --- Cloud escalation (the `escalate` command): re-OCR FAILED cards with a strong cloud
    # vision model that reads hard cursive far better. The only stage that leaves the machine.
    openrouter_api_key: str | None = None  # copy OPEN_ROUTER_KEY from .env
    openrouter_ocr_model: str = "google/gemini-2.5-flash"

    # --- Directories (resolved relative to the project root if not absolute) ---
    input_dir: Path = Field(default=Path("inbox"))
    output_dir: Path = Field(default=Path("output"))
    samples_dir: Path = Field(default=Path("samples"))

    def _resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def inbox(self) -> Path:
        return self._resolve(self.input_dir)

    @property
    def outbox(self) -> Path:
        return self._resolve(self.output_dir)

    @property
    def samples(self) -> Path:
        return self._resolve(self.samples_dir)


def load_settings() -> Settings:
    """Construct :class:`Settings`. Kept as a function so tests can patch the environment."""
    return Settings()
