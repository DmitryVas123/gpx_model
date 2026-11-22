"""Digital elevation model datasets and their provenance (ADR-012, ADR-013)."""

from dataclasses import dataclass
from typing import Literal

DemSource = Literal["opentopography", "local"]


@dataclass(frozen=True)
class DemDataset:
    id: str  # OpenTopography ``demtype`` for downloaded datasets
    name: str
    resolution_m: float  # nominal ground resolution
    surface: str  # "surface" (DSM: may include trees/buildings) or "terrain" (DTM)
    vertical_datum: str
    horizontal_crs: str  # as published; local tiles are read in their own CRS
    attribution: str  # must be shown wherever results derived from it are shown
    source: DemSource
    # Local datasets: where the user obtains the GeoTIFF tiles. They are read
    # from disk; ``alpha get-tiles`` can download them from a metalink.
    obtain: str | None = None
    # Local datasets: hosts a metalink may point to (``alpha get-tiles``).
    download_hosts: tuple[str, ...] = ()


DATASETS: dict[str, DemDataset] = {
    "COP30": DemDataset(
        id="COP30",
        name="Copernicus DEM GLO-30",
        resolution_m=30.0,
        surface="surface",
        vertical_datum="EGM2008",
        horizontal_crs="EPSG:4326",
        attribution=(
            "Copernicus DEM GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and "
            "Space GmbH 2014-2018, provided under COPERNICUS by the European Union "
            "and ESA; all rights reserved. Retrieved via OpenTopography."
        ),
        source="opentopography",
    ),
    # Bavarian open-data terrain model (bare earth: no trees or buildings),
    # read from GeoTIFF tiles the user downloads (ADR-013).
    "BY_DGM1": DemDataset(
        id="BY_DGM1",
        name="Digitales Geländemodell 1 m Bayern (DGM1)",
        resolution_m=1.0,
        surface="terrain",
        vertical_datum="DHHN2016",
        horizontal_crs="EPSG:25832",
        attribution=(
            "Datenquelle: Bayerische Vermessungsverwaltung \N{EN DASH} "
            "www.geodaten.bayern.de (CC BY 4.0)"
        ),
        source="local",
        obtain="DGM1 GeoTIFF tiles from geodaten.bayern.de/opengeodata",
        download_hosts=("download1.bayernwolke.de", "download2.bayernwolke.de"),
    ),
}
DEFAULT_DATASET = "COP30"
