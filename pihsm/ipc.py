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
import socket
from hashlib import sha384

from .verify import verify_message


log = logging.getLogger(__name__)


def open_activated_socket(fd=3):
    sock = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
    log.info('opened %r', sock)
    return sock


def _validate_size(size, max_size):
    assert type(max_size) is int and max_size > 0
    if type(size) is not int:
        raise TypeError(
            'size: need a {!r}; got a {!r}'.format(int, type(size))
        )
    if not (0 <= size <= max_size):
        raise ValueError(
            'need 0 <= size <= {}; got {}'.format(max_size, size)
        )
    return size


def _recv_into_once(sock, dst):
    assert type(dst) is memoryview
    max_size = len(dst)
    size = sock.recv_into(dst)
    return _validate_size(size, max_size)


def _recv_into(sock, dst):
    start = 0
    stop = len(dst)
    while start < stop:
        received = _recv_into_once(sock, dst[start:])
        if received == 0:
            break
        start += received
    return start


def _send_once(sock, src):
    assert type(src) is memoryview
    max_size = len(src)
    size = sock.send(src)
    return _validate_size(size, max_size)


def _send(sock, src):
    assert type(src) is bytes and len(src) > 0
    src = memoryview(src)
    start = 0
    stop = len(src)
    while start < stop:
        sent = _send_once(sock, src[start:])
        if sent == 0:
            break
        start += sent
    if start != stop:
        raise ValueError(
            'expected to send {} bytes, but sent {}'.format(stop, start)
        )
    return start


class Server:
    __slots__ = ('sock', 'sizes', 'max_size', 'dst')

    def __init__(self, sock, *sizes):
        for s in sizes:
            assert type(s) is int and s > 0
        self.sock = sock
        self.sizes = sizes
        self.max_size = max(sizes)
        self.dst = memoryview(bytearray(max(sizes)))

    def serve_forever(self):
        while True:
            (sock, address) = self.sock.accept()
            try:
                sock.settimeout(1)
                request = self.read_request(sock)
                log.info('%s byte request', len(request))
                response = self.handle_request(request)
                log.info('%s byte response', len(response))
                _send(sock, response)
            except:
                log.exception('Error handling request:')
            finally:
                sock.close()

    def read_request(self, sock):
        log.info('Reading request from connection %r', sock)
        request = sock.recv(self.max_size)
        size = len(request)
        if size not in self.sizes:
            raise ValueError(
                'bad request size {!r} not in {!r}'.format(size, self.sizes)
            )
        verify_message(request)
        return request

    def handle_request(self, request):
        raise NotImplementedError(
            '%s.handle_request(request)'.format(self.__class__.__name__)
        )


class DisplayServer(Server):
    __slots__ = ('manager',)

    def __init__(self, manager, sock):
        super().__init__(sock, 96, 400)
        self.manager = manager

    def handle_request(self, request):
        self.manager.update_screens(request)
        d = sha384(request).digest()
        return d


class Client:
    __slots__ = ('filename', 'dst')

    def __init__(self, filename, response_size):
        self.filename = filename
        self.dst = memoryview(bytearray(response_size))

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(self.filename)
        return sock

    def make_request(self, request):
        sock = self.connect()
        try:
            _send(sock, request)
            size = _recv_into(sock, self.dst)
            if size != len(self.dst):
                raise ValueError(
                    'bad response size: expected {}; got {}'.format(
                        len(self.dst), size
                    )
                )
            return self.dst.tobytes()
        finally:
            sock.close()


class DisplayClient(Client):
    def __init__(self, filename):
        super().__init__(filename, 48)

    def make_request(self, request):
        digest = sha384(request).digest()
        response = super().make_request(request)
        assert response == digest
        return response

