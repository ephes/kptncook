"""Audit frozen runtime and development requirements on the active interpreter."""

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="kptncook-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--all-groups",
                "--no-hashes",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "uv",
                "tool",
                "run",
                "--python",
                sys.executable,
                "pip-audit==2.10.1",
                "--disable-pip",
                "--no-deps",
                "-r",
                str(requirements),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
