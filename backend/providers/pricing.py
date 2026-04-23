"""Provider-aware pricing loaded from prices.yaml."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
import yaml


class ModelPrice(BaseModel):
    model_config = {"protected_namespaces": ()}

    provider: str
    model_id: str
    input_per_mtok_usd: float
    output_per_mtok_usd: float

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_per_mtok_usd
            + output_tokens / 1_000_000 * self.output_per_mtok_usd
        )


PRICES_PATH = Path(__file__).resolve().parents[2] / "prices.yaml"
DEFAULT_OPENROUTER_MODEL = "minimax/minimax-m2.7"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_MODEL = DEFAULT_OPENROUTER_MODEL


@lru_cache(maxsize=1)
def _load_prices() -> dict[tuple[str, str], ModelPrice]:
    raw = yaml.safe_load(PRICES_PATH.read_text(encoding="utf-8")) or {}
    prices: dict[tuple[str, str], ModelPrice] = {}
    for provider, models in raw.items():
        if not isinstance(models, dict):
            continue
        for model_id, spec in models.items():
            if not isinstance(spec, dict):
                continue
            prices[(str(provider), str(model_id))] = ModelPrice(
                provider=str(provider),
                model_id=str(model_id),
                input_per_mtok_usd=float(spec["input_per_mtok_usd"]),
                output_per_mtok_usd=float(spec["output_per_mtok_usd"]),
            )
    return prices


def price_for(provider: str, model_id: str) -> ModelPrice:
    prices = _load_prices()
    key = (provider, model_id)
    if key not in prices:
        raise KeyError(f"No price entry for provider={provider!r} model={model_id!r}. Add to prices.yaml.")
    return prices[key]
