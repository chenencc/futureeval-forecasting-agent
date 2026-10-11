"""Offline gate permits internal event-loop plumbing, never network access."""
import asyncio
import socket
import unittest

from ForecastAgent.intelligence.linux_gate import forbidden_network


class NetworkBoundaryTests(unittest.TestCase):
    def test_socketpair_and_event_loop_work_with_network_blocked(self):
        async def value():
            return 7
        with forbidden_network():
            left, right = socket.socketpair()
            try:
                left.sendall(b'x')
                self.assertEqual(right.recv(1), b'x')
                self.assertEqual(asyncio.run(value()), 7)
            finally:
                left.close()
                right.close()

    def test_external_and_independent_loopback_connections_are_blocked(self):
        with forbidden_network():
            for address in [('203.0.113.1', 443), ('127.0.0.1', 80), ('::1', 80)]:
                with self.subTest(address=address), socket.socket() as sock:
                    with self.assertRaisesRegex(AssertionError, 'Network forbidden'):
                        sock.connect(address)
            with self.assertRaisesRegex(AssertionError, 'Network forbidden'):
                socket.create_connection(('127.0.0.1', 80))
