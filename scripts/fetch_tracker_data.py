import json
import os
from datetime import datetime, timedelta
import urllib.request

RESORTS_FILE = "resorts.json"
OUTPUT_FILE = "public/data/el-nino-tracker.json"

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-Tracker/1.0'})
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def get_weekly_metrics(lat, lon):
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=7)
    
    # 1. Fetch Current Week Actuals via Open-Meteo Weather API
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&start_date={start_date}&end_date={today}"
        f"&daily=snowfall_sum,precipitation_sum,freezing_level_height_max,temperature_2m_mean"
        f"&timezone=auto"
    )
    current_data = fetch_json(current_url)["daily"]
    
    current_snow = sum(filter(None, current_data.get("snowfall_sum", [])))
    current_precip = sum(filter(None, current_data.get("precipitation_sum", [])))
    avg_freezing_level = (
        sum(filter(None, current_data.get("freezing_level_height_max", []))) / 7
        if current_data.get("freezing_level_height_max") else 0
    )

    # 2. Fetch 10-Year ERA5 Historical Baseline for the exact same calendar week (2014-2024)
    era5_snow_totals = []
    for year in range(2014, 2024):
        hist_start = f"{year}-{start_date.strftime('%m-%d')}"
        hist_end = f"{year}-{today.strftime('%m-%d')}"
        
        archive_url = (
            f"https://archive-api.open-meteo.com/v1/archive?"
            f"latitude={lat}&longitude={lon}"
            f"&start_date={hist_start}&end_date={hist_end}"
            f"&daily=snowfall_sum"
            f"&timezone=auto"
        )
        try:
            hist_data = fetch_json(archive_url)["daily"]
            era5_snow_totals.append(sum(filter(None, hist_data.get("snowfall_sum", []))))
        except Exception:
            continue

    baseline_snow = sum(era5_snow_totals) / len(era5_snow_totals) if era5_snow_totals else 1.0
    
    # Calculate anomaly vs baseline
    snow_anomaly_pct = round(((current_snow - baseline_snow) / max(baseline_snow, 0.1)) * 100, 1)

    return {
        "current_snowfall_cm": round(current_snow, 1),
        "baseline_snowfall_cm": round(baseline_snow, 1),
        "snow_anomaly_pct": snow_anomaly_pct,
        "weekly_precip_mm": round(current_precip, 1),
        "avg_freezing_level_m": round(avg_freezing_level)
    }

def main():
    with open(RESORTS_FILE, "r") as f:
        resorts = json.load(f)

    results = []
    for r in resorts:
        print(f"Processing {r['name']}...")
        metrics = get_weekly_metrics(r["latitude"], r["longitude"])
        results.append({**r, **metrics})

    # Summary Statistics across whole sample
    high_alt = [r for r in results if r["tier"] == "High Altitude"]
    low_alt = [r for r in results if r["tier"] == "Low Altitude"]

    avg_high_anomaly = sum(r["snow_anomaly_pct"] for r in high_alt) / len(high_alt) if high_alt else 0
    avg_low_anomaly = sum(r["snow_anomaly_pct"] for r in low_alt) / len(low_alt) if low_alt else 0

    output_payload = {
        "last_updated": datetime.utcnow().isoformat(),
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

    print("Data processing complete.")

if __name__ == "__main__":
    main()
