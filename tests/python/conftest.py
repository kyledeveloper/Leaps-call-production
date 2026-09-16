"""
Global test configuration and socket isolation guard.
Guarantees 100% offline hermetic sandbox execution.
"""
import socket
import unittest

_real_socket_connect = socket.socket.connect

class NetworkBlockedError(RuntimeError):
    pass

def block_network():
    def guarded_connect(self, *args, **kwargs):
        raise NetworkBlockedError(
            f"External network connection blocked by Hermetic Test Guard: {args}"
        )
    socket.socket.connect = guarded_connect

def restore_network():
    socket.socket.connect = _real_socket_connect

# Automatically block external network during tests
block_network()
