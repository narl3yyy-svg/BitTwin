"""A sync side: either a local folder or an SFTP folder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .fs_local import LocalFS


@dataclass
class Location:
    kind: str = "local"            # "local" or "sftp"
    path: str = ""                 # local folder
    sftp: Optional[object] = None  # SFTPConfig

    @property
    def is_set(self) -> bool:
        return bool(self.path) if self.kind == "local" else self.sftp is not None

    @property
    def label(self) -> str:
        if self.kind == "sftp" and self.sftp is not None:
            return self.sftp.label
        return self.path

    def open(self):
        if self.kind == "sftp":
            from . import secrets
            from .fs_sftp import SFTPFS
            secrets.fill_in(self.sftp)  # saved passphrase, if the user chose to keep one
            return SFTPFS(self.sftp)
        return LocalFS(self.path)
