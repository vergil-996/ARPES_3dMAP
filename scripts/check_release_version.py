"""Fail a release build when its Git tag and application version differ."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_metadata import APP_VERSION
from update_service import SemanticVersion


def main(tag_name: str) -> int:
    match = re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag_name.strip())
    if match is None:
        print(f"Release tag must have the form vX.Y.Z; received {tag_name!r}.", file=sys.stderr)
        return 2
    tag_version = ".".join(match.groups())
    SemanticVersion.parse(APP_VERSION)
    if tag_version != APP_VERSION:
        print(
            f"Tag {tag_name} does not match app_metadata.APP_VERSION={APP_VERSION}.",
            file=sys.stderr,
        )
        return 3
    print(f"Validated BandScope release v{APP_VERSION}.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/check_release_version.py vX.Y.Z")
    raise SystemExit(main(sys.argv[1]))
