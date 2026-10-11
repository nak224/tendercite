"""Usage-derived text costs, distinct from estimates and a provider invoice."""

from decimal import Decimal


def token_count(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"Missing or invalid usage count: {name}")
    return value


def optional_count(details, name):
    if name not in details:
        return None
    return token_count(details[name], name)


def account_usage(usage, config):
    if not isinstance(usage, dict):
        raise ValueError("Provider usage is missing")
    prompt = token_count(usage.get("prompt_tokens"), "prompt_tokens")
    completion = token_count(usage.get("completion_tokens"), "completion_tokens")
    total = token_count(usage.get("total_tokens"), "total_tokens")
    if total != prompt + completion:
        raise ValueError("Provider total tokens do not equal prompt plus completion tokens")
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    prompt_details = {} if prompt_details is None else prompt_details
    completion_details = {} if completion_details is None else completion_details
    if not isinstance(prompt_details, dict) or not isinstance(completion_details, dict):
        raise ValueError("Malformed token detail objects")
    for details in (prompt_details, completion_details):
        for category in ("audio_tokens", "video_tokens", "image_tokens"):
            if details.get(category, 0) != 0:
                raise ValueError("Unsupported non-text usage category; pricing is uncertain")
    reasoning = optional_count(completion_details, "reasoning_tokens")
    cached = optional_count(prompt_details, "cached_tokens")
    writes = [
        token_count(d[key], key)
        for d, key in (
            (prompt_details, "cache_write_tokens"),
            (usage, "cache_write_input_tokens"),
            (usage, "cache_creation_input_tokens"),
        )
        if key in d
    ]
    if len(set(writes)) > 1:
        raise ValueError("Conflicting cache-write usage counts")
    write = writes[0] if writes else None
    if (reasoning or 0) > completion or (cached or 0) + (write or 0) > prompt:
        raise ValueError("Token subcategories exceed their parent counts")
    rates = {
        "input_per_million": config.pricing.input_per_million,
        "output_per_million": config.pricing.output_per_million,
        "cached_input_per_million": None,
        "cache_write_input_per_million": None,
    }
    tier_threshold = None
    if config.usage_pricing:
        for key in ("cached_input_per_million", "cache_write_input_per_million"):
            rates[key] = getattr(config.usage_pricing, key)
        for tier in config.usage_pricing.tiers:
            if prompt > tier.above_prompt_tokens:
                tier_threshold = tier.above_prompt_tokens
                rates = {key: getattr(tier, key) for key in rates}
    effective = {
        key: value if value is not None else rates["input_per_million"]
        for key, value in rates.items()
    }
    ordinary = prompt - (cached or 0) - (write or 0)
    cost = (
        ordinary * effective["input_per_million"]
        + (cached or 0) * effective["cached_input_per_million"]
        + (write or 0) * effective["cache_write_input_per_million"]
        + completion * effective["output_per_million"]
    ) / Decimal(1_000_000)
    return {
        "provider_reported_usage": usage,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "cached_input_tokens": cached,
        "cache_write_tokens": write,
        "total_tokens": total,
        "visible_completion_tokens": None if reasoning is None else completion - reasoning,
        "usage_derived_cost": str(cost),
        "currency": config.pricing.currency,
        "applied_rates_per_million": {key: str(value) for key, value in effective.items()},
        "tier_above_prompt_tokens": tier_threshold,
        "assumptions": (
            "Reasoning is a subset of completion, not an additional charge. Cache counts are "
            "subsets of prompt. Without explicit cache rates, the declared uncached rate applies; "
            "no discount is invented. Missing optional categories remain unknown. Not an invoice."
        ),
        "final_provider_billing": "REQUIRES_DASHBOARD_RECONCILIATION",
    }


def check_reservation_rates(config):
    """Don't introduce a rate exceeding the unchanged conservative reservation rates."""
    if not config.usage_pricing:
        return
    options = [config.usage_pricing, *config.usage_pricing.tiers]
    for rates in options:
        for key in (
            "input_per_million",
            "cached_input_per_million",
            "cache_write_input_per_million",
        ):
            value = getattr(rates, key, None)
            if value is not None and value > config.pricing.input_per_million:
                raise ValueError("Usage input tier exceeds unchanged reservation pricing")
        value = getattr(rates, "output_per_million", None)
        if value is not None and value > config.pricing.output_per_million:
            raise ValueError("Usage output tier exceeds unchanged reservation pricing")
