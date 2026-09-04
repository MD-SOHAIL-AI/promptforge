"""Backend-only credential storage for API provider secrets.

The production Windows implementation uses Credential Manager.  Callers only
store a stable provider identifier in normal settings; secret values never
enter the JSON settings document.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol


class CredentialStoreError(RuntimeError):
    """Raised when the operating-system credential store is unavailable."""


class CredentialStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, secret: str) -> None: ...
    def delete(self, provider_id: str) -> None: ...


class MemoryCredentialStore:
    """Explicit in-memory store for tests; never selected in production."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self._values.get(provider_id)

    def set(self, provider_id: str, secret: str) -> None:
        if not secret:
            raise CredentialStoreError("credential_secret_empty")
        self._values[provider_id] = secret

    def delete(self, provider_id: str) -> None:
        self._values.pop(provider_id, None)


class UnavailableCredentialStore:
    """Fail closed on platforms without an implemented secure store."""

    def get(self, provider_id: str) -> str | None:
        del provider_id
        return None

    def set(self, provider_id: str, secret: str) -> None:
        del provider_id, secret
        raise CredentialStoreError("secure_credential_store_unavailable")

    def delete(self, provider_id: str) -> None:
        del provider_id


if sys.platform == "win32":
    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class WindowsCredentialStore:
    _TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2
    _NOT_FOUND = 1168

    def __init__(self, namespace: str = "ForgeX/APIProvider") -> None:
        if sys.platform != "win32":
            raise CredentialStoreError("windows_credential_manager_unavailable")
        self.namespace = namespace
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]

    def _target(self, provider_id: str) -> str:
        safe_id = provider_id.strip().casefold()
        if not safe_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in safe_id):
            raise CredentialStoreError("credential_provider_id_invalid")
        return f"{self.namespace}/{safe_id}"

    def get(self, provider_id: str) -> str | None:
        pointer = ctypes.POINTER(_CREDENTIALW)()
        if not self._advapi.CredReadW(self._target(provider_id), self._TYPE_GENERIC, 0, ctypes.byref(pointer)):
            error = ctypes.get_last_error()
            if error == self._NOT_FOUND:
                return None
            raise CredentialStoreError("credential_read_failed")
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                return None
            raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            self._advapi.CredFree(pointer)

    def set(self, provider_id: str, secret: str) -> None:
        if not isinstance(secret, str) or not secret.strip():
            raise CredentialStoreError("credential_secret_empty")
        blob = secret.strip().encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = _CREDENTIALW()
        credential.Type = self._TYPE_GENERIC
        credential.TargetName = self._target(provider_id)
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._PERSIST_LOCAL_MACHINE
        credential.UserName = "ForgeX"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialStoreError("credential_write_failed")

    def delete(self, provider_id: str) -> None:
        if self._advapi.CredDeleteW(self._target(provider_id), self._TYPE_GENERIC, 0):
            return
        error = ctypes.get_last_error()
        if error != self._NOT_FOUND:
            raise CredentialStoreError("credential_delete_failed")


def default_credential_store() -> CredentialStore:
    return WindowsCredentialStore() if sys.platform == "win32" else UnavailableCredentialStore()
