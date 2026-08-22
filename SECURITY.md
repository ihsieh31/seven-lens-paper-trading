# Security Policy and Trust Boundaries

Seven-Lens is a personal, Paper-only trading research system under development. It can
connect to Alpaca Paper read-only (GET-only, `P2-E` verified); real order submission is deferred to `P7` supervised gate and no live-money endpoint exists. Downloading market data, scheduling work, or submitting orders beyond the verified read-only path is not authorized. Passing a foundation test does not authorize a later phase or establish production readiness.

## Supported security scope

Security fixes are applied to the current `main` line. Reports should identify the commit,
affected boundary, reproducible input, expected invariant, and observed result. Do not include
real credentials, Keychain contents, broker account data, or proprietary source material in an
issue. Use an obviously fake value and a minimal reproducer.

This is a single-user public repository without a private disclosure channel. Until one is
explicitly established, report non-sensitive issues through GitHub Issues and describe a
credential-bearing or otherwise sensitive report only as a redacted request for a private
channel. Never publish an active secret. Suspected credential exposure requires immediate
revocation outside this repository before investigation continues.

## Non-negotiable invariants

- The codebase has no Alpaca live endpoint, live adapter, or mode switch.
- LLM and research components never receive broker authority.
- Unknown, missing, stale, malformed, ambiguous, or unauthorized inputs fail closed.
- PostgreSQL audit/domain ledgers are append-only and accept only registered typed payloads.
- PostgreSQL migration ownership and runtime access are separate capabilities.
- Logs, metrics, traces, audit payloads, exceptions, argv, environment, and committed files do
  not carry credentials or database DSNs.
- Telemetry failure cannot change business state, transaction outcome, retry behavior, or audit.

## Trust boundaries

### Configuration

Configuration sources are untrusted text. Existing broker and Tavily constructors validate an
exact typed schema and reject unknown keys. Future P2 composition must preserve this contract:
raw mappings may exist only at the parsing edge; validated typed values are passed to adapters;
there is no generic `dict[str, object]` bag, attribute fallback, or permissive default at the
execution boundary. A new setting requires its own type, validation, tests, and decision record.

### Secrets and Keychain

Production application code can request only fixed, typed `SecretRef` values through a scoped
provider. The macOS adapter performs an exact read-only generic-password query with UI disabled;
zero or multiple results, denial, timeout, malformed data, and backend failure are fatal. It has
no environment, argv, database, shell, fake, or second-provider fallback.

The native query uses exact service/account matching with `kSecMatchLimitOne` (exact hit) and a hard 2-second spawned-worker timeout with UI disabled; the prior `kSecMatchLimitAll` was replaced after the P2-E live verification exposed `errSecParam` on `ReturnData+MatchAll`. `NSData` normalization handles PyObjC bridging. The fake contract suite does not constitute native Keychain smoke evidence; real Keychain happy-path has been exercised via live P2-E read-only verification, but formal disposable adversarial smoke (locked/denied/malformed/timeout) remains deferred and requires a dedicated namespace and separate authorization before execution.

### PostgreSQL ownership and credentials

Migration and schema-owner credentials are an operator-only capability. They may be used for
checksummed migration, rollback in a disposable restore drill, and runtime-role provisioning;
they must never be supplied to a long-running application process.

The application runtime uses a distinct externally created login role. Before use,
`provision_runtime_role()` grants the bounded repository/function capabilities and
`verify_runtime_role()` proves that the role is non-owner, has no elevated role flags or owner
membership, cannot create schema/temp objects, cannot directly mutate protected state, and can
execute only the approved functions. A failed proof stops startup or deployment.

The P2 composition root (`application/composition.py`) defines this contract: configuration is
parsed once at the exact-schema edge into typed frozen values (no generic mapping bag), the
runtime database password uses the exact `POSTGRES_RUNTIME_PASSWORD` secret reference, and
`compose_runtime_dsn` in the infrastructure layer is the single bounded reveal point. The
composed `RuntimeDsn` never discloses itself through `str` or `repr`. Owner and runtime DSNs
are never persisted as config snapshots and must not enter logs, telemetry, audit, exception
messages, or command-line arguments; application-layer code may not import `urllib` or any
network/backend SDK. The current PostgreSQL integration DSN is disposable, fake, job-local
test input only.

P3 evidence adds a narrower publication boundary. Runtime may register bounded metadata and use
approved analysis-stage functions, but it has read-only table access and no EXECUTE right on
`publish_source_object(text)`. A trusted operator-side repository accepts only the exact local
`FileContentStore` capability and publishes only after reading the object, recomputing its SHA-256,
and matching its staged byte size. `verify_runtime_role()` checks every P3 table privilege
(`SELECT` only) and function ACL, including TRUNCATE/REFERENCES/TRIGGER drift;
`create_analysis_run` independently binds
the run snapshot to the referenced evidence packet.

The execution engine (`execution_service.py`) is the single submission entry point and
enforces the pause contract in-process: `build_execution_stack(..., control=...)` injects
the same control repository used by reconciliation, and `submit_from_outbox` checks
`entries_paused` before any state transition, commit, or broker call, raising
`ExecutionPausedError` with zero side effects when paused (except emergency RISK_EXIT
flows). This guarantees a paused system cannot create new exposure even if outbox workers
run; see ADR-021 and `tests/test_execution_pause_remediation.py`.

### SECURITY DEFINER functions

Privileged functions use a fixed `search_path` with `pg_catalog` first, trusted `public` next,
and `pg_temp` last; authoritative relations and row types are schema-qualified. `PUBLIC` has no
`CREATE` on the authoritative schema, no database `TEMPORARY`, and no execute right on protected
functions. Runtime callers cannot replace functions, alter guard triggers, directly mutate lease
or job state, or create temporary shadow relations.

### Persisted JSON and event payloads

`JsonObject` is a canonical value with fixed limits on nesting depth, total nodes, object/list
width, key/string byte length, and final UTF-8 serialized size. Limit failures are bounded and do
not echo input. These controls apply to persisted JSON values; raw documents and evidence must
remain in a separately designed content-addressed evidence boundary.

Domain and audit ledgers do not accept arbitrary JSON. Each allowed event has a closed typed
payload and derived event name. PostgreSQL independently enforces the same registry with check
constraints. Adding an event therefore requires a typed domain model, database registry change,
migration/restore test, serialization test, and explicit data-classification review.

## Verification expectations

- `./scripts/verify_p1.sh` is the locked quality/non-integration gate.
- `./scripts/verify_p1.sh --postgres` additionally runs zero-skip PostgreSQL 16 integration.
- PostgreSQL security claims require a real PostgreSQL test, including catalog privilege checks
  and adversarial attempts from the runtime role.
- Native Keychain claims require separately authorized macOS smoke evidence; fake tests are
  clearly labeled and cannot be upgraded into that evidence.
- Coverage percentage, dependency audit, SBOM, license scan, and secret scan are quality and
  supply-chain controls to be introduced as separately scoped gates. Their absence is not
  represented as a proven exploit, and their future addition must not weaken the two required P1
  jobs or silently change repository permissions.

## Incident handling

On suspected secret exposure: stop the affected process, revoke/rotate the credential at its
authority, preserve only redacted evidence, inspect audit and repository history, and do not
resume until the exact source is removed and fail-closed tests pass. On PostgreSQL ownership or
privilege drift: stop runtime writes, revoke the runtime role, inspect catalog ownership/ACLs from
the owner connection, restore the approved grants, run the full PostgreSQL gate, then reconcile
authoritative state before resuming later-phase operations.
