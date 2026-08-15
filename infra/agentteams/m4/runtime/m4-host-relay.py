#!/usr/bin/env python3
"""Fixed, byte-blind M4 TCP relay for the two loopback host adapters.

This process deliberately has no configurable target, proxy protocol, request
parser, credential access, or per-connection logging.  It exists only because
the provider-free M4 bridge has IP masquerading disabled and therefore cannot
reach Docker Desktop's host gateway directly.
"""

from __future__ import annotations

import select
import signal
import socket
import sys
import threading
import time


LISTEN_HOST = "172.20.0.254"
UPSTREAM_HOST = "host.docker.internal"
PORTS = (18190, 18191)
CONNECT_TIMEOUT_SECONDS = 5.0
IO_TIMEOUT_SECONDS = 120.0
SELECT_TIMEOUT_SECONDS = 1.0
MAX_CONNECTIONS = 32
BUFFER_SIZE = 64 * 1024

_stop = threading.Event()
_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
_listeners: list[socket.socket] = []


def _close_quietly(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def _relay_connection(client: socket.socket, port: int) -> None:
    upstream: socket.socket | None = None
    try:
        upstream = socket.create_connection(
            (UPSTREAM_HOST, port),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        client.settimeout(IO_TIMEOUT_SECONDS)
        upstream.settimeout(IO_TIMEOUT_SECONDS)
        peers = {client: upstream, upstream: client}
        last_activity = time.monotonic()

        while not _stop.is_set():
            try:
                readable, _, exceptional = select.select(
                    (client, upstream),
                    (),
                    (client, upstream),
                    SELECT_TIMEOUT_SECONDS,
                )
            except (OSError, ValueError):
                return
            if exceptional:
                return
            if not readable:
                if time.monotonic() - last_activity >= IO_TIMEOUT_SECONDS:
                    return
                continue
            for source in readable:
                try:
                    data = source.recv(BUFFER_SIZE)
                except (OSError, TimeoutError):
                    return
                if not data:
                    return
                last_activity = time.monotonic()
                try:
                    peers[source].sendall(data)
                except (OSError, TimeoutError):
                    return
    except OSError:
        # Connection failures are intentionally silent: request metadata and
        # credentials must never be reflected into relay logs.
        return
    finally:
        _close_quietly(client)
        _close_quietly(upstream)
        _slots.release()


def _accept_loop(listener: socket.socket, port: int) -> None:
    while not _stop.is_set():
        try:
            client, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        if not _slots.acquire(blocking=False):
            _close_quietly(client)
            continue
        thread = threading.Thread(
            target=_relay_connection,
            args=(client, port),
            daemon=True,
        )
        thread.start()


def _request_stop(_signum: int, _frame: object) -> None:
    _stop.set()
    for listener in tuple(_listeners):
        _close_quietly(listener)


def _main() -> int:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    threads: list[threading.Thread] = []
    try:
        for port in PORTS:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((LISTEN_HOST, port))
            listener.listen(MAX_CONNECTIONS)
            listener.settimeout(SELECT_TIMEOUT_SECONDS)
            _listeners.append(listener)
            thread = threading.Thread(
                target=_accept_loop,
                args=(listener, port),
                daemon=True,
            )
            threads.append(thread)
            thread.start()
    except OSError:
        _request_stop(0, None)
        sys.stderr.write("M4_HOST_RELAY_START_FAILED\n")
        sys.stderr.flush()
        return 78

    print(
        "M4_HOST_RELAY_READY=172.20.0.254:18190,172.20.0.254:18191",
        flush=True,
    )
    _stop.wait()
    for thread in threads:
        thread.join(timeout=2.0)
    print("M4_HOST_RELAY_STOPPED=true", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
