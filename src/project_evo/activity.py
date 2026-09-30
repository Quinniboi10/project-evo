from .database import PrimaryTableRow

from pathlib import Path
from threading import Event, Lock, Thread

import argparse
import json
import logging
import math
import socket
import time

DEFAULT_PORT = 49371
MAX_BYTES = 1_048_576

def port_number(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535")
    return port

class StatusServer:
    def __init__(self, database: Path, port: int):
        self.database = str(database.resolve())
        self.active: dict[str, dict] = {}
        self.lock = Lock()
        self.stopped = Event()
        self.socket = socket.create_server(("127.0.0.1", port))
        self.socket.settimeout(0.2)
        self.thread = Thread(target=self._serve, daemon=True)
        self.thread.start()

    def add(self, child: PrimaryTableRow, island: int):
        with self.lock:
            self.active[child.uuid] = {"uuid": child.uuid, "parent_id": child.parent_id, "island_id": island, "task": child.task, "started": time.time()}

    def remove(self, uuid: str):
        with self.lock:
            self.active.pop(uuid, None)

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.socket.accept()
                with connection:
                    connection.settimeout(1)
                    with self.lock:
                        attempts = list(self.active.values())
                    payload = json.dumps({"database": self.database, "active": attempts}).encode()
                    if len(payload) <= MAX_BYTES:
                        connection.sendall(payload)
            except TimeoutError:
                continue
            except OSError as error:
                if not self.stopped.is_set():
                    logging.log(logging.WARNING, f"Dashboard status connection failed: {error}")

    def close(self):
        self.stopped.set()
        self.socket.close()
        self.thread.join()

def read_active(database: Path, port: int) -> list[dict]|None:
    # A separate deadline bounds even a peer that keeps sending partial data.
    deadline = time.monotonic() + 1
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            data = bytearray()
            while len(data) <= MAX_BYTES:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = connection.recv(min(65536, MAX_BYTES + 1 - len(data)))
                if time.monotonic() > deadline:
                    return None
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_BYTES:
                return None
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get("database") != str(database.resolve()):
            return None
        attempts = payload.get("active")
        if not isinstance(attempts, list):
            return None
        for row in attempts:
            if not (isinstance(row, dict) and isinstance(row.get("uuid"), str) and 0 < len(row["uuid"]) <= 128
                    and type(row.get("parent_id")) is int and row["parent_id"] > 0
                    and type(row.get("island_id")) is int and row["island_id"] >= 0
                    and row.get("task") in ("EXPLORE", "IMPROVE")
                    and type(row.get("started")) in (int, float) and math.isfinite(row["started"]) and row["started"] > 0):
                return None
        if len({row["uuid"] for row in attempts}) != len(attempts):
            return None
        return attempts
    except (OSError, ValueError, RecursionError, OverflowError):
        return None
