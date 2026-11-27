"""Collect the documentation into build/site_src/ for MkDocs (GitHub Pages).

The Markdown files in the repository root and docs/adr/ stay the single
source. This script copies them under site page names and rewrites the
links between them, so they work both on GitHub and on the site:

    python scripts/build_site_docs.py && mkdocs build --strict

It fails when an ADR exists that mkdocs.yml does not list, so a new decision
record cannot silently disappear from the site.
"""

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "site_src"

# Repository file -> site page.
PAGES = {
    "README.md": "index.md",
    "PROJECT_VISION.md": "vision.md",
    "DATA_MODEL.md": "data-model.md",
    "ARCHITECTURE.md": "architecture.md",
    "MVP_ROADMAP.md": "roadmap.md",
    "CLAUDE.md": "ai-assistants.md",
}
LINK = re.compile(r"\]\((?P<target>[^)#\s]+)(?P<anchor>#[^)\s]*)?\)")


def _site_target(target: str, page_dir: str) -> str | None:
    """Where a repository link points on the site (None: leave unchanged)."""
    if target.startswith(("http://", "https://", "mailto:")):
        return None
    name = Path(target).name
    if target in PAGES or (name in PAGES and "/" not in target):
        page = PAGES[name]
        return page if page_dir == "" else f"../{page}"
    if target.endswith("docs/adr/README.md") or (
        page_dir == "adr" and target == "README.md"
    ):
        return "adr/index.md" if page_dir == "" else "index.md"
    if re.fullmatch(r"(?:docs/adr/)?\d{3}-[\w-]+\.md", target):
        return f"adr/{name}" if page_dir == "" else name
    return None


def _rewrite(text: str, page_dir: str) -> str:
    def replace(m: re.Match[str]) -> str:
        new = _site_target(m.group("target"), page_dir)
        if new is None:
            return m.group(0)
        return f"]({new}{m.group('anchor') or ''})"

    return LINK.sub(replace, text)


def _listed_adrs() -> set[str]:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    return set(re.findall(r"adr/(\d{3}-[\w-]+\.md)", config))


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "adr").mkdir(parents=True)
    for source, page in PAGES.items():
        text = (ROOT / source).read_text(encoding="utf-8")
        (OUT / page).write_text(_rewrite(text, ""), encoding="utf-8", newline="\n")
    adrs = sorted((ROOT / "docs" / "adr").glob("*.md"))
    for path in adrs:
        page = "index.md" if path.name == "README.md" else path.name
        text = path.read_text(encoding="utf-8")
        (OUT / "adr" / page).write_text(
            _rewrite(text, "adr"), encoding="utf-8", newline="\n"
        )
    missing = {p.name for p in adrs if p.name != "README.md"} - _listed_adrs()
    if missing:
        print(f"ADRs missing from mkdocs.yml nav: {', '.join(sorted(missing))}")
        return 1
    print(f"wrote {len(PAGES) + len(adrs)} pages to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
