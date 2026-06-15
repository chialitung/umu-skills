# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""python -m umu_sdk.skills.install 入口."""

from __future__ import annotations

import sys

from .install import main

if __name__ == "__main__":
    sys.exit(main())
