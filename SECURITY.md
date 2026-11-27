# Security Policy

BitTwin copies and deletes files, so security and data safety are taken seriously.

## Supported versions

| Version | Supported |
|---|---|
| Latest release on `main` | ✅ |
| Older versions | ❌ Please update |

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report it privately through GitHub instead:

1. Go to the repository's **Security** tab.
2. Click **Report a vulnerability**.
3. Describe the problem, how to reproduce it, and the impact you expect.

You'll get a reply as soon as possible. Once a fix is released you'll be credited,
unless you'd rather stay anonymous.

## What counts as a security issue

For example:

- BitTwin reading, writing or deleting anything **outside the two folders you chose**
- A way for an SFTP server or a crafted file/folder name to make BitTwin do that
- A way to make a corrupted copy pass verification
- Passphrases, passwords or private keys being written to disk or logs
- SFTP host-key checking being bypassed

## How BitTwin protects you

- **No network access** except to SFTP servers you add yourself. No telemetry,
  no update checks, no accounts.
- **Never needs root.** `install.sh` installs into your home folder. It asks before
  running `sudo apt` for missing Ubuntu packages.
- **Stays inside the chosen folders.** Names containing `/`, `..` or NUL from a server
  are rejected, every path is validated, symbolic links are never followed, and BitTwin
  refuses to write through a folder that is a symbolic link.
- **Verified writes.** Each copy goes to a temp file, is `fsync`ed, read back and
  SHA-256-checked, then atomically renamed into place.
- **SFTP host keys** are confirmed on first contact. A changed key blocks the connection.
- **Secrets stay in memory.** Key passphrases and passwords are never saved.
  Settings, logs and sync history are readable by your account only (`0700`/`0600`).
- **Untrusted names are shown as plain text**, never rendered as HTML.
- Every change is checked automatically by the test suite, **CodeQL**, **Bandit** and
  **ShellCheck** (see the Actions tab).

## Limits

- File names and folder paths appear in BitTwin's local log files
  (`~/.local/state/BitTwin/logs`). Those files are private to your account.
- Python can't reliably wipe secrets from memory, so a passphrase stays in memory
  until BitTwin closes.
- BitTwin protects against tricks *during* a sync. It can't protect files from
  another program that has your user permissions.
