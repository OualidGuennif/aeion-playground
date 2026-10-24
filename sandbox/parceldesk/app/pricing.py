"""ParcelDesk pricing rule — sandbox baseline.

Contract:
- input: integer grams, 1..30000
- price: 500 cents for the first started kilogram,
  300 cents for every additional started kilogram
- output: integer cents

This baseline intentionally contains the started-kilogram rounding defect used by
mission PD01. It is a copy used by the sandbox, never by the client surface.
"""

from __future__ import annotations

FIRST_KG_CENTS = 500
NEXT_KG_CENTS = 300
MIN_GRAMS = 1
MAX_GRAMS = 30_000


def quote(grams: int) -> int:
    if isinstance(grams, bool) or not isinstance(grams, int):
        raise TypeError("grams must be an integer")
    if grams < MIN_GRAMS or grams > MAX_GRAMS:
        raise ValueError("grams must be between 1 and 30000")

    # DEFECT: floor division ignores the started kilogram.
    kilograms = grams // 1000
    return FIRST_KG_CENTS + max(0, kilograms - 1) * NEXT_KG_CENTS
