import json
import os
import time
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

# True Summit/Glacier coordinates, active autumn glacier status, and operational mid-mountain hub overrides
RESORT_GEO_OVERRIDES = {
    # Austria
    "kaprun":         {"peak_lat": 47.1883, "peak_lon": 12.6875, "mid_elev": 2450, "glacier": True},
    "stubai-glacier": {"peak_lat": 46.9792, "peak_lon": 11.1108, "mid_elev": 2620, "glacier": True},
    "solden":         {"peak_lat": 46.9242, "peak_lon": 10.9161, "mid_elev": 2675, "glacier": True},
    "obergurgl":      {"peak_lat": 46.8450, "peak_lon": 11.0560, "glacier": False},
    "st-anton":       {"peak_lat": 47.1578, "peak_lon": 10.2131, "glacier": False},
    "ischgl":         {"peak_lat": 46.9422, "peak_lon": 10.3200, "glacier": False},
    "mayrhofen":      {"peak_lat": 47.1330, "peak_lon": 11.8050, "glacier": False},
    "zell-am-see":    {"peak_lat": 47.3292, "peak_lon": 12.7375, "glacier": False},
    "kitzbuhel":      {"peak_lat": 47.4120, "peak_lon": 12.3600, "glacier": False},
    "saalbach":       {"peak_lat": 47.3620, "peak_lon": 12.6020, "glacier": False},
    "obertauern":     {"peak_lat": 47.2620, "peak_lon": 13.5650, "glacier": False},
    # Switzerland
    "zermatt":        {"peak_lat": 45.9383, "peak_lon": 7.7297,  "mid_elev": 2939, "glacier": True},
    "saas-fee":       {"peak_lat": 46.0583, "peak_lon": 7.9050,  "mid_elev": 2870, "glacier": True},
    "engelberg":      {"peak_lat": 46.7720, "peak_lon": 8.4378,  "mid_elev": 2428, "glacier": True},
    "verbier":        {"peak_lat": 46.0803, "peak_lon": 7.3189,  "glacier": True},
    "crans-montana":  {"peak_lat": 46.3820, "peak_lon": 7.5160,  "glacier": True},
    "st-moritz":      {"peak_lat": 46.5040, "peak_lon": 9.7950,  "glacier": False},
    "grindelwald":    {"peak_lat": 46.5780, "peak_lon": 7.9620,  "glacier": False},
    "adelboden-lenk": {"peak_lat": 46.4250, "peak_lon": 7.5280,  "glacier": False},
    # France
    "tignes":         {"peak_lat": 45.4142, "peak_lon": 6.8711,  "mid_elev": 2778, "glacier": True},
    "val-disere":     {"peak_lat": 45.4111, "peak_lon": 7.0403,  "mid_elev": 2653, "glacier": True},
    "les-deux-alpes": {"peak_lat": 44.9911, "peak_lon": 6.1783,  "mid_elev": 2625, "glacier": True},
    "chamonix":       {"peak_lat": 45.9500, "peak_lon": 6.9600,  "mid_elev": 2438, "glacier": True},
    "val-thorens":    {"peak_lat": 45.2750, "peak_lon": 6.6050,  "glacier": True},
    "belle-plagne":   {"peak_lat": 45.4920, "peak_lon": 6.7740,  "glacier": True},
    "les-arcs":       {"peak_lat": 45.5310, "peak_lon": 6.8490,  "glacier": True},
    "meribel":        {"peak_lat": 45.3260, "peak_lon": 6.5920,  "glacier": False},
    "avoriaz":        {"peak_lat": 46.1780, "peak_lon": 6.8050,  "glacier": False},
    "isola-2000":     {"peak_lat": 44.1750, "peak_lon": 7.1680,  "glacier": False},
    "serre-chevalier":{"peak_lat": 44.9120, "peak_lon": 6.5550,  "glacier": False},
    "flaine":         {"peak_lat": 45.9820, "peak_lon": 6.7250,  "glacier": False},
    # Italy & Germany
    "cervinia":       {"peak_lat": 45.9383, "peak_lon": 7.7297,  "mid_elev": 2812, "glacier": True},
    "cortina":        {"peak_lat": 46.5380, "peak_lon": 12.0620, "glacier": False},
    "livigno":        {"peak_lat": 46.5200, "peak_lon": 10.1100, "glacier": False},
    "courmayeur":     {"peak_lat": 45.7780, "peak_lon": 6.9250,  "glacier": False},
    "garmisch":       {"peak_lat": 47.4110, "peak_lon": 10.9850, "glacier": False}
}

def fetch_json(url, timeout=15, retries=3, backoff_factor=3):
    req = urllib.request.Request(url, headers={'User-Agent': 'EuropeSki-UnifiedTracker/2.0'})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise e
            sleep_time = backoff_factor * (attempt + 1)
            time.sleep(sleep_time)

def simulate_piste_snowpack(snow_list, temp_list, elev_m, is_glacier=False):
    """
    Runs a 60-day hourly piste accumulation, compaction, and degree-hour melt model.
    Accounts for preserved autumn glacier/snow-farming base on high-alpine glacier sectors.
    """
    now_month = datetime.now(timezone.utc).month
    # Autumn/Early-Winter preserved glacier & snow-farming base (Oct-Dec)
    if is_glacier and now_month in (10, 11, 12, 1, 2, 3, 4, 5):
        if elev_m >= 2900:
            pack_cm = 48.0 + min(30.0, (elev_m - 2900) * 0.04)
        elif elev_m >= 2400:
            pack_cm = 18.0 + ((elev_m - 2400) / 500.0) * 30.0
        else:
            pack_cm = 0.0
    else:
        pack_cm = 0.0

    n = min(len(snow_list), len(temp_list))
    # Exclude the trailing 24 forecast hours so we only model up to the current hour
    cutoff = max(0, n - 24)

    for i in range(cutoff):
        s = snow_list[i] if snow_list[i] is not None else 0.0
        t = temp_list[i] if temp_list[i] is not None else 0.0

        # Add fresh snowfall (compacted piste equivalent ~0.78 of fresh dendritic snow)
        if s > 0:
            pack_cm += s * 0.78

        # Slow natural settling when cold
        if pack_cm > 0 and t <= 0.5:
            pack_cm *= 0.9998

        # Degree-hour ablation (melt) when air temperature at this elevation exceeds +0.5°C
        if pack_cm > 0 and t > 0.5:
            if is_glacier and elev_m >= 2400:
                melt_factor = 0.012 if elev_m >= 2850 else 0.022
            else:
                melt_factor = 0.025 if elev_m >= 2300 else (0.040 if elev_m >= 1800 else 0.060)
            pack_cm = max(0.0, pack_cm - (t - 0.5) * melt_factor)

    return round(pack_cm, 1)

def extract_tier_metrics(hourly_data, elev_m, is_glacier=False):
    snow_list = hourly_data.get("snowfall") or []
    precip_list = hourly_data.get("precipitation") or []
    freezing_list = hourly_data.get("freezing_level_height") or []
    depth_list = hourly_data.get("snow_depth") or []
    temp_list = hourly_data.get("temperature_2m") or []

    # Slice the most recent 7 completed days (168 hours prior to the 24h forecast window)
    end_idx = max(0, len(snow_list) - 24)
    start_7d = max(0, end_idx - 168)

    snow_7d = snow_list[start_7d:end_idx] if end_idx > 0 else snow_list
    precip_7d = precip_list[start_7d:end_idx] if end_idx > 0 else precip_list
    freezing_7d = freezing_list[start_7d:end_idx] if end_idx > 0 else freezing_list
    depth_upto_now = depth_list[:end_idx] if end_idx > 0 else depth_list

    snow_cm = sum(f for f in snow_7d if f is not None)
    precip_mm = sum(p for p in precip_7d if p is not None)
    valid_freezing = [fl for fl in freezing_7d if fl is not None]
    avg_freezing_m = sum(valid_freezing) / len(valid_freezing) if valid_freezing else 0

    valid_depths = [d for d in depth_upto_now if d is not None]
    raw_depth_cm = (valid_depths[-1] * 100.0) if valid_depths else 0.0
    # Filter out NWP permanent glacier ice-mask constants (>= 250 cm in autumn)
    if raw_depth_cm >= 250.0:
        raw_depth_cm = 0.0

    modelled_depth_cm = simulate_piste_snowpack(snow_list, temp_list, elev_m, is_glacier=is_glacier)
    final_depth_cm = max(raw_depth_cm, modelled_depth_cm)

    return {
        "snow_cm": snow_cm,
        "precip_mm": precip_mm,
        "avg_freezing_m": avg_freezing_m,
        "depth_cm": final_depth_cm
    }

def get_resort_metrics(resort_id, base_lat, base_lon, base_elev, mid_elev, peak_elev, previous_resort_data=None):
    override = RESORT_GEO_OVERRIDES.get(resort_id, {})
    peak_lat = override.get("peak_lat", base_lat)
    peak_lon = override.get("peak_lon", base_lon)
    mid_lat = round((base_lat + peak_lat) / 2.0, 4)
    mid_lon = round((base_lon + peak_lon) / 2.0, 4)
    is_glacier = override.get("glacier", peak_elev >= 3150)

    # Query 60 days of hourly history across true Base, Mid, and Peak coordinates & elevations
    current_url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={base_lat},{mid_lat},{peak_lat}"
        f"&longitude={base_lon},{mid_lon},{peak_lon}"
        f"&elevation={base_elev},{mid_elev},{peak_elev}"
        f"&past_days=60&forecast_days=1"
        f"&hourly=snowfall,precipitation,freezing_level_height,snow_depth,temperature_2m"
        f"&timezone=auto"
    )
    
    fetch_failed = False
    try:
        response_json = fetch_json(current_url, timeout=20)
        if isinstance(response_json, list) and len(response_json) == 3:
            base_hourly = response_json[0].get("hourly", {})
            mid_hourly = response_json[1].get("hourly", {})
            peak_hourly = response_json[2].get("hourly", {})
        else:
            single_hourly = response_json.get("hourly", {}) if isinstance(response_json, dict) else {}
            base_hourly = mid_hourly = peak_hourly = single_hourly
    except Exception as e:
        print(f"Error fetching forecast for {resort_id}: {e}", flush=True)
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

    base_tier = extract_tier_metrics(base_hourly, base_elev, is_glacier=False)
    mid_tier = extract_tier_metrics(mid_hourly, mid_elev, is_glacier=is_glacier)
    peak_tier = extract_tier_metrics(peak_hourly, peak_elev, is_glacier=is_glacier)

    current_snow = mid_tier["snow_cm"]
    current_precip = mid_tier["precip_mm"]
    avg_freezing_level = mid_tier["avg_freezing_m"]
    current_snow_depth = mid_tier["depth_cm"]
    base_snow_depth = base_tier["depth_cm"]
    peak_snow_depth = peak_tier["depth_cm"]

    # Ensure monotonic vertical snowpack profile (Base <= Mid <= Peak)
    if current_snow_depth > 0 and round(peak_snow_depth, 1) <= round(current_snow_depth, 1):
        peak_gain_100m = max(0.0, (peak_elev - mid_elev) / 100.0)
        peak_snow_depth = current_snow_depth * (1.0 + peak_gain_100m * 0.04)

    if round(base_snow_depth, 1) > round(current_snow_depth, 1):
        base_drop_100m = max(0.0, (mid_elev - base_elev) / 100.0)
        melt_rate = 0.085 if avg_freezing_level > base_elev else 0.05
        base_snow_depth = max(0.0, current_snow_depth * (1.0 - base_drop_100m * melt_rate))

    # Dynamic rolling 10-year historical window using mid-mountain coordinates
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=7)
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
            f"latitude={mid_lat}&longitude={mid_lon}&elevation={mid_elev}"
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
    if era5_depth_totals:
        baseline_depth = sum(era5_depth_totals) / len(era5_depth_totals)
    else:
        # Fallback if ERA5 grid cell is masked as permanent glacier ice (>=250cm, e.g. Zermatt)
        baseline_depth = round(baseline_snow * 1.9, 1) if baseline_snow > 0 else 0.0

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

def process_public_tracker():
    resorts_file = "resorts.json"
    output_file = "data/el-nino-tracker.json"
    history_file = "data/season-history.json"

    if not os.path.exists(resorts_file):
        print(f"Skipping public tracker: {resorts_file} not found.", flush=True)
        return

    with open(resorts_file, "r") as f:
        resorts = json.load(f)

    previous_resorts_map = {}
    if os.path.exists(output_file):
        try:
            with open(output_file, "r") as f:
                prev_data = json.load(f)
                for pr in prev_data.get("resorts", []):
                    if "id" in pr:
                        previous_resorts_map[pr["id"]] = pr
        except Exception:
            pass

    results = []
    marquee_depths = {}
    target_marquee_ids = {"meribel", "zermatt", "kitzbuhel", "cortina"}

    for r in resorts:
        rid = r["id"]
        print(f"Processing public resort: {r['name']}...", flush=True)
        base_elev = r.get("base_elevation", 1500)
        peak_elev = r.get("peak_elevation", 2500)
        override = RESORT_GEO_OVERRIDES.get(rid, {})
        mid_elev = override.get("mid_elev", r.get("mid_elevation", round((base_elev + peak_elev) / 2)))
        
        prev_resort = previous_resorts_map.get(rid)
        metrics = get_resort_metrics(rid, r["latitude"], r["longitude"], base_elev, mid_elev, peak_elev, prev_resort)
        
        resort_data = {**r, **metrics}
        results.append(resort_data)

        if rid in target_marquee_ids or rid.startswith("cortina"):
            marquee_depths[rid.replace("cortina-dampezzo", "cortina")] = resort_data["snow_depth_cm"]

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

    avg_high_base_tier = avg_key(high_alt, "snow_depth_base_cm")
    avg_mid_base_tier = avg_key(mid_alt, "snow_depth_base_cm")
    avg_low_base_tier = avg_key(low_alt, "snow_depth_base_cm")

    avg_high_peak_tier = avg_key(high_alt, "snow_depth_peak_cm")
    avg_mid_peak_tier = avg_key(mid_alt, "snow_depth_peak_cm")
    avg_low_peak_tier = avg_key(low_alt, "snow_depth_peak_cm")

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

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output_payload, f, indent=2)

    history_data = {"history": []}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
                history_data = json.load(f)
        except Exception:
            pass

    history_entries = [entry for entry in history_data.get("history", []) if entry.get("date") != today_str]
    
    new_entry = {
        "date": today_str,
        "high_alt_snow_depth_cm": avg_high_depth,
        "mid_alt_snow_depth_cm": avg_mid_depth,
        "low_alt_snow_depth_cm": avg_low_depth,
        "high_alt_base_depth_cm": avg_high_base_tier,
        "high_alt_peak_depth_cm": avg_high_peak_tier,
        "mid_alt_base_depth_cm": avg_mid_base_tier,
        "mid_alt_peak_depth_cm": avg_mid_peak_tier,
        "low_alt_base_depth_cm": avg_low_base_tier,
        "low_alt_peak_depth_cm": avg_low_peak_tier,
        "meribel_snow_depth_cm": marquee_depths.get("meribel", 0.0),
        "zermatt_snow_depth_cm": marquee_depths.get("zermatt", 0.0),
        "kitzbuhel_snow_depth_cm": marquee_depths.get("kitzbuhel", 0.0),
        "cortina_snow_depth_cm": marquee_depths.get("cortina", 0.0)
    }

    for r in results:
        rid = r["id"]
        new_entry[f"{rid}_depth_cm"] = r["snow_depth_cm"]
        new_entry[f"{rid}_base_depth_cm"] = r["snow_depth_base_cm"]
        new_entry[f"{rid}_peak_depth_cm"] = r["snow_depth_peak_cm"]

    history_entries.append(new_entry)
    history_entries.sort(key=lambda x: x["date"])

    with open(history_file, "w") as f:
        json.dump({"history": history_entries}, f, indent=2)

    print("Public tracker update complete.", flush=True)

def process_personal_tracker():
    resorts_file = "private-data/personal-resorts.json"
    output_file = "data/personal-tracker.json"
    history_file = "data/personal-history.json"

    if not os.path.exists(resorts_file):
        print(f"Skipping personal tracker: {resorts_file} not found.", flush=True)
        return

    with open(resorts_file, "r") as f:
        resorts = json.load(f)

    previous_resorts_map = {}
    if os.path.exists(output_file):
        try:
            with open(output_file, "r") as f:
                prev_data = json.load(f)
                for pr in prev_data.get("resorts", []):
                    if "id" in pr:
                        previous_resorts_map[pr["id"]] = pr
        except Exception:
            pass

    results = []
    for r in resorts:
        rid = r["id"]
        print(f"Processing personal resort: {r['name']}...", flush=True)
        base_elev = r.get("base_elevation", 1500)
        peak_elev = r.get("peak_elevation", 2500)
        override = RESORT_GEO_OVERRIDES.get(rid, {})
        mid_elev = override.get("mid_elev", r.get("mid_elevation", round((base_elev + peak_elev) / 2)))
        
        prev_resort = previous_resorts_map.get(rid)
        metrics = get_resort_metrics(rid, r["latitude"], r["longitude"], base_elev, mid_elev, peak_elev, prev_resort)
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

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output_payload, f, indent=2)

    history_data = {"history": []}
    if os.path.exists(history_file):
        try:
            with open(history_file, "r") as f:
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

    with open(history_file, "w") as f:
        json.dump({"history": history_entries}, f, indent=2)

    print("Personal tracker update complete.", flush=True)

def main():
    print("Starting unified ski weather data pipeline...", flush=True)
    process_public_tracker()
    process_personal_tracker()
    print("Unified weather pipeline complete.", flush=True)

if __name__ == "__main__":
    main()
