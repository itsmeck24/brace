# What every results file records about how it was made.

import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version


def run_info(packages=("duckdb", "scikit-learn", "numpy")):
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()

    dirty = bool(git("status", "--porcelain", "--", "*.py", "*.sql"))
    vers = {}
    for p in packages:
        try:
            vers[p] = version(p)
        except Exception:
            pass
    return {
        "command": "python " + " ".join(sys.argv),
        "commit": (git("rev-parse", "--short", "HEAD") or "none") + (" (uncommitted changes)" if dirty else ""),
        "ran_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "packages": vers,
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
    }
