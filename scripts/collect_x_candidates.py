from __future__ import annotations

import argparse
from pathlib import Path

from briefing.config import project_root
from briefing.opencli_social import collect_x_candidates


def main() -> int:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect whitelisted X discovery candidates through OpenCLI.")
    parser.add_argument("--config", type=Path, default=root / "sources.yaml")
    parser.add_argument("--output", type=Path, default=root / "data" / "opencli-x-candidates.json")
    parser.add_argument("--profile", default="daily-briefing")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--per-account-limit", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    args = parser.parse_args()

    payload = collect_x_candidates(
        config_path=args.config.resolve(),
        output_path=args.output.resolve(),
        profile=args.profile,
        lookback_hours=args.lookback_hours,
        per_account_limit=args.per_account_limit,
        timeout_seconds=args.timeout_seconds,
        progress=lambda message: print(message, flush=True),
    )
    checked = sum(len(lane["accounts_checked"]) for lane in payload["lanes"].values())
    failed = sum(len(lane["accounts_failed"]) for lane in payload["lanes"].values())
    print(f"output={args.output.resolve()}")
    print(f"accounts_checked={checked}")
    print(f"accounts_failed={failed}")
    print(f"candidates={len(payload['items'])}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
