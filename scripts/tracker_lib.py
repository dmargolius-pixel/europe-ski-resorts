"""Shared resort configuration, Open-Meteo HTTP client, climatology lookup, and validation."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from typing import Any

USER_AGENT = "EuropeSki-UnifiedTracker/3.0"

# True summit/glacier coordinates, operational mid-mountain hubs, and glacier sector flags:
# - glacier_peak: True if the summit has a glacier / perennial snowfield
# - glacier_mid:  True if the mid-mountain hub is an active autumn glacier/snow-farming sector
RESORT_GEO_OVERRIDES: dict[str, dict[str, Any]] = {
    # Austria
    "kaprun": {"peak_lat": 47.1883, "peak_lon": 12.6875, "mid_elev": 2450, "glacier_peak": True, "glacier_mid": True},
    "stubai-glacier": {"peak_lat": 46.9792, "peak_lon": 11.1108, "mid_elev": 2620, "glacier_peak": True, "glacier_mid": True},
    "solden": {"peak_lat": 46.9242, "peak_lon": 10.9161, "mid_elev": 2675, "glacier_peak": True, "glacier_mid": True},
    "obergurgl": {"peak_lat": 46.8450, "peak_lon": 11.0560, "glacier_peak": False, "glacier_mid": False},
    "st-anton": {"peak_lat": 47.1578, "peak_lon": 10.2131, "glacier_peak": False, "glacier_mid": False},
    "ischgl": {"peak_lat": 46.9422, "peak_lon": 10.3200, "glacier_peak": False, "glacier_mid": False},
    "mayrhofen": {"peak_lat": 47.1330, "peak_lon": 11.8050, "glacier_peak": False, "glacier_mid": False},
    "zell-am-see": {"peak_lat": 47.3292, "peak_lon": 12.7375, "glacier_peak": False, "glacier_mid": False},
    "kitzbuhel": {"peak_lat": 47.4120, "peak_lon": 12.3600, "glacier_peak": False, "glacier_mid": False},
    "saalbach": {"peak_lat": 47.3620, "peak_lon": 12.6020, "glacier_peak": False, "glacier_mid": False},
    "saalbach-leogang": {"peak_lat": 47.3728, "peak_lon": 12.6203, "glacier_peak": False, "glacier_mid": False},
    "obertauern": {"peak_lat": 47.2620, "peak_lon": 13.5650, "glacier_peak": False, "glacier_mid": False},
    # Switzerland
    "zermatt": {"peak_lat": 45.9383, "peak_lon": 7.7297, "mid_elev": 2939, "glacier_peak": True, "glacier_mid": True},
    "saas-fee": {"peak_lat": 46.0583, "peak_lon": 7.9050, "mid_elev": 2870, "glacier_peak": True, "glacier_mid": True},
    "engelberg": {"peak_lat": 46.7720, "peak_lon": 8.4378, "glacier_peak": True, "glacier_mid": False},
    "verbier": {"peak_lat": 46.0803, "peak_lon": 7.3189, "glacier_peak": True, "glacier_mid": False},
    "crans-montana": {"peak_lat": 46.3820, "peak_lon": 7.5160, "glacier_peak": True, "glacier_mid": False},
    "st-moritz": {"peak_lat": 46.5040, "peak_lon": 9.7950, "glacier_peak": False, "glacier_mid": False},
    "grindelwald": {"peak_lat": 46.5780, "peak_lon": 7.9620, "glacier_peak": False, "glacier_mid": False},
    "adelboden-lenk": {"peak_lat": 46.4250, "peak_lon": 7.5280, "glacier_peak": False, "glacier_mid": False},
    # France
    "tignes": {"peak_lat": 45.4142, "peak_lon": 6.8711, "mid_elev": 2778, "glacier_peak": True, "glacier_mid": True},
    "val-disere": {"peak_lat": 45.4111, "peak_lon": 7.0403, "mid_elev": 2653, "glacier_peak": True, "glacier_mid": False},
    "les-deux-alpes": {"peak_lat": 44.9911, "peak_lon": 6.1783, "mid_elev": 2625, "glacier_peak": True, "glacier_mid": False},
    "chamonix": {"peak_lat": 45.9500, "peak_lon": 6.9600, "mid_elev": 2438, "glacier_peak": True, "glacier_mid": False},
    "val-thorens": {"peak_lat": 45.2750, "peak_lon": 6.6050, "glacier_peak": True, "glacier_mid": False},
    "belle-plagne": {"peak_lat": 45.4920, "peak_lon": 6.7740, "glacier_peak": True, "glacier_mid": False},
    "les-arcs": {"peak_lat": 45.5310, "peak_lon": 6.8490, "glacier_peak": True, "glacier_mid": False},
    "meribel": {"peak_lat": 45.3260, "peak_lon": 6.5920, "glacier_peak": False, "glacier_mid": False},
    "avoriaz": {"peak_lat": 46.1780, "peak_lon": 6.8050, "glacier_peak": False, "glacier_mid": False},
    "isola-2000": {"peak_lat": 44.1750, "peak_lon": 7.1680, "glacier_peak": False, "glacier_mid": False},
    "serre-chevalier": {"peak_lat": 44.9120, "peak_lon": 6.5550, "glacier_peak": False, "glacier_mid": False},
    "flaine": {"peak_lat": 45.9820, "peak_lon": 6.7250, "glacier_peak": False, "glacier_mid": False},
    # Italy & Germany
    "cervinia": {"peak_lat": 45.9383, "peak_lon": 7.7297, "mid_elev": 2812, "glacier_peak": True, "glacier_mid": True},
    "cortina": {"peak_lat": 46.5380, "peak_lon": 12.0620, "glacier_peak": False, "glacier_mid": False},
    "livigno": {"peak_lat": 46.5200, "peak_lon": 10.1100, "glacier_peak": False, "glacier_mid": False},
    "courmayeur": {"peak_lat": 45.7780, "peak_lon": 6.9250, "glacier_peak": False, "glacier_mid": False},
    "garmisch": {"peak_lat": 47.4110, "peak_lon": 10.9850, "glacier_peak": False, "glacier_mid": False},
}


def resolve_resort_geometry(resort: dict[str, Any]) -> dict[str, Any]:
    """Resolve base, mid, and peak coordinates, elevations, and glacier flags."""
    rid = resort["id"]
    base_lat = float(resort["latitude"])
    base_lon = float(resort["longitude"])
    base_elev = int(resort.get("base_elevation", 1500))
    peak_elev = int(resort.get("peak_elevation", 2500))
    override = RESORT_GEO_OVERRIDES.get(rid, {})

    mid_elev = int(override.get("mid_elev", resort.get("mid_elevation", round((base_elev + peak_elev) / 2))))
    peak_lat = float(override.get("peak_lat", base_lat))
    peak_lon = float(override.get("peak_lon", base_lon))
    mid_lat = round((base_lat + peak_lat) / 2.0, 4)
    mid_lon = round((base_lon + peak_lon) / 2.0, 4)

    return {
        "id": rid,
        "name": resort["name"],
        "base_lat": base_lat,
        "base_lon": base_lon,
        "mid_lat": mid_lat,
        "mid_lon": mid_lon,
        "peak_lat": peak_lat,
        "peak_lon": peak_lon,
        "base_elev": base_elev,
        "mid_elev": mid_elev,
        "peak_elev": peak_elev,
        "is_glacier_mid": bool(override.get("glacier_mid", False)),
        "is_glacier_peak": bool(override.get("glacier_peak", peak_elev >= 3200)),
    }


def deduplicate_resorts(
    public_resorts: list[dict[str, Any]],
    personal_resorts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return unique resorts across public and personal lists, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in [*public_resorts, *personal_resorts]:
        rid = entry["id"]
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(entry)
    return unique


def _parse_retry_after(header_value: str | None) -> float | None:
    if not header_value:
        return None
    try:
        seconds = float(header_value.strip())
        return max(1.0, min(120.0, seconds))
    except ValueError:
        return None


def fetch_json(
    url: str,
    timeout: int = 25,
    retries: int = 4,
    base_backoff: float = 2.0,
    max_429_sleep: float = 65.0,
) -> Any:
    """Fetch JSON via urllib with explicit HTTP 429 / Retry-After handling and clear diagnostics."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            last_error = err
            retry_after = _parse_retry_after(err.headers.get("Retry-After") if err.headers else None)
            body_preview = ""
            try:
                body_preview = err.read(240).decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            print(
                f"[WARN] HTTP {err.code} on attempt {attempt + 1}/{retries} ({url[:95]}...): {body_preview}",
                flush=True,
            )
            if err.code == 400 or attempt == retries - 1:
                raise
            if err.code == 429:
                wait_s = retry_after if retry_after is not None else min(max_429_sleep, 15.0 * (attempt + 1))
            else:
                wait_s = base_backoff * (2**attempt)
            time.sleep(wait_s)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            last_error = err
            print(
                f"[WARN] Network/parse error on attempt {attempt + 1}/{retries} ({url[:95]}...): {err}",
                flush=True,
            )
            if attempt == retries - 1:
                raise
            time.sleep(base_backoff * (2**attempt))

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url}")


def lookup_climatology_baseline(
    climatology: dict[str, Any] | None,
    resort_id: str,
    target_date: date,
    previous_resort_data: dict[str, Any] | None = None,
) -> tuple[float, float, float]:
    """Return (baseline_snowfall_cm, baseline_snow_depth_cm, summer_min_depth_cm) for MM-DD."""
    mm_dd = target_date.strftime("%m-%d")
    resort_entry = (climatology or {}).get("resorts", {}).get(resort_id)

    if resort_entry:
        days = resort_entry.get("days", {})
        pair = days.get(mm_dd)
        if pair is None and mm_dd == "02-29":
            pair = days.get("02-28")
        if isinstance(pair, list) and len(pair) == 2:
            summer_floor = float(resort_entry.get("summer_min_depth_cm", 0.0) or 0.0)
            return float(pair[0]), float(pair[1]), summer_floor

    if previous_resort_data:
        prev_snow = float(previous_resort_data.get("baseline_snowfall_cm", 0.0) or 0.0)
        prev_depth = float(previous_resort_data.get("baseline_snow_depth_cm", 0.0) or 0.0)
        return prev_snow, prev_depth, 0.0

    return 0.0, 0.0, 0.0


def compute_snow_anomaly_pct(current_snow_cm: float, baseline_snow_cm: float) -> float:
    """Compute percentage anomaly of 7-day snowfall versus 10-year baseline."""
    if baseline_snow_cm < 2.0 and current_snow_cm < 2.0:
        return 0.0
    if baseline_snow_cm < 0.5:
        return 100.0 if current_snow_cm >= 2.0 else 0.0
    return round(((current_snow_cm - baseline_snow_cm) / baseline_snow_cm) * 100.0, 1)


def is_already_updated_today(output_path: str, today_utc: date) -> bool:
    """Check whether output_path already has a valid snapshot for today_utc."""
    try:
        with open(output_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        ts = payload.get("last_updated", "")
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        resorts = payload.get("resorts", [])
        if parsed.date() != today_utc or not resorts:
            return False
        # Treat a run where every baseline was zeroed out (e.g. Nov 27 failure) as NOT validly updated.
        if today_utc.month in (11, 12, 1, 2, 3, 4):
            nonzero_baselines = sum(
                1 for r in resorts if (r.get("baseline_snow_depth_cm", 0) or 0) > 0 or (r.get("baseline_snowfall_cm", 0) or 0) > 0
            )
            if nonzero_baselines == 0:
                return False
        return True
    except Exception:
        return False


def validate_tracker_results(
    results: list[dict[str, Any]],
    expected_count: int,
    failed_count: int,
    target_date: date,
    label: str,
) -> list[str]:
    """Return a list of validation errors; empty list means output is safe to commit."""
    errors: list[str] = []
    if len(results) != expected_count:
        errors.append(f"{label}: expected {expected_count} resorts, got {len(results)}.")

    max_allowed_failures = max(1, math.floor(expected_count * 0.10))
    if failed_count > max_allowed_failures:
        errors.append(
            f"{label}: {failed_count}/{expected_count} forecast fetches failed (limit {max_allowed_failures})."
        )

    # During core Alpine snow months (Nov-Apr), baselines must never collapse to 0.0 across the board.
    if target_date.month in (11, 12, 1, 2, 3, 4) and results:
        nonzero_snow_base = sum(1 for r in results if (r.get("baseline_snowfall_cm") or 0) > 0)
        nonzero_depth_base = sum(1 for r in results if (r.get("baseline_snow_depth_cm") or 0) > 0)
        if nonzero_snow_base == 0 or nonzero_depth_base == 0:
            errors.append(
                f"{label}: 10-year baselines collapsed to 0.0 across all resorts "
                f"(nonzero snowfall={nonzero_snow_base}, nonzero depth={nonzero_depth_base})."
            )

    for r in results:
        for field in ("snow_depth_cm", "snow_depth_base_cm", "snow_depth_peak_cm", "current_snowfall_cm"):
            val = r.get(field)
            if not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
                errors.append(f"{label}: invalid {field}={val!r} for {r.get('id')}.")

    return errors
