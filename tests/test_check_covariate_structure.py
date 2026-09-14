from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workflow" / "scripts"))
import check_covariate_structure as ccs  # noqa: E402


def _samples(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["sample_id", "condition", "layout", "fastq_1", "batch", "site"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})


def _pca(path: Path, coords: dict[str, tuple[float, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "PC1", "PC2"])
        for sid, (pc1, pc2) in coords.items():
            writer.writerow([sid, pc1, pc2])


def test_batch_explaining_pc1_is_review_required():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "batch": ["x", "x", "y", "y"],
        "site": ["p", "q", "p", "q"],
    }
    pca = {"s1": (-10.0, 1.0), "s2": (-9.0, -1.0), "s3": (10.0, 1.0), "s4": (9.0, -1.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "REVIEW_REQUIRED"
    batch_msgs = [m for m in result["messages"] if "batch" in m["message"]]
    assert batch_msgs and batch_msgs[0]["status"] == "REVIEW_REQUIRED"
    assert "aliased" in batch_msgs[0]["message"]


def test_incidental_column_passes():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "site": ["p", "q", "p", "q"],
    }
    pca = {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03), "s4": (9.0, 0.06)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "PASS"
    assert any("site" in m["message"] for m in result["messages"])


def test_column_in_design_formula_is_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "batch": ["x", "x", "y", "y"],
    }
    pca = {"s1": (-10.0, 0.0), "s2": (-9.0, 0.0), "s3": (10.0, 0.0), "s4": (9.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ batch + condition", "condition")
    assert not any("batch" in m["message"] for m in result["messages"])


def test_single_level_column_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "organism": ["human", "human", "human", "human"],
    }
    pca = {"s1": (-1.0, 0.0), "s2": (-1.0, 0.0), "s3": (1.0, 0.0), "s4": (1.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert not any("organism" in m["message"] for m in result["messages"])


def test_sample_level_column_skipped():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "A", "B", "B"],
        "notes": ["n1", "n2", "n3", "n4"],
    }
    pca = {"s1": (-1.0, 0.0), "s2": (-1.0, 0.0), "s3": (1.0, 0.0), "s4": (1.0, 0.0)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert not any("notes" in m["message"] for m in result["messages"])


def test_negative_wrong_column_set_hides_the_batch_effect():
    # Feeding the check a column set that omits the batch column that actually explains PC1
    # must not surface REVIEW_REQUIRED -- this is the gate's own negative test.
    samples = ["s1", "s2", "s3", "s4"]
    columns = {"condition": ["A", "A", "B", "B"], "site": ["p", "q", "p", "q"]}
    pca = {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03), "s4": (9.0, 0.06)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "PASS"


def test_pasilla_replicate_two_levels_four_samples_is_pass_not_review_required():
    # H4: raw R^2 has a null expectation of (k-1)/(n-1); a 2-level, 4-sample column at
    # raw R^2=0.69 was the defect this fix exists to catch -- it must now be PASS, with
    # the adjusted R^2 and F-test p-value printed rather than hidden.
    samples = ["s1", "s2", "s3", "s4"]
    columns = {
        "condition": ["A", "B", "A", "B"],
        "replicate": ["1", "1", "2", "2"],
    }
    pc2 = {"s1": -0.5, "s2": -2.0, "s3": 1.5, "s4": 0.5}
    pca = {sid: (0.0, pc2[sid]) for sid in samples}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    rep_msgs = [m for m in result["messages"] if "replicate" in m["message"]]
    assert rep_msgs and rep_msgs[0]["status"] == "PASS"
    assert "adjusted R^2" in rep_msgs[0]["message"] and "p=" in rep_msgs[0]["message"]
    assert "numeric column treated as categorical" in rep_msgs[0]["message"]


def test_real_batch_effect_in_six_samples_is_still_flagged():
    samples = [f"s{i}" for i in range(1, 7)]
    columns = {
        "condition": ["A", "A", "A", "B", "B", "B"],
        "batch": ["x", "x", "y", "x", "y", "y"],
    }
    pca = {
        "s1": (-1.0, 10.0), "s2": (-1.2, 9.5), "s3": (-0.9, -9.8),
        "s4": (1.0, 9.6), "s5": (0.8, -10.2), "s6": (1.1, -9.6),
    }
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    batch_msgs = [m for m in result["messages"] if "batch" in m["message"]]
    assert batch_msgs and batch_msgs[0]["status"] == "REVIEW_REQUIRED"
    assert result["status"] == "REVIEW_REQUIRED"


def _descriptive_column_case(values: list[str]) -> dict:
    samples = [f"s{i}" for i in range(1, 7)]
    pca = {"s1": (-10.0, 1.0), "s2": (-9.0, -1.0), "s3": (-9.5, 0.5),
           "s4": (10.0, 1.0), "s5": (9.0, -1.0), "s6": (9.5, -0.5)}
    columns = {
        "condition": ["ctrl", "ctrl", "ctrl", "trt", "trt", "trt"],
        "batch": ["b1", "b1", "b2", "b1", "b2", "b2"],
        "library_name": values,
        "sample_title": list(values),
    }
    return ccs.evaluate(samples, columns, pca, "~ condition", "condition")


@pytest.mark.parametrize("values", [
    # Mirrors the contrast groups: a descriptive label, not a confounder.
    ["control lib", "control lib", "control lib", "treated lib", "treated lib", "treated lib"],
    # Partially filled so that "named" versus blank partitions exactly like the contrast.
    ["HepG2 lib", "HepG2 lib", "HepG2 lib", "", "", ""],
    ["lib A", "lib A", "", "lib B", "lib B", ""],
    ["", "", "", "", "", ""],
    [f"lib {i}" for i in range(6)],
])
def test_descriptive_free_text_columns_are_never_screened(values):
    # Before 0.31.0 the first two shapes reached the perfectly-aliased branch and returned
    # REVIEW_REQUIRED for a column that only names the library.
    result = _descriptive_column_case(values)
    assert not any("library_name" in m["message"] or "sample_title" in m["message"]
                   for m in result["messages"])


def test_a_genuine_technical_covariate_is_still_screened():
    # The same sheet still tests batch: excluding the descriptive columns must not silence
    # the screen. batch is confounded with neither PC here, so it reports PASS by name.
    result = _descriptive_column_case(["lib A", "lib A", "", "lib B", "lib B", ""])
    batch_msgs = [m for m in result["messages"] if "'batch'" in m["message"]]
    assert batch_msgs and "adjusted R^2" in batch_msgs[0]["message"]


def test_end_to_end_sheet_with_library_name_is_not_flagged(tmp_path):
    # Through the script's own CLI and _read_samples, not evaluate() with a hand-built dict:
    # that is the path the pipeline takes.
    samples = tmp_path / "samples.tsv"
    header = ["sample_id", "library_name", "condition", "layout", "fastq_1", "batch"]
    rows = [
        ["s1", "HepG2 lib", "ctrl", "single", "a.fq", "b1"],
        ["s2", "HepG2 lib", "ctrl", "single", "b.fq", "b1"],
        ["s3", "HepG2 lib", "ctrl", "single", "c.fq", "b2"],
        ["s4", "", "trt", "single", "d.fq", "b1"],
        ["s5", "", "trt", "single", "e.fq", "b2"],
        ["s6", "", "trt", "single", "f.fq", "b2"],
    ]
    with samples.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="	")
        writer.writerow(header)
        writer.writerows(rows)
    pca = tmp_path / "pca.csv"
    _pca(pca, {"s1": (-10.0, 1.0), "s2": (-9.0, -1.0), "s3": (-9.5, 0.5),
               "s4": (10.0, 1.0), "s5": (9.0, -1.0), "s6": (9.5, -0.5)})
    out = tmp_path / "check.json"
    argv = ["check_covariate_structure", "--pca", str(pca), "--samples", str(samples),
            "--design", "~ condition", "--out", str(out)]
    old_argv = sys.argv
    sys.argv = argv
    try:
        assert ccs.main() == 0
    finally:
        sys.argv = old_argv
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert not any("library_name" in m["message"] for m in payload["messages"])
    assert any("'batch'" in m["message"] for m in payload["messages"])


def test_excluded_columns_match_app_constants_schema():
    constants_path = Path(__file__).resolve().parents[1] / "app" / "constants.py"
    text = constants_path.read_text(encoding="utf-8")
    required = re.search(r"REQUIRED_METADATA_COLUMNS\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    optional = re.search(r"OPTIONAL_METADATA_COLUMNS\s*=\s*\[(.*?)\]", text, re.DOTALL).group(1)
    schema_columns = set(re.findall(r'"([^"]+)"', required)) | set(re.findall(r'"([^"]+)"', optional))
    assert ccs.EXCLUDED_COLUMNS.issubset(schema_columns)


# --- n-aware effect-size floor (0.31.0) -------------------------------------------------

import importlib.util  # noqa: E402
import math  # noqa: E402
import random  # noqa: E402

_HTML_REPORT = Path(__file__).resolve().parents[1] / "workflow" / "scripts" / "make_html_report.py"

# The 0.30.1 rule: a fixed adjusted-R^2 floor that only bound at small n.
OLD_ADJ_R2_REVIEW_THRESHOLD = 0.5


def _old_rule(a: dict) -> bool:
    return a["adj_r2"] > OLD_ADJ_R2_REVIEW_THRESHOLD and a["p"] < ccs.P_REVIEW_THRESHOLD


def _load_html_report():
    spec = importlib.util.spec_from_file_location("covariate_msgs_html_report", _HTML_REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _balanced_two_group_pc(n: int, target_r2: float) -> tuple[list[float], list[str]]:
    # Residual pattern with zero mean inside each group, so the between-group sum of squares
    # comes only from the group offset d; d is then solved for the requested R^2 rather than
    # tuned by hand: R^2 = n*d^2 / (n*d^2 + SSW).
    half = n // 2
    levels = ["x"] * half + ["y"] * half
    within = [i - (half - 1) / 2 for i in range(half)]
    ssw = 2 * sum(v * v for v in within)
    d = math.sqrt(target_r2 / (1.0 - target_r2) * ssw / n)
    values = [d + v for v in within] + [-d + v for v in within]
    return values, levels


def test_chance_ceiling_reproduces_published_f_quantiles():
    # Two methods that can disagree: the module's own p-value round-trip, and printed
    # F(0.95) table values (the quantile is the specification, so the table is the oracle).
    published = {(1, 1): 161.4, (1, 2): 18.513, (1, 4): 7.709, (1, 16): 4.494,
                 (2, 1): 199.5, (2, 3): 9.552, (2, 9): 4.256}
    for (df1, df2), table in published.items():
        q = ccs._f_quantile(0.05, df1, df2)
        assert abs(q - table) <= 0.05 + 0.001 * table
        assert abs(ccs._f_test_pvalue(q, df1, df2) - 0.05) < 1e-6
    # The floor is that quantile mapped through adj_r2 = 1 - (n-1)/(F*df1 + df2).
    for n, k in [(4, 2), (6, 3), (12, 2), (18, 2)]:
        df1, df2 = k - 1, n - k
        floor = ccs.chance_adj_r2(n, df1, df2)
        assert abs(floor - (1.0 - (n - 1) / (ccs._f_quantile(0.05, df1, df2) * df1 + df2))) < 1e-12
    # It has to fall with n: that is the whole point of replacing the fixed 0.5.
    floors = [ccs.chance_adj_r2(n, 1, n - 2) for n in (4, 6, 8, 12, 18, 24)]
    assert floors == sorted(floors, reverse=True)


def test_moderate_effect_fires_under_the_n_aware_floor_but_not_under_the_old_fixed_floor():
    # The two documented misses of the 0.30.1 rule, reproduced from their (n, R^2) alone.
    for n, target_r2 in [(18, 0.35), (12, 0.45)]:
        values, levels = _balanced_two_group_pc(n, target_r2)
        a = ccs._anova(values, levels)
        assert abs(a["r2"] - target_r2) < 1e-9
        fires, floor = ccs.screen_column(a, n)
        assert a["p"] < ccs.P_REVIEW_THRESHOLD
        assert a["adj_r2"] > floor
        assert fires
        assert not _old_rule(a), (n, a["adj_r2"])


def test_moderate_effect_at_n18_is_review_required_end_to_end():
    n, target_r2 = 18, 0.35
    values, levels = _balanced_two_group_pc(n, target_r2)
    rng = random.Random(4242)
    samples = [f"s{i}" for i in range(n)]
    columns = {"condition": ["A", "B"] * (n // 2), "batch": levels}
    pca = {s: (values[i], rng.gauss(0.0, 0.01)) for i, s in enumerate(samples)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    batch = [m for m in result["messages"] if "'batch'" in m["message"]]
    assert batch and batch[0]["status"] == "REVIEW_REQUIRED"
    assert result["status"] == "REVIEW_REQUIRED"
    assert f"n={n}" in batch[0]["message"] and "chance ceiling" in batch[0]["message"]


def test_permuted_labels_do_not_fire_and_the_null_rate_is_calibrated():
    n = 18
    values, levels = _balanced_two_group_pc(n, 0.35)
    shuffled = list(levels)
    random.Random(11).shuffle(shuffled)
    fires, _ = ccs.screen_column(ccs._anova(values, shuffled), n)
    assert not fires

    # Monte Carlo of the per-column decision under the exact null: each replicate redraws the
    # PC vector (standard normal) and reshuffles a balanced label vector, so this measures the
    # rule's own false-positive rate rather than one fixed sample's permutation distribution.
    # Band: 10,000 reps put the binomial SE at sqrt(.05*.95/10000)=0.0022, so +-0.015 is ~6.9
    # SE -- wide enough that seed noise cannot fail a rule that is in fact calibrated.
    reps = 10000
    rng = random.Random(20260913)
    base = ["x"] * (n // 2) + ["y"] * (n // 2)
    hits = 0
    for _ in range(reps):
        pc = [rng.gauss(0.0, 1.0) for _ in range(n)]
        labels = list(base)
        rng.shuffle(labels)
        if ccs.screen_column(ccs._anova(pc, labels), n)[0]:
            hits += 1
    rate = hits / reps
    assert 0.035 <= rate <= 0.065, rate


def test_uia_validation_replicate_column_still_passes():
    # Read-only fixture reproduced from the real project BulkSeqProjects/uia_validation:
    # 6 yeast samples, design ~ batch + condition, replicate column at F(2,3)=3.56, p=0.161.
    samples = ["control_1", "control_2", "control_3", "treated_1", "treated_2", "treated_3"]
    columns = {
        "condition": ["control", "control", "control", "treated", "treated", "treated"],
        "layout": [""] * 6,
        "replicate": ["1", "2", "3", "1", "2", "3"],
        "batch": ["1", "2", "3", "1", "2", "3"],
    }
    pca = {
        "control_1": (-25.3329155474702, 2.55244862388874),
        "control_2": (-24.9535569550814, 1.88670823664112),
        "control_3": (-24.9651642742998, -4.40825591316447),
        "treated_1": (25.0216105359973, -1.83334122317251),
        "treated_2": (25.5418411222557, 3.58737558444951),
        "treated_3": (24.6881851185984, -1.78493530864239),
    }
    result = ccs.evaluate(samples, columns, pca, "~ batch + condition", "condition")
    rep = [m for m in result["messages"] if "'replicate'" in m["message"]]
    assert rep and rep[0]["status"] == "PASS"
    assert result["status"] == "PASS"
    assert "F(2,3)=3.56" in rep[0]["message"] and "p=0.161" in rep[0]["message"]


def test_sample_ids_missing_from_the_pca_table_are_warning_not_pass():
    samples = ["s1", "s2", "s3", "s4"]
    columns = {"condition": ["A", "A", "B", "B"], "site": ["p", "q", "p", "q"]}
    pca = {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03)}
    result = ccs.evaluate(samples, columns, pca, "~ condition", "condition")
    assert result["status"] == "WARNING"
    assert len(result["messages"]) == 1
    assert "not assessed" in result["messages"][0]["message"]
    assert not any(m["status"] == "PASS" for m in result["messages"])


def _every_check23_message() -> list[dict[str, str]]:
    samples = ["s1", "s2", "s3", "s4"]
    pca = {"s1": (-10.0, 1.0), "s2": (-9.0, -1.0), "s3": (10.0, 1.0), "s4": (9.0, -1.0)}
    scenarios = [
        ({"condition": ["A", "A", "B", "B"], "batch": ["x", "x", "y", "y"],
          "site": ["p", "q", "p", "q"]}, pca, "~ condition"),
        ({"condition": ["A", "A", "B", "B"]}, pca, "~ condition"),
        ({"condition": ["A", "A", "B", "B"], "site": ["p", "q", "p", "q"]},
         {"s1": (-10.0, 0.05), "s2": (-9.0, -0.08), "s3": (10.0, -0.03), "s4": (9.0, 0.06)},
         "~ condition"),
        ({"condition": ["A", "A", "B", "B"], "site": ["p", "q", "p", "q"]},
         {"s1": (-10.0, 1.0)}, "~ condition"),
    ]
    out = []
    for columns, coords, design in scenarios:
        out.extend(ccs.evaluate(samples, columns, coords, design, "condition")["messages"])
    n18, levels = _balanced_two_group_pc(18, 0.35)
    ids = [f"s{i}" for i in range(18)]
    out.extend(ccs.evaluate(
        ids, {"condition": ["A", "B"] * 9, "batch": levels},
        {s: (n18[i], 0.001 * i) for i, s in enumerate(ids)}, "~ condition", "condition")["messages"])
    return out


def test_check23_messages_survive_the_sanity_parser():
    mhr = _load_html_report()
    messages = _every_check23_message()
    assert {m["status"] for m in messages} == {"PASS", "REVIEW_REQUIRED", "WARNING"}
    for m in messages:
        # aggregate_sanity_checks.py writes one line per message: "  - {status}: {message}".
        assert "\n" not in m["message"] and "\r" not in m["message"]
        assert not re.match(r"^(PASS|WARNING|FAIL|REVIEW_REQUIRED):", m["message"])
        text = "\n".join([
            "BulkSeq Studio validation checks", "=" * 31, "Overall: REVIEW_REQUIRED", "",
            f"23_covariate_structure_qc: {m['status']}",
            f"  - {m['status']}: {m['message']}", "",
        ])
        overall, checks = mhr._parse_sanity(text)
        assert overall == "REVIEW_REQUIRED"
        assert len(checks) == 1 and checks[0]["name"] == "23_covariate_structure_qc"
        assert checks[0]["status"] == m["status"]
        assert checks[0]["messages"] == [m["message"]]
