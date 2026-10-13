"""Offline monitor regressions; only account and HTTP boundaries are replaced."""
import contextlib
import datetime as dt
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import monitor


def payload(five=20, weekly=40):
    return {"rateLimits": {
        "primary": {"windowDurationMins": 300, "usedPercent": five, "resetsAt": 2000000000},
        "secondary": {"windowDurationMins": 10080, "usedPercent": weekly, "resetsAt": 2000500000},
    }}


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / "state" / "reset-state.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(monitor, "STATE_PATH", self.path))
        self.stack.enter_context(patch.object(monitor, "utc_now", return_value=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)))
        self.reader = self.stack.enter_context(patch.object(monitor, "read_usage", return_value=payload()))
        self.ping = self.stack.enter_context(patch.object(monitor, "discord_ping"))
        self.publish = self.stack.enter_context(patch.object(monitor, "publish_status", return_value="disabled"))
        # Fail closed if any test accidentally reaches a real HTTP boundary.
        self.stack.enter_context(patch.object(monitor.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.state = {"version": 1, "windows": {}}

    def check(self, five=20, weekly=40):
        self.reader.return_value = payload(five, weekly)
        return monitor.run_check(self.state)

    def test_initial_seed_persists_without_alert(self):
        result = self.check(0, 0)
        self.assertTrue(result["firstRun"])
        self.assertEqual(result["alertsSent"], 0)
        self.assertEqual(monitor.load_json(self.path, {}), self.state)
        self.ping.assert_not_called()

    def test_reset_notifies_once_across_reload(self):
        self.check()
        self.assertEqual(self.check(0)["alertsSent"], 1)
        self.state = monitor.load_json(self.path, {})
        self.assertEqual(self.check(0)["alertsSent"], 0)
        self.assertEqual(self.ping.call_args.args[0]["label"], "5-hour")
        self.assertEqual(self.ping.call_count, 1)

    def test_usage_decrease_above_zero_is_not_reset(self):
        self.check(70)
        self.assertEqual(self.check(5)["alertsSent"], 0)
        self.ping.assert_not_called()

    def test_exhausted_weekly_quota_suppresses_five_hour_alert(self):
        self.check(20, 100)
        self.assertEqual(self.check(0, 100)["alertsSent"], 0)
        self.ping.assert_not_called()

    def test_both_resets_emit_both_windows(self):
        self.check()
        self.assertEqual(self.check(0, 0)["alertsSent"], 2)
        self.assertEqual([c.args[0]["label"] for c in self.ping.call_args_list], ["5-hour", "weekly"])

    def test_notification_failure_preserves_reset_for_retry(self):
        self.check()
        self.ping.side_effect = RuntimeError("offline delivery failed")
        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            self.check(0)
        self.assertEqual(monitor.load_json(self.path, {})["windows"]["300"]["usedPercent"], 20)
        self.state = monitor.load_json(self.path, {})
        self.ping.side_effect = None
        self.assertEqual(self.check(0)["alertsSent"], 1)

    def test_partial_delivery_retries_only_failed_window(self):
        self.check()
        self.ping.side_effect = [None, RuntimeError("weekly delivery failed")]
        with self.assertRaisesRegex(RuntimeError, "weekly delivery failed"):
            self.check(0, 0)
        self.state = monitor.load_json(self.path, {})
        self.assertEqual(self.state["windows"]["300"]["usedPercent"], 0)
        self.assertEqual(self.state["windows"]["10080"]["usedPercent"], 40)
        self.ping.reset_mock(side_effect=True)
        self.assertEqual(self.check(0, 0)["alertsSent"], 1)
        self.assertEqual(self.ping.call_args.args[0]["label"], "weekly")

    def test_failed_reset_retries_even_after_usage_resumes(self):
        self.check()
        self.ping.side_effect = RuntimeError("delivery failed")
        with self.assertRaises(RuntimeError):
            self.check(0)
        self.state = monitor.load_json(self.path, {})
        self.ping.reset_mock(side_effect=True)
        self.assertEqual(self.check(3)["alertsSent"], 1)
        self.assertEqual(self.ping.call_args.args[0]["label"], "5-hour")

    def test_null_usage_rejected_without_state_change(self):
        self.check()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.check(None)
        self.assertEqual(self.path.read_bytes(), before)

    def test_first_delivery_failure_preserves_both_detected_resets(self):
        self.check()
        self.ping.side_effect = RuntimeError("first delivery failed")
        with self.assertRaises(RuntimeError):
            self.check(0, 0)
        self.state = monitor.load_json(self.path, {})
        self.ping.reset_mock(side_effect=True)
        self.assertEqual(self.check(3, 5)["alertsSent"], 2)

    def test_missing_usage_does_not_fabricate_reset_or_change_state(self):
        self.check()
        before = self.path.read_bytes()
        invalid = payload()
        del invalid["rateLimits"]["primary"]["usedPercent"]
        self.reader.return_value = invalid
        with self.assertRaises(ValueError):
            monitor.run_check(self.state)
        self.assertEqual(self.path.read_bytes(), before)
        self.ping.assert_not_called()

    def test_nonfinite_usage_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                monitor.normalise_windows(payload(value)["rateLimits"])

    def test_empty_payload_leaves_persisted_state_unchanged(self):
        self.check()
        before = self.path.read_bytes()
        self.reader.return_value = {}
        with self.assertRaisesRegex(RuntimeError, "no primary/secondary"):
            monitor.run_check(self.state)
        self.assertEqual(self.path.read_bytes(), before)

    def test_codex_bucket_preferred_with_legacy_fallback(self):
        legacy = payload()["rateLimits"]
        preferred = payload(13)["rateLimits"]
        both = {"rateLimits": legacy, "rateLimitsByLimitId": {"codex": preferred, "other": {}}}
        self.assertEqual(monitor.choose_snapshot(both)["primary"]["usedPercent"], 13)
        self.assertEqual(monitor.choose_snapshot({"rateLimits": legacy}), legacy)

    def test_status_publish_failure_does_not_lose_delivered_reset(self):
        self.check()
        self.publish.side_effect = RuntimeError("offline status unavailable")
        self.assertEqual(self.check(0)["statusPublish"], "failed")
        self.state = monitor.load_json(self.path, {})
        self.assertEqual(self.check(0)["alertsSent"], 0)

    def test_state_json_bom_missing_and_corruption(self):
        self.assertEqual(monitor.load_json(self.path, {"version": 1}), {"version": 1})
        self.path.parent.mkdir()
        self.path.write_text('{"version": 1}', encoding="utf-8-sig")
        self.assertEqual(monitor.load_json(self.path, {}), {"version": 1})
        self.path.write_text('{broken', encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            monitor.load_json(self.path, {})

    def test_alignment_waits_only_for_earliest_upcoming_reset(self):
        state = {"windows": {"300": {"resetsAt": 110, "label": "5-hour"}, "10080": {"resetsAt": 120}}}
        with patch.object(monitor.time, "time", return_value=100), patch.object(monitor.time, "sleep") as sleep:
            self.assertEqual(monitor.wait_for_predicted_reset(state), {"label": "5-hour", "resetsAt": 110})
            sleep.assert_called_once_with(18)

    def test_alignment_ignores_past_and_distant_resets(self):
        state = {"windows": {"300": {"resetsAt": 90}, "10080": {"resetsAt": 99999}}}
        with patch.object(monitor.time, "time", return_value=100), patch.object(monitor.time, "sleep") as sleep:
            self.assertIsNone(monitor.wait_for_predicted_reset(state))
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
