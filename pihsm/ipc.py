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


import logging
import time
import socket

from .common import compute_digest
from .verify import verify_message


log = logging.getLogger(__name__)


def open_activated_socket(fd=3):
    sock = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
    log.info('opened %r', sock)
    return sock


class Server:
    __slots__ = ('sock', 'sizes', 'max_size')

    def __init__(self, sock, *sizes):
        for s in sizes:
            assert type(s) is int and s > 0
        self.sock = sock
        self.sizes = sizes
        self.max_size = max(sizes)

    def serve_forever(self):
        while True:
            (sock, address) = self.sock.accept()
            try:
                sock.settimeout(1)
                self.handle_connection(sock)
            except:
                log.exception('Error handling request:')
            finally:
                sock.close()

    def handle_connection(self, sock):
        request = sock.recv(self.max_size)
        size = len(request)
        if size not in self.sizes:
            raise ValueError(
                'bad request size {!r} not in {!r}'.format(size, self.sizes)
            )
        verify_message(request)
        log.info('%s byte request', len(request))

        response = self.handle_request(request)
        log.info('%s byte response', len(response))
        sock.send(response)

    def handle_request(self, request):
        return compute_digest(request)


class PrivateServer(Server):
    __slots__ = ('display_client', 'signer')

    def __init__(self, sock, display_client, signer):
        super().__init__(sock, 224)
        self.signer = signer
        self.display_client = display_client

    def handle_request(self, request):
        signed = self.signer.sign(int(time.time()), request)
        self.display_client.make_request(signed)
        return signed


class DisplayServer(Server):
    __slots__ = ('manager',)

    def __init__(self, sock, manager):
        super().__init__(sock, 96, 400)
        self.manager = manager

    def handle_request(self, request):
        self.manager.update_screens(request)
        return compute_digest(request)


class Client:
    __slots__ = ('filename', 'response_size')

    def __init__(self, filename, response_size):
        self.filename = filename
        self.response_size = response_size

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(self.filename)
        return sock

    def _make_request(self, request):
        sock = self.connect()
        try:
            sock.send(request)
            response = sock.recv(self.response_size)
            if len(response) != self.response_size:
                raise ValueError(
                    'bad response size: expected {}; got {}'.format(
                        self.response_size, len(response)
                    )
                )
            return response
        finally:
            sock.close()


class DisplayClient(Client):
    __slots__ = tuple()

    def __init__(self, filename='/run/pihsm/display.socket'):
        super().__init__(filename, 48)

    def make_request(self, request):
        digest = compute_digest(request)
        response = self._make_request(request)
        assert response == digest
        return response


class PrivateClient(Client):
    __slots__ = tuple()

    def __init__(self, filename='/run/pihsm/private.socket'):
        super().__init__(filename, 400)

    def make_request(self, request):
        response = self._make_request(request)
        verify_message(response)
        assert response.endswith(request)
        return response
