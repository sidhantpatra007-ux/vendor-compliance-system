"""Reliable, restart-safe Companies House Streaming API consumer.

This module only transports stream events.  The application supplies the
database callbacks, which keeps Companies House transport details separate
from the compliance/scoring domain.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


LOGGER = logging.getLogger(__name__)
BASE_URL = "https://stream.companieshouse.gov.uk"

STREAM_ENDPOINTS = {
    "companies": "/companies",
    "filings": "/filings",
    "charges": "/charges",
    "insolvency_cases": "/insolvency-cases",
    "officers": "/officers",
    "persons_with_significant_control": "/persons-with-significant-control",
}

CheckpointGetter = Callable[[str], Optional[int]]
CheckpointSaver = Callable[[str, int], None]
CheckpointResetter = Callable[[str], None]
EventHandler = Callable[[str, dict[str, Any]], None]


@dataclass
class StreamHealth:
    connected: bool = False
    last_event_at: Optional[float] = None
    last_error: Optional[str] = None
    reconnects: int = 0


class CompaniesHouseStreamSupervisor:
    """Runs one long-lived, restart-safe thread per Companies House stream."""

    def __init__(
        self,
        api_key: str,
        get_checkpoint: CheckpointGetter,
        save_checkpoint: CheckpointSaver,
        reset_checkpoint: CheckpointResetter,
        handle_event: EventHandler,
    ) -> None:
        self._api_key = api_key
        self._get_checkpoint = get_checkpoint
        self._save_checkpoint = save_checkpoint
        self._reset_checkpoint = reset_checkpoint
        self._handle_event = handle_event
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._health = {name: StreamHealth() for name in STREAM_ENDPOINTS}
        self._health_lock = threading.Lock()

    def start(self) -> None:
        if self._threads:
            return
        for stream_name, endpoint in STREAM_ENDPOINTS.items():
            thread = threading.Thread(
                target=self._run_stream,
                args=(stream_name, endpoint),
                name=f"companies-house-{stream_name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=10)
        self._threads.clear()

    def status(self) -> dict[str, dict[str, Any]]:
        with self._health_lock:
            return {
                name: {
                    "connected": health.connected,
                    "last_event_at": health.last_event_at,
                    "last_error": health.last_error,
                    "reconnects": health.reconnects,
                }
                for name, health in self._health.items()
            }

    def _set_health(self, stream_name: str, **changes: Any) -> None:
        with self._health_lock:
            health = self._health[stream_name]
            for key, value in changes.items():
                setattr(health, key, value)

    def _run_stream(self, stream_name: str, endpoint: str) -> None:
        backoff_seconds = 2
        while not self._stop_event.is_set():
            params: dict[str, int] = {}
            checkpoint = self._get_checkpoint(stream_name)
            if checkpoint is not None:
                params["timepoint"] = checkpoint

            try:
                timeout = httpx.Timeout(connect=15.0, read=None, write=15.0, pool=15.0)
                with httpx.Client(auth=(self._api_key, ""), timeout=timeout) as client:
                    with client.stream("GET", f"{BASE_URL}{endpoint}", params=params) as response:
                        if response.status_code == 416 and checkpoint is not None:
                            # The server no longer retains this checkpoint. Do not spin on
                            # it forever: resume from the live stream and surface the event
                            # gap in logs for an operator to investigate.
                            LOGGER.warning(
                                "Stream %s checkpoint %s is too old; resuming live events",
                                stream_name,
                                checkpoint,
                            )
                            self._reset_checkpoint(stream_name)
                            self._set_health(
                                stream_name,
                                connected=False,
                                last_error="Stored timepoint expired; resumed live stream",
                            )
                            continue

                        response.raise_for_status()
                        self._set_health(stream_name, connected=True, last_error=None)
                        backoff_seconds = 2

                        for line in response.iter_lines():
                            if self._stop_event.is_set():
                                return
                            if not line:
                                continue
                            event = json.loads(line)
                            timepoint = event.get("event", {}).get("timepoint")
                            if not isinstance(timepoint, int):
                                LOGGER.warning("Ignoring malformed %s stream event", stream_name)
                                continue

                            # Saving the checkpoint only after the event handler returns is
                            # what guarantees at-least-once delivery over reconnects.
                            self._handle_event(stream_name, event)
                            self._save_checkpoint(stream_name, timepoint)
                            self._set_health(stream_name, last_event_at=time.time())

            except (httpx.HTTPError, OSError, json.JSONDecodeError, ValueError) as exc:
                self._set_health(
                    stream_name,
                    connected=False,
                    last_error=str(exc),
                    reconnects=self._health[stream_name].reconnects + 1,
                )
                LOGGER.warning(
                    "Companies House %s stream disconnected (%s); retrying in %ss",
                    stream_name,
                    exc,
                    backoff_seconds,
                )
                self._stop_event.wait(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)
            except Exception:
                # Application handler errors must be highly visible, but the
                # consumer remains alive and retries the uncheckpointed event.
                self._set_health(stream_name, connected=False, last_error="Unhandled event processing error")
                LOGGER.exception("Unhandled error in Companies House %s stream", stream_name)
                self._stop_event.wait(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60)
