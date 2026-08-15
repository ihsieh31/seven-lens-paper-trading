"""Credential-free deterministic fakes for the P1-C1 secret boundary."""

from __future__ import annotations

from collections.abc import Mapping

from seven_lens.application.ports.secrets import SecretProviderError
from seven_lens.infrastructure.macos_keychain import NativeLookupResult
from seven_lens.security.secret_values import SecretRef, SecretValue


class FakeSecretProvider:
    def __init__(
        self,
        values: Mapping[SecretRef, SecretValue] | None = None,
        failures: Mapping[SecretRef, SecretProviderError] | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._failures = dict(failures or {})
        self.calls: list[SecretRef] = []

    def get_secret(self, ref: SecretRef) -> SecretValue:
        self.calls.append(ref)
        failure = self._failures.get(ref)
        if failure is not None:
            raise failure
        return self._values[ref]


class FakeLookupRunner:
    def __init__(
        self,
        result: NativeLookupResult | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.result = result or NativeLookupResult(0, (b"fake-default-secret",))
        self.failure = failure
        self.calls: list[tuple[str, str, float]] = []

    def lookup(self, service: str, account: str, timeout_seconds: float) -> NativeLookupResult:
        self.calls.append((service, account, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        return self.result


class FakeNativeKeychainBridge:
    def __init__(self, status: int, result: object) -> None:
        self.status = status
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def lookup(self, service: str, account: str) -> tuple[int, object]:
        self.calls.append((service, account))
        return self.status, self.result


class FakeConnection:
    def __init__(self, *, poll_result: bool = False, message: object = None) -> None:
        self.poll_result = poll_result
        self.message = message
        self.closed = False
        self.sent: list[object] = []

    def poll(self, timeout: float = 0.0) -> bool:
        del timeout
        return self.poll_result

    def recv(self) -> object:
        return self.message

    def send(self, value: object) -> None:
        self.sent.append(value)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, *, alive: bool = True, exitcode: int | None = None) -> None:
        self.exitcode = exitcode
        self.alive = alive
        self.started = False
        self.terminated = False
        self.killed = False
        self.closed = False
        self.join_calls: list[float | None] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False
        self.exitcode = -15

    def kill(self) -> None:
        self.killed = True
        self.alive = False
        self.exitcode = -9

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)

    def close(self) -> None:
        self.closed = True


class FakeProcessContext:
    def __init__(
        self,
        receive_connection: FakeConnection,
        send_connection: FakeConnection,
        process: FakeProcess,
    ) -> None:
        self.receive_connection = receive_connection
        self.send_connection = send_connection
        self.process = process
        self.process_args: tuple[object, ...] | None = None
        self.daemon: bool | None = None

    def Pipe(self, duplex: bool = True) -> tuple[FakeConnection, FakeConnection]:
        assert duplex is False
        return self.receive_connection, self.send_connection

    def Process(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        daemon: bool = False,
    ) -> FakeProcess:
        assert callable(target)
        self.process_args = args
        self.daemon = daemon
        return self.process
