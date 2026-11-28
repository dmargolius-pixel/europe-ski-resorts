"""One-time / annual generator for data/climatology-baselines.json.

Fetches 10 complete years (2016-2025, plus Dec 25-31 2015 for clean New Year
7-day rolling windows) of daily ERA5 snowfall_sum and snow_depth_mean from
Open-Meteo's Archive API for every unique resort across resorts.json and
private-data/personal-resorts.json.

Supports checkpointing (--resume by default) so interrupted or rate-limited
runs continue smoothly without re-querying completed resorts.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracker_lib import deduplicate_resorts, fetch_json, resolve_resort_geometry

DEFAULT_START_YEAR = 2016
DEFAULT_END_YEAR = 2025
ERA5_GLACIER_MASK_CM = 250.0
SUMMER_MASK_THRESHOLD_CM = 100.0


def compute_resort_climatology_from_daily(
    times: list[str],
    snowfall_sums: list[float | None],
    snow_depth_means_m: list[float | None],
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
) -> dict[str, Any]:
    """Compute 366-day [baseline_snowfall_7d_cm, baseline_snow_depth_cm] and summer floor."""
    n = min(len(times), len(snowfall_sums), len(snow_depth_means_m))
    daily_snow = [float(snowfall_sums[i]) if snowfall_sums[i] is not None else 0.0 for i in range(n)]
    daily_depth_cm = [
        float(snow_depth_means_m[i]) * 100.0 if snow_depth_means_m[i] is not None else None
        for i in range(n)
    ]

    # Measure each year's July-August minimum ERA5 snow depth to detect permanent glacier cells.
    summer_mins_by_year: dict[int, list[float]] = defaultdict(list)
    for i in range(n):
        iso = times[i]
        year = int(iso[:4])
        month = int(iso[5:7])
        if start_year <= year <= end_year and month in (7, 8) and daily_depth_cm[i] is not None:
            summer_mins_by_year[year].append(daily_depth_cm[i])  # type: ignore[arg-type]

    yearly_summer_mins = [min(vals) for vals in summer_mins_by_year.values() if vals]
    summer_min_depth_cm = round(statistics.median(yearly_summer_mins), 1) if yearly_summer_mins else 0.0
    era5_glacier_masked = summer_min_depth_cm >= SUMMER_MASK_THRESHOLD_CM

    snow_by_mmdd: dict[str, list[float]] = defaultdict(list)
    depth_by_mmdd: dict[str, list[float]] = defaultdict(list)

    for i in range(n):
        iso = times[i]
        year = int(iso[:4])
        if year < start_year or year > end_year:
            continue
        mm_dd = iso[5:10]

        # Exact 7-day trailing snowfall window (i-6 .. i inclusive), naturally spanning Dec -> Jan.
        win_start = max(0, i - 6)
        snow_7d = sum(daily_snow[win_start : i + 1])
        snow_by_mmdd[mm_dd].append(snow_7d)

        raw_d = daily_depth_cm[i]
        if raw_d is not None:
            if era5_glacier_masked:
                # Subtract persistent ERA5 glacier floor to recover seasonal snowpack signal.
                seasonal_d = max(0.0, raw_d - summer_min_depth_cm)
                if seasonal_d < ERA5_GLACIER_MASK_CM:
                    depth_by_mmdd[mm_dd].append(seasonal_d)
            elif raw_d < ERA5_GLACIER_MASK_CM:
                depth_by_mmdd[mm_dd].append(raw_d)

    days_out: dict[str, list[float]] = {}
    for mm_dd in sorted(snow_by_mmdd.keys()):
        snow_vals = snow_by_mmdd[mm_dd]
        depth_vals = depth_by_mmdd.get(mm_dd, [])
        mean_snow = sum(snow_vals) / len(snow_vals) if snow_vals else 0.0
        if len(depth_vals) >= 3 or (mm_dd == "02-29" and len(depth_vals) >= 1):
            mean_depth = sum(depth_vals) / len(depth_vals)
        else:
            mean_depth = mean_snow * 1.9 if mean_snow > 0 else 0.0
        days_out[mm_dd] = [round(mean_snow, 1), round(mean_depth, 1)]

    if "02-29" not in days_out and "02-28" in days_out:
        days_out["02-29"] = list(days_out["02-28"])

    return {
        "summer_min_depth_cm": summer_min_depth_cm,
        "era5_glacier_masked": era5_glacier_masked,
        "days": days_out,
    }


def fetch_resort_era5_history(
    geom: dict[str, Any],
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    """Fetch 10-year daily ERA5 snowfall_sum & snow_depth_mean anchored to base_lat/lon + mid_elev."""
    # Start on Dec 25 of the prior year so Jan 1 of start_year has a full 7-day trailing sum.
    start_date = f"{start_year - 1}-12-25"
    end_date = f"{end_year}-12-31"
    url = (
        "https://archive-api.open-meteo.com/v1/archive?"
        f"latitude={geom['base_lat']}&longitude={geom['base_lon']}&elevation={geom['mid_elev']}"
        f"&start_date={start_date}&end_date={end_date}"
        "&daily=snowfall_sum,snow_depth_mean"
        "&timezone=UTC"
    )
    payload = fetch_json(url, timeout=30, retries=5, base_backoff=3.0, max_429_sleep=65.0)
    daily = payload.get("daily", {})
    computed = compute_resort_climatology_from_daily(
        daily.get("time") or [],
        daily.get("snowfall_sum") or [],
        daily.get("snow_depth_mean") or [],
        start_year=start_year,
        end_year=end_year,
    )
    return {
        "latitude": geom["base_lat"],
        "longitude": geom["base_lon"],
        "mid_elevation": geom["mid_elev"],
        **computed,
    }


def build_climatology(
    root_dir: Path,
    output_path: Path,
    start_year: int = DEFAULT_START_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    sleep_seconds: float = 1.5,
    resume: bool = True,
) -> dict[str, Any]:
    public_resorts = json.loads((root_dir / "resorts.json").read_text(encoding="utf-8"))
    personal_path = root_dir / "private-data" / "personal-resorts.json"
    personal_resorts = (
        json.loads(personal_path.read_text(encoding="utf-8")) if personal_path.exists() else []
    )
    unique_resorts = deduplicate_resorts(public_resorts, personal_resorts)

    existing_resorts: dict[str, Any] = {}
    if resume and output_path.exists():
        try:
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            if loaded.get("years") == [start_year, end_year]:
                existing_resorts = loaded.get("resorts", {})
        except Exception:
            pass

    resorts_out = dict(existing_resorts)
    total = len(unique_resorts)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for idx, resort in enumerate(unique_resorts, start=1):
        rid = resort["id"]
        if rid in resorts_out and len(resorts_out[rid].get("days", {})) >= 365:
            print(f"[{idx}/{total}] Skipping {rid} (already cached).", flush=True)
            continue

        geom = resolve_resort_geometry(resort)
        print(
            f"[{idx}/{total}] Fetching {start_year}-{end_year} ERA5 daily history for {resort['name']} "
            f"(mid {geom['mid_elev']}m)...",
            flush=True,
        )
        resorts_out[rid] = fetch_resort_era5_history(geom, start_year, end_year)

        # Checkpoint after every resort so rate-limit interruptions never lose progress.
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "Open-Meteo ERA5 Archive (daily snowfall_sum & snow_depth_mean)",
            "years": [start_year, end_year],
            "window_days": 7,
            "resort_count": len(resorts_out),
            "resorts": resorts_out,
        }
        output_path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")

        if idx < total and sleep_seconds > 0:
            time.sleep(sleep_seconds)

    final_payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Open-Meteo ERA5 Archive (daily snowfall_sum & snow_depth_mean)",
        "years": [start_year, end_year],
        "window_days": 7,
        "resort_count": len(resorts_out),
        "resorts": resorts_out,
    }
    output_path.write_text(json.dumps(final_payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Climatology complete: {len(resorts_out)}/{total} resorts saved to {output_path}.", flush=True)
    return final_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Precompute 10-year ERA5 daily snow climatology.")
    parser.add_argument("--root", default=".", help="Repository root containing resorts.json.")
    parser.add_argument("--output", default="data/climatology-baselines.json", help="Output JSON path.")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    parser.add_argument("--sleep", type=float, default=1.5, help="Polite pause (seconds) between resorts.")
    parser.add_argument("--no-resume", action="store_true", help="Rebuild all resorts from scratch.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    build_climatology(
        root_dir=root,
        output_path=out,
        start_year=args.start_year,
        end_year=args.end_year,
        sleep_seconds=args.sleep,
        resume=not args.no_resume,
    )


if __name__ == "__main__":
    main()
