import json
import os
import time
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

RESORTS_FILE = "resorts.json"
OUTPUT_FILE = "data/el-nino-tracker.json"
HISTORY_FILE = "data/season-history.json"

def fetch_json(url, timeout=12, retries=3, backoff_factor=3):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-Tracker/1.0'})
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
    # Query Base, Mid, and Peak in a single batched Open-Meteo call
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

    # Scale Peak independently if the NWP grid cell returned the same (or lower) depth as Mid-Mountain
    if current_snow_depth > 0 and round(peak_snow_depth, 1) <= round(current_snow_depth, 1):
        peak_gain_100m = max(0.0, (peak_elev - mid_elev) / 100.0)
        peak_snow_depth = current_snow_depth * (1.0 + peak_gain_100m * 0.04)

    # Scale Base independently if the NWP grid cell returned the same (or higher) depth as Mid-Mountain
    if current_snow_depth > 0 and round(base_snow_depth, 1) >= round(current_snow_depth, 1):
        base_drop_100m = max(0.0, (mid_elev - base_elev) / 100.0)
        melt_rate = 0.085 if avg_freezing_level > base_elev else 0.05
        base_snow_depth = max(0.0, current_snow_depth * (1.0 - base_drop_100m * melt_rate))

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=7)
    # Dynamically calculate the last 10 completed calendar years
    current_calendar_year = datetime.now(timezone.utc).year
    start_historical_year = current_calendar_year - 10
    end_historical_year = current_calendar_year - 1
    
    era5_snow_totals = []
    era5_depth_totals = []
    
    for year in range(start_historical_year, end_historical_year + 1):
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
        except Exception as e:
            print(f"Note: Could not read previous tracker file for fallback cache: {e}", flush=True)

    results = []
    marquee_depths = {}
    target_marquee_ids = {"meribel", "zermatt", "kitzbuhel", "cortina"}

    for r in resorts:
        print(f"Processing {r['name']}...", flush=True)
        base_elev = r.get("base_elevation", 1500)
        peak_elev = r.get("peak_elevation", 2500)
        mid_elev = round((base_elev + peak_elev) / 2)
        
        prev_resort = previous_resorts_map.get(r["id"])
        metrics = get_resort_metrics(
            r["latitude"],
            r["longitude"],
            base_elev,
            mid_elev,
            peak_elev,
            prev_resort
        )
        
        resort_data = {**r, **metrics}
        results.append(resort_data)

        if r["id"] in target_marquee_ids:
            marquee_depths[r["id"]] = resort_data["snow_depth_cm"]
        elif r["id"].startswith("cortina"):
            marquee_depths["cortina"] = resort_data["snow_depth_cm"]

    # Group by mid-mountain elevation: High (>= 2200m), Mid (1800-2199m), Low (< 1800m)
    high_alt = [r for r in results if r.get("mid_elevation", 0) >= 2200]
    mid_alt = [r for r in results if 1800 <= r.get("mid_elevation", 0) < 2200]
    low_alt = [r for r in results if r.get("mid_elevation", 0) < 1800]

    def avg_key(arr, key):
        return round(sum(r.get(key, 0.0) for r in arr) / len(arr), 1) if arr else 0.0

    avg_high_anomaly = avg_key(high_alt, "snow_anomaly_pct")
    avg_mid_anomaly = avg_key(mid_alt, "snow_anomaly_pct")
    avg_low_anomaly = avg_key(low_alt, "snow_anomaly_pct")

    avg_high_depth = avg_key(high_alt, "snow_depth_cm")
    avg_mid_depth = avg_key(mid_alt, "snow_depth_cm")
    avg_low_depth = avg_key(low_alt, "snow_depth_cm")

    avg_high_base_tier_depth = avg_key(high_alt, "snow_depth_base_cm")
    avg_mid_base_tier_depth = avg_key(mid_alt, "snow_depth_base_cm")
    avg_low_base_tier_depth = avg_key(low_alt, "snow_depth_base_cm")

    avg_high_peak_tier_depth = avg_key(high_alt, "snow_depth_peak_cm")
    avg_mid_peak_tier_depth = avg_key(mid_alt, "snow_depth_peak_cm")
    avg_low_peak_tier_depth = avg_key(low_alt, "snow_depth_peak_cm")

    avg_high_base_depth = avg_key(high_alt, "baseline_snow_depth_cm")
    avg_mid_base_depth = avg_key(mid_alt, "baseline_snow_depth_cm")
    avg_low_base_depth = avg_key(low_alt, "baseline_snow_depth_cm")

    valid_freezing_all = [r["avg_freezing_level_m"] for r in results if r.get("avg_freezing_level_m", 0) > 0]
    overall_freezing_level = round(sum(valid_freezing_all) / len(valid_freezing_all)) if valid_freezing_all else 0

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    output_payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "overall_sample_size": len(results),
            "high_altitude_snow_anomaly_pct": avg_high_anomaly,
            "mid_altitude_snow_anomaly_pct": avg_mid_anomaly,
            "low_altitude_snow_anomaly_pct": avg_low_anomaly,
            "high_altitude_snow_depth_cm": avg_high_depth,
            "mid_altitude_snow_depth_cm": avg_mid_depth,
            "low_altitude_snow_depth_cm": avg_low_depth,
            "high_altitude_baseline_depth_cm": avg_high_base_depth,
            "mid_altitude_baseline_depth_cm": avg_mid_base_depth,
            "low_altitude_baseline_depth_cm": avg_low_base_depth,
            "overall_avg_freezing_level_m": overall_freezing_level
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
            history_data = {"history": []}

    history_entries = [entry for entry in history_data.get("history", []) if entry.get("date") != today_str]
    
    new_entry = {
        "date": today_str,
        # Tier Mid-Mountain Averages (legacy & standard)
        "high_alt_snow_depth_cm": avg_high_depth,
        "mid_alt_snow_depth_cm": avg_mid_depth,
        "low_alt_snow_depth_cm": avg_low_depth,
        # Tier Base & Peak Averages for Tier Elevation Ribbon Mode
        "high_alt_base_depth_cm": avg_high_base_tier_depth,
        "high_alt_peak_depth_cm": avg_high_peak_tier_depth,
        "mid_alt_base_depth_cm": avg_mid_base_tier_depth,
        "mid_alt_peak_depth_cm": avg_mid_peak_tier_depth,
        "low_alt_base_depth_cm": avg_low_base_tier_depth,
        "low_alt_peak_depth_cm": avg_low_peak_tier_depth,
        # Legacy Marquee Resort Keys
        "meribel_snow_depth_cm": marquee_depths.get("meribel", 0.0),
        "zermatt_snow_depth_cm": marquee_depths.get("zermatt", 0.0),
        "kitzbuhel_snow_depth_cm": marquee_depths.get("kitzbuhel", 0.0),
        "cortina_snow_depth_cm": marquee_depths.get("cortina", 0.0)
    }

    # Store Base, Mid, and Peak snow depth for all 41 tracked resorts
    for r in results:
        rid = r["id"]
        new_entry[f"{rid}_depth_cm"] = r["snow_depth_cm"]
        new_entry[f"{rid}_base_depth_cm"] = r["snow_depth_base_cm"]
        new_entry[f"{rid}_peak_depth_cm"] = r["snow_depth_peak_cm"]

    history_entries.append(new_entry)
    history_entries.sort(key=lambda x: x["date"])

    with open(HISTORY_FILE, "w") as f:
        json.dump({"history": history_entries}, f, indent=2)

    print("Data processing and seasonal history update complete.", flush=True)

if __name__ == "__main__":
    main()
