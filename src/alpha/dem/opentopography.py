"""OpenTopography global DEM API client (network access lives only here).

The API key is sent as a query parameter, as the API requires; it is never
logged, stored, or included in error messages.
"""

import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://portal.opentopography.org/API/globaldem"
TIMEOUT_S = 120
MAX_TILE_BYTES = 64 * 1024 * 1024


class DemDownloadError(Exception):
    """The provider did not deliver a tile. The message never contains the key."""


def fetch_geotiff(
    dataset_id: str,
    bounds: tuple[float, float, float, float],
    api_key: str,
    timeout_s: float = TIMEOUT_S,
) -> bytes:
    south, north, west, east = bounds
    query = urllib.parse.urlencode(
        {
            "demtype": dataset_id,
            "south": f"{south:.6f}",
            "north": f"{north:.6f}",
            "west": f"{west:.6f}",
            "east": f"{east:.6f}",
            "outputFormat": "GTiff",
            "API_Key": api_key,
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{query}", headers={"User-Agent": "alpha-gpx (local analysis tool)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            data: bytes = response.read(MAX_TILE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace").strip()
        detail = detail.replace(api_key, "***")
        if exc.code in (401, 403):
            raise DemDownloadError(
                f"OpenTopography rejected the API key (HTTP {exc.code})"
            ) from None
        raise DemDownloadError(
            f"OpenTopography returned HTTP {exc.code}: {detail[:200]}"
        ) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DemDownloadError(f"cannot reach OpenTopography: {reason}") from None

    if len(data) > MAX_TILE_BYTES:
        raise DemDownloadError("tile larger than expected; refusing to store it")
    if not (data.startswith(b"II*\x00") or data.startswith(b"MM\x00*")):
        text = data[:300].decode("utf-8", "replace").replace(api_key, "***")
        raise DemDownloadError(f"OpenTopography did not return a GeoTIFF: {text}")
    return data
