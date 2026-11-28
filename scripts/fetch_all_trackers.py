"""Unified daily ski weather data pipeline for public (41) and personal (10) trackers.

Improvements over v2:
- Deduplicates overlapping resorts (46 unique resorts instead of 51).
- Batches Open-Meteo /v1/forecast requests (10 resorts = 30 elevation points per call -> ~5 requests).
- Reads 10-year ERA5 baselines from precomputed data/climatology-baselines.json (0 archive calls/day).
- Slices hourly arrays through the current local hour (captures overnight snowfall).
- Persists cumulative season snowpack state in data/snowpack-state.json (fixes 60-day winter cliff).
- Validates outputs before writing so broken/empty runs never silently overwrite live feeds.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from payload_builders import build_personal_payloads, build_public_payloads
from snowpack_model import extract_tier_metrics
from tracker_lib import (
    compute_snow_anomaly_pct,
    deduplicate_resorts,
    fetch_json,
    is_already_updated_today,
    lookup_climatology_baseline,
    resolve_resort_geometry,
    validate_tracker_results,
)

FORECAST_BATCH_SIZE = 10
FORECAST_PAST_DAYS = 60


def _build_forecast_url(geoms: list[dict[str, Any]]) -> str:
    lats: list[str] = []
    lons: list[str] = []
    elevs: list[str] = []
    for g in geoms:
        lats.extend([str(g["base_lat"]), str(g["mid_lat"]), str(g["peak_lat"])])
        lons.extend([str(g["base_lon"]), str(g["mid_lon"]), str(g["peak_lon"])])
        elevs.extend([str(g["base_elev"]), str(g["mid_elev"]), str(g["peak_elev"])])
    return (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={','.join(lats)}"
        f"&longitude={','.join(lons)}"
        f"&elevation={','.join(elevs)}"
        f"&past_days={FORECAST_PAST_DAYS}&forecast_days=1"
        "&hourly=snowfall,precipitation,freezing_level_height,snow_depth,temperature_2m"
        "&timezone=auto"
    )


def fetch_forecasts_batched(
    geoms: list[dict[str, Any]],
    batch_size: int = FORECAST_BATCH_SIZE,
) -> dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Fetch 3-elevation forecast payloads for all unique resorts in compact multi-location batches."""
    by_id: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}

    for start in range(0, len(geoms), batch_size):
        chunk = geoms[start : start + batch_size]
        url = _build_forecast_url(chunk)
        expected_points = len(chunk) * 3
        try:
            payload = fetch_json(url, timeout=25, retries=4, base_backoff=2.0)
            if isinstance(payload, list) and len(payload) == expected_points:
                for idx, g in enumerate(chunk):
                    offset = idx * 3
                    by_id[g["id"]] = (payload[offset], payload[offset + 1], payload[offset + 2])
                continue
            raise ValueError(
                f"Unexpected batch payload length: expected {expected_points}, got {len(payload) if isinstance(payload, list) else type(payload).__name__}"
            )
        except Exception as err:
            print(f"[WARN] Batch {start // batch_size + 1} failed ({err}); falling back per resort.", flush=True)
            for g in chunk:
                try:
                    single = fetch_json(_build_forecast_url([g]), timeout=20, retries=3, base_backoff=2.0)
                    if isinstance(single, list) and len(single) == 3:
                        by_id[g["id"]] = (single[0], single[1], single[2])
                except Exception as single_err:
                    print(f"[ERROR] Forecast fetch failed for {g['id']}: {single_err}", flush=True)

    return by_id


def compute_resort_metrics(
    geom: dict[str, Any],
    triplet: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None,
    climatology: dict[str, Any] | None,
    resort_state: dict[str, Any],
    previous_resort_data: dict[str, Any] | None,
    now_utc: datetime,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Compute full tracker metrics for one resort. Returns (metrics, updated_state, fetch_failed)."""
    rid = geom["id"]
    today_utc = now_utc.date()
    baseline_snow, baseline_depth, era5_summer_floor = lookup_climatology_baseline(
        climatology, rid, today_utc, previous_resort_data
    )

    if triplet is None:
        if previous_resort_data:
            fallback_mid_depth = float(previous_resort_data.get("snow_depth_cm", 0.0) or 0.0)
            fallback_mid_snow = float(previous_resort_data.get("current_snowfall_cm", 0.0) or 0.0)
            return {
                "mid_elevation": geom["mid_elev"],
                "current_snowfall_cm": fallback_mid_snow,
                "snowfall_base_cm": float(previous_resort_data.get("snowfall_base_cm", fallback_mid_snow) or 0.0),
                "snowfall_peak_cm": float(previous_resort_data.get("snowfall_peak_cm", fallback_mid_snow) or 0.0),
                "baseline_snowfall_cm": round(baseline_snow, 1),
                "snow_anomaly_pct": compute_snow_anomaly_pct(fallback_mid_snow, baseline_snow),
                "weekly_precip_mm": float(previous_resort_data.get("weekly_precip_mm", 0.0) or 0.0),
                "avg_freezing_level_m": int(previous_resort_data.get("avg_freezing_level_m", 0) or 0),
                "snow_depth_cm": fallback_mid_depth,
                "snow_depth_base_cm": float(previous_resort_data.get("snow_depth_base_cm", fallback_mid_depth) or 0.0),
                "snow_depth_peak_cm": float(previous_resort_data.get("snow_depth_peak_cm", fallback_mid_depth) or 0.0),
                "baseline_snow_depth_cm": round(baseline_depth, 1),
            }, resort_state, True
        return {
            "mid_elevation": geom["mid_elev"],
            "current_snowfall_cm": 0.0,
            "snowfall_base_cm": 0.0,
            "snowfall_peak_cm": 0.0,
            "baseline_snowfall_cm": round(baseline_snow, 1),
            "snow_anomaly_pct": 0.0,
            "weekly_precip_mm": 0.0,
            "avg_freezing_level_m": 0,
            "snow_depth_cm": 0.0,
            "snow_depth_base_cm": 0.0,
            "snow_depth_peak_cm": 0.0,
            "baseline_snow_depth_cm": round(baseline_depth, 1),
        }, resort_state, True

    base_resp, mid_resp, peak_resp = triplet
    base_tier, base_state = extract_tier_metrics(
        base_resp.get("hourly", {}),
        geom["base_elev"],
        utc_offset_seconds=int(base_resp.get("utc_offset_seconds", 0) or 0),
        is_glacier_sector=False,
        is_base_tier=True,
        prior_state=resort_state.get("base"),
        era5_summer_floor_cm=0.0,
        now_utc=now_utc,
    )
    mid_tier, mid_state = extract_tier_metrics(
        mid_resp.get("hourly", {}),
        geom["mid_elev"],
        utc_offset_seconds=int(mid_resp.get("utc_offset_seconds", 0) or 0),
        is_glacier_sector=geom["is_glacier_mid"],
        is_base_tier=False,
        prior_state=resort_state.get("mid"),
        era5_summer_floor_cm=era5_summer_floor,
        now_utc=now_utc,
    )
    peak_tier, peak_state = extract_tier_metrics(
        peak_resp.get("hourly", {}),
        geom["peak_elev"],
        utc_offset_seconds=int(peak_resp.get("utc_offset_seconds", 0) or 0),
        is_glacier_sector=geom["is_glacier_peak"],
        is_base_tier=False,
        prior_state=resort_state.get("peak"),
        era5_summer_floor_cm=era5_summer_floor,
        now_utc=now_utc,
    )

    current_snow = mid_tier["snow_cm"]
    current_precip = mid_tier["precip_mm"]
    avg_freezing_level = mid_tier["avg_freezing_m"]
    current_snow_depth = mid_tier["depth_cm"]
    base_snow_depth = base_tier["depth_cm"]
    peak_snow_depth = peak_tier["depth_cm"]

    if current_snow_depth > 0 and round(peak_snow_depth, 1) <= round(current_snow_depth, 1):
        peak_gain_100m = max(0.0, (geom["peak_elev"] - geom["mid_elev"]) / 100.0)
        peak_snow_depth = current_snow_depth * (1.0 + peak_gain_100m * 0.04)

    if round(base_snow_depth, 1) > round(current_snow_depth, 1):
        base_drop_100m = max(0.0, (geom["mid_elev"] - geom["base_elev"]) / 100.0)
        melt_rate = 0.085 if avg_freezing_level > geom["base_elev"] else 0.05
        base_snow_depth = max(0.0, current_snow_depth * (1.0 - base_drop_100m * melt_rate))

    updated_state = {
        "base": base_state,
        "mid": mid_state,
        "peak": peak_state,
    }
    return {
        "mid_elevation": geom["mid_elev"],
        "current_snowfall_cm": round(current_snow, 1),
        "snowfall_base_cm": round(base_tier["snow_cm"], 1),
        "snowfall_peak_cm": round(peak_tier["snow_cm"], 1),
        "baseline_snowfall_cm": round(baseline_snow, 1),
        "snow_anomaly_pct": compute_snow_anomaly_pct(current_snow, baseline_snow),
        "weekly_precip_mm": round(current_precip, 1),
        "avg_freezing_level_m": round(avg_freezing_level),
        "snow_depth_cm": round(current_snow_depth, 1),
        "snow_depth_base_cm": round(base_snow_depth, 1),
        "snow_depth_peak_cm": round(peak_snow_depth, 1),
        "baseline_snow_depth_cm": round(baseline_depth, 1),
    }, updated_state, False


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def run_pipeline(root_dir: Path, skip_if_updated_today: bool = False, dry_run: bool = False) -> int:
    now_utc = datetime.now(timezone.utc)
    public_out = root_dir / "data" / "el-nino-tracker.json"
    public_hist = root_dir / "data" / "season-history.json"
    personal_out = root_dir / "data" / "personal-tracker.json"
    personal_hist = root_dir / "data" / "personal-history.json"
    state_path = root_dir / "data" / "snowpack-state.json"
    clim_path = root_dir / "data" / "climatology-baselines.json"

    if skip_if_updated_today and is_already_updated_today(str(public_out), now_utc.date()):
        print(f"Trackers already have a valid update for {now_utc.date().isoformat()}; skipping.", flush=True)
        return 0

    public_resorts = _load_json(root_dir / "resorts.json", [])
    personal_resorts = _load_json(root_dir / "private-data" / "personal-resorts.json", [])
    unique_resorts = deduplicate_resorts(public_resorts, personal_resorts)
    if not unique_resorts:
        print("[ERROR] No resorts found in resorts.json or private-data/personal-resorts.json.", flush=True)
        return 1

    climatology = _load_json(clim_path, None)
    if climatology is None:
        print(f"[WARN] {clim_path} not found; falling back to previous baseline values.", flush=True)

    prev_map: dict[str, dict[str, Any]] = {}
    for prev_file in (public_out, personal_out):
        prev_data = _load_json(prev_file, {})
        for pr in prev_data.get("resorts", []):
            if "id" in pr:
                prev_map[pr["id"]] = pr

    prior_state_doc = _load_json(state_path, {"resorts": {}})
    prior_states = prior_state_doc.get("resorts", {}) if isinstance(prior_state_doc, dict) else {}

    geoms = [resolve_resort_geometry(r) for r in unique_resorts]
    print(
        f"Fetching batched Open-Meteo forecasts for {len(geoms)} unique resorts "
        f"(deduplicated from {len(public_resorts)} public + {len(personal_resorts)} personal)...",
        flush=True,
    )
    forecasts_by_id = fetch_forecasts_batched(geoms)

    metrics_by_id: dict[str, dict[str, Any]] = {}
    updated_states: dict[str, Any] = {}
    failed_ids: set[str] = set()

    for geom in geoms:
        rid = geom["id"]
        metrics, next_state, failed = compute_resort_metrics(
            geom=geom,
            triplet=forecasts_by_id.get(rid),
            climatology=climatology,
            resort_state=prior_states.get(rid, {}),
            previous_resort_data=prev_map.get(rid),
            now_utc=now_utc,
        )
        metrics_by_id[rid] = metrics
        updated_states[rid] = next_state
        if failed:
            failed_ids.add(rid)

    public_payload, public_hist_payload = build_public_payloads(
        public_resorts, metrics_by_id, _load_json(public_hist, {"history": []}), now_utc
    )
    pub_failed = sum(1 for r in public_resorts if r["id"] in failed_ids)
    validation_errors = validate_tracker_results(
        public_payload["resorts"], len(public_resorts), pub_failed, now_utc.date(), "public-tracker"
    )

    personal_payload: dict[str, Any] | None = None
    personal_hist_payload: dict[str, Any] | None = None
    if personal_resorts:
        personal_payload, personal_hist_payload = build_personal_payloads(
            personal_resorts, metrics_by_id, _load_json(personal_hist, {"history": []}), now_utc
        )
        pers_failed = sum(1 for r in personal_resorts if r["id"] in failed_ids)
        validation_errors.extend(
            validate_tracker_results(
                personal_payload["resorts"], len(personal_resorts), pers_failed, now_utc.date(), "personal-tracker"
            )
        )

    if validation_errors:
        print("[ERROR] Pre-commit validation failed; refusing to overwrite tracker files:", flush=True)
        for err in validation_errors:
            print(f"  - {err}", flush=True)
        return 1

    if dry_run:
        print(
            f"[DRY-RUN] Validation passed for {len(public_resorts)} public + {len(personal_resorts)} personal resorts "
            f"(failed forecasts: {len(failed_ids)}).",
            flush=True,
        )
        return 0

    public_out.parent.mkdir(parents=True, exist_ok=True)
    public_out.write_text(json.dumps(public_payload, indent=2) + "\n", encoding="utf-8")
    public_hist.write_text(json.dumps(public_hist_payload, indent=2) + "\n", encoding="utf-8")
    if personal_payload is not None and personal_hist_payload is not None:
        personal_out.write_text(json.dumps(personal_payload, indent=2) + "\n", encoding="utf-8")
        personal_hist.write_text(json.dumps(personal_hist_payload, indent=2) + "\n", encoding="utf-8")

    state_payload = {
        "schema_version": 1,
        "updated_at": now_utc.isoformat(),
        "resorts": updated_states,
    }
    state_path.write_text(json.dumps(state_payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Tracker update complete: {len(public_resorts)} public + {len(personal_resorts)} personal resorts "
        f"({len(geoms)} unique fetched, {len(failed_ids)} fallback).",
        flush=True,
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and update public and personal ski weather trackers.")
    parser.add_argument("--root", default=".", help="Repository root directory.")
    parser.add_argument(
        "--skip-if-updated-today",
        action="store_true",
        help="Skip cleanly when data/el-nino-tracker.json already has a valid snapshot for today's UTC date.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing output JSON.")
    args = parser.parse_args()
    sys.exit(run_pipeline(Path(args.root).resolve(), args.skip_if_updated_today, args.dry_run))


if __name__ == "__main__":
    main()
