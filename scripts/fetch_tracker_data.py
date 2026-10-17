import json
import os
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

RESORTS_FILE = "resorts.json"
OUTPUT_FILE = "data/el-nino-tracker.json"

def fetch_json(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-Tracker/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())

def get_weekly_metrics(lat, lon):
    # Live forecast endpoint uses daily snowfall_sum
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&past_days=7&forecast_days=1"
        f"&daily=snowfall_sum,precipitation_sum,freezing_level_height_max"
        f"&timezone=auto"
    )
    
    try:
        response_json = fetch_json(current_url, timeout=10)
        current_data = response_json.get("daily", {})
    except Exception as e:
        print(f"Error fetching forecast for lat {lat}, lon {lon}: {e}", flush=True)
        current_data = {}

    snow_list = current_data.get("snowfall_sum") or []
    precip_list = current_data.get("precipitation_sum") or []
    freezing_list = current_data.get("freezing_level_height_max") or []

    current_snow = sum(f for f in snow_list if f is not None)
    current_precip = sum(p for p in precip_list if p is not None)
    valid_freezing = [fl for fl in freezing_list if fl is not None]
    avg_freezing_level = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0

    # 10-Year ERA5 Historical Baseline (2014-2024) using hourly snowfall parameter
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=7)
    era5_snow_totals = []
    
    for year in range(2014, 2024):
        hist_start = f"{year}-{start_date.strftime('%m-%d')}"
        hist_end = f"{year}-{today.strftime('%m-%d')}"
        
        archive_url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}"
            f"&start_date={hist_start}&end_date={hist_end}"
            f"&hourly=snowfall"
            f"&timezone=auto"
        )
        try:
            hist_json = fetch_json(archive_url, timeout=10)
            hist_data = hist_json.get("hourly", {})
            hist_snow_list = hist_data.get("snowfall") or []
            total_hist = sum(s for s in hist_snow_list if s is not None)
            era5_snow_totals.append(total_hist)
        except Exception:
            continue

    baseline_snow = sum(era5_snow_totals) / len(era5_snow_totals) if era5_snow_totals else 1.0
    snow_anomaly_pct = round(((current_snow - baseline_snow) / max(baseline_snow, 0.1)) * 100, 1)

    return {
        "current_snowfall_cm": round(current_snow, 1),
        "baseline_snowfall_cm": round(baseline_snow, 1),
        "snow_anomaly_pct": snow_anomaly_pct,
        "weekly_precip_mm": round(current_precip, 1),
        "avg_freezing_level_m": round(avg_freezing_level)
    }

def main():
    if not os.path.exists(RESORTS_FILE):
        print(f"Error: {RESORTS_FILE} not found.", flush=True)
        return

    with open(RESORTS_FILE, "r") as f:
        resorts = json.load(f)

    results = []
    for r in resorts:
        print(f"Processing {r['name']}...", flush=True)
        metrics = get_weekly_metrics(r["latitude"], r["longitude"])
        results.append({**r, **metrics})

    high_alt = [r for r in results if r.get("base_elevation", 0) > 1800]
    low_alt = [r for r in results if r.get("base_elevation", 0) < 1200]

    avg_high_anomaly = sum(r["snow_anomaly_pct"] for r in high_alt) / len(high_alt) if high_alt else 0
    avg_low_anomaly = sum(r["snow_anomaly_pct"] for r in low_alt) / len(low_alt) if low_alt else 0

    output_payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "overall_sample_size": len(results),
            "high_altitude_snow_anomaly_pct": round(avg_high_anomaly, 1),
            "low_altitude_snow_anomaly_pct": round(avg_low_anomaly, 1)
        },
        "resorts": results
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output_payload, f, indent=2)

    print("Data processing complete.", flush=True)

if __name__ == "__main__":
    main()
