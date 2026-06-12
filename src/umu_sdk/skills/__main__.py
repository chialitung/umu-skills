"""python -m umu_sdk.skills.install 入口."""

from __future__ import annotations

import sys

from .install import main

if __name__ == "__main__":
    sys.exit(main())
