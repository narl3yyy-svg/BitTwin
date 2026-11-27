"""A tiny in-process SFTP server (paramiko) serving a local folder.

Used only by the test suite so the SFTP backend can be tested end-to-end
without an external server. Accepts one public key.
"""

import os
import socket
import threading

import logging

import paramiko
from paramiko import SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface
from paramiko.sftp import SFTP_FAILURE, SFTP_OK

logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class _Server(paramiko.ServerInterface):
    def __init__(self, allowed_key, username):
        self.allowed_key = allowed_key
        self.username = username

    def check_auth_publickey(self, username, key):
        if username == self.username and key == self.allowed_key:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "publickey"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else \
            paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _Handle(SFTPHandle):
    def stat(self):
        try:
            return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def chattr(self, attr):
        try:
            SFTPServer.set_file_attr(self.filename, attr)
            return SFTP_OK
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)


class _SFTP(SFTPServerInterface):
    ROOT = "/"

    def _real(self, path):
        return os.path.join(self.ROOT, self.canonicalize(path).lstrip("/"))

    def canonicalize(self, path):
        return os.path.normpath("/" + path).replace("//", "/")

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                a = SFTPAttributes.from_stat(os.lstat(os.path.join(real, name)))
                a.filename = name
                out.append(a)
            return out
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def stat(self, path):
        try:
            return SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def lstat(self, path):
        try:
            return SFTPAttributes.from_stat(os.lstat(self._real(path)))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o600)
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        if (flags & os.O_CREAT) and attr is not None:
            attr._flags &= ~attr.FLAG_PERMISSIONS
            SFTPServer.set_file_attr(real, attr)
        if flags & os.O_WRONLY:
            mode = "ab" if flags & os.O_APPEND else "wb"
        elif flags & os.O_RDWR:
            mode = "a+b" if flags & os.O_APPEND else "r+b"
        else:
            mode = "rb"
        try:
            f = os.fdopen(fd, mode)
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        h = _Handle(flags)
        h.filename = real
        h.readfile = f
        h.writefile = f
        return h

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK

    def rename(self, old, new):
        if os.path.exists(self._real(new)):
            return SFTP_FAILURE
        try:
            os.rename(self._real(old), self._real(new))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK

    def posix_rename(self, old, new):
        try:
            os.replace(self._real(old), self._real(new))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK

    def mkdir(self, path, attr):
        try:
            os.mkdir(self._real(path))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK

    def rmdir(self, path):
        try:
            os.rmdir(self._real(path))
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK

    def chattr(self, path, attr):
        try:
            SFTPServer.set_file_attr(self._real(path), attr)
        except OSError as e:
            return SFTPServer.convert_errno(e.errno)
        return SFTP_OK


class TestSFTPServer:
    def __init__(self, root, client_pubkey, username="tester", sftp_cls=None):
        self.root = root
        self.sftp_cls = sftp_cls or _SFTP
        self.host_key = paramiko.RSAKey.generate(2048)
        self.client_pubkey = client_pubkey
        self.username = username
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(10)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self._transports = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        t = paramiko.Transport(conn)
        t.add_server_key(self.host_key)
        root = self.root

        class Bound(self.sftp_cls):
            ROOT = root

        t.set_subsystem_handler("sftp", SFTPServer, Bound)
        self._transports.append(t)
        try:
            t.start_server(server=_Server(self.client_pubkey, self.username))
        except (paramiko.SSHException, EOFError, OSError):
            t.close()  # client hung up (e.g. rejected our host key)

    def stop(self):
        self._stop = True
        try:
            self.sock.close()
        except OSError:
            pass
        for t in self._transports:
            t.close()
