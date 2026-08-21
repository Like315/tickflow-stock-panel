"""Backfill AI training minute bars from the public Hugging Face archive."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from app.data_providers.huggingface_archive import (
    HuggingFaceAshareMinuteArchive,
    backfill,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    project_root = Path(__file__).resolve().parents[2]
    data_dir = project_root / "data"
    parser.add_argument("--data-dir", type=Path, default=data_dir)
    parser.add_argument(
        "--candidate-dir",
        type=Path,
        default=(
            data_dir
            / "user_data"
            / "investment_expert"
            / "training"
            / "candidates"
        ),
    )
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--download-raw", action="store_true")
    parser.add_argument("--raw-workers", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args()
    coverage = HuggingFaceAshareMinuteArchive(args.data_dir).coverage()
    manifest = backfill(
        data_dir=args.data_dir,
        candidate_dir=args.candidate_dir,
        start_date=args.start,
        end_date=args.end,
        batch_size=max(1, args.batch_size),
        threads=max(1, args.threads),
        download_raw=args.download_raw,
        raw_workers=max(1, args.raw_workers),
        revision=coverage.revision or "main",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
