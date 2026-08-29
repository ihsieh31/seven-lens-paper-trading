"""Operator CLI that sets the analysis provider endpoint and model.

Exactly two mutating commands persist the next-startup analysis route:

    python -m seven_lens.cli.analysis_provider set-endpoint https://integrate.api.nvidia.com/v1
    python -m seven_lens.cli.analysis_provider set-model openai/gpt-oss-120b

``show`` and ``validate`` are read-only conveniences.  The CLI never touches
API keys, never opens a network connection, and prints only bounded canonical
summaries without HOME paths, temporary paths, or secrets.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import sys
import uuid
from contextlib import suppress
from pathlib import Path

from seven_lens.config.analysis_provider import (
    AnalysisProviderConfig,
    ConfigSource,
    canonical_base_url,
    canonical_model_id,
    canonical_operator_bytes,
    load_analysis_provider_config,
    validate_production_root,
)
from seven_lens.config.errors import ConfigurationError

_EXIT_OK = 0
_EXIT_USAGE = 2
_EXIT_INVALID = 3
_EXIT_ENVIRONMENT = 4

_ROOT_ENV_OVERRIDE = "SEVEN_LENS_ANALYSIS_PROVIDER_CONFIG_ROOT"
_LOCK_SUFFIX = ".lock"

_SUMMARY_FIELDS = (
    "changed",
    "config_source",
    "generation",
    "base_url",
    "full_endpoint",
    "model_id",
    "route_config_hash",
    "restart_required",
)


class _CliError(RuntimeError):
    """One fixed, non-disclosing CLI failure with its exit code."""

    def __init__(self, message: str, *, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m seven_lens.cli.analysis_provider",
        description="persist the analysis provider endpoint and model for new processes",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    set_endpoint = subcommands.add_parser(
        "set-endpoint", help="persist the base URL (never a full chat-completions URL)"
    )
    set_endpoint.add_argument("base_url")
    set_model = subcommands.add_parser("set-model", help="persist the exact model id")
    set_model.add_argument("model_id")
    subcommands.add_parser("show", help="print the current route summary; never writes")
    subcommands.add_parser("validate", help="validate the stored route; never writes")
    args = parser.parse_args(argv)
    try:
        if args.command == "set-endpoint":
            _emit(_update_config("base_url", args.base_url))
        elif args.command == "set-model":
            _emit(_update_config("model_id", args.model_id))
        elif args.command == "show" or args.command == "validate":
            _emit(_summary(load_analysis_provider_config(_resolve_root()), changed=None))
        else:  # pragma: no cover - argparse rejects unknown commands
            parser.error("unknown command")
    except _CliError as error:
        print(f"analysis-provider: {error}", file=sys.stderr)
        return error.exit_code
    except ConfigurationError as error:
        print(f"analysis-provider: {error}", file=sys.stderr)
        return _EXIT_INVALID
    return _EXIT_OK


def _summary(config: AnalysisProviderConfig, *, changed: bool | None) -> dict[str, object]:
    return {
        "changed": changed,
        "config_source": config.config_source.value,
        "generation": config.generation,
        "base_url": config.base_url,
        "full_endpoint": config.full_endpoint,
        "model_id": config.model_id,
        "route_config_hash": config.route_config_hash,
        "restart_required": changed is True,
    }


def _emit(summary: dict[str, object]) -> None:
    payload = {key: value for key, value in summary.items() if value is not None}
    print(
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    )


def _update_config(field: str, raw_value: str) -> dict[str, object]:
    """Apply one operator field change under the fixed serialized algorithm."""

    if field == "base_url":
        canonical = canonical_base_url(raw_value)
    elif field == "model_id":
        canonical = canonical_model_id(raw_value)
    else:  # pragma: no cover - internal callers only
        raise _CliError("analysis provider command is invalid", exit_code=_EXIT_INVALID)

    root = _ensure_root()
    lock_path = root / ("analysis-provider.json" + _LOCK_SUFFIX)
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    temp_path: Path | None = None
    try:
        metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise _CliError("analysis provider config lock is unsafe", exit_code=_EXIT_ENVIRONMENT)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current = load_analysis_provider_config(root)
        base_url = current.base_url if field != "base_url" else canonical
        model_id = current.model_id if field != "model_id" else canonical
        if base_url == current.base_url and model_id == current.model_id:
            return _summary(current, changed=False)
        next_generation = (
            current.generation + 1 if current.config_source is ConfigSource.OPERATOR_FILE else 1
        )
        updated = AnalysisProviderConfig(
            config_source=ConfigSource.OPERATOR_FILE,
            generation=next_generation,
            base_url=base_url,
            model_id=model_id,
        )
        payload = canonical_operator_bytes(updated)
        temp_path = root / f".analysis-provider.{uuid.uuid4().hex}.tmp"
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        _verify_before_replace(root, temp_path)
        os.replace(temp_path, root / "analysis-provider.json")
        temp_path = None
        _fsync_directory(root)
        return _summary(updated, changed=True)
    except ConfigurationError as error:
        raise _CliError(str(error), exit_code=_EXIT_INVALID) from None
    except OSError:
        raise _CliError(
            "analysis provider config write failed", exit_code=_EXIT_ENVIRONMENT
        ) from None
    finally:
        if temp_path is not None:
            with suppress(OSError):
                os.unlink(temp_path)
        with suppress(OSError):
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _verify_before_replace(root: Path, temp_path: Path) -> None:
    directory_metadata = os.lstat(root)
    if (
        not stat.S_ISDIR(directory_metadata.st_mode)
        or stat.S_IMODE(directory_metadata.st_mode) & 0o022
    ):
        raise _CliError("analysis provider config root is unsafe", exit_code=_EXIT_ENVIRONMENT)
    temp_metadata = os.lstat(temp_path)
    if not stat.S_ISREG(temp_metadata.st_mode) or stat.S_IMODE(temp_metadata.st_mode) & 0o077:
        raise _CliError("analysis provider config write failed", exit_code=_EXIT_ENVIRONMENT)
    target = root / "analysis-provider.json"
    try:
        target_metadata = os.lstat(target)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISREG(target_metadata.st_mode):
        raise _CliError("analysis provider config target is unsafe", exit_code=_EXIT_ENVIRONMENT)


def _fsync_directory(root: Path) -> None:
    try:
        fd = os.open(root, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _resolve_root() -> Path:
    override = os.environ.get(_ROOT_ENV_OVERRIDE)
    if override is not None:
        root = Path(override)
        if not root.is_absolute():
            raise _CliError("analysis provider config root is invalid", exit_code=_EXIT_ENVIRONMENT)
        return validate_production_root(root)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    if not base.is_absolute():
        raise _CliError("analysis provider config root is invalid", exit_code=_EXIT_ENVIRONMENT)
    return validate_production_root(base / "seven-lens")


def _ensure_root() -> Path:
    root = _resolve_root()
    if _ROOT_ENV_OVERRIDE not in os.environ:
        base = root.parent
        try:
            os.makedirs(base, mode=0o700, exist_ok=True)
        except OSError:
            raise _CliError(
                "analysis provider config root is invalid", exit_code=_EXIT_ENVIRONMENT
            ) from None
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        metadata = os.lstat(root)
    except OSError:
        raise _CliError(
            "analysis provider config root is invalid", exit_code=_EXIT_ENVIRONMENT
        ) from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise _CliError("analysis provider config root is unsafe", exit_code=_EXIT_ENVIRONMENT)
    return root


if __name__ == "__main__":
    raise SystemExit(main())
