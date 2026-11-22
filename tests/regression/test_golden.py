"""Golden analysis outputs for the reference files.

Any change in calculated output fails these tests. If the change is intended:
bump ALGORITHM_VERSION when calculations changed, then run

    python -m pytest tests/regression --update-golden

and review the diff of the golden files before committing.

Golden files for the shareable samples are committed in tests/regression/golden/.
Golden files for personal activities live in data/local/golden/ (git-ignored)
because they are derived from personal data.
"""

import difflib
from pathlib import Path

import pytest

from alpha.domain.analysis import analyse
from alpha.gpx.reader import read_gpx
from alpha.report import file_report, to_json

ROOT = Path(__file__).resolve().parents[2]
COMMITTED_GOLDEN = Path(__file__).resolve().parent / "golden"
LOCAL_GOLDEN = ROOT / "data" / "local" / "golden"

CASES = [
    ("data/samples/input.gpx", COMMITTED_GOLDEN),
    ("data/samples/input2.gpx", COMMITTED_GOLDEN),
    ("data/local/Nebelhorn_push.gpx", LOCAL_GOLDEN),
    ("data/local/Gleitweg_Seealpsee_Zeigersattel.gpx", LOCAL_GOLDEN),
]


def render(path: Path) -> str:
    data = path.read_bytes()
    doc = read_gpx(data)
    results = [(source, analyse(source.track)) for source in doc.tracks]
    return to_json(file_report(path.name, data, doc, results))


@pytest.mark.parametrize(
    ("relative", "golden_dir"), CASES, ids=[Path(c[0]).stem for c in CASES]
)
def test_matches_golden(relative: str, golden_dir: Path, update_golden: bool) -> None:
    source = ROOT / relative
    if not source.exists():
        pytest.skip(f"{relative} not present (personal data is not in Git)")
    golden = golden_dir / f"{source.stem}.json"
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
        pytest.fail(f"analysis of {relative} changed:\n{diff[:4000]}")


def test_render_is_deterministic() -> None:
    path = ROOT / "data/samples/input.gpx"
    assert render(path) == render(path)
