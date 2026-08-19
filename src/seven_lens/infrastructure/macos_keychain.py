"""Read-only macOS Keychain adapter with a terminable spawned lookup worker."""

from __future__ import annotations

import importlib
import math
import multiprocessing
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from types import ModuleType
from typing import Any, Protocol, cast

from seven_lens.application.ports.secrets import (
    KeychainLocked,
    MalformedSecret,
    SecretAccessDenied,
    SecretAmbiguous,
    SecretBackendUnavailable,
    SecretLookupTimeout,
    SecretNotFound,
)
from seven_lens.security.secret_values import (
    SecretRef,
    SecretValue,
    SecretValueError,
    validated_secret_ref_identity,
)

DEFAULT_KEYCHAIN_TIMEOUT_SECONDS = 2.0

_ERR_SEC_SUCCESS = 0
_ERR_SEC_USER_CANCELED = -128
_ERR_SEC_MISSING_ENTITLEMENT = -34018
_ERR_SEC_NOT_AVAILABLE = -25291
_ERR_SEC_AUTH_FAILED = -25293
_ERR_SEC_ITEM_NOT_FOUND = -25300
_ERR_SEC_INTERACTION_NOT_ALLOWED = -25308
_ERR_SEC_INTERACTION_REQUIRED = -25315
_ERR_SEC_NO_ACCESS_FOR_ITEM = -25243
_ERR_SEC_NOT_LOGGED_IN = -67729

_ACCESS_DENIED_STATUSES = frozenset(
    {
        _ERR_SEC_AUTH_FAILED,
        _ERR_SEC_USER_CANCELED,
        _ERR_SEC_MISSING_ENTITLEMENT,
        _ERR_SEC_NO_ACCESS_FOR_ITEM,
    }
)
_LOCKED_STATUSES = frozenset(
    {
        _ERR_SEC_INTERACTION_NOT_ALLOWED,
        _ERR_SEC_INTERACTION_REQUIRED,
        _ERR_SEC_NOT_LOGGED_IN,
    }
)
_IPC_VERSION = "seven-lens-keychain-v1"
_IPC_RESULT = "result"
_IPC_BRIDGE_FAILURE = "bridge_failure"
_CLEANUP_JOIN_SECONDS = 0.2


class NativeKeychainBridge(Protocol):
    """Narrow native boundary used only inside the lookup worker."""

    def lookup(self, service: str, account: str) -> tuple[int, object]: ...


class _SecurityModule(Protocol):
    kSecClass: object
    kSecClassGenericPassword: object
    kSecAttrService: object
    kSecAttrAccount: object
    kSecReturnData: object
    kSecMatchLimit: object
    kSecMatchLimitOne: object
    kSecUseAuthenticationUI: object
    kSecUseAuthenticationUIFail: object

    def SecItemCopyMatching(
        self,
        query: dict[object, object],
        result: None,
    ) -> tuple[int, object]: ...


class PyObjCKeychainBridge:
    """Security.framework generic-password exact-read bridge."""

    def __init__(
        self,
        security_module: ModuleType | None = None,
    ) -> None:
        try:
            self._security = cast(
                _SecurityModule,
                security_module or importlib.import_module("Security"),
            )
        except Exception:
            raise SecretBackendUnavailable from None

    def lookup(self, service: str, account: str) -> tuple[int, object]:
        try:
            query = {
                self._security.kSecClass: self._security.kSecClassGenericPassword,
                self._security.kSecAttrService: service,
                self._security.kSecAttrAccount: account,
                self._security.kSecReturnData: True,
                self._security.kSecMatchLimit: self._security.kSecMatchLimitOne,
                self._security.kSecUseAuthenticationUI: (
                    self._security.kSecUseAuthenticationUIFail
                ),
            }
            response = self._security.SecItemCopyMatching(query, None)
        except Exception:
            raise SecretBackendUnavailable from None
        if type(response) is not tuple or len(response) != 2 or type(response[0]) is not int:
            raise SecretBackendUnavailable
        return response


class NativeLookupResult:
    """Internal raw-byte result with a non-disclosing representation."""

    __slots__ = ("_items", "status")

    def __init__(self, status: int, items: tuple[bytes, ...] | None) -> None:
        self.status = status
        self._items = items

    @property
    def items(self) -> tuple[bytes, ...] | None:
        return self._items

    def __repr__(self) -> str:
        return "NativeLookupResult([REDACTED])"


class KeychainLookupRunner(Protocol):
    def lookup(self, service: str, account: str, timeout_seconds: float) -> NativeLookupResult: ...


class _Connection(Protocol):
    def poll(self, timeout: float = 0.0) -> bool: ...

    def recv(self) -> object: ...

    def send(self, value: object) -> None: ...

    def close(self) -> None: ...


class _Process(Protocol):
    exitcode: int | None

    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def close(self) -> None: ...


class _ProcessContext(Protocol):
    def Pipe(self, duplex: bool = True) -> tuple[_Connection, _Connection]: ...

    def Process(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
        daemon: bool = False,
    ) -> _Process: ...


class SpawnedKeychainLookupRunner:
    """Run one native lookup in a child that can be terminated on timeout."""

    def __init__(self, context: _ProcessContext | None = None) -> None:
        self._context = context or cast(_ProcessContext, multiprocessing.get_context("spawn"))

    def lookup(self, service: str, account: str, timeout_seconds: float) -> NativeLookupResult:
        receive_connection, send_connection = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_keychain_worker_entry,
            args=(send_connection, service, account),
            daemon=True,
        )
        started = False
        timed_out = False
        try:
            process.start()
            started = True
            send_connection.close()
            if not receive_connection.poll(timeout_seconds):
                if process.exitcode is not None:
                    raise SecretBackendUnavailable
                timed_out = True
                raise SecretLookupTimeout
            message = receive_connection.recv()
            return _parse_worker_message(message)
        except (SecretBackendUnavailable, SecretLookupTimeout):
            raise
        except Exception:
            raise SecretBackendUnavailable from None
        finally:
            receive_connection.close()
            if not started:
                send_connection.close()
                with suppress(ValueError):
                    process.close()
            if started:
                if not timed_out:
                    process.join(_CLEANUP_JOIN_SECONDS)
                if process.is_alive():
                    process.terminate()
                    process.join(_CLEANUP_JOIN_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(_CLEANUP_JOIN_SECONDS)
                process.close()


class MacOSKeychainSecretProvider:
    """Fail-closed adapter for exact read-only Keychain lookups."""

    def __init__(
        self,
        runner: KeychainLookupRunner | None = None,
        *,
        timeout_seconds: float = DEFAULT_KEYCHAIN_TIMEOUT_SECONDS,
        platform: str | None = None,
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("Keychain timeout must be between 0 and 30 seconds")
        self._runner = runner or SpawnedKeychainLookupRunner()
        self._timeout_seconds = float(timeout_seconds)
        self._platform = sys.platform if platform is None else platform

    def get_secret(self, ref: SecretRef) -> SecretValue:
        identity = validated_secret_ref_identity(ref)
        if identity is None:
            raise SecretBackendUnavailable
        if self._platform != "darwin":
            raise SecretBackendUnavailable
        _, account, service = identity
        try:
            result = self._runner.lookup(
                service,
                account,
                self._timeout_seconds,
            )
        except (
            SecretLookupTimeout,
            SecretBackendUnavailable,
        ):
            raise
        except Exception:
            raise SecretBackendUnavailable from None
        return _map_native_result(result)


def _keychain_worker_entry(connection: _Connection, service: str, account: str) -> None:
    """Child entrypoint: never print, log, persist, or stringify native results."""
    try:
        result = _execute_bridge_lookup(PyObjCKeychainBridge(), service, account)
        connection.send((_IPC_VERSION, _IPC_RESULT, result.status, result.items))
    except BaseException:
        with suppress(BaseException):
            connection.send((_IPC_VERSION, _IPC_BRIDGE_FAILURE, 0, None))
    finally:
        connection.close()


def _execute_bridge_lookup(
    bridge: NativeKeychainBridge,
    service: str,
    account: str,
) -> NativeLookupResult:
    status, raw_result = bridge.lookup(service, account)
    return NativeLookupResult(status, _normalize_native_items(status, raw_result))


def _normalize_native_items(status: int, raw_result: object) -> tuple[bytes, ...] | None:
    if status != _ERR_SEC_SUCCESS:
        return ()
    if raw_result is None:
        return ()
    if isinstance(raw_result, bytes):
        return (bytes(raw_result),)
    if not isinstance(raw_result, Sequence) or isinstance(raw_result, (str, bytes, bytearray)):
        try:
            converted = memoryview(cast(Any, raw_result)).tobytes()
        except (TypeError, ValueError):
            return None
        return (converted,)
    normalized: list[bytes] = []
    for item in raw_result:
        if type(item) is bytes:
            normalized.append(item)
            continue
        try:
            normalized.append(memoryview(item).tobytes())
        except (TypeError, ValueError):
            return None
    return tuple(normalized)


def _parse_worker_message(message: object) -> NativeLookupResult:
    if type(message) is not tuple or len(message) != 4:
        raise SecretBackendUnavailable
    version, kind, status, items = message
    if version != _IPC_VERSION or kind not in {_IPC_RESULT, _IPC_BRIDGE_FAILURE}:
        raise SecretBackendUnavailable
    if kind == _IPC_BRIDGE_FAILURE:
        raise SecretBackendUnavailable
    if type(status) is not int:
        raise SecretBackendUnavailable
    if items is not None and (
        type(items) is not tuple or any(type(item) is not bytes for item in items)
    ):
        raise SecretBackendUnavailable
    return NativeLookupResult(status, items)


def _map_native_result(result: NativeLookupResult) -> SecretValue:
    if not isinstance(result, NativeLookupResult):
        raise SecretBackendUnavailable
    if result.status == _ERR_SEC_ITEM_NOT_FOUND:
        raise SecretNotFound
    if result.status in _ACCESS_DENIED_STATUSES:
        raise SecretAccessDenied
    if result.status in _LOCKED_STATUSES:
        raise KeychainLocked
    if result.status != _ERR_SEC_SUCCESS:
        raise SecretBackendUnavailable
    items = result.items
    if items is None:
        raise MalformedSecret
    if not items:
        raise SecretNotFound
    if len(items) != 1:
        raise SecretAmbiguous
    try:
        return SecretValue.from_bytes(items[0])
    except SecretValueError:
        raise MalformedSecret from None
