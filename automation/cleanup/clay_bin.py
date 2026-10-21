"""Resolve which `clay` binary the WSL-side verification scripts should run.

WHY THIS EXISTS. Every WSL helper in this directory used to open with:

    c = sorted(glob.glob(os.path.join(base, "clay-*-linux-x64")) + ...)
    b = c[-1] if c else "clay"

`sorted()` on those filenames is LEXICOGRAPHIC, so with a cache holding
0.1.14, 0.1.17, 0.3.0, 0.6.0 and 0.16.0 it picks **0.6.0** — "6" sorts after
"3" and after "1". Clay's server rejects anything below 0.9.0 with
`upgrade_required`, so the call returns a non-zero exit and no stdout.

That is not a loud failure. `apply_ps_supabase.marker_present` treats an empty
column listing as "could not tell" and fails CLOSED as *already applied*, which
is correct as a credit guard — but it means every table in a fleet pass is
silently skipped with "already present", and the pass reports success having
done nothing. Observed 2026-09-12 on the Highways CE classification pass.

THE FIX: sort by PARSED version, newest first, and fall back to whatever `clay`
is on the clean PATH. `pick()` goes further and actually probes the candidates,
so a cached binary that the server later rejects self-heals instead of wedging
the fleet again the next time Clay raises its minimum.

The clean PATH matters and is not incidental: inherited Windows PATH entries
leak into WSL and can shadow the Linux binary with the win32 one, which then
fails in a much more confusing way.
"""

import glob
import json
import os
import re
import subprocess

CLEAN = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
CACHE = "~/.config/clay/bin"


def _version_key(path):
    """(1, (major, minor, patch)) for a parseable name, (0, ()) otherwise.

    The leading flag keeps unparseable names last rather than letting them
    compare against tuples of ints.
    """
    m = re.search(r"clay-(\d+)\.(\d+)\.(\d+)-linux-", os.path.basename(path))
    return (1, tuple(int(g) for g in m.groups())) if m else (0, ())


def candidates():
    """Cached binaries newest-first by parsed version, then PATH's `clay`."""
    base = os.path.expanduser(CACHE)
    cached = glob.glob(os.path.join(base, "clay-*-linux-x64")) + \
        glob.glob(os.path.join(base, "clay-*-linux-arm64"))
    cached.sort(key=_version_key, reverse=True)
    return cached + ["clay"]


def env():
    """Process env with the clean PATH forced."""
    e = dict(os.environ)
    e["PATH"] = CLEAN
    return e


def run(binary, args, timeout=120):
    """(ok, parsed_json_or_None, stderr). ok only when stdout parsed as JSON
    AND the payload is not an {"error": ...} envelope — `upgrade_required`
    arrives as valid JSON on a non-zero exit, so returncode alone is not the
    test."""
    try:
        r = subprocess.run([binary] + list(args), capture_output=True,
                           text=True, env=env(), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, None, str(e)[:300]
    if not (r.stdout or "").strip():
        return False, None, (r.stderr or "")[:300]
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return False, None, (r.stderr or r.stdout or "")[:300]
    if isinstance(d, dict) and d.get("error"):
        return False, d, json.dumps(d["error"])[:300]
    return r.returncode == 0, d, (r.stderr or "")[:300]


def pick(probe_args=("whoami",)):
    """First candidate that answers `probe_args` cleanly. Falls back to the
    newest cached name so callers still get a path to report in an error."""
    for b in candidates():
        ok, _d, _err = run(b, probe_args, timeout=60)
        if ok:
            return b
    c = candidates()
    return c[0] if c else "clay"
