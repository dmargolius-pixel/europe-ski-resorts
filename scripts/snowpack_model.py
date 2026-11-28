"""Cumulative seasonal piste snowpack model, current-hour slicing, and glacier-mask handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

ACTIVE_SEASON_MONTHS = (10, 11, 12, 1, 2, 3, 4, 5)
GLACIER_DEPOT_MONTH = 10
GLACIER_DEPOT_DAY = 15

# Thresholds for separating permanent NWP glacier masks from genuine mid-winter snow depths.
EARLY_SEASON_MONTHS = (9, 10, 11)
EARLY_SEASON_GLACIER_FLOOR_CM = 180.0
IMPLAUSIBLE_RAW_DEPTH_CM = 600.0
UNFLAGGED_GLACIER_RAW_CM = 250.0
UNFLAGGED_GLACIER_MODELLED_CEILING_CM = 120.0


def get_ski_season_id(now_utc: datetime) -> tuple[str, int]:
    """Return ('YYYY-YYYY', season_start_year) where seasons run 1 Sep to 31 Aug."""
    start_year = now_utc.year if now_utc.month >= 9 else now_utc.year - 1
    return f"{start_year}-{start_year + 1}", start_year


def find_current_hour_end_idx(
    time_list: list[str],
    utc_offset_seconds: int = 0,
    now_utc: datetime | None = None,
) -> int:
    """Return exclusive slice index covering all hourly data up through the current local hour.

    Replaces the old `len(snow_list) - 24` shortcut so morning runs (e.g. 05:15 UTC / 06:15 CET)
    include overnight snowfall from 00:00 to 06:00 local time rather than stopping at 23:00 yesterday.
    """
    if not time_list:
        return 0
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    local_now = now + timedelta(seconds=int(utc_offset_seconds or 0))
    cutoff_iso = local_now.strftime("%Y-%m-%dT%H:00")

    end_idx = 0
    for idx, ts in enumerate(time_list):
        if ts <= cutoff_iso:
            end_idx = idx + 1
        else:
            break
    return end_idx


def glacier_depot_target_cm(elev_m: int, is_glacier_sector: bool, month: int) -> float:
    """Return target mid-October snow-farming/glacier piste base depth (cm)."""
    if not is_glacier_sector or month not in ACTIVE_SEASON_MONTHS:
        return 0.0
    if elev_m >= 2900:
        return 68.0 + min(25.0, (elev_m - 2900) * 0.045)
    if elev_m >= 2400:
        return 45.0 + ((elev_m - 2400) / 500.0) * 23.0
    return 0.0


def simulate_piste_snowpack(
    time_list: list[str],
    snow_list: list[float | None],
    temp_list: list[float | None],
    elev_m: int,
    end_idx: int,
    is_glacier_sector: bool = False,
    prior_state: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> tuple[float, dict[str, Any] | None]:
    """Run cumulative hourly piste accumulation, settling, and degree-hour melt simulation.

    Fixes two seasonal bugs:
    1. Anchors glacier depot deployment to fixed 15 October rather than a sliding 21-day window.
    2. Carries snowpack state forward across daily runs (season-long from 1 Sep to 31 Aug)
       so November/December snow does not drop off a 60-day rolling window in January-April.
    """
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    season_id, season_start_year = get_ski_season_id(now)
    depot_iso = f"{season_start_year}-{GLACIER_DEPOT_MONTH:02d}-{GLACIER_DEPOT_DAY:02d}T00:00"
    depot_cm = glacier_depot_target_cm(elev_m, is_glacier_sector, now.month)

    n = min(len(time_list), len(snow_list), len(temp_list), end_idx)
    if n <= 0:
        return 0.0, prior_state

    start_idx = 0
    pack_cm = 0.0
    depot_applied = False

    if (
        isinstance(prior_state, dict)
        and prior_state.get("season") == season_id
        and isinstance(prior_state.get("last_hour"), str)
    ):
        last_hour = prior_state["last_hour"]
        # Carry forward cleanly if last_hour is present inside the fetched window.
        try:
            pos = time_list[:n].index(last_hour)
            start_idx = pos + 1
            pack_cm = max(0.0, float(prior_state.get("pack_cm", 0.0) or 0.0))
            depot_applied = bool(prior_state.get("depot_applied", False))
        except ValueError:
            # If last_hour is newer than the window (clock skew), preserve prior pack safely.
            if last_hour >= time_list[n - 1]:
                return round(max(0.0, float(prior_state.get("pack_cm", 0.0) or 0.0)), 1), prior_state
            start_idx = 0
            pack_cm = 0.0
            depot_applied = False

    # If bootstrapping mid-season after 15 October with no prior state, deploy glacier base at window start.
    if start_idx == 0 and not depot_applied and depot_cm > 0 and time_list[0] > depot_iso:
        pack_cm = max(pack_cm, depot_cm)
        depot_applied = True

    for i in range(start_idx, n):
        if not depot_applied and depot_cm > 0 and time_list[i] >= depot_iso:
            pack_cm = max(pack_cm, depot_cm)
            depot_applied = True

        s = float(snow_list[i]) if snow_list[i] is not None else 0.0
        t = float(temp_list[i]) if temp_list[i] is not None else 0.0

        # Compacted piste equivalent (~0.78 of fresh dendritic snowfall).
        if s > 0:
            pack_cm += s * 0.78

        # Gradual settling when sub-zero / near freezing.
        if pack_cm > 0 and t <= 0.5:
            pack_cm *= 0.9998

        # Degree-hour ablation when air temperature exceeds +0.5C.
        if pack_cm > 0 and t > 0.5:
            if is_glacier_sector and elev_m >= 2400:
                melt_factor = 0.006 if elev_m >= 2850 else 0.010
            else:
                melt_factor = 0.025 if elev_m >= 2300 else (0.040 if elev_m >= 1800 else 0.060)
            pack_cm = max(0.0, pack_cm - (t - 0.5) * melt_factor)

    if depot_cm > 0:
        pack_cm = max(pack_cm, round(depot_cm * 0.92, 1))

    final_pack = round(pack_cm, 1)
    next_state = {
        "season": season_id,
        "last_hour": time_list[n - 1],
        "pack_cm": final_pack,
        "depot_applied": depot_applied,
        "nwp_glacier_floor_cm": float((prior_state or {}).get("nwp_glacier_floor_cm", 0.0) or 0.0),
    }
    return final_pack, next_state


def resolve_raw_snow_depth_cm(
    depth_upto_now_m: list[float | None],
    modelled_depth_cm: float,
    now_utc: datetime,
    prior_nwp_floor_cm: float = 0.0,
    era5_summer_floor_cm: float = 0.0,
) -> tuple[float, float]:
    """Convert raw NWP snow_depth (m) to seasonal cm without zeroing genuine >250 cm winter snowpacks.

    Returns (seasonal_raw_depth_cm, updated_nwp_glacier_floor_cm).
    """
    valid_cm = [float(d) * 100.0 for d in depth_upto_now_m if d is not None]
    if not valid_cm:
        return 0.0, max(0.0, prior_nwp_floor_cm)

    raw_latest_cm = valid_cm[-1]
    min_window_cm = min(valid_cm)
    nwp_floor_cm = max(0.0, prior_nwp_floor_cm)

    # Record permanent NWP glacier floor during autumn when seasonal snow is still shallow.
    if now_utc.month in EARLY_SEASON_MONTHS and min_window_cm >= EARLY_SEASON_GLACIER_FLOOR_CM:
        nwp_floor_cm = round(min_window_cm, 1)
    elif nwp_floor_cm == 0.0 and era5_summer_floor_cm >= 100.0 and min_window_cm >= 150.0:
        nwp_floor_cm = round(min_window_cm, 1)

    adjusted_cm = max(0.0, raw_latest_cm - nwp_floor_cm) if nwp_floor_cm >= 100.0 else raw_latest_cm

    # Guard against unflagged permanent glacier cells without cutting off real 250-450 cm winter bases.
    plausibility_ceiling = max(450.0, modelled_depth_cm * 2.5 + 100.0)
    if adjusted_cm >= IMPLAUSIBLE_RAW_DEPTH_CM:
        adjusted_cm = 0.0
    elif adjusted_cm >= UNFLAGGED_GLACIER_RAW_CM and modelled_depth_cm < UNFLAGGED_GLACIER_MODELLED_CEILING_CM:
        adjusted_cm = 0.0
    elif adjusted_cm > plausibility_ceiling:
        adjusted_cm = 0.0

    return round(adjusted_cm, 1), nwp_floor_cm


def extract_tier_metrics(
    hourly_data: dict[str, Any],
    elev_m: int,
    utc_offset_seconds: int = 0,
    is_glacier_sector: bool = False,
    is_base_tier: bool = False,
    prior_state: dict[str, Any] | None = None,
    era5_summer_floor_cm: float = 0.0,
    now_utc: datetime | None = None,
) -> tuple[dict[str, float], dict[str, Any] | None]:
    """Compute 7-day snowfall, precip, freezing level, and settled snow depth for one elevation tier."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    time_list = hourly_data.get("time") or []
    snow_list = hourly_data.get("snowfall") or []
    precip_list = hourly_data.get("precipitation") or []
    freezing_list = hourly_data.get("freezing_level_height") or []
    depth_list = hourly_data.get("snow_depth") or []
    temp_list = hourly_data.get("temperature_2m") or []

    if time_list:
        end_idx = find_current_hour_end_idx(time_list, utc_offset_seconds=utc_offset_seconds, now_utc=now)
    else:
        end_idx = max(0, len(snow_list) - 24)
    start_7d = max(0, end_idx - 168)

    snow_7d = snow_list[start_7d:end_idx] if end_idx > 0 else snow_list
    precip_7d = precip_list[start_7d:end_idx] if end_idx > 0 else precip_list
    freezing_7d = freezing_list[start_7d:end_idx] if end_idx > 0 else freezing_list
    depth_upto_now = depth_list[:end_idx] if end_idx > 0 else depth_list

    snow_cm = sum(float(f) for f in snow_7d if f is not None)
    precip_mm = sum(float(p) for p in precip_7d if p is not None)
    valid_freezing = [float(fl) for fl in freezing_7d if fl is not None]
    avg_freezing_m = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0.0

    modelled_depth_cm, next_state = simulate_piste_snowpack(
        time_list=time_list,
        snow_list=snow_list,
        temp_list=temp_list,
        elev_m=elev_m,
        end_idx=end_idx,
        is_glacier_sector=is_glacier_sector,
        prior_state=prior_state,
        now_utc=now,
    )

    prior_nwp_floor = float((prior_state or {}).get("nwp_glacier_floor_cm", 0.0) or 0.0)
    raw_depth_cm, updated_nwp_floor = resolve_raw_snow_depth_cm(
        depth_upto_now_m=depth_upto_now,
        modelled_depth_cm=modelled_depth_cm,
        now_utc=now,
        prior_nwp_floor_cm=prior_nwp_floor,
        era5_summer_floor_cm=era5_summer_floor_cm,
    )
    if isinstance(next_state, dict):
        next_state["nwp_glacier_floor_cm"] = updated_nwp_floor

    # Prevent high-canyon NWP grid cells from bleeding snow onto warm valley bases.
    if is_base_tier and avg_freezing_m > (elev_m + 250) and snow_cm < 1.0:
        raw_depth_cm = 0.0

    final_depth_cm = max(raw_depth_cm, modelled_depth_cm)
    return {
        "snow_cm": snow_cm,
        "precip_mm": precip_mm,
        "avg_freezing_m": avg_freezing_m,
        "depth_cm": final_depth_cm,
    }, next_state
