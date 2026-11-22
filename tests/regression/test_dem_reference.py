"""DEM comparison on the reference tracks, from locally cached COP30 tiles.

Never downloads: skipped unless the tiles are already in data/local/dem/.
Documents the ADR-012 finding that DEM-based gain sampled every 20 m is close
to the gain Strava reports (1443 m and 1309 m).
"""

from pathlib import Path

import pytest

from alpha.dem import DemProvider, DemUnavailableError, LocalTileProvider
from alpha.domain.reference_elevation import (
    compare_elevation,
    interpolate_along_track,
    reference_knots,
)
from alpha.gpx.reader import read_gpx

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/local/dem"


@pytest.mark.parametrize(
    ("relative", "dem_ascent_m"),
    [
        ("data/samples/input.gpx", 1404),
        ("data/samples/input2.gpx", 1331),
        ("data/local/Nebelhorn_push.gpx", 1441),
        ("data/local/Gleitweg_Seealpsee_Zeigersattel.gpx", 1311),
    ],
)
def test_dem_ascent_at_20_m_spacing(relative: str, dem_ascent_m: float) -> None:
    path = ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} not present")
    track = read_gpx(path.read_bytes()).tracks[0].track
    knots = reference_knots(track, 20.0)
    try:
        sample = DemProvider(CACHE, "COP30", api_key=None).sample(
            track.lat_deg[knots], track.lon_deg[knots]
        )
    except DemUnavailableError:
        pytest.skip("COP30 tiles not cached locally")
    series = interpolate_along_track(track, knots, sample.elevation_m)
    c = compare_elevation(track, series, 3.0, 3.0)
    assert c.ascent_reference_m == pytest.approx(dem_ascent_m, abs=5)
    assert c.reference_coverage == 1.0
    assert abs(c.median_difference_m or 0) < 10  # no gross datum/alignment error


@pytest.mark.parametrize(
    ("relative", "dgm1_ascent_m", "drift_warning"),
    [
        # ADR-013/014: canopy effect on Gleitweg; barometer drift on Nebelhorn.
        ("data/local/Nebelhorn_push.gpx", 1436, True),
        ("data/local/Gleitweg_Seealpsee_Zeigersattel.gpx", 1239, False),
        # Forest walk: COP30 gives 1080 m, the terrain model 845 m (ADR-013).
        ("data/local/Wanderung_am_Abend.gpx", 845, False),
        ("data/local/Lunch_Hike.gpx", 578, False),
    ],
)
def test_terrain_model_ascent_and_drift(
    relative: str, dgm1_ascent_m: float, drift_warning: bool
) -> None:
    path = ROOT / relative
    if not path.exists():
        pytest.skip(f"{relative} not present")
    track = read_gpx(path.read_bytes()).tracks[0].track
    knots = reference_knots(track, 20.0)
    try:
        sample = LocalTileProvider(CACHE / "BY_DGM1", "BY_DGM1").sample(
            track.lat_deg[knots], track.lon_deg[knots]
        )
    except DemUnavailableError:
        pytest.skip("BY_DGM1 tiles not present locally")
    series = interpolate_along_track(track, knots, sample.elevation_m)
    c = compare_elevation(track, series, 3.0, 3.0)
    assert c.ascent_reference_m == pytest.approx(dgm1_ascent_m, abs=5)
    assert bool(c.warnings) is drift_warning
