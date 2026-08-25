"""Explicit P3-F eval entry points; defaults to no operation without a command."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from seven_lens.evals.provider_eval import (
    LiveEvalAuthorization,
    live_plan_summary,
    run_production_live_eval,
)
from seven_lens.evals.runner import run_and_verify_frozen


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m seven_lens.evals")
    subcommands = parser.add_subparsers(dest="command", required=True)
    offline = subcommands.add_parser("offline", help="run frozen offline scripted evaluation")
    offline.add_argument("--fixtures", type=Path, required=True)
    offline.add_argument("--frozen-report", type=Path, required=True)
    live_plan = subcommands.add_parser(
        "live-plan", help="validate and print a live plan; never sends requests"
    )
    live_plan.add_argument("--authorization-file", type=Path, required=True)
    live_plan.add_argument("--trusted-config-hash", required=True)
    live_plan.add_argument("--fixtures", type=Path, required=True)
    live_run = subcommands.add_parser(
        "live-run", help="execute the externally authorized production Agnes live evaluation"
    )
    live_run.add_argument("--authorization-file", type=Path, required=True)
    live_run.add_argument("--trusted-config-hash", required=True)
    live_run.add_argument("--trusted-grant-sha256", required=True)
    live_run.add_argument("--grant-file", type=Path, required=True)
    live_run.add_argument("--fixtures", type=Path, required=True)
    live_run.add_argument("--evidence-filename", required=True)
    live_run.add_argument("--execute-live", action="store_true")
    args = parser.parse_args()
    if args.command == "offline":
        report = run_and_verify_frozen(args.fixtures, args.frozen_report)
        print(report.to_bytes().decode("utf-8"), end="")
        return 0 if report.wire["offline_passed"] is True else 1
    authorization = LiveEvalAuthorization.from_json(
        _read_regular_file(args.authorization_file, maximum_bytes=1_048_576)
    )
    if args.command == "live-plan":
        print(
            json.dumps(
                dict(
                    live_plan_summary(
                        authorization,
                        args.trusted_config_hash,
                        corpus_root=args.fixtures,
                    )
                ),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    if args.execute_live is not True:
        parser.error("live-run is network-disabled unless --execute-live is explicit")
    supplied_grant = _read_grant(args.grant_file)
    run, evidence, evidence_path = run_production_live_eval(
        repo_root=Path(__file__).resolve().parents[3],
        corpus_root=args.fixtures,
        authorization=authorization,
        trusted_config_hash=args.trusted_config_hash,
        trusted_grant_sha256=args.trusted_grant_sha256,
        supplied_grant=supplied_grant,
        evidence_filename=args.evidence_filename,
    )
    print(
        json.dumps(
            {
                "audit_root_hash": run.audit_root_hash,
                "evidence_hash": evidence.evidence_hash,
                "evidence_path": str(evidence_path),
                "execution_kind": run.execution_kind,
                "fallback_count": run.fallback_count,
                "request_count": run.request_count,
                "total_tokens": run.total_tokens,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _read_regular_file(
    path: Path, *, maximum_bytes: int, require_private_mode: bool = False
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= maximum_bytes
            or (require_private_mode and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            raise ValueError("CLI input must be one bounded regular non-symlink file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("CLI input must be one bounded regular non-symlink file")
        return b"".join(chunks)
    except OSError as error:
        raise ValueError("CLI input must be one bounded regular non-symlink file") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_grant(path: Path) -> str:
    try:
        value = _read_regular_file(path, maximum_bytes=4_096, require_private_mode=True).decode(
            "utf-8"
        )
    except UnicodeDecodeError:
        raise ValueError("live grant file is not UTF-8") from None
    if not value or "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError("live grant must be one exact non-empty line without a terminator")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
