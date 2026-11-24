"""Enforce the dependency rules from ARCHITECTURE.md §1 and CLAUDE.md §4."""

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "alpha"

# pyproj is used only for pure geodesic math (no I/O); see ARCHITECTURE.md §1.
DOMAIN_ALLOWED_THIRD_PARTY = {"numpy", "pyproj"}


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imports(path: Path) -> Iterator[tuple[str, int]]:
    """Yield (absolute-or-relative module name, relative level) for each import."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


def test_source_tree_exists() -> None:
    assert (SRC / "domain").is_dir()
    assert (SRC / "gpx").is_dir()


@pytest.mark.parametrize("path", _python_files(SRC / "domain"), ids=lambda p: p.name)
def test_domain_imports_only_stdlib_numpy_and_domain(path: Path) -> None:
    for module, level in _imports(path):
        if level > 0:
            # Relative imports must stay inside the domain package.
            depth_in_domain = len(path.relative_to(SRC / "domain").parts) - 1
            assert level <= depth_in_domain + 1, (
                f"{path.name}: relative import escapes alpha.domain"
            )
            continue
        top = module.split(".")[0]
        if top == "alpha":
            assert module == "alpha.domain" or module.startswith("alpha.domain."), (
                f"{path.name}: domain must not import {module}"
            )
        else:
            assert (
                top in sys.stdlib_module_names or top in DOMAIN_ALLOWED_THIRD_PARTY
            ), f"{path.name}: domain must not import {module}"


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_gpxpy_only_imported_by_gpx_adapter(path: Path) -> None:
    if (SRC / "gpx") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] != "gpxpy", (
            f"{path.relative_to(SRC)}: gpxpy may only be imported in alpha.gpx"
        )


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_sqlalchemy_only_imported_by_persistence(path: Path) -> None:
    if (SRC / "persistence") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] != "sqlalchemy", (
            f"{path.relative_to(SRC)}: sqlalchemy may only be imported in "
            "alpha.persistence"
        )


# Inner layers must not depend on outer ones (ARCHITECTURE.md §1).
FORBIDDEN_LAYER_IMPORTS = {
    "persistence": ("alpha.application", "alpha.cli", "alpha.gpx", "alpha.report"),
    "gpx": ("alpha.application", "alpha.cli", "alpha.persistence", "alpha.report"),
    "application": ("alpha.cli", "alpha.api", "alpha.report"),
}

# Fitting is pure: it sees samples, never storage, files, or the network
# (ADR-005, ADR-016). The domain never imports it.
MODELLING_ALLOWED = (
    "alpha.domain",
    "alpha.modelling",
    "numpy",
    "math",
    "dataclasses",
    "datetime",
    "enum",
    "collections",
    "typing",
    "__future__",
)


def test_modelling_is_pure() -> None:
    for path in _python_files(SRC / "modelling"):
        for module, level in _imports(path):
            assert level == 0, f"{path.name}: use absolute imports"
            assert module.startswith(MODELLING_ALLOWED), (
                f"alpha.modelling/{path.name} must not import {module}"
            )
    for path in _python_files(SRC / "domain"):
        for module, _ in _imports(path):
            assert not module.startswith("alpha.modelling"), (
                f"alpha.domain/{path.name} must not import {module}"
            )


@pytest.mark.parametrize("layer", sorted(FORBIDDEN_LAYER_IMPORTS))
def test_layers_do_not_import_outer_layers(layer: str) -> None:
    forbidden = FORBIDDEN_LAYER_IMPORTS[layer]
    for path in _python_files(SRC / layer):
        for module, level in _imports(path):
            assert level == 0, f"{path.name}: use absolute imports across layers"
            assert not module.startswith(forbidden), (
                f"alpha.{layer}/{path.name} must not import {module}"
            )


WEB_PACKAGES = ("fastapi", "pydantic", "starlette", "uvicorn")


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_web_frameworks_only_in_api(path: Path) -> None:
    if (SRC / "api") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] not in WEB_PACKAGES, (
            f"{path.relative_to(SRC)}: {module} may only be imported in alpha.api"
        )


NETWORK = ("urllib", "http", "socket", "requests", "httpx")


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_rasters_only_in_dem_adapter(path: Path) -> None:
    """Raster access stays in the DEM adapter (ADR-012)."""
    if (SRC / "dem") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] != "rasterio", (
            f"{path.relative_to(SRC)}: rasterio may only be imported in alpha.dem"
        )


@pytest.mark.parametrize("path", _python_files(SRC), ids=lambda p: p.name)
def test_network_only_in_adapters(path: Path) -> None:
    """Network access only in the opt-in adapters: DEM downloads (ADR-012) and
    the local language model (ADR-020)."""
    if (SRC / "dem") in path.parents or (SRC / "llm") in path.parents:
        return
    for module, _ in _imports(path):
        assert module.split(".")[0] not in NETWORK, (
            f"{path.relative_to(SRC)}: {module} may only be imported in alpha.dem "
            "or alpha.llm"
        )


def test_explain_is_pure() -> None:
    """Explanations are built from report JSON only (ADR-020)."""
    allowed = (
        "alpha.explain",
        "re",
        "json",
        "math",
        "dataclasses",
        "typing",
        "collections",
        "__future__",
    )
    for path in _python_files(SRC / "explain"):
        for module, level in _imports(path):
            assert level == 0, f"{path.name}: use absolute imports"
            assert any(module == a or module.startswith(a + ".") for a in allowed), (
                f"alpha.explain/{path.name} must not import {module}"
            )


def test_llm_adapter_only_talks_to_this_computer() -> None:
    import pytest as _pytest

    from alpha.llm import OllamaRewriter

    for url in (
        "http://example.org:11434",
        "https://127.0.0.1:11434",
        "http://10.0.0.5",
    ):
        with _pytest.raises(ValueError, match="local"):
            OllamaRewriter(base_url=url)
    OllamaRewriter(base_url="http://localhost:11434")
