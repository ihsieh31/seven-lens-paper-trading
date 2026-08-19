"""Fake-only native bridge, status mapping, and hard-timeout cleanup tests."""

from __future__ import annotations

from types import ModuleType
from typing import cast

import pytest

from fakes.secrets import (
    FakeConnection,
    FakeLookupRunner,
    FakeNativeKeychainBridge,
    FakeProcess,
    FakeProcessContext,
)
from seven_lens.application.ports.secrets import (
    KeychainLocked,
    MalformedSecret,
    SecretAccessDenied,
    SecretAmbiguous,
    SecretBackendUnavailable,
    SecretLookupTimeout,
    SecretNotFound,
)
from seven_lens.infrastructure.macos_keychain import (
    MacOSKeychainSecretProvider,
    NativeLookupResult,
    PyObjCKeychainBridge,
    SpawnedKeychainLookupRunner,
    _execute_bridge_lookup,
)
from seven_lens.security.secret_values import SecretKind, SecretRef

FAKE_SECRET_TEXT = "fake-native-secret-00000000"
REF = SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY)


class DuckTypedSecretRef:
    def __init__(self) -> None:
        self.property_reads = 0

    @property
    def keychain_service(self) -> str:
        self.property_reads += 1
        return "attacker.service"

    @property
    def keychain_account(self) -> str:
        self.property_reads += 1
        return "attacker-account"


class FakeSecurity(ModuleType):
    kSecClass = "class"
    kSecClassGenericPassword = "generic-password"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecReturnData = "return-data"
    kSecMatchLimit = "match-limit"
    kSecMatchLimitOne = "one"
    kSecUseAuthenticationUI = "authentication-ui"
    kSecUseAuthenticationUIFail = "fail"

    def __init__(self) -> None:
        super().__init__("FakeSecurity")
        self.queries: list[dict[object, object]] = []

    def SecItemCopyMatching(
        self,
        query: dict[object, object],
        result: None,
    ) -> tuple[int, object]:
        assert result is None
        self.queries.append(query)
        return 0, [FAKE_SECRET_TEXT.encode()]


def test_pyobjc_bridge_builds_one_exact_read_only_noninteractive_query() -> None:
    security = FakeSecurity()
    bridge = PyObjCKeychainBridge(cast(ModuleType, security))

    status, result = bridge.lookup(REF.keychain_service, REF.keychain_account)

    assert status == 0
    assert result == [FAKE_SECRET_TEXT.encode()]
    assert security.queries == [
        {
            "class": "generic-password",
            "service": "seven-lens.paper-trading.alpaca-paper.secret-key",
            "account": "primary",
            "return-data": True,
            "match-limit": "one",
            "authentication-ui": "fail",
        }
    ]
    assert not any(
        name in vars(security) for name in ("SecItemAdd", "SecItemUpdate", "SecItemDelete")
    )


def test_keychain_provider_returns_one_valid_exact_result() -> None:
    runner = FakeLookupRunner(NativeLookupResult(0, (FAKE_SECRET_TEXT.encode(),)))
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")

    value = provider.get_secret(REF)

    assert value.reveal_text() == FAKE_SECRET_TEXT
    assert runner.calls == [(REF.keychain_service, "primary", 2.0)]


@pytest.mark.parametrize(
    ("ref", "service", "account"),
    [
        (
            SecretRef.primary(SecretKind.ALPACA_PAPER_KEY_ID),
            "seven-lens.paper-trading.alpaca-paper.key-id",
            "primary",
        ),
        (
            SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY),
            "seven-lens.paper-trading.alpaca-paper.secret-key",
            "primary",
        ),
        (
            SecretRef.primary(SecretKind.OPENAI_API_KEY),
            "seven-lens.paper-trading.openai.api-key",
            "primary",
        ),
        (
            SecretRef.tavily("acct-01"),
            "seven-lens.paper-trading.tavily.api-key",
            "acct-01",
        ),
    ],
)
def test_valid_refs_still_use_fixed_keychain_identity(
    ref: SecretRef,
    service: str,
    account: str,
) -> None:
    runner = FakeLookupRunner()
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")

    provider.get_secret(ref)

    assert runner.calls == [(service, account, 2.0)]


def test_corrupted_internal_account_fails_before_arbitrary_runner_arguments() -> None:
    runner = FakeLookupRunner()
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")
    ref = SecretRef.primary(SecretKind.ALPACA_PAPER_SECRET_KEY)
    object.__setattr__(ref, "_account_id", "attacker-account")

    with pytest.raises(SecretBackendUnavailable):
        provider.get_secret(ref)

    assert runner.calls == []


def test_uninitialized_exact_ref_fails_before_arbitrary_runner_arguments() -> None:
    runner = FakeLookupRunner()
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")
    forged = object.__new__(SecretRef)
    object.__setattr__(forged, "_kind", SecretKind.OPENAI_API_KEY)
    object.__setattr__(forged, "_account_id", "primary")

    with pytest.raises(SecretBackendUnavailable):
        provider.get_secret(forged)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("raw_result", "expected_items"),
    [
        (None, ()),
        ([b"fake-one"], (b"fake-one",)),
        ([b"fake-one", b"fake-two"], (b"fake-one", b"fake-two")),
        ([bytearray(b"fake-data-compatible")], (b"fake-data-compatible",)),
        ([object()], None),
    ],
)
def test_injected_fake_native_bridge_normalizes_results_without_keychain(
    raw_result: object,
    expected_items: tuple[bytes, ...] | None,
) -> None:
    bridge = FakeNativeKeychainBridge(0, raw_result)

    result = _execute_bridge_lookup(bridge, REF.keychain_service, REF.keychain_account)

    assert result.status == 0
    assert result.items == expected_items
    assert bridge.calls == [(REF.keychain_service, REF.keychain_account)]


@pytest.mark.parametrize(
    ("status", "items", "error"),
    [
        (-25300, (), SecretNotFound),
        (0, (), SecretNotFound),
        (0, (b"fake-one", b"fake-two"), SecretAmbiguous),
        (-25293, (), SecretAccessDenied),
        (-128, (), SecretAccessDenied),
        (-34018, (), SecretAccessDenied),
        (-25243, (), SecretAccessDenied),
        (-25308, (), KeychainLocked),
        (-25315, (), KeychainLocked),
        (-67729, (), KeychainLocked),
        (-25291, (), SecretBackendUnavailable),
        (-99999, (), SecretBackendUnavailable),
        (0, None, MalformedSecret),
        (0, (b" bad",), MalformedSecret),
    ],
)
def test_keychain_provider_maps_native_status_and_result_failures(
    status: int,
    items: tuple[bytes, ...] | None,
    error: type[Exception],
) -> None:
    provider = MacOSKeychainSecretProvider(
        FakeLookupRunner(NativeLookupResult(status, items)),
        platform="darwin",
    )

    with pytest.raises(error):
        provider.get_secret(REF)


def test_non_darwin_fails_before_runner_call() -> None:
    runner = FakeLookupRunner()
    provider = MacOSKeychainSecretProvider(runner, platform="linux")

    with pytest.raises(SecretBackendUnavailable):
        provider.get_secret(REF)
    assert runner.calls == []


def test_duck_typed_ref_is_rejected_before_arbitrary_runner_arguments() -> None:
    runner = FakeLookupRunner()
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")
    forged = DuckTypedSecretRef()

    with pytest.raises(SecretBackendUnavailable):
        provider.get_secret(forged)  # type: ignore[arg-type]

    assert runner.calls == []
    assert forged.property_reads == 0


def test_typed_runner_failures_propagate_without_fallback() -> None:
    runner = FakeLookupRunner(failure=SecretLookupTimeout())
    provider = MacOSKeychainSecretProvider(runner, platform="darwin")

    with pytest.raises(SecretLookupTimeout):
        provider.get_secret(REF)
    assert len(runner.calls) == 1


def test_spawned_worker_timeout_terminates_joins_and_closes_every_resource() -> None:
    receive = FakeConnection(poll_result=False)
    send = FakeConnection()
    process = FakeProcess(alive=True, exitcode=None)
    context = FakeProcessContext(receive, send, process)
    runner = SpawnedKeychainLookupRunner(context)

    with pytest.raises(SecretLookupTimeout):
        runner.lookup(REF.keychain_service, REF.keychain_account, 0.001)

    assert process.started is True
    assert process.terminated is True
    assert process.closed is True
    assert process.is_alive() is False
    assert process.join_calls == [0.2]
    assert receive.closed is True
    assert send.closed is True
    assert context.daemon is True


def test_spawned_worker_crash_and_malformed_ipc_fail_closed_and_cleanup() -> None:
    crashed_receive = FakeConnection(poll_result=False)
    crashed_send = FakeConnection()
    crashed_process = FakeProcess(alive=False, exitcode=7)
    crashed_context = FakeProcessContext(crashed_receive, crashed_send, crashed_process)

    with pytest.raises(SecretBackendUnavailable):
        SpawnedKeychainLookupRunner(crashed_context).lookup(
            REF.keychain_service,
            REF.keychain_account,
            0.001,
        )
    assert crashed_process.closed is True
    assert crashed_receive.closed is True
    assert crashed_send.closed is True

    malformed_receive = FakeConnection(poll_result=True, message=("bad",))
    malformed_send = FakeConnection()
    malformed_process = FakeProcess(alive=False, exitcode=0)
    malformed_context = FakeProcessContext(
        malformed_receive,
        malformed_send,
        malformed_process,
    )
    with pytest.raises(SecretBackendUnavailable):
        SpawnedKeychainLookupRunner(malformed_context).lookup(
            REF.keychain_service,
            REF.keychain_account,
            0.001,
        )
    assert malformed_process.closed is True
    assert malformed_receive.closed is True
    assert malformed_send.closed is True
