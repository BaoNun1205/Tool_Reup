"""Helpers for formatting media time values."""

from __future__ import annotations


def format_seconds(value: float) -> str:
    return "%.3f" % max(0.0, value)


def parse_fraction(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" not in value:
        return float(value)
    numerator, denominator = value.split("/", 1)
    if float(denominator) == 0:
        return 0.0
    return float(numerator) / float(denominator)
