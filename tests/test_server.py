import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient


import unittest.mock as mock
with mock.patch("os.path.isdir", return_value=False):
    from server.main import app, adapter, check_alerts, SimulatedPLCAdapter, ActuatorState

client = TestClient(app)


class TestSimulatedPLCAdapter:
    def test_sensor_reading_returns_all_fields(self):
        plc = SimulatedPLCAdapter()
        s = plc.read_sensors()
        assert s.ph is not None
        assert s.dissolved_oxygen is not None
        assert s.temperature is not None
        assert s.turbidity is not None

    def test_sensor_values_within_realistic_range(self):
        plc = SimulatedPLCAdapter()
        for _ in range(50):
            s = plc.read_sensors()
            assert 0 <= s.ph <= 14
            assert 0 <= s.dissolved_oxygen <= 25
            assert -10 <= s.temperature <= 60
            assert s.turbidity >= 0

    def test_actuator_write_updates_state(self):
        plc = SimulatedPLCAdapter()
        assert plc.get_actuators().pump_feed is False
        plc.write_actuator("pump_feed", True)
        assert plc.get_actuators().pump_feed is True

    def test_led_write_stores_value(self):
        plc = SimulatedPLCAdapter()
        plc.write_actuator("led_intensity", 75)
        assert plc.get_actuators().led_intensity == 75

    def test_confirm_returns_current_state(self):
        plc = SimulatedPLCAdapter()
        plc.write_actuator("pump_feed", True)
        confirmed = plc.confirm_actuator_state()
        assert confirmed.pump_feed is True


class TestAlertEngine:
    def _reading(self, ph=7.0, do=8.0, temp=22.0, turb=15.0):
        from server.main import SensorReading
        from datetime import datetime, timezone
        return SensorReading(
            timestamp=datetime.now(timezone.utc).isoformat(),
            ph=ph, dissolved_oxygen=do, temperature=temp, turbidity=turb
        )

    def test_no_alerts_on_normal_values(self):
        alerts = check_alerts(self._reading())
        assert alerts == []

    def test_ph_low_triggers_alert(self):
        alerts = check_alerts(self._reading(ph=5.0))
        assert any("PH LOW" in a for a in alerts)

    def test_ph_high_triggers_alert(self):
        alerts = check_alerts(self._reading(ph=9.5))
        assert any("PH HIGH" in a for a in alerts)

    def test_do_low_triggers_alert(self):
        alerts = check_alerts(self._reading(do=1.0))
        assert any("DISSOLVED_OXYGEN LOW" in a for a in alerts)

    def test_temp_high_triggers_alert(self):
        alerts = check_alerts(self._reading(temp=38.0))
        assert any("TEMPERATURE HIGH" in a for a in alerts)

    def test_turbidity_high_triggers_alert(self):
        alerts = check_alerts(self._reading(turb=150.0))
        assert any("TURBIDITY HIGH" in a for a in alerts)


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "uptime_s" in data

class TestActuatorEndpoint:
    def test_get_actuators_returns_state(self):
        r = client.get("/api/actuators")
        assert r.status_code == 200
        data = r.json()
        assert "pump_feed" in data
        assert "led_intensity" in data

    def test_command_pump_feed_on(self):
        r = client.post("/api/actuators/command", json={
            "actuator": "pump_feed", "value": True, "operator": "test"
        })
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "accepted"
        assert data["confirmed"]["pump_feed"] is True

    def test_command_led_intensity_50(self):
        r = client.post("/api/actuators/command", json={
            "actuator": "led_intensity", "value": 50, "operator": "test"
        })
        assert r.status_code == 202
        assert r.json()["confirmed"]["led_intensity"] == 50

    def test_command_unknown_actuator_rejected(self):
        r = client.post("/api/actuators/command", json={
            "actuator": "turbo_laser", "value": True
        })
        assert r.status_code == 422  

    def test_command_led_out_of_range_rejected(self):
        r = client.post("/api/actuators/command", json={
            "actuator": "led_intensity", "value": 200, "operator": "test"
        })
        assert r.status_code == 422

    def test_command_led_negative_rejected(self):
        r = client.post("/api/actuators/command", json={
            "actuator": "led_intensity", "value": -10, "operator": "test"
        })
        assert r.status_code == 422

class TestMetricsEndpoint:
    def test_metrics_returns_required_fields(self):
        r = client.get("/api/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_s" in data
        assert "messages_sent" in data
        assert "errors" in data
        assert "message_rate_per_min" in data
        assert "connected_clients" in data
