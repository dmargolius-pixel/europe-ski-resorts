import json
import os
from datetime import datetime, timedelta, timezone
import urllib.request

RESORTS_FILE = "resorts.json"
OUTPUT_FILE = "public/data/el-nino-tracker.json"

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-Tracker/1.0'})
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def get_weekly_metrics(lat, lon):
    # Use past_days=7 for the live forecast endpoint
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&past_days=7&forecast_days=1"
        f"&daily=snowfall_sum,precipitation_sum,freezing_level_height_max"
        f"&timezone=auto"
    )
    try:
        response_json = fetch_json(current_url)
        current_data = response_json.get("daily", {})
    except Exception as e:
        print(f"Error fetching forecast for lat {lat}, lon {lon}: {e}")
        current_data = {}

    snow_list = current_data.get("snowfall_sum") or []
    precip_list = current_data.get("precipitation_sum") or []
    freezing_list = current_data.get("freezing_level_height_max") or []

    current_snow = sum(f for f in snow_list if f is not None)
    current_precip = sum(p for p in precip_list if p is not None)
    valid_freezing = [fl for fl in freezing_list if fl is not None]
    avg_freezing_level = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0

    # 10-Year ERA5 Historical Baseline (2014-2024)
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
            f"&daily=snowfall_sum"
            f"&timezone=auto"
        )
        try:
            hist_json = fetch_json(archive_url)
            hist_data = hist_json.get("daily", {})
            hist_snow_list = hist_data.get("snowfall_sum") or []
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
        print(f"Error: {
