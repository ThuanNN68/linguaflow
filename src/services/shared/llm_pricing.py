"""Reference USD pricing for the models `DEFAULT_MODELS` (src/services/shared/llm.py)
can serve, used only to turn the token counts already on `translation_attempts`
into an order-of-magnitude cost estimate for the admin stats screen.

**These figures are not billing-accurate and will drift.** Provider price
sheets change without notice and this table is not fetched from anywhere — it
is a snapshot, entered by hand, of what each model's $ per million input and
output tokens looked like when this file was last touched. Treat a cost shown
from it as "roughly this much", never as an invoice line, and re-check it
against the provider's current pricing page before quoting it to anyone who
will act on the number. A model with no entry here shows no cost at all in the
UI rather than a wrong one — silently defaulting an unpriced model to $0 would
be a worse error than admitting the price is unknown.

A handful of entries below are marked ESTIMATED — a newer model whose price
sheet was not available, priced by carrying forward the closest sibling model's
rate rather than left blank. That is a judgment call to keep the cost screen
useful, not a claim that figure is confirmed; check its comment before relying
on it.

`FALLBACK_MODEL_NAME` (tier 2, ADR-07) is priced at zero with real confidence
rather than left unpriced: `translate_with_secondary_provider` calls the free
`deep-translator` web endpoint, not a metered API, so there is no per-token
bill to estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.services.language.fallback_translator import FALLBACK_MODEL_NAME


@dataclass(frozen=True)
class ModelPrice:
    """$ per million tokens, input and output priced separately.

    Providers charge output tokens at a different rate than input — usually
    higher, since generation costs more than reading — so a single blended
    rate would misprice any run whose input/output ratio differs from
    whatever ratio the blend assumed.
    """

    input_per_million_usd: float
    output_per_million_usd: float


# Keyed by the exact string `model_served` stores — the provider's own model
# id, not `DEFAULT_MODELS`' provider key. Entered by hand from each provider's
# public pricing page; see the module docstring for how stale this can get.
PRICE_PER_MILLION_TOKENS_USD: dict[str, ModelPrice] = {
    "llama-3.3-70b-versatile": ModelPrice(0.59, 0.79),  # Groq
    "deepseek-chat": ModelPrice(0.27, 1.10),  # DeepSeek, standard (non-cached) rate
    "gemini-2.5-flash": ModelPrice(0.30, 2.50),  # Google, non-"thinking" output
    # `LLM_MODEL` in .env is pinned to this one, newer than 2.5-flash — no
    # confirmed price sheet for it was available when this table was written,
    # so it carries 2.5-flash's rate forward as the closest known reference
    # rather than being left unpriced. Replace with the real figure once
    # Google publishes one; do not treat this line as confirmed.
    "gemini-3.7-flash": ModelPrice(0.30, 2.50),  # Google — ESTIMATED, see above
    "gpt-4o-mini": ModelPrice(0.15, 0.60),  # OpenAI
    "gpt-4o": ModelPrice(2.50, 10.00),  # OpenAI
    "mistral-small-latest": ModelPrice(0.15, 0.60),  # Mistral Small 4
    FALLBACK_MODEL_NAME: ModelPrice(0.0, 0.0),  # deep-translator: not metered
}


def get_model_price(model: str) -> ModelPrice | None:
    """Return the matching family price, including dated model snapshots."""
    # Providers do not agree on a stable separator in their reported model
    # name.  Gemini, for example, can report ``gemini 3.7 flash`` while the
    # configured model and price catalog use ``gemini-3.7-flash``.  Treat
    # whitespace and underscores as separators before looking up the family;
    # otherwise tokens are recorded but their cost is silently unpriced.
    normalized = "-".join((model or "").strip().lower().replace("_", " ").split())
    # Providers may append a dated snapshot to the stable family id. Match the
    # longest family first so `gpt-4o-mini-*` never falls into `gpt-4o`.
    family = next(
        (
            name
            for name in sorted(PRICE_PER_MILLION_TOKENS_USD, key=len, reverse=True)
            if normalized == name or normalized.startswith(f"{name}-")
        ),
        None,
    )
    return PRICE_PER_MILLION_TOKENS_USD.get(family) if family else None


def estimate_cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> float | None:
    """Rough $ cost for this many tokens on this model, or None if unpriced.

    None is a real answer, not a missing one — see the module docstring for
    why an unpriced model must not be reported as free.
    """
    price = get_model_price(model)
    if price is None:
        return None
    return (
        input_tokens / 1_000_000 * price.input_per_million_usd
        + output_tokens / 1_000_000 * price.output_per_million_usd
    )
