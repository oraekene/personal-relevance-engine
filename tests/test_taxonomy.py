from __future__ import annotations

import pytest

from pre import taxonomy


def test_exactly_seventeen_dimensions() -> None:
    assert len(taxonomy.DIMENSIONS) == 17


def test_validate_passes_on_canonical_taxonomy() -> None:
    taxonomy.validate()


def test_codes_unique_and_lowercase() -> None:
    codes = [d.code for d in taxonomy.DIMENSIONS]
    assert len(set(codes)) == 17
    assert all(c == c.lower() for c in codes)


def test_every_dimension_has_scaffold() -> None:
    for d in taxonomy.DIMENSIONS:
        assert len(d.sub_dimensions) >= 3, f"{d.code} lacks interview scaffold"


def test_expected_dimensions_present() -> None:
    expected = {
        "physical_health",
        "mental_wellbeing",
        "career",
        "business",
        "financial",
        "social",
        "relationship",
        "family",
        "housing",
        "community_civic",
        "education",
        "leisure",
        "environment",
        "safety",
        "spirituality",
        "reputational",
        "autonomy_time",
    }
    assert set(taxonomy.DIMENSIONS_BY_CODE) == expected


def test_validate_rejects_wrong_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(taxonomy, "DIMENSIONS", taxonomy.DIMENSIONS[:5])
    with pytest.raises(ValueError, match="expected 17"):
        taxonomy.validate()
