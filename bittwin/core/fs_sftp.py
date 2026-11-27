"""SFTP backend (remote servers / cloud drives over SSH)."""

from __future__ import annotations

import base64
import hashlib
import os
import posixpath
import socket
import stat
from dataclasses import dataclass
from typing import BinaryIO, Callable, Optional

import paramiko
from paramiko.sftp import CMD_EXTENDED, CMD_EXTENDED_REPLY

from .fs_local import CHUNK, is_excluded, join_rel, temp_name_for
from .model import Cancelled, Entry

ProgressCB = Optional[Callable[[int], None]]
CancelCB = Optional[Callable[[], bool]]


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "BitTwin")
    os.makedirs(path, exist_ok=True)
    return path


def known_hosts_path() -> str:
    return os.path.join(config_dir(), "known_hosts")


def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class UnknownHostKey(Exception):
    """Raised on first contact with a server so the user can confirm it."""

    def __init__(self, hostname: str, key: paramiko.PKey):
        self.hostname = hostname
        self.key = key
        self.key_type = key.get_name()
        self.fingerprint = fingerprint(key)
        super().__init__(f"Unknown host key for {hostname}: {self.fingerprint}")


class HostKeyChanged(Exception):
    pass


class _AskPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise UnknownHostKey(hostname, key)


def trust_host_key(hostname: str, key: paramiko.PKey) -> None:
    hk = paramiko.HostKeys()
    path = known_hosts_path()
    if os.path.exists(path):
        hk.load(path)
    hk.add(hostname, key.get_name(), key)
    hk.save(path)
    os.chmod(path, 0o600)


@dataclass
class SFTPConfig:
    host: str
    port: int = 22
    username: str = ""
    auth: str = "key"           # "key" or "password"
    key_path: str = ""
    passphrase: str = ""        # unlocks the private key; never written to disk
    password: str = ""          # only for auth == "password"; never written to disk
    remote_path: str = "/"

    @property
    def label(self) -> str:
        port = "" if self.port == 22 else f":{self.port}"
        return f"sftp://{self.username}@{self.host}{port}{self.remote_path}"

    def public_dict(self) -> dict:
        """Everything except secrets, for saving the connection."""
        return {"host": self.host, "port": self.port, "username": self.username,
                "auth": self.auth, "key_path": self.key_path,
                "remote_path": self.remote_path}


def load_private_key(path: str, passphrase: str) -> paramiko.PKey:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Private key file not found: {path}")
    try:
        return paramiko.PKey.from_path(path, passphrase.encode() if passphrase else None)
    except (paramiko.PasswordRequiredException, TypeError) as exc:
        raise PermissionError("This private key is protected - enter its passphrase.") from exc
    except paramiko.SSHException as exc:
        msg = str(exc)
        if "passphrase" in msg.lower() or "decrypt" in msg.lower() or "padding" in msg.lower():
            raise PermissionError("Wrong passphrase for the private key.") from exc
        raise ValueError(f"Could not read private key: {msg}") from exc
    except ValueError as exc:
        raise PermissionError("Wrong passphrase for the private key.") from exc


def connect(cfg: SFTPConfig, timeout: float = 15.0) -> tuple[paramiko.SSHClient, paramiko.SFTPClient]:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    kh = known_hosts_path()
    if os.path.exists(kh):
        client.load_host_keys(kh)
    client.set_missing_host_key_policy(_AskPolicy())

    kwargs = dict(hostname=cfg.host, port=int(cfg.port), username=cfg.username,
                  timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                  allow_agent=False, look_for_keys=False)
    if cfg.auth == "key":
        kwargs["pkey"] = load_private_key(cfg.key_path, cfg.passphrase)
    else:
        kwargs["password"] = cfg.password
    try:
        client.connect(**kwargs)
    except UnknownHostKey:
        client.close()
        raise
    except paramiko.BadHostKeyException as exc:
        client.close()
        raise HostKeyChanged(
            f"WARNING: the host key for {cfg.host} has CHANGED.\n"
            f"Expected {fingerprint(exc.expected_key)}, got {fingerprint(exc.key)}.\n"
            "This could mean someone is intercepting the connection. "
            "BitTwin refuses to connect.") from exc
    except paramiko.AuthenticationException as exc:
        client.close()
        raise PermissionError(f"Authentication failed for {cfg.username}@{cfg.host}.") from exc
    except (socket.timeout, TimeoutError) as exc:
        client.close()
        raise ConnectionError(f"Connection to {cfg.host}:{cfg.port} timed out.") from exc
    except (socket.gaierror, OSError, paramiko.SSHException) as exc:
        client.close()
        raise ConnectionError(f"Could not connect to {cfg.host}:{cfg.port}: {exc}") from exc
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    sftp = client.open_sftp()
    return client, sftp


class SFTPFS:
    kind = "sftp"

    def __init__(self, cfg: SFTPConfig):
        self.cfg = cfg
        self.client, self.sftp = connect(cfg)
        root = cfg.remote_path or "."
        try:
            self.root = self.sftp.normalize(root)
        except IOError as exc:
            self.close()
            raise FileNotFoundError(f"Remote folder does not exist: {root}") from exc

    @property
    def display(self) -> str:
        return self.cfg.label

    @property
    def uri(self) -> str:
        return f"sftp://{self.cfg.username}@{self.cfg.host}:{self.cfg.port}{self.root}"

    def _p(self, rel: str) -> str:
        return posixpath.join(self.root, rel) if rel else self.root

    def check_root(self) -> None:
        try:
            st = self.sftp.stat(self.root)
        except IOError as exc:
            raise FileNotFoundError(f"Remote folder does not exist: {self.root}") from exc
        if not stat.S_ISDIR(st.st_mode or 0):
            raise NotADirectoryError(f"Remote path is not a folder: {self.root}")

    def free_space(self) -> Optional[int]:
        try:
            t, msg = self.sftp._request(CMD_EXTENDED, "statvfs@openssh.com", self.root)
            if t != CMD_EXTENDED_REPLY:
                return None
            msg.get_int64()            # f_bsize
            frsize = msg.get_int64()   # f_frsize
            msg.get_int64()            # f_blocks
            msg.get_int64()            # f_bfree
            bavail = msg.get_int64()
            return bavail * frsize
        except Exception:
            return None

    def close(self) -> None:
        try:
            self.sftp.close()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass

    # ----------------------------------------------------------------- scan
    def scan(self, excludes: list[str], cancel: CancelCB = None,
             progress: Optional[Callable[[str], None]] = None,
             start: str = "") -> dict[str, Entry]:
        """Walk the tree below `start` (default: the root). Paths are root-relative."""
        self.check_root()
        result: dict[str, Entry] = {}
        stack = [start]
        while stack:
            parent = stack.pop()
            if cancel and cancel():
                raise Cancelled()
            if progress:
                progress(parent)
            try:
                items = self.sftp.listdir_attr(self._p(parent))
            except IOError as exc:
                raise OSError(f"Cannot read remote folder '{self._p(parent)}': {exc}") from exc
            for a in items:
                name = a.filename
                rel = join_rel(parent, name)
                if is_excluded(rel, name, excludes):
                    continue
                mode = a.st_mode or 0
                mtime = float(a.st_mtime or 0)
                if stat.S_ISLNK(mode):
                    result[rel] = Entry(rel, False, 0, mtime, 0, is_link=True)
                elif stat.S_ISDIR(mode):
                    result[rel] = Entry(rel, True, 0, mtime, stat.S_IMODE(mode))
                    stack.append(rel)
                elif stat.S_ISREG(mode):
                    result[rel] = Entry(rel, False, a.st_size or 0, mtime, stat.S_IMODE(mode))
        return result

    def stat(self, rel: str) -> Optional[Entry]:
        try:
            a = self.sftp.lstat(self._p(rel))
        except FileNotFoundError:
            return None
        except IOError:
            return None
        mode = a.st_mode or 0
        return Entry(rel, stat.S_ISDIR(mode), a.st_size or 0, float(a.st_mtime or 0),
                     stat.S_IMODE(mode), is_link=stat.S_ISLNK(mode))

    # ------------------------------------------------------------ reading
    def open_read(self, rel: str) -> BinaryIO:
        f = self.sftp.open(self._p(rel), "rb", bufsize=CHUNK)
        try:
            f.prefetch()
        except Exception:
            pass
        return f

    def hash_file(self, rel: str, uncached: bool = True, progress: ProgressCB = None,
                  cancel: CancelCB = None) -> tuple[str, int]:
        """SHA-256 of the remote file, read back over the wire."""
        h = hashlib.sha256()
        total = 0
        with self.open_read(rel) as f:
            while True:
                if cancel and cancel():
                    raise Cancelled()
                buf = f.read(CHUNK)
                if not buf:
                    break
                h.update(buf)
                total += len(buf)
                if progress:
                    progress(len(buf))
        return h.hexdigest(), total

    # ------------------------------------------------------------ writing
    def create_temp(self, rel: str) -> tuple[str, BinaryIO]:
        tmp = temp_name_for(rel)
        path = self._p(tmp)
        try:
            self.sftp.remove(path)
        except IOError:
            pass
        f = self.sftp.open(path, "wb", bufsize=CHUNK)
        f.set_pipelined(True)
        return tmp, f

    def finish_temp(self, f) -> None:
        try:
            f.flush()
            # wait for every pipelined write to be acknowledged
            self.sftp._finish_responses(f)
            try:
                # Ask the server to fsync (OpenSSH extension); ignore if unsupported.
                self.sftp._request(CMD_EXTENDED, "fsync@openssh.com", f.handle)
            except Exception:
                pass
        finally:
            f.close()

    def set_attrs(self, rel: str, mtime: float, mode: Optional[int]) -> None:
        path = self._p(rel)
        if mode is not None:
            try:
                self.sftp.chmod(path, mode & 0o7777)
            except IOError:
                pass
        self.sftp.utime(path, (int(mtime), int(mtime)))

    def replace(self, src_rel: str, dst_rel: str) -> None:
        src, dst = self._p(src_rel), self._p(dst_rel)
        try:
            self.sftp.posix_rename(src, dst)
        except IOError:
            # Server lacks posix-rename: fall back to remove + rename.
            try:
                self.sftp.remove(dst)
            except IOError:
                pass
            self.sftp.rename(src, dst)

    def _makedirs(self, path: str) -> None:
        parts = []
        while path and path != "/":
            try:
                st = self.sftp.stat(path)
                if stat.S_ISDIR(st.st_mode or 0):
                    break
                raise NotADirectoryError(path)
            except FileNotFoundError:
                parts.append(path)
                path = posixpath.dirname(path)
        for p in reversed(parts):
            try:
                self.sftp.mkdir(p)
            except IOError:
                st = self.sftp.stat(p)  # raced / already exists
                if not stat.S_ISDIR(st.st_mode or 0):
                    raise

    def move(self, src_rel: str, dst_rel: str) -> None:
        dst = self._p(dst_rel)
        self._makedirs(posixpath.dirname(dst))
        self.replace(src_rel, dst_rel)

    def mkdir(self, rel: str) -> None:
        self._makedirs(self._p(rel))

    def remove(self, rel: str) -> None:
        self.sftp.remove(self._p(rel))

    def remove_temp(self, rel: str) -> None:
        try:
            self.sftp.remove(self._p(rel))
        except IOError:
            pass

    def rmdir(self, rel: str) -> bool:
        try:
            if self.sftp.listdir(self._p(rel)):
                return False
            self.sftp.rmdir(self._p(rel))
            return True
        except FileNotFoundError:
            return True
