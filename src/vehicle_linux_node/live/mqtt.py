"""paho-mqtt (MQTT 5) transport. paho is imported lazily (extra ``live``)."""

from __future__ import annotations

import ipaddress
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# paho keeps messages in its own queue while the link is slow; bound it so a long ride on a
# bad LTE link cannot grow memory without limit (the app-level offline queue is bounded too).
MAX_QUEUED_MESSAGES = 1000
MAX_INFLIGHT_MESSAGES = 20
ALLOW_PLAINTEXT_ENV = "VEHICLE_MQTT_ALLOW_PLAINTEXT"


class ConfigError(ValueError):
    """Missing or invalid live-uplink configuration (exit code 2, no restart loop)."""


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int
    tls: bool
    device_id: str
    token: str


def parse_url(url: str) -> tuple[str, int, bool]:
    parsed = urlparse(url)
    if parsed.scheme not in ("mqtt", "mqtts") or not parsed.hostname:
        raise ConfigError(
            f"VEHICLE_MQTT_URL must be mqtt://host[:port] or mqtts://..., got {url!r}"
        )
    tls = parsed.scheme == "mqtts"
    return parsed.hostname, parsed.port or (8883 if tls else 1883), tls


def is_local_host(host: str) -> bool:
    """Loopback, RFC1918/private, link-local, ``localhost`` or ``*.local`` (mDNS)."""
    name = host.lower().rstrip(".")
    if name == "localhost" or name.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False  # a public-looking DNS name is never assumed local
    return ip.is_loopback or ip.is_private or ip.is_link_local


def read_token(env: dict[str, str] | None = None) -> str:
    """Device token from ``VEHICLE_MQTT_TOKEN_FILE`` (preferred) or ``VEHICLE_MQTT_TOKEN``."""
    env = os.environ if env is None else env
    file = env.get("VEHICLE_MQTT_TOKEN_FILE")
    if file:
        try:
            mode = Path(file).stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                log.warning(
                    "VEHICLE_MQTT_TOKEN_FILE %s is group/world accessible; chmod 600 it", file
                )
            token = Path(file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"cannot read VEHICLE_MQTT_TOKEN_FILE: {exc}") from exc
    else:
        token = env.get("VEHICLE_MQTT_TOKEN", "").strip()
    if not token:
        raise ConfigError("no device token: set VEHICLE_MQTT_TOKEN_FILE or VEHICLE_MQTT_TOKEN")
    return token


def config_from_env(
    env: dict[str, str] | None = None, *, allow_plaintext: bool = False
) -> BrokerConfig:
    env = os.environ if env is None else env
    url = env.get("VEHICLE_MQTT_URL")
    device_id = env.get("VEHICLE_DEVICE_ID", "").strip()
    if not url:
        raise ConfigError("VEHICLE_MQTT_URL is not set")
    if not device_id or "/" in device_id or "+" in device_id or "#" in device_id:
        raise ConfigError("VEHICLE_DEVICE_ID must be set and free of '/', '+', '#'")
    host, port, tls = parse_url(url)
    allow_plaintext = allow_plaintext or env.get(ALLOW_PLAINTEXT_ENV) == "1"
    if not tls and not allow_plaintext and not is_local_host(host):
        raise ConfigError(
            f"refusing plaintext mqtt:// to {host!r} (the device token would travel in clear): "
            f"use mqtts://, a loopback/private/.local host, or --allow-plaintext "
            f"({ALLOW_PLAINTEXT_ENV}=1); position is never sent without TLS"
        )
    return BrokerConfig(host, port, tls, device_id, read_token(env))


class PahoTransport:
    """Background-thread MQTT 5 client; username = device_id, password = device token."""

    def __init__(self, cfg: BrokerConfig):
        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.device_id,
            protocol=mqtt.MQTTv5,
        )
        self._client.username_pw_set(cfg.device_id, cfg.token)
        if cfg.tls:
            self._client.tls_set()  # system CA bundle, hostname verified
        self._client.max_queued_messages_set(MAX_QUEUED_MESSAGES)
        self._client.max_inflight_messages_set(MAX_INFLIGHT_MESSAGES)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._cfg = cfg
        self._up = False

    @property
    def connected(self) -> bool:
        return self._up

    def start(self) -> None:
        self._client.connect_async(self._cfg.host, self._cfg.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> bool:
        if not self._up:
            return False
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        # MQTT_ERR_QUEUE_SIZE (paho queue full) is "not accepted": the caller keeps it queued.
        return info.rc == self._mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, _client, _userdata, _flags, reason_code, _props=None) -> None:
        self._up = not reason_code.is_failure
        log.info("mqtt connect: %s", reason_code)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _props=None) -> None:
        self._up = False
        log.warning("mqtt disconnected: %s", reason_code)
