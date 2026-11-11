"""Publish a Session Bandit transcript only after a successful producer run."""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from prompt_history.filesystem import private_directory


def publish(destination: Path, command: list[str]) -> None:
    with private_directory(destination.parent), \
            tempfile.TemporaryDirectory(prefix=".transcripts-", dir=destination.parent) as directory:
        staged = Path(directory) / "transcripts.jsonl"
        with staged.open("xb") as output:
            staged.chmod(0o600)
            subprocess.run(command, stdout=output, check=True)
        staged.replace(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        parser.error("producer command required")
    publish(args.destination, args.command)
