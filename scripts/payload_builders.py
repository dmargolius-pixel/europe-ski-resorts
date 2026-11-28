"""JSON payload builders for public (41-resort) and personal (10-resort) tracker outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _avg_key(arr: list[dict[str, Any]], key: str) -> float:
    return round(sum(float(r.get(key, 0.0) or 0.0) for r in arr) / len(arr), 1) if arr else 0.0


def build_public_payloads(
    public_resorts: list[dict[str, Any]],
    metrics_by_id: dict[str, dict[str, Any]],
    existing_history: dict[str, Any],
    now_utc: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build el-nino-tracker.json and updated season-history.json payloads."""
    results = [{**r, **metrics_by_id[r["id"]]} for r in public_resorts]
    target_marquee_ids = {"meribel", "zermatt", "kitzbuhel", "cortina"}
    marquee_depths: dict[str, float] = {}
    for row in results:
        rid = row["id"]
        if rid in target_marquee_ids or rid.startswith("cortina"):
            marquee_depths[rid.replace("cortina-dampezzo", "cortina")] = row["snow_depth_cm"]

    high_alt = [r for r in results if r.get("mid_elevation", 0) >= 2200]
    mid_alt = [r for r in results if 1800 <= r.get("mid_elevation", 0) < 2200]
    low_alt = [r for r in results if r.get("mid_elevation", 0) < 1800]
    valid_freezing = [r["avg_freezing_level_m"] for r in results if r.get("avg_freezing_level_m", 0) > 0]
    overall_freezing = round(sum(valid_freezing) / len(valid_freezing)) if valid_freezing else 0

    today_str = now_utc.strftime("%Y-%m-%d")
    tracker_payload = {
        "last_updated": now_utc.isoformat(),
        "summary": {
            "overall_sample_size": len(results),
            "high_altitude_snow_anomaly_pct": _avg_key(high_alt, "snow_anomaly_pct"),
            "mid_altitude_snow_anomaly_pct": _avg_key(mid_alt, "snow_anomaly_pct"),
            "low_altitude_snow_anomaly_pct": _avg_key(low_alt, "snow_anomaly_pct"),
            "high_altitude_snow_depth_cm": _avg_key(high_alt, "snow_depth_cm"),
            "mid_altitude_snow_depth_cm": _avg_key(mid_alt, "snow_depth_cm"),
            "low_altitude_snow_depth_cm": _avg_key(low_alt, "snow_depth_cm"),
            "high_altitude_baseline_depth_cm": _avg_key(high_alt, "baseline_snow_depth_cm"),
            "mid_altitude_baseline_depth_cm": _avg_key(mid_alt, "baseline_snow_depth_cm"),
            "low_altitude_baseline_depth_cm": _avg_key(low_alt, "baseline_snow_depth_cm"),
            "overall_avg_freezing_level_m": overall_freezing,
        },
        "resorts": results,
    }

    history_entries = [e for e in existing_history.get("history", []) if e.get("date") != today_str]
    new_entry: dict[str, Any] = {
        "date": today_str,
        "high_alt_snow_depth_cm": tracker_payload["summary"]["high_altitude_snow_depth_cm"],
        "mid_alt_snow_depth_cm": tracker_payload["summary"]["mid_altitude_snow_depth_cm"],
        "low_alt_snow_depth_cm": tracker_payload["summary"]["low_altitude_snow_depth_cm"],
        "high_alt_base_depth_cm": _avg_key(high_alt, "snow_depth_base_cm"),
        "high_alt_peak_depth_cm": _avg_key(high_alt, "snow_depth_peak_cm"),
        "mid_alt_base_depth_cm": _avg_key(mid_alt, "snow_depth_base_cm"),
        "mid_alt_peak_depth_cm": _avg_key(mid_alt, "snow_depth_peak_cm"),
        "low_alt_base_depth_cm": _avg_key(low_alt, "snow_depth_base_cm"),
        "low_alt_peak_depth_cm": _avg_key(low_alt, "snow_depth_peak_cm"),
        "meribel_snow_depth_cm": marquee_depths.get("meribel", 0.0),
        "zermatt_snow_depth_cm": marquee_depths.get("zermatt", 0.0),
        "kitzbuhel_snow_depth_cm": marquee_depths.get("kitzbuhel", 0.0),
        "cortina_snow_depth_cm": marquee_depths.get("cortina", 0.0),
    }
    for row in results:
        rid = row["id"]
        new_entry[f"{rid}_depth_cm"] = row["snow_depth_cm"]
        new_entry[f"{rid}_base_depth_cm"] = row["snow_depth_base_cm"]
        new_entry[f"{rid}_peak_depth_cm"] = row["snow_depth_peak_cm"]

    history_entries.append(new_entry)
    history_entries.sort(key=lambda x: x["date"])
    return tracker_payload, {"history": history_entries}


def build_personal_payloads(
    personal_resorts: list[dict[str, Any]],
    metrics_by_id: dict[str, dict[str, Any]],
    existing_history: dict[str, Any],
    now_utc: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build personal-tracker.json and updated personal-history.json payloads."""
    results = [{**r, **metrics_by_id[r["id"]]} for r in personal_resorts]
    avg_depth = _avg_key(results, "snow_depth_cm")
    valid_freezing = [r["avg_freezing_level_m"] for r in results if r.get("avg_freezing_level_m", 0) > 0]
    avg_freezing = round(sum(valid_freezing) / len(valid_freezing)) if valid_freezing else 0

    today_str = now_utc.strftime("%Y-%m-%d")
    tracker_payload = {
        "last_updated": now_utc.isoformat(),
        "summary": {
            "sample_size": len(results),
            "average_snow_depth_cm": avg_depth,
            "overall_avg_freezing_level_m": avg_freezing,
        },
        "resorts": results,
    }

    history_entries = [e for e in existing_history.get("history", []) if e.get("date") != today_str]
    entry: dict[str, Any] = {"date": today_str, "average_snow_depth_cm": avg_depth}
    for row in results:
        rid = row["id"]
        entry[f"{rid}_depth_cm"] = row["snow_depth_cm"]
        entry[f"{rid}_base_depth_cm"] = row["snow_depth_base_cm"]
        entry[f"{rid}_peak_depth_cm"] = row["snow_depth_peak_cm"]

    history_entries.append(entry)
    history_entries.sort(key=lambda x: x["date"])
    return tracker_payload, {"history": history_entries}
