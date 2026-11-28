"""Unit tests for the refactored ski weather tracker pipeline."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import fetch_all_trackers
from build_climatology import compute_resort_climatology_from_daily
from payload_builders import build_personal_payloads, build_public_payloads
from snowpack_model import (
    extract_tier_metrics,
    find_current_hour_end_idx,
    resolve_raw_snow_depth_cm,
    simulate_piste_snowpack,
)
from tracker_lib import (
    compute_snow_anomaly_pct,
    deduplicate_resorts,
    is_already_updated_today,
    lookup_climatology_baseline,
    resolve_resort_geometry,
    validate_tracker_results,
)


def _hourly_series(start_iso: str, hours: int) -> list[str]:
    start = datetime.fromisoformat(start_iso)
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(hours)]


class TrackerPipelineTests(unittest.TestCase):
    def test_deduplicate_resorts_removes_overlap(self) -> None:
        pub = [{"id": "meribel", "name": "Méribel"}, {"id": "zermatt", "name": "Zermatt"}]
        pers = [{"id": "meribel", "name": "Méribel"}, {"id": "tignes", "name": "Tignes"}]
        unique = deduplicate_resorts(pub, pers)
        self.assertEqual([r["id"] for r in unique], ["meribel", "zermatt", "tignes"])

    def test_find_current_hour_end_idx_includes_overnight_snow(self) -> None:
        # 2 full local CET days: Nov 27 00:00 .. Nov 28 23:00 (48 hours)
        times = _hourly_series("2026-11-27T00:00", 48)
        # Cron runs at 05:15 UTC = 06:15 CET -> should include 00:00..06:00 CET on Nov 28 (index 30 inclusive -> end_idx 31).
        now_utc = datetime(2026, 11, 28, 5, 15, tzinfo=timezone.utc)
        end_idx = find_current_hour_end_idx(times, utc_offset_seconds=3600, now_utc=now_utc)
        self.assertEqual(end_idx, 31)
        self.assertEqual(times[end_idx - 1], "2026-11-28T06:00")

    def test_glacier_depot_anchored_to_15_october_not_sliding_21_days(self) -> None:
        # 40-day window starting 1 Oct 2026 -> 15 Oct is hour 336.
        times = _hourly_series("2026-10-01T00:00", 40 * 24)
        snow = [0.0] * len(times)
        # Warm 5C in early Oct before depot, cold -4C from 15 Oct onward.
        temps = [5.0 if i < 14 * 24 else -4.0 for i in range(len(times))]

        pack_before, _ = simulate_piste_snowpack(
            times, snow, temps, elev_m=2939, end_idx=13 * 24, is_glacier_sector=True,
            now_utc=datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc),
        )
        # Before 15 Oct depot has not been deployed (active Oct floor applies after step loop -> check state).
        pack_after, state_after = simulate_piste_snowpack(
            times, snow, temps, elev_m=2939, end_idx=35 * 24, is_glacier_sector=True,
            now_utc=datetime(2026, 11, 5, 5, 0, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(state_after)
        assert state_after is not None
        self.assertTrue(state_after["depot_applied"])
        self.assertGreaterEqual(pack_after, 62.0)

        # Carrying forward into February must NOT re-inject the depot if snow has already accumulated.
        feb_times = _hourly_series("2027-01-15T00:00", 30 * 24)
        feb_snow = [0.0] * len(feb_times)
        feb_temps = [-5.0] * len(feb_times)
        prior_jan = {
            "season": "2026-2027",
            "last_hour": feb_times[0],
            "pack_cm": 195.0,
            "depot_applied": True,
            "nwp_glacier_floor_cm": 0.0,
        }
        feb_pack, feb_state = simulate_piste_snowpack(
            feb_times, feb_snow, feb_temps, elev_m=2939, end_idx=len(feb_times),
            is_glacier_sector=True, prior_state=prior_jan,
            now_utc=datetime(2027, 2, 14, 5, 0, tzinfo=timezone.utc),
        )
        # Settling from 195 cm over ~30 cold days preserves >160 cm (no 60-day cliff to 0!).
        self.assertGreater(feb_pack, 160.0)
        self.assertEqual(feb_state["season"], "2026-2027")

    def test_cumulative_snowpack_carries_november_snow_into_february(self) -> None:
        # Simulate non-glacier resort with 140 cm carried into mid-Jan, then dry cold month.
        jan_feb_times = _hourly_series("2027-01-10T00:00", 25 * 24)
        snow = [0.0] * len(jan_feb_times)
        temps = [-6.0] * len(jan_feb_times)
        prior = {
            "season": "2026-2027",
            "last_hour": "2027-01-15T06:00",
            "pack_cm": 140.0,
            "depot_applied": False,
            "nwp_glacier_floor_cm": 0.0,
        }
        pack, state = simulate_piste_snowpack(
            jan_feb_times, snow, temps, elev_m=2100, end_idx=len(jan_feb_times),
            is_glacier_sector=False, prior_state=prior,
            now_utc=datetime(2027, 2, 4, 5, 0, tzinfo=timezone.utc),
        )
        # Without carry-forward this would have been 0.0 cm!
        self.assertGreater(pack, 120.0)
        self.assertEqual(state["last_hour"], jan_feb_times[-1])

    def test_resolve_raw_snow_depth_keeps_genuine_winter_above_250cm_and_filters_glacier_masks(self) -> None:
        feb_now = datetime(2027, 2, 20, 5, 15, tzinfo=timezone.utc)
        nov_now = datetime(2026, 11, 20, 5, 15, tzinfo=timezone.utc)

        # Case 1: Genuine deep winter snowpack (295 cm raw, 240 cm modelled, 0 floor) -> preserved!
        winter_series = [0.5 + (i * 0.01) for i in range(246)]  # ends at 2.95 m
        raw_cm, floor = resolve_raw_snow_depth_cm(winter_series, modelled_depth_cm=240.0, now_utc=feb_now)
        self.assertEqual(raw_cm, 295.0)
        self.assertEqual(floor, 0.0)

        # Case 2: Autumn permanent NWP glacier cell (min 320 cm, latest 365 cm) -> floor recorded & subtracted!
        glacier_series = [3.20] * 100 + [3.65] * 20
        adj_cm, detected_floor = resolve_raw_snow_depth_cm(glacier_series, modelled_depth_cm=55.0, now_utc=nov_now)
        self.assertEqual(detected_floor, 320.0)
        self.assertEqual(adj_cm, 45.0)

        # Case 3: Unflagged early-season >250 cm spike with tiny modelled pack -> rejected!
        spike_series = [0.1] * 50 + [3.10]
        spike_cm, _ = resolve_raw_snow_depth_cm(spike_series, modelled_depth_cm=25.0, now_utc=nov_now)
        self.assertEqual(spike_cm, 0.0)

    def test_climatology_handles_new_year_wrap_and_leap_day(self) -> None:
        # Build synthetic 3-year daily series spanning Dec 25 2019 .. Dec 31 2022 (includes leap year 2020).
        start = date(2019, 12, 25)
        end = date(2022, 12, 31)
        times: list[str] = []
        snow: list[float] = []
        depth: list[float] = []
        cur = start
        while cur <= end:
            times.append(cur.isoformat())
            # 5 cm/day in Dec-Jan -> 7-day rolling sum on Jan 2 must be 35.0 cm across Dec 27..Jan 2!
            snow.append(5.0 if cur.month in (12, 1, 2) else 0.0)
            depth.append(0.80 if cur.month in (12, 1, 2) else 0.0)
            cur += timedelta(days=1)

        clim_resort = compute_resort_climatology_from_daily(times, snow, depth, start_year=2020, end_year=2022)
        self.assertIn("01-02", clim_resort["days"])
        self.assertEqual(clim_resort["days"]["01-02"], [35.0, 80.0])
        self.assertIn("02-29", clim_resort["days"])

        clim_doc = {"resorts": {"test-resort": clim_resort}}
        b_snow, b_depth, _ = lookup_climatology_baseline(clim_doc, "test-resort", date(2027, 1, 2))
        self.assertEqual((b_snow, b_depth), (35.0, 80.0))

    def test_validation_blocks_zeroed_winter_baselines(self) -> None:
        broken_rows = [
            {
                "id": "chamonix",
                "snow_depth_cm": 165.0,
                "snow_depth_base_cm": 0.0,
                "snow_depth_peak_cm": 320.0,
                "current_snowfall_cm": 80.0,
                "baseline_snowfall_cm": 0.0,
                "baseline_snow_depth_cm": 0.0,
            }
        ]
        errors = validate_tracker_results(broken_rows, 1, 0, date(2026, 11, 27), "public-tracker")
        self.assertTrue(any("collapsed to 0.0" in e for e in errors))

    def test_extract_tier_metrics_counts_overnight_dump(self) -> None:
        times = _hourly_series("2026-11-27T00:00", 48)
        snow = [0.0] * 48
        # 20 cm falls between 01:00 and 05:00 CET on Nov 28 (indices 25..29)
        for idx in range(25, 30):
            snow[idx] = 4.0
        hourly = {
            "time": times,
            "snowfall": snow,
            "precipitation": snow,
            "freezing_level_height": [1500.0] * 48,
            "snow_depth": [0.20] * 48,
            "temperature_2m": [-3.0] * 48,
        }
        metrics, _ = extract_tier_metrics(
            hourly,
            elev_m=2200,
            utc_offset_seconds=3600,
            now_utc=datetime(2026, 11, 28, 5, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(round(metrics["snow_cm"], 1), 20.0)
        self.assertGreaterEqual(metrics["depth_cm"], 20.0)
        self.assertEqual(compute_snow_anomaly_pct(20.0, 10.0), 100.0)

    def test_is_already_updated_today_rejects_zeroed_winter_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tracker.json"
            p.write_text(
                json.dumps({
                    "last_updated": "2026-11-28T05:16:00+00:00",
                    "resorts": [{"id": "chamonix", "baseline_snow_depth_cm": 0.0, "baseline_snowfall_cm": 0.0}],
                }),
                encoding="utf-8",
            )
            self.assertFalse(is_already_updated_today(str(p), date(2026, 11, 28)))

            p.write_text(
                json.dumps({
                    "last_updated": "2026-11-28T05:16:00+00:00",
                    "resorts": [{"id": "chamonix", "baseline_snow_depth_cm": 71.0, "baseline_snowfall_cm": 27.0}],
                }),
                encoding="utf-8",
            )
            self.assertTrue(is_already_updated_today(str(p), date(2026, 11, 28)))

    def test_fetch_forecasts_batched_falls_back_per_resort_on_batch_error(self) -> None:
        geoms = [
            resolve_resort_geometry({
                "id": "meribel", "name": "Méribel", "latitude": 45.39, "longitude": 6.56,
                "base_elevation": 1450, "peak_elevation": 2952,
            }),
            resolve_resort_geometry({
                "id": "zermatt", "name": "Zermatt", "latitude": 45.97, "longitude": 7.74,
                "base_elevation": 1620, "peak_elevation": 3883,
            }),
        ]
        calls: list[str] = []

        def fake_fetch(url: str, **_: object) -> list[dict[str, object]]:
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("transient batch error")
            return [{"hourly": {}}, {"hourly": {}}, {"hourly": {}}]

        with mock.patch.object(fetch_all_trackers, "fetch_json", side_effect=fake_fetch):
            result = fetch_all_trackers.fetch_forecasts_batched(geoms, batch_size=10)

        self.assertEqual(set(result.keys()), {"meribel", "zermatt"})
        self.assertEqual(len(calls), 3)  # 1 failed batch + 2 single-resort retries

    def test_payload_builders_emit_expected_schema(self) -> None:
        now = datetime(2026, 11, 28, 5, 16, tzinfo=timezone.utc)
        resorts = [{
            "id": "meribel", "name": "Méribel", "country": "France",
            "latitude": 45.3975, "longitude": 6.5641, "base_elevation": 1450, "peak_elevation": 2952,
        }]
        metrics = {
            "meribel": {
                "mid_elevation": 2201, "current_snowfall_cm": 85.0, "snowfall_base_cm": 50.0,
                "snowfall_peak_cm": 82.0, "baseline_snowfall_cm": 24.0, "snow_anomaly_pct": 254.2,
                "weekly_precip_mm": 120.0, "avg_freezing_level_m": 2050, "snow_depth_cm": 110.0,
                "snow_depth_base_cm": 33.0, "snow_depth_peak_cm": 170.0, "baseline_snow_depth_cm": 54.0,
            }
        }
        pub, pub_hist = build_public_payloads(resorts, metrics, {"history": []}, now)
        pers, pers_hist = build_personal_payloads(resorts, metrics, {"history": []}, now)
        self.assertEqual(pub["summary"]["overall_sample_size"], 1)
        self.assertEqual(pub_hist["history"][-1]["meribel_depth_cm"], 110.0)
        self.assertEqual(pers["summary"]["sample_size"], 1)
        self.assertEqual(pers_hist["history"][-1]["meribel_peak_depth_cm"], 170.0)


if __name__ == "__main__":
    unittest.main()
