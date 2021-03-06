# pihsm: Turn your Raspberry Pi into a Hardware Security Module 
# Copyright (C) 2017 System76, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from unittest import TestCase
import os
import socket

from nacl.exceptions import BadSignatureError

from .helpers import iter_permutations, random_u64, random_digest, TempDir
from ..sign import Signer, build_signing_form
from .. import common
from .. import verify
from  .. import ipc


class MockSocket:
    def __init__(self, *returns):
        self._returns = list(returns)
        self._calls = []

    def recv(self, size):
        self._calls.append(('recv', size))
        return self._returns.pop(0)

    def send(self, src):
        self._calls.append(('send', src))
        return len(src)


class MockClient:
    def __init__(self, *returns):
        self._returns = list(returns)
        self._calls = []

    def make_request(self, request):
        self._calls.append(request)
        return self._returns.pop(0)


class MockDisplayClient:
    def __init__(self):
        self._calls = []

    def make_request(self, request):
        self._calls.append(request)


class MockManager:
    def __init__(self):
        self._calls = []

    def update_screens(self, request):
        self._calls.append(request)


class TestServer(TestCase):
    def test_init(self):
        for size in [common.DIGEST, common.REQUEST]:
            sock = MockSocket()
            server = ipc.Server(sock, size)
            self.assertIs(server.sock, sock)
            self.assertIs(server.request_size, size)

    def test_handle_connection(self):
        for size in [common.DIGEST, common.REQUEST]:
            server = ipc.Server(None, size)

            # Bad size:
            for bad in [size - 1, size + 1]:
                sock = MockSocket(os.urandom(bad))
                with self.assertRaises(ValueError) as cm:
                    server.handle_connection(sock)
                self.assertEqual(str(cm.exception),
                    'bad request: expected {} bytes; got {}'.format(size, bad)
                )
                self.assertEqual(sock._calls, [('recv', size)])

            # Good size, should be handed off to Server.handle_request():
            sock = MockSocket(os.urandom(size))
            with self.assertRaises(NotImplementedError) as cm:
                server.handle_connection(sock)
            self.assertEqual(str(cm.exception),
                'Server.handle_request(request)'
            )
            self.assertEqual(sock._calls, [('recv', size)])


class TestPrivateServer(TestCase):
    def test_init(self):
        sock = MockSocket()
        display_client = MockDisplayClient()
        signer = Signer()
        server = ipc.PrivateServer(sock, display_client, signer)
        self.assertIs(server.sock, sock)
        self.assertIs(server.display_client, display_client)
        self.assertIs(server.signer, signer)
        self.assertEqual(sock._calls, [])
        self.assertEqual(display_client._calls, [])
        self.assertEqual(signer.counter, 0)

    def test_handle_request(self):
        sock = MockSocket()
        display_client = MockDisplayClient()
        signer = Signer()
        server = ipc.PrivateServer(sock, display_client, signer)

        s = Signer()
        req1 = s.sign(os.urandom(48))

        # Make sure request signature is checked:
        for bad in iter_permutations(req1):
            with self.assertRaises(BadSignatureError) as cm:
                server.handle_request(bad)
            self.assertEqual(str(cm.exception),
                'Signature was forged or corrupt'
            )
        self.assertEqual(sock._calls, [])
        self.assertIs(signer.tail, signer.genesis)
        self.assertEqual(display_client._calls, [])

        # Good request:
        response1 = server.handle_request(req1)
        self.assertIs(type(response1), bytes)
        self.assertEqual(len(response1), 400)
        self.assertTrue(response1.endswith(req1))
        self.assertIs(signer.tail, response1)
        self.assertEqual(signer.counter, 1)
        self.assertEqual(sock._calls, [])
        self.assertEqual(display_client._calls, [response1])

        # Should return same response if exact immediate request is reused:
        self.assertIs(server.handle_request(req1), response1)
        self.assertIs(signer.tail, response1)
        self.assertEqual(signer.counter, 1)
        self.assertEqual(sock._calls, [])
        self.assertEqual(display_client._calls, [response1, response1])

        # Another good request:
        req2 = s.sign(os.urandom(48))

        # Make sure request signature is checked:
        for bad in iter_permutations(req2):
            with self.assertRaises(BadSignatureError) as cm:
                server.handle_request(bad)
            self.assertEqual(str(cm.exception),
                'Signature was forged or corrupt'
            )
        self.assertEqual(sock._calls, [])
        self.assertIs(signer.tail, response1)
        self.assertEqual(display_client._calls, [response1, response1])

        # Good request:
        response2 = server.handle_request(req2)
        self.assertIs(type(response2), bytes)
        self.assertEqual(len(response2), 400)
        self.assertTrue(response2.endswith(req2))
        self.assertIs(signer.tail, response2)
        self.assertEqual(signer.counter, 2)
        self.assertEqual(sock._calls, [])
        self.assertEqual(display_client._calls,
            [response1, response1, response2]
        )

        # Should return same response if exact immediate request is reused:
        self.assertIs(server.handle_request(req2), response2)
        self.assertIs(signer.tail, response2)
        self.assertEqual(signer.counter, 2)
        self.assertEqual(sock._calls, [])
        self.assertEqual(display_client._calls,
            [response1, response1, response2, response2]
        )


class TestClientServer(TestCase):
    def test_init(self):
        sock = MockSocket()
        serial_client = MockClient()
        signer = Signer()
        server = ipc.ClientServer(sock, serial_client, signer)
        self.assertIs(server.sock, sock)
        self.assertIs(server.serial_client, serial_client)
        self.assertIs(server.signer, signer)
        self.assertEqual(sock._calls, [])
        self.assertEqual(serial_client._calls, [])
        self.assertEqual(signer.counter, 0)

    def test_handle_request(self):
        s1 = Signer()
        digest = os.urandom(48)
        ts = random_u64()
        sf = build_signing_form(s1.public, s1.previous, 1, ts, digest)
        request = bytes(s1.key.sign(sf))

        s2 = Signer()
        response = s2.sign(request)

        sock = MockSocket()
        serial_client = MockClient(response)
        server = ipc.ClientServer(sock, serial_client, s1)
        self.assertEqual(s1.counter, 0)
        self.assertEqual(server.handle_request(digest, ts), response)
        self.assertEqual(s1.counter, 1)
        self.assertEqual(sock._calls, [])
        self.assertEqual(serial_client._calls, [request])


def _run_server(queue, filename, build_func, *build_args):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(filename)
        sock.listen(5)
        server = build_func(sock, *build_args)
        queue.put(None)
        server.serve_forever()
    except Exception as e:
        queue.put(e)
        raise e


def _start_server(filename, build_func, *build_args):
    import multiprocessing
    queue = multiprocessing.Queue()
    process = multiprocessing.Process(
        target=_run_server,
        args=(queue, filename, build_func) + build_args,
        daemon=True,
    )
    process.start()
    status = queue.get()
    if isinstance(status, Exception):
        process.terminate()
        process.join()
        raise status
    return process


class TempServer:
    def __init__(self, build_func, *build_args):
        self.tmpdir = TempDir()
        self.filename = self.tmpdir.join('temp.socket')
        self.process = _start_server(self.filename, build_func, *build_args)

    def __del__(self):
        self.terminate()

    def terminate(self):
        if getattr(self, 'process', None) is not None:
            self.process.terminate()
            self.process.join()


def _build_private_server(sock):
    return ipc.PrivateServer(sock, MockDisplayClient(), Signer())

class MockSerialClient:
    def __init__(self):
        self._signer = Signer()

    def make_request(self, request):
        return self._signer.sign(request)


def _build_client_server(sock):
    return ipc.ClientServer(sock, MockSerialClient(), Signer())


class TestLiveIPC(TestCase):
    def test_private_ipc(self):
        server = TempServer(_build_private_server)        
        client = ipc.PrivateClient(server.filename)
        s = Signer()

        a1 = s.sign(os.urandom(48))
        b1 = client.make_request(a1)
        self.assertIs(type(b1), bytes)
        self.assertEqual(len(b1), 400)
        self.assertEqual(b1[176:], a1)
        self.assertEqual(verify.get_counter(b1), 1)

        a2 = s.sign(os.urandom(48))
        b2 = client.make_request(a2)
        self.assertIs(type(b2), bytes)
        self.assertEqual(len(b2), 400)
        self.assertEqual(b2[176:], a2)
        self.assertEqual(verify.get_counter(b2), 2)

        self.assertNotEqual(b1[:176], b2[:176])
        self.assertEqual(verify.get_pubkey(b1), verify.get_pubkey(b2))

    def test_request_ipc(self):
        server = TempServer(_build_client_server)        
        client = ipc.ClientClient(server.filename)
        for i in range(100):
            digest = random_digest()
            response = client.make_request(digest)
            self.assertTrue(response.endswith(digest))


