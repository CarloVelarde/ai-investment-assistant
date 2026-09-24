"""Pinned model pricing and conservative integer-microdollar allowances."""

from collections.abc import Mapping
from decimal import ROUND_CEILING, Decimal

POLICY_VERSION = "2026-09-22-v1"
CLASSIFIER_MODEL = "gpt-5.4-nano-2026-03-17"
RESEARCH_MODEL = "gpt-5.4-mini-2026-03-17"
CLASSIFIER_ALLOWANCE = 82_500
RESEARCH_ALLOWANCE = 1_620_000
RESEARCH_REQUEST_ALLOWANCE = 318_000
SEARCH_FEE = 10_000
_RATES = {
    CLASSIFIER_MODEL: (Decimal("0.20"), Decimal("1.25")),
    RESEARCH_MODEL: (Decimal("0.75"), Decimal("4.50")),
}


def extract_usage(body: Mapping[str, object]) -> tuple[int, int] | None:
    """Read provider counters independently of semantic output validation."""

    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if (
        not isinstance(input_tokens, int)
        or isinstance(input_tokens, bool)
        or input_tokens < 0
    ):
        return None
    if (
        not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or output_tokens < 0
    ):
        return None
    return input_tokens, output_tokens


def token_charge(model: str, input_tokens: int, output_tokens: int) -> int:
    rates = _RATES.get(model)
    if rates is None:
        raise ValueError("model pricing unavailable")
    if min(input_tokens, output_tokens) < 0:
        raise ValueError("negative token usage")
    return int(
        (
            Decimal(input_tokens) * rates[0] + Decimal(output_tokens) * rates[1]
        ).to_integral_value(rounding=ROUND_CEILING)
    )
