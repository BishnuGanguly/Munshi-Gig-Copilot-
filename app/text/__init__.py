"""Deterministic text preparation: digits of any script to 0-9, spoken Indian amounts to digits, and
platform spellings to their canonical form. No translation and no language detection - the model reads the
rider's own words."""
from .normalize import canonical_platforms, extract_numbers, normalize
from .numbers import normalize_digits, words_to_digits

__all__ = ["normalize", "canonical_platforms", "extract_numbers", "normalize_digits", "words_to_digits"]
