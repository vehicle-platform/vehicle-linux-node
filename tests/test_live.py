"""Live uplink (D-109) with fake Kuksa and fake MQTT, no network."""

from __future__ import annotations

import json
import sys
import threading
import types

import pytest

from vehicle_linux_node.live import cli, mqtt, service
from vehicle_linux_node.live.core import LivePublisher, encode_live, live_topic


class FakeTransport:
    def __init__(self, connected: bool = True):
        self.connected = connected
        self.sent: list[tuple[str, bytes, int, bool]] = []

    def publish(self, topic, payload, qos, retain):
        self.sent.append((topic, payload, qos, retain))
        return True


def _live(tr, **kw):
    return LivePublisher("dev1", tr, **kw)


def test_live_message_contract():
    tr = FakeTransport()
    _live(tr).ingest({"Vehicle.Speed": 12.5}, 1000)
    live = [m for m in tr.sent if "/live/" in m[0]]
    assert live == [("vehicle/dev1/live/Vehicle.Speed", encode_live(1000, 12.5), 1, False)]
    assert json.loads(live[0][1]) == {"ts": 1000, "value": 12.5}


def test_state_is_retained_with_every_last_value():
    tr = FakeTransport()
    p = _live(tr)
    p.ingest({"A": 1, "B": True}, 0)
    p.ingest({"A": 2}, 100)
    state = [m for m in tr.sent if m[0] == "vehicle/dev1/state"][-1]
    assert state[2:] == (1, True)
    assert json.loads(state[1]) == {"A": 2, "B": True}


def test_rate_limit_per_signal_but_state_follows():
    tr = FakeTransport()
    p = _live(tr, rate_hz=10)  # 100 ms
    for t in range(0, 300, 20):
        p.ingest({"A": t}, t)
    p.ingest({"B": 1}, 10)  # another signal is independent
    live_a = [m for m in tr.sent if m[0].endswith("/live/A")]
    assert [json.loads(m[1])["ts"] for m in live_a] == [0, 100, 200]
    assert any(m[0].endswith("/live/B") for m in tr.sent)
    assert p.stats.rate_limited == 12
    state = [m for m in tr.sent if m[0].endswith("/state")][-1]
    assert json.loads(state[1])["A"] == 280


def test_offline_queue_is_bounded_drops_oldest_and_drains_in_order():
    tr = FakeTransport(connected=False)
    p = _live(tr, rate_hz=1000, queue_max=3)
    for i in range(5):
        p.ingest({"A": i}, i * 10)
    assert tr.sent == [] and p.backlog == 3 and p.stats.dropped == 2
    tr.connected = True
    p.flush()
    values = [json.loads(m[1])["value"] for m in tr.sent if "/live/" in m[0]]
    assert values == [2, 3, 4]
    assert tr.sent[-1][0] == "vehicle/dev1/state" and tr.sent[-1][3] is True
    assert p.backlog == 0


def test_refused_publish_keeps_message_queued():
    class Refuse(FakeTransport):
        def publish(self, *a):
            return False

    p = _live(Refuse())
    p.ingest({"A": 1}, 0)
    assert p.backlog == 1 and p.stats.published == 0


def test_non_json_values_are_rejected():
    tr = FakeTransport()
    p = _live(tr)
    p.ingest({"A": float("nan"), "B": [1], "C": "ok"}, 0)
    assert p.stats.rejected == 2
    assert [m[0] for m in tr.sent if "/live/" in m[0]] == ["vehicle/dev1/live/C"]


def test_health_topic():
    tr = FakeTransport()
    _live(tr).publish_health({"window": "W1", "n": 3})
    assert tr.sent[0][0] == "vehicle/dev1/health"
    assert json.loads(tr.sent[0][1]) == {"window": "W1", "n": 3}


def test_topic_helper():
    assert live_topic("d", "Vehicle.X") == "vehicle/d/live/Vehicle.X"


def test_signal_names_come_from_the_defs_and_position_is_excluded():
    from vehicle_linux_node import defs
    from vehicle_linux_node.blackbox.core import is_position_path

    paths = service.signal_paths()
    assert paths and not any(is_position_path(p) for p in paths)
    assert set(paths) <= {m.vss_path for m in defs.dbc2vss_mappings()}
    assert set(paths) <= set(service.signal_paths(include_position=True))


def test_url_parsing():
    assert mqtt.parse_url("mqtt://h") == ("h", 1883, False)
    assert mqtt.parse_url("mqtts://h") == ("h", 8883, True)
    assert mqtt.parse_url("mqtts://h:9") == ("h", 9, True)
    with pytest.raises(mqtt.ConfigError):
        mqtt.parse_url("http://h")


def test_config_and_token_sources(tmp_path):
    f = tmp_path / "tok"
    f.write_text("secret\n")
    env = {
        "VEHICLE_MQTT_URL": "mqtts://b",
        "VEHICLE_DEVICE_ID": "d1",
        "VEHICLE_MQTT_TOKEN_FILE": str(f),
    }
    cfg = mqtt.config_from_env(env)
    assert (cfg.host, cfg.tls, cfg.device_id, cfg.token) == ("b", True, "d1", "secret")
    assert mqtt.read_token({"VEHICLE_MQTT_TOKEN": " t "}) == "t"
    for bad in (
        {},
        {**env, "VEHICLE_DEVICE_ID": "a/b"},
        {**env, "VEHICLE_MQTT_TOKEN_FILE": "/nope"},
    ):
        with pytest.raises(mqtt.ConfigError):
            mqtt.config_from_env(bad)


def test_cli_exits_2_without_config(monkeypatch, capsys):
    for k in (
        "VEHICLE_MQTT_URL",
        "VEHICLE_DEVICE_ID",
        "VEHICLE_MQTT_TOKEN",
        "VEHICLE_MQTT_TOKEN_FILE",
    ):
        monkeypatch.delenv(k, raising=False)
    assert cli.main([]) == 2
    assert "VEHICLE_MQTT_URL" in capsys.readouterr().err


def test_health_file_publishes_on_change_only(tmp_path):
    f = tmp_path / "health.json"
    h = service.HealthFile(f)
    assert h.poll() is None  # missing
    f.write_text('{"w": 1}')
    assert h.poll() == {"w": 1}
    assert h.poll() is None
    f.write_text("not json")
    assert h.poll() is None


def test_run_with_fake_kuksa(monkeypatch, tmp_path):
    dp = types.SimpleNamespace
    batches = [
        {"Vehicle.Speed": dp(value=5.0), "Vehicle.Gone": None},
        {"Vehicle.Speed": dp(value=6.0), "Vehicle.Rpm": dp(value=900)},
    ]

    class FakeVSS:
        def __init__(self, host, port):
            assert (host, port) == ("127.0.0.1", 55555)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def subscribe_current_values(self, paths):
            assert "Vehicle.Speed" in paths
            yield from batches

    mod = types.ModuleType("kuksa_client.grpc")
    mod.VSSClient = FakeVSS
    monkeypatch.setitem(sys.modules, "kuksa_client", types.ModuleType("kuksa_client"))
    monkeypatch.setitem(sys.modules, "kuksa_client.grpc", mod)
    tr = FakeTransport()
    ticks = iter(range(0, 10_000, 500))
    live = service.run(
        "127.0.0.1:55555",
        tr,
        "dev1",
        paths=["Vehicle.Speed", "Vehicle.Rpm"],
        stop=threading.Event(),
        clock=lambda: next(ticks),
    )
    topics = [m[0] for m in tr.sent]
    assert topics.count("vehicle/dev1/live/Vehicle.Speed") == 2
    assert "vehicle/dev1/live/Vehicle.Rpm" in topics
    assert live.stats.rejected == 0
