"""Runtime configuration, loaded from environment / `.env` (prefix ``RECIPE_``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (src/recipe_pipeline/config.py -> project root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All tunables. Override any field via a ``RECIPE_<FIELD>`` env var or `.env` entry."""

    model_config = SettingsConfigDict(
        env_prefix="RECIPE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Local models (Ollama on this Mac) ---
    ollama_host: str = "http://localhost:11434"
    # VISION model (the review pass sees the page image). gemma4:e4b advertises the `vision`
    # capability; the gemma4 MLX tags are text-only. Verify with `ollama show`.
    vision_model: str = "gemma4:e4b"
    # Text-LLM stages (structure, enrich, orientation A/B). e4b honors Ollama JSON-schema output
    # and is fast. (The gemma4 MLX tags are slow and ignore structured-output format.)
    enrich_model: str = "gemma4:e4b"
    # Context window per Ollama request. Ollama's default (~4096) silently TRUNCATES longer
    # prompts (dropping the system rules / start of transcripts) — bad for fidelity. A vision
    # request also spends tokens encoding the image, so keep this generous.
    ollama_num_ctx: int = 16384

    # --- Metadata enrichment via SearXNG (port-forward svc/searxng; JSON API) ---
    searxng_url: str = "http://localhost:8888"

    # --- Mealie upload target (port-forward svc/mealie; plain ClusterIP, no Authentik) ---
    mealie_url: str = "http://localhost:9000"
    mealie_api_token: str | None = None
    # Fixed tags added to EVERY uploaded recipe, so this batch is filterable in Mealie:
    # source person, that it's a scan, and the pipeline version. Comma-separated.
    group_tags: str = "a family member,Scan,recipe-pipeline-v1"

    @property
    def group_tag_list(self) -> list[str]:
        return [t.strip() for t in self.group_tags.split(",") if t.strip()]

    # --- OCR backend (single engine). Apple Vision: accurate, on-device, orientation-tolerant.
    # (TrOCR was removed — universally worse on this corpus and it broke the divergence gate.)
    ocr_a: str = "applevision"

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
