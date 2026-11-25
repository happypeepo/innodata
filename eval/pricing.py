"""USD per 1M tokens (input, output) and the --dry-run estimate (eval/PLAN.md D9).

shortcut: prices are from third-party listings (2026-11-26) that disagree; where they conflict we take the
HIGHER figure so --budget-usd stops early rather than late. Check each provider's pricing page before the
paid run and update here; costs in results are recomputed from tokens on every run, so an update needs no
new API calls.
"""

PRICES = {
    "mock": (0.0, 0.0),
    "gemini-robotics-er-2-preview": (2.00, 10.00),  # listings: $1/$5 intro to 2026-12-31, else $2/$10
    "gemini-3.1-pro-preview": (2.00, 12.00),        # <=200k-token prompts
    "claude-opus-5-5": (5.00, 25.00),               # VERIFY
    "gpt-6.1-sol": (2.00, 10.00),                   # VERIFY
}

# rough per-call guesses for --dry-run, replaced by real numbers after the pilot
EST_TOKENS_PER_IMAGE = 1300
EST_TOKENS_TEXT = 200
EST_TOKENS_OUT = 400  # thinking models can go well past this; the pilot tells us


def cost(model_id: str, tokens_in: int, tokens_out: int) -> float:
    if model_id not in PRICES:
        raise SystemExit(f"no price for {model_id!r}: add it to eval/pricing.py before a paid run")
    pin, pout = PRICES[model_id]
    return round((tokens_in * pin + tokens_out * pout) / 1e6, 6)


def estimate(model_id: str, n_images_per_call: list[int]) -> float:
    return sum(cost(model_id, EST_TOKENS_TEXT + n * EST_TOKENS_PER_IMAGE, EST_TOKENS_OUT)
               for n in n_images_per_call)
