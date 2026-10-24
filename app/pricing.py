"""ParcelDesk pricing rule.

The product contract is intentionally small and testable:
  - €5.00 for the first kilogram;
  - €3.00 for every additional *started* kilogram;
  - valid range: 1 g through 30,000 g;
  - integer grams in, integer cents out.

The `buggy` flag reproduces the real sandbox baseline defect (floor division
instead of started-kilogram rounding). It is a deterministic client-side
reference calculation. Missions repair isolated sandbox copies; those patches
are not deployed to this reference calculator.
"""

from __future__ import annotations

from typing import Any

MIN_GRAMS = 1
MAX_GRAMS = 30_000
BASE_CENTS = 500
EXTRA_KG_CENTS = 300


def quote(grams: int, buggy: bool = False) -> dict[str, Any]:
    if not isinstance(grams, int) or isinstance(grams, bool):
        raise ValueError("grams must be an integer")
    if grams < MIN_GRAMS or grams > MAX_GRAMS:
        raise ValueError(f"grams must be between {MIN_GRAMS} and {MAX_GRAMS}")

    if grams <= 1_000:
        extra_kg = 0
        cents = BASE_CENTS
    else:
        remainder = grams - 1_000
        if buggy:
            extra_kg = remainder // 1_000
        else:
            extra_kg = (remainder + 999) // 1_000
        cents = BASE_CENTS + extra_kg * EXTRA_KG_CENTS

    breakdown = [{"label": "First kilogram", "cents": BASE_CENTS}]
    if extra_kg:
        breakdown.append(
            {
                "label": f"{extra_kg} additional started kilogram{'s' if extra_kg > 1 else ''}",
                "cents": extra_kg * EXTRA_KG_CENTS,
            }
        )

    return {
        "grams": grams,
        "kilograms": round(grams / 1_000, 3),
        "cents": cents,
        "euros": f"{cents / 100:.2f} €".replace(".", ","),
        "extra_kg": extra_kg,
        "breakdown": breakdown,
        "rule": "5 € first kg + 3 € per additional started kg",
        "buggy": buggy,
    }


def quote_or_error(grams: Any, buggy: bool = False) -> dict[str, Any]:
    try:
        parsed = int(grams)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Weight must be an integer number of grams."}
    try:
        result = quote(parsed, buggy=buggy)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **result}
