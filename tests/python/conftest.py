"""
Global test configuration and socket isolation guard.
Guarantees 100% offline hermetic sandbox execution.
"""
import os
import socket
import tempfile
import unittest
from pathlib import Path

_PERSIST_ROOT = tempfile.mkdtemp(prefix="leaps_hermetic_")
os.environ["LEAPS_IV_HISTORY_PATH"] = str(Path(_PERSIST_ROOT) / "iv_history.json")
os.environ["LEAPS_DAILY_BAR_CACHE_PATH"] = str(Path(_PERSIST_ROOT) / "daily_bars.json")

_real_socket_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname

class NetworkBlockedError(RuntimeError):
    pass

def block_network():
    def guarded_connect(self, *args, **kwargs):
        raise NetworkBlockedError(
            f"External network connection blocked by Hermetic Test Guard: {args}"
        )
    def guarded_getaddrinfo(*args, **kwargs):
        raise NetworkBlockedError(
            f"External DNS resolution blocked by Hermetic Test Guard: {args}"
        )
    def guarded_gethostbyname(*args, **kwargs):
        raise NetworkBlockedError(
            f"External DNS resolution blocked by Hermetic Test Guard: {args}"
        )

    socket.socket.connect = guarded_connect
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname

def restore_network():
    socket.socket.connect = _real_socket_connect
    socket.getaddrinfo = _real_getaddrinfo
    socket.gethostbyname = _real_gethostbyname

# Automatically block external network during tests
block_network()
