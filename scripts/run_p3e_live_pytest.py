"""Spawn-safe fixed pytest entrypoint for the P3-E live acceptance case."""

from __future__ import annotations


def main() -> int:
    import pytest

    return pytest.main(
        [
            "tests/integration/test_p3e_live_provider.py::test_authorized_six_case_current_route_conformance",
            "-s",
            "-ra",
            "--tb=short",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
