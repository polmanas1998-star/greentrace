"""Whisp (FAO / OpenForis, Forest Data Partnership): one plot through its public API, only with WHISP_API_KEY.

The API as its code shows it (github.com/forestdatapartnership/whisp-app, api/src, read on 24/10/2026):
    POST https://whisp.openforis.org/api/submit/geojson     header X-API-KEY, body a GeoJSON FeatureCollection
                                                            and "analysisOptions": {"unitType": "ha"}
    -> {"code": "analysis_completed", "data": <FeatureCollection, one Feature per plot>, "context": {"token"}}
       or {"code": "analysis_queued" | "analysis_processing", "context": {"token"}} while it runs, then
    GET  https://whisp.openforis.org/api/status/{token}      same header, same envelope
An error answers {"code": "auth_invalid_api_key" | ..., "message": ...} with its HTTP status.

What is read in the result (openforis_whisp/risk.py): Ind_01_treecover ("yes"/"no": tree cover in 2020 in any of
the datasets Whisp combines), Ind_04_disturbance_after_2020 ("yes"/"no"), risk_pcrop (perennial crops such as
cocoa: "low", "high" or "more_info_needed") and EUFO_2020 (the JRC GFC2020 forest, in ha). Whisp runs on
Google Earth Engine on its side; nothing here needs an Earth Engine account.
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from shapely.geometry import mapping

from app.checks import landcover
from app.geo import settings

Transport = Callable[[str, str, dict, Optional[bytes]], tuple[int, Any]]
NAME = "Whisp (FAO/OpenForis)"
RUNNING = {"analysis_queued", "analysis_processing"}


class WhispUnavailable(RuntimeError):
    """Whisp did not give a result: the reason is the message."""


def api_key(env: Optional[dict] = None) -> Optional[str]:
    return ((env if env is not None else os.environ).get("WHISP_API_KEY") or "").strip() or None


def default_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=settings.WHISP_TIMEOUT_S) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        except ValueError:
            return error.code, {"code": f"http_{error.code}", "message": str(error)}
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise WhispUnavailable(f"{url}: {error}") from error


def feature_collection(plot: dict) -> dict[str, Any]:
    """The plot's outline (a point-declared plot is the disk of its area, as the rules measure it)."""
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"plot_id": plot["plot_id"]},
                          "geometry": mapping(landcover.outline(plot))}],
            "analysisOptions": {"unitType": "ha", "externalIdColumn": "plot_id"}}


def analyse(plot: dict, key: str, transport: Transport = default_transport,
            sleep: Callable[[float], None] = time.sleep, base: str = settings.WHISP_URL) -> dict[str, Any]:
    """The properties Whisp gives the plot; WhispUnavailable with the reason otherwise."""
    headers = {"X-API-KEY": key, "Content-Type": "application/json", "Accept": "application/json"}
    status, answer = transport("POST", f"{base}/submit/geojson", headers,
                               json.dumps(feature_collection(plot)).encode("utf-8"))
    waited = 0.0
    while isinstance(answer, dict) and answer.get("code") in RUNNING:
        token = (answer.get("context") or {}).get("token") or (answer.get("data") or {}).get("token")
        if not token or waited >= settings.WHISP_TIMEOUT_S:
            raise WhispUnavailable(f"the analysis was still {answer.get('code')} after {waited:g} s")
        sleep(settings.WHISP_POLL_S)
        waited += settings.WHISP_POLL_S
        status, answer = transport("GET", f"{base}/status/{token}", headers, None)
    if not isinstance(answer, dict) or answer.get("code") != "analysis_completed":
        code = answer.get("code") if isinstance(answer, dict) else None
        message = answer.get("message") if isinstance(answer, dict) else None
        raise WhispUnavailable(f"HTTP {status}, {code or 'no code'}: {message or 'no message'}")
    features = (answer.get("data") or {}).get("features") or []
    if len(features) != 1 or not isinstance(features[0].get("properties"), dict):
        raise WhispUnavailable(f"expected one Feature in the result, got {len(features)}")
    return features[0]["properties"]


def reading(properties: dict[str, Any]) -> tuple[str, Optional[bool], str]:
    """(says, supports "forest in 2020 lost after 2020", detail) from Whisp's indicators."""
    cover, after = properties.get("Ind_01_treecover"), properties.get("Ind_04_disturbance_after_2020")
    detail = (f"Ind_01_treecover={cover}, Ind_04_disturbance_after_2020={after}, "
              f"risk_pcrop={properties.get('risk_pcrop')}, EUFO_2020={properties.get('EUFO_2020')} ha")
    if cover == "no":
        return "no_tree_cover_2020", False, detail
    if cover == "yes" and after == "yes":
        return "tree_cover_2020_and_disturbance_after_2020", True, detail
    if cover == "yes" and after == "no":
        return "tree_cover_2020_no_disturbance_after_2020", False, detail
    return "no_data", None, detail
