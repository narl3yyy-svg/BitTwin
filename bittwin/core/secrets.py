"""Optional storage of SFTP passphrases/passwords in the desktop's password store.

On Kubuntu this is KDE Wallet; on GNOME it is GNOME Keyring (both via the
`keyring` library, package python3-keyring). Secrets are stored encrypted by the
desktop - BitTwin itself never writes them to disk. If no secure store is
available, saving is simply not offered. There is deliberately no plain-text
fallback.
"""

from __future__ import annotations

from typing import Optional

SERVICE = "BitTwin SFTP"

# Backends that would store secrets unencrypted / not at all.
_INSECURE = ("fail", "null", "plaintext", "keyrings.alt")


def _secure_backend():
    try:
        import keyring
        from keyring.backends import chainer
    except Exception:
        return None
    try:
        kr = keyring.get_keyring()
    except Exception:
        return None
    backends = kr.backends if isinstance(kr, chainer.ChainerBackend) else [kr]
    secure = [b for b in backends
              if not any(bad in f"{type(b).__module__}.{type(b).__name__}".lower()
                         for bad in _INSECURE)]
    return secure[0] if secure else None


def available() -> bool:
    """True if a secure desktop password store can be used."""
    return _secure_backend() is not None


def backend_name() -> str:
    b = _secure_backend()
    if b is None:
        return ""
    name = type(b).__module__.lower()
    if "kwallet" in name:
        return "KDE Wallet"
    if "secretservice" in name or "libsecret" in name:
        return "your system keyring"
    return getattr(b, "name", "your system keyring")


def account_for(cfg) -> str:
    """Identify which secret this is: one per server account and key file."""
    what = f"key:{cfg.key_path}" if cfg.auth == "key" else "password"
    return f"{cfg.username}@{cfg.host}:{int(cfg.port)}|{what}"


def save(cfg, secret: str) -> bool:
    b = _secure_backend()
    if b is None or not secret:
        return False
    try:
        b.set_password(SERVICE, account_for(cfg), secret)
        return True
    except Exception:
        return False


def load(cfg) -> Optional[str]:
    b = _secure_backend()
    if b is None:
        return None
    try:
        return b.get_password(SERVICE, account_for(cfg))
    except Exception:
        return None


def forget(cfg) -> None:
    b = _secure_backend()
    if b is None:
        return
    try:
        b.delete_password(SERVICE, account_for(cfg))
    except Exception:
        pass  # nothing stored


def fill_in(cfg) -> None:
    """If the user chose to save this connection's secret, load it into cfg."""
    if not getattr(cfg, "remember_secret", False):
        return
    if cfg.auth == "key" and not cfg.passphrase:
        cfg.passphrase = load(cfg) or ""
    elif cfg.auth == "password" and not cfg.password:
        cfg.password = load(cfg) or ""
