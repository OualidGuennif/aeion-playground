"""Boundary contract for the ParcelDesk quote rule."""

from __future__ import annotations

import pytest

from app.pricing import quote


@pytest.mark.parametrize(
    ("grams", "cents"),
    [
        (1, 500),
        (999, 500),
        (1000, 500),
        (1001, 800),
        (1200, 800),
        (1500, 800),
        (1999, 800),
        (2000, 800),
        (2001, 1100),
        (2500, 1100),
        (29999, 9200),
        (30000, 9200),
    ],
)
def test_started_kilogram_boundaries(grams: int, cents: int) -> None:
    assert quote(grams) == cents


@pytest.mark.parametrize("grams", [0, -1, 30001])
def test_rejects_out_of_range(grams: int) -> None:
    with pytest.raises(ValueError):
        quote(grams)


@pytest.mark.parametrize("grams", [1.5, "1200", True, None])
def test_rejects_non_integer(grams: object) -> None:
    with pytest.raises(TypeError):
        quote(grams)  # type: ignore[arg-type]
