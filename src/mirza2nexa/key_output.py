"""Publish a CLI key only after writing it to a private file on the same volume."""

from __future__ import annotations

import os
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4


def _write_windows_key(path: Path, data: bytes) -> None:
    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32security

    # Set a protected DACL at creation. An inherited ACL can grant other users access even
    # if the file was created with mode 0600, which Python 3.12 on Windows cannot enforce.
    try:
        volume = win32file.GetVolumePathName(str(path.parent))
        _, _, _, flags, _ = win32api.GetVolumeInformation(volume)
        if not flags & win32con.FILE_PERSISTENT_ACLS:
            raise OSError("the key output volume does not support persistent ACLs")
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        with closing(token):
            user_sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, user_sid)
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorDacl(True, dacl, False)
        attributes.SECURITY_DESCRIPTOR.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED,
        )
        handle = win32file.CreateFile(
            str(path), win32con.GENERIC_WRITE, 0, attributes, win32con.CREATE_NEW,
            win32con.FILE_ATTRIBUTE_NORMAL, None,
        )
        try:
            with closing(handle):
                status, written = win32file.WriteFile(handle, data)
                if status or written != len(data):
                    raise OSError("incomplete key write")
                win32file.FlushFileBuffers(handle)
        except BaseException:
            path.unlink()
            raise
    except pywintypes.error as exc:
        raise OSError("cannot create a Windows key file with a private ACL") from exc


def publish_key(path: Path, key: str) -> None:
    data = (key + "\n").encode("utf-8")
    if os.name == "nt":
        staged = path.with_name(f".mirza-key-{uuid4().hex}.tmp")
        _write_windows_key(staged, data)
        try:
            # Windows rename fails when the destination already exists.
            staged.rename(path)
        finally:
            staged.unlink(missing_ok=True)
        return

    fd, name = tempfile.mkstemp(prefix=".mirza-key-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Same-directory link publishes the complete inode without replacing an existing
        # file or following a destination symlink. Unlink only our private staging name.
        os.link(staged, path)
    finally:
        staged.unlink()
