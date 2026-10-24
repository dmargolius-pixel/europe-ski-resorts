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

def get_resort_metrics(lat, lon, elevation, previous_resort_data=None):
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&elevation={elevation}"
        f"&past_days=7&forecast_days=1"
        f"&hourly=snowfall,precipitation,freezing_level_height,snow_depth"
        f"&timezone=auto"
    )
    
    fetch_failed = False
    try:
        response_json = fetch_json(current_url, timeout=12)
        current_data = response_json.get("hourly", {})
    except Exception as e:
        print(f"Error fetching forecast for lat {lat}, lon {lon}: {e}", flush=True)
        current_data = {}
        fetch_failed = True

    if fetch_failed and previous_resort_data:
        return {
            "mid_elevation": elevation,
            "current_snowfall_cm": previous_resort_data.get("current_snowfall_cm", 0.0),
            "baseline_snowfall_cm": previous_resort_data.get("baseline_snowfall_cm", 0.0),
            "snow_anomaly_pct": previous_resort_data.get("snow_anomaly_pct", 0.0),
            "weekly_precip_mm": previous_resort_data.get("weekly_precip_mm", 0.0),
            "avg_freezing_level_m": previous_resort_data.get("avg_freezing_level_m", 0),
            "snow_depth_cm": previous_resort_data.get("snow_depth_cm", 0.0),
            "baseline_snow_depth_cm": previous_resort_data.get("baseline_snow_depth_cm", 0.0)
        }

    snow_list = current_data.get("snowfall") or []
    precip_list = current_data.get("precipitation") or []
    freezing_list = current_data.get("freezing_level_height") or []
    depth_list = current_data.get("snow_depth") or []

    current_snow = sum(f for f in snow_list if f is not None)
    current_precip = sum(p for p in precip_list if p is not None)
    valid_freezing = [fl for fl in freezing_list if fl is not None]
    avg_freezing_level = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0

    valid_depths = [d for d in depth_list if d is not None]
    current_snow_depth = (valid_depths[-1] * 100) if valid_depths else 0.0

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=7)
    era5_snow_totals = []
    era5_depth_totals = []
    
    for year in range(2014, 2024):
        hist_start = f"{year}-{start_date.strftime('%m-%d')}"
        hist_end = f"{year}-{today.strftime('%m-%d')}"
        
        archive_url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}&elevation={elevation}"
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
        "mid_elevation": elevation,
        "current_snowfall_cm": round(current_snow, 1),
        "baseline_snowfall_cm": round(baseline_snow, 1),
        "snow_anomaly_pct": snow_anomaly_pct,
        "weekly_precip_mm": round(current_precip, 1),
        "avg_freezing_level_m": round(avg_freezing_level),
        "snow_depth_cm": round(current_snow_depth, 1),
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
        metrics = get_resort_metrics(r["latitude"], r["longitude"], mid_elev, prev_resort)
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

    history_entries.append(entry)
    history_entries.sort(key=lambda x: x["date"])

    with open(HISTORY_FILE, "w") as f:
        json.dump({"history": history_entries}, f, indent=2)

    print("Personal data processing complete.", flush=True)

if __name__ == "__main__":
    main()
