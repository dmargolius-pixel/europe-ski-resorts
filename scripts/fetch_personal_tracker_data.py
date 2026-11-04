import json
import os
import time
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

RESORTS_FILE = "private-data/personal-resorts.json"
OUTPUT_FILE = "data/personal-tracker.json"
HISTORY_FILE = "data/personal-history.json"

def fetch_json(url, timeout=12, retries=3, backoff_factor=3):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-PersonalTracker/1.0'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise e
            sleep_time = backoff_factor * (attempt + 1)
            time.sleep(sleep_time)

def extract_tier_metrics(hourly_data):
    snow_list = hourly_data.get("snowfall") or []
    precip_list = hourly_data.get("precipitation") or []
    freezing_list = hourly_data.get("freezing_level_height") or []
    depth_list = hourly_data.get("snow_depth") or []

    snow_cm = sum(f for f in snow_list if f is not None)
    precip_mm = sum(p for p in precip_list if p is not None)
    valid_freezing = [fl for fl in freezing_list if fl is not None]
    avg_freezing_m = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0

    valid_depths = [d for d in depth_list if d is not None]
    depth_cm = (valid_depths[-1] * 100) if valid_depths else 0.0

    return {
        "snow_cm": snow_cm,
        "precip_mm": precip_mm,
        "avg_freezing_m": avg_freezing_m,
        "depth_cm": depth_cm
    }

def get_resort_metrics(lat, lon, base_elev, mid_elev, peak_elev, previous_resort_data=None):
    # Query Base, Mid, and Peak in a single batched Open-Meteo request
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat},{lat},{lat}&longitude={lon},{lon},{lon}"
        f"&elevation={base_elev},{mid_elev},{peak_elev}"
        f"&past_days=7&forecast_days=1"
        f"&hourly=snowfall,precipitation,freezing_level_height,snow_depth"
        f"&timezone=auto"
    )
    
    fetch_failed = False
    try:
        response_json = fetch_json(current_url, timeout=15)
        if isinstance(response_json, list) and len(response_json) == 3:
            base_hourly = response_json[0].get("hourly", {})
            mid_hourly = response_json[1].get("hourly", {})
            peak_hourly = response_json[2].get("hourly", {})
        else:
            single_hourly = response_json.get("hourly", {}) if isinstance(response_json, dict) else {}
            base_hourly = mid_hourly = peak_hourly = single_hourly
    except Exception as e:
        print(f"Error fetching forecast for lat {lat}, lon {lon}: {e}", flush=True)
        base_hourly = mid_hourly = peak_hourly = {}
        fetch_failed = True

    if fetch_failed and previous_resort_data:
        fallback_mid_depth = previous_resort_data.get("snow_depth_cm", 0.0)
        fallback_mid_snow = previous_resort_data.get("current_snowfall_cm", 0.0)
        return {
            "mid_elevation": mid_elev,
            "current_snowfall_cm": fallback_mid_snow,
            "snowfall_base_cm": previous_resort_data.get("snowfall_base_cm", fallback_mid_snow),
            "snowfall_peak_cm": previous_resort_data.get("snowfall_peak_cm", fallback_mid_snow),
            "baseline_snowfall_cm": previous_resort_data.get("baseline_snowfall_cm", 0.0),
            "snow_anomaly_pct": previous_resort_data.get("snow_anomaly_pct", 0.0),
            "weekly_precip_mm": previous_resort_data.get("weekly_precip_mm", 0.0),
            "avg_freezing_level_m": previous_resort_data.get("avg_freezing_level_m", 0),
            "snow_depth_cm": fallback_mid_depth,
            "snow_depth_base_cm": previous_resort_data.get("snow_depth_base_cm", fallback_mid_depth),
            "snow_depth_peak_cm": previous_resort_data.get("snow_depth_peak_cm", fallback_mid_depth),
            "baseline_snow_depth_cm": previous_resort_data.get("baseline_snow_depth_cm", 0.0)
        }

    base_tier = extract_tier_metrics(base_hourly)
    mid_tier = extract_tier_metrics(mid_hourly)
    peak_tier = extract_tier_metrics(peak_hourly)

    current_snow = mid_tier["snow_cm"]
    current_precip = mid_tier["precip_mm"]
    avg_freezing_level = mid_tier["avg_freezing_m"]
    current_snow_depth = mid_tier["depth_cm"]

    base_snow_depth = base_tier["depth_cm"]
    peak_snow_depth = peak_tier["depth_cm"]

    # If the weather model grid cell returns identical raw snow_depth across all 3 elevations,
    # apply a physical lapse-rate & freezing-level scaling factor for Base and Peak depth
    if current_snow_depth > 0 and (round(base_snow_depth, 1) == round(current_snow_depth, 1) == round(peak_snow_depth, 1)):
        peak_gain_100m = max(0.0, (peak_elev - mid_elev) / 100.0)
        peak_snow_depth = current_snow_depth * (1.0 + peak_gain_100m * 0.04)

        base_drop_100m = max(0.0, (mid_elev - base_elev) / 100.0)
        # Apply steeper melt reduction if the 7-day average freezing level is above the village base
        melt_rate = 0.085 if avg_freezing_level > base_elev else 0.05
        base_snow_depth = max(0.0, current_snow_depth * (1.0 - base_drop_100m * melt_rate))

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=7)
    era5_snow_totals = []
    era5_depth_totals = []
    
    for year in range(2014, 2024):
        hist_start = f"{year}-{start_date.strftime('%m-%d')}"
        hist_end = f"{year}-{today.strftime('%m-%d')}"
        
        archive_url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}&elevation={mid_elev}"
            f"&start_date={hist_start}&end_date={hist_end}"
            f"&hourly=snowfall,snow_depth"
            f"&timezone=auto"
        )
        try:
            hist_json = fetch_json(archive_url, timeout=12)
            hist_data = hist_json.get("hourly", {})
            hist_snow_list = hist_data.get("snowfall") or []
            total_hist = sum(s for s in hist_snow_list if s is not None)
            era5_snow_totals.append(total_hist)

            hist_depth_list = [d for d in (hist_data.get("snow_depth") or []) if d is not None]
            if hist_depth_list:
                depth_cm = hist_depth_list[-1] * 100
                if depth_cm < 250:
                    era5_depth_totals.append(depth_cm)
        except Exception:
            continue

    baseline_snow = sum(era5_snow_totals) / len(era5_snow_totals) if era5_snow_totals else 0.0
    baseline_depth = sum(era5_depth_totals) / len(era5_depth_totals) if era5_depth_totals else 0.0

    if baseline_snow < 2.0 and current_snow < 2.0:
        snow_anomaly_pct = 0.0
    elif baseline_snow < 0.5:
        snow_anomaly_pct = 100.0 if current_snow >= 2.0 else 0.0
    else:
        snow_anomaly_pct = round(((current_snow - baseline_snow) / baseline_snow) * 100, 1)

    return {
        "mid_elevation": mid_elev,
        "current_snowfall_cm": round(current_snow, 1),
        "snowfall_base_cm": round(base_tier["snow_cm"], 1),
        "snowfall_peak_cm": round(peak_tier["snow_cm"], 1),
        "baseline_snowfall_cm": round(baseline_snow, 1),
        "snow_anomaly_pct": snow_anomaly_pct,
        "weekly_precip_mm": round(current_precip, 1),
        "avg_freezing_level_m": round(avg_freezing_level),
        "snow_depth_cm": round(current_snow_depth, 1),
        "snow_depth_base_cm": round(base_snow_depth, 1),
        "snow_depth_peak_cm": round(peak_snow_depth, 1),
        "baseline_snow_depth_cm": round(baseline_depth, 1)
    }

def main():
    if not os.path.exists(RESORTS_FILE):
        print(f"Error: {RESORTS_FILE} not found.", flush=True)
        return

    with open(RESORTS_FILE, "r") as f:
        resorts = json.load(f)

    previous_resorts_map = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r") as f:
                prev_data = json.load(f)
                for pr in prev_data.get("resorts", []):
                    if "id" in pr:
                        previous_resorts_map[pr["id"]] = pr
        except Exception:
            pass

    results = []
    for r in resorts:
        print(f"Processing personal resort: {r['name']}...", flush=True)
        base_elev = r.get("base_elevation", 1500)
        peak_elev = r.get("peak_elevation", 2500)
        mid_elev = round((base_elev + peak_elev) / 2)
        
        prev_resort = previous_resorts_map.get(r["id"])
        metrics = get_resort_metrics(r["latitude"], r["longitude"], base_elev, mid_elev, peak_elev, prev_resort)
        results.append({**r, **metrics})

    valid_depths = [r["snow_depth_cm"] for r in results]
    avg_snow_depth = round(sum(valid_depths) / len(valid_depths), 1) if valid_depths else 0.0

    valid_freezing = [r["avg_freezing_level_m"] for r in results if r.get("avg_freezing_level_m", 0) > 0]
    avg_freezing = round(sum(valid_freezing) / len(valid_freezing)) if valid_freezing else 0

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    output_payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "sample_size": len(results),
            "average_snow_depth_cm": avg_snow_depth,
            "overall_avg_freezing_level_m": avg_freezing
        },
        "resorts": results
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output_payload, f, indent=2)

    history_data = {"history": []}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                history_data = json.load(f)
        except Exception:
            pass

    history_entries = [entry for entry in history_data.get("history", []) if entry.get("date") != today_str]
    
    entry = {"date": today_str, "average_snow_depth_cm": avg_snow_depth}
    for r in results:
        entry[f"{r['id']}_depth_cm"] = r["snow_depth_cm"]
        entry[f"{r['id']}_base_depth_cm"] = r["snow_depth_base_cm"]
        entry[f"{r['id']}_peak_depth_cm"] = r["snow_depth_peak_cm"]

    history_entries.append(entry)
    history_entries.sort(key=lambda x: x["date"])

    with open(HISTORY_FILE, "w") as f:
        json.dump({"history": history_entries}, f, indent=2)

    print("Personal data processing complete.", flush=True)

if __name__ == "__main__":
    main()
