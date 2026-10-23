"""Run the Clay CLI from Python on Windows (through WSL) or natively.

The Clay CLI refuses to run on Windows ("unsupported OS"), so on Windows every
call goes through `wsl bash -lc`, and file paths are converted to /mnt/c/...
Set CLAY_VIA_WSL=0 to force a native `clay`, =1 to force WSL.
"""

import json
import os
import shlex
import subprocess

VIA_WSL = os.environ.get("CLAY_VIA_WSL", "1" if os.name == "nt" else "0") == "1"


def to_cli_path(p):
    """A path the CLI can open. Under WSL that is the /mnt/<drive>/... form."""
    p = os.path.abspath(p)
    if VIA_WSL and len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def clay(args, check=True):
    """Run `clay <args>` (a list) and return parsed JSON (or raw text)."""
    cmd = "clay " + " ".join(shlex.quote(str(a)) for a in args)
    full = ["wsl", "bash", "-lc", cmd] if VIA_WSL else ["bash", "-lc", cmd]
    r = subprocess.run(full, capture_output=True,
                       env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0 and check:
        raise RuntimeError(f"clay {args[:3]} failed: "
                           f"{(r.stderr.decode('utf-8', 'replace') or out)[:400]}")
    try:
        return json.loads(out)
    except ValueError:
        return out
