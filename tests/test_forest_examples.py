"""Independent consistency checks for the synthetic forest explorer fixture."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs_src" / "forest-examples.json"


def _manual_dl(effects, standard_errors):
    variances = [max(se, 1e-6) ** 2 for se in standard_errors]
    fixed_weights = [1 / variance for variance in variances]
    fixed_mean = sum(weight * effect for weight, effect in zip(fixed_weights, effects)) / sum(fixed_weights)
    q = sum(weight * (effect - fixed_mean) ** 2 for weight, effect in zip(fixed_weights, effects))
    c_term = sum(fixed_weights) - sum(weight**2 for weight in fixed_weights) / sum(fixed_weights)
    tau2 = max(0, (q - (len(effects) - 1)) / c_term)
    weights = [1 / (variance + tau2) for variance in variances]
    estimate = sum(weight * effect for weight, effect in zip(weights, effects)) / sum(weights)
    standard_error = math.sqrt(1 / sum(weights))
    return estimate, estimate - 1.959963984540054 * standard_error, estimate + 1.959963984540054 * standard_error, tau2, [weight / sum(weights) for weight in weights]


def validate_fixture(data):
    assert data["provenance"]["source"].startswith("Synthetic")
    assert len(data["studies"]) == 4
    assert {scenario["id"] for scenario in data["scenarios"]} == {"consistent", "uncertain", "opposing"}
    for scenario in data["scenarios"]:
        assert len(scenario["effects"]) == len(scenario["se"]) == 4
        assert all(se > 0 for se in scenario["se"])
        assert len(scenario["fits"]) == 5
        assert scenario["fits"][0]["omitted"] is None
        for fit in scenario["fits"]:
            omitted = fit["omitted"]
            keep = [index for index in range(4) if omitted is None or index != omitted - 1]
            values = _manual_dl([scenario["effects"][index] for index in keep], [scenario["se"][index] for index in keep])
            estimate, ci_low, ci_high, tau2, weights = values
            full_weights = [0 if index not in keep else weights[keep.index(index)] for index in range(4)]
            assert fit["estimate"] == pytest.approx(estimate, abs=1e-10)
            assert fit["ciLow"] == pytest.approx(ci_low, abs=1e-10)
            assert fit["ciHigh"] == pytest.approx(ci_high, abs=1e-10)
            assert fit["tau2"] == pytest.approx(tau2, abs=1e-10)
            assert fit["weights"] == pytest.approx(full_weights, abs=1e-10)
            assert sum(fit["weights"]) == pytest.approx(1, abs=1e-12)


def test_forest_fixture_matches_independent_dl_formula():
    validate_fixture(json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_forest_fixture_rejects_changed_estimate():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["scenarios"][0]["fits"][0]["estimate"] += 0.1
    with pytest.raises(AssertionError):
        validate_fixture(data)


def test_forest_fixture_rejects_changed_normalized_weight():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["scenarios"][1]["fits"][0]["weights"][0] += 0.01
    with pytest.raises(AssertionError):
        validate_fixture(data)
