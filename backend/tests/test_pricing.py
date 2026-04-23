"""Pricing table tests loaded from prices.yaml."""
from __future__ import annotations

import pytest

from backend.providers.pricing import price_for


def test_price_for_openrouter_model():
    price = price_for("openrouter", "minimax/minimax-m2.7")
    assert price.provider == "openrouter"
    assert price.model_id == "minimax/minimax-m2.7"


def test_price_for_openai_model():
    price = price_for("openai", "gpt-4o-mini")
    assert price.provider == "openai"
    assert price.model_id == "gpt-4o-mini"


def test_missing_price_entry_raises():
    with pytest.raises(KeyError):
        price_for("openai", "missing-model")
