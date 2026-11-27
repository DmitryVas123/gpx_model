"""Golden analysis outputs for the reference files.

Any change in calculated output fails these tests. If the change is intended:
bump ALGORITHM_VERSION when calculations changed, then run

    python -m pytest tests/regression --update-golden

and review the diff of the golden files before committing.

All reference GPX files are personal and live in data/local/ (git-ignored),
so their golden files live in data/local/golden/ too. On a fresh clone these
tests are skipped; the synthetic fixtures cover the same code paths.
"""

import difflib
from pathlib import Path

import pytest

from alpha.domain.analysis import analyse
from alpha.gpx.reader import read_gpx
from alpha.report import file_report, to_json
from tests.references import GOLDEN, reference

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# Reference keys (tests/references.py); goldens are data/local/golden/<key>.json.
CASES = ["plan_1", "plan_2", "activity_a", "activity_b"]


def render(path: Path) -> str:
    data = path.read_bytes()
    doc = read_gpx(data)
    results = [(source, analyse(source.track)) for source in doc.tracks]
    return to_json(file_report(path.name, data, doc, results))


@pytest.mark.parametrize("key", CASES)
def test_matches_golden(key: str, update_golden: bool) -> None:
    source = reference(key)
    golden = GOLDEN / f"{key}.json"
    actual = render(source)

    if update_golden:
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8", newline="\n")
        return
    if not golden.exists():
        pytest.fail(f"no golden file {golden}; run pytest with --update-golden")

    expected = golden.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                "golden",
                "actual",
                n=1,
            )
        )
        pytest.fail(f"analysis of {key} changed:\n{diff[:4000]}")


def test_render_is_deterministic() -> None:
    for path in (FIXTURES / "route_only.gpx", FIXTURES / "activity_two_segments.gpx"):
        assert render(path) == render(path)
