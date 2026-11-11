# What every results file records about how it was made.

import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path


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
    # module form keeps the user's home folder out of saved results
    script = Path(sys.argv[0])
    cmd = f"python -m brace.{script.stem}" if script.parent.name == "brace" else f"python {script.name}"
    return {
        "command": " ".join([cmd, *sys.argv[1:]]),
        "commit": (git("rev-parse", "--short", "HEAD") or "none") + (" (uncommitted changes)" if dirty else ""),
        "ran_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "packages": vers,
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
    }
