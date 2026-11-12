"""The audiences step: take out the people who are already in the ICP's Clay audience. Free, read-only in Clay.

User rule (12 Nov 2026): "once the marketing manager reviews the tables, after we have applied the changes she
asked for, which is the step before email enrichments, you should always then do the audiences step: check the
LinkedIn URL for each person that we have against the LinkedIn URLs in audiences in Clay." Decisions the same day:
  - a person found in the audience is REMOVED from the list (no email lookup, not delivered);
  - the check runs against ONE named audience per ICP (`clay_audience` in the ICP's icp.yaml), not all people.

Where it sits: after gtm_fixes.py / approval.py apply (the manager's changes are in), before the Supabase
pre-check and the email workflow. It runs on every people file that goes forward, the LinkedIn-only one too.

    python audiences_check.py --people "people - approved.csv" --people "<S> - review - LinkedIn only.csv" \
        --icp-config config/icps/<icp>/icp.yaml            # dry run: counts only
    python audiences_check.py ... --apply                  # writes the files below
    (--audience "<name or audseg_ id>" instead of --icp-config; --out-dir, --linkedin-col, --batch, --fresh)

--apply writes, and never edits the input files (the approved file keeps its hash):
  "<file> - not in audiences.csv"   the people who go forward. This is the file for the next step.
  "audiences - removed.csv"         the removed people + Audiences Record ID, SFDC Contact ID, Source File
  "audiences_check.json"            audience, date, counts and the sha256 of every file written

How a match is made. Clay's audience stores LinkedIn URLs in several spellings (http://www., https://linkedin.com,
with or without a trailing slash) and its "Equal" only ignores case, so an exact search misses people. So:
  1. search the audience for records whose LinkedIn URL CONTAINS "/in/<slug>", a few hundred slugs per call;
  2. fetch those records and compare the profile slug exactly, here ("/in/john-smith" must not match
     "/in/john-smith-123").
People with no LinkedIn profile URL cannot be checked. They stay, and the run says how many.

It fails closed: any Clay error stops the run, so "could not ask" is never read as "not in the audience". Clay's
rate limit (exit 4) is waited out. Finished batches are kept in audiences_check_state.json for the day, so a
killed run resumes; --fresh starts again (an audience is a live filter, its members change).
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _clay import VIA_WSL, to_cli_path  # noqa: E402

csv.field_size_limit(10**9)

LINKEDIN_PATH = ["contact_entity_field_values", "field", "linkedin_url"]
REMOVED_BECAUSE = "already in Clay audience"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def slug(url):
    """The profile slug of a LinkedIn person URL, lower-case and %-decoded. '' when it is not one."""
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", (url or "").strip(), re.I)
    return unquote(m.group(1)).lower() if m else ""


def _search_values(url):
    """What to search for one URL: the slug as written and, when it differs, %-decoded."""
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", (url or "").strip(), re.I)
    raw = m.group(1).lower() if m else ""
    return {"/in/" + raw, "/in/" + unquote(raw)} if raw else set()


def run_clay(args, tries=8):
    """`clay <args>` -> parsed JSON. Waits out the rate limit (exit 4); stops on anything else."""
    cmd = "clay " + " ".join(shlex.quote(str(a)) for a in args)
    full = ["wsl", "bash", "-lc", cmd] if VIA_WSL else ["bash", "-lc", cmd]
    for _ in range(tries):
        r = subprocess.run(full, capture_output=True, env={**os.environ, "MSYS_NO_PATHCONV": "1"})
        out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
        if r.returncode == 0:
            try:
                return json.loads(out)
            except ValueError:
                raise SystemExit(f"clay {' '.join(map(str, args[:3]))}: not JSON: {out[:300]}")
        if r.returncode == 4:
            m = re.search(r'"retryAfter"\s*:\s*(\d+)', err + out)
            wait = int(m.group(1)) + 2 if m else 62
            print(f"  Clay rate limit: waiting {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise SystemExit(f"clay {' '.join(map(str, args[:3]))} failed (exit {r.returncode}), nothing was written: "
                         f"{(err or out)[:400]}")
    raise SystemExit("Clay kept answering 'rate limited'. Nothing was written. Run it again later; it resumes.")


def resolve_audience(ref, clay):
    """Audience name or audseg_ id -> {id, name, filter}. A name must match exactly one people audience."""
    ref = (ref or "").strip()
    if not ref:
        raise SystemExit("no audience given: set clay_audience in the ICP's icp.yaml (ask the user which audience "
                         "this ICP is checked against), or pass --audience")
    if not ref.startswith("audseg_"):
        hits, cursor = [], None
        while True:
            page = clay(["audiences", "list", "--entity-type", "people"] + (["--cursor", cursor] if cursor else []))
            hits += [a for a in page.get("data") or [] if (a.get("name") or "").strip().lower() == ref.lower()]
            cursor = page.get("cursor")
            if not cursor:
                break
        if len(hits) != 1:
            raise SystemExit(f"audience {ref!r} matches {len(hits)} people audiences in Clay. "
                             "Ask the user for the exact name or the audseg_ id.")
        ref = hits[0]["id"]
    a = clay(["audiences", "get", ref])
    if a.get("entityType") != "people" or not isinstance(a.get("filter"), dict):
        raise SystemExit(f"audience {ref} is not a people audience with a filter: {str(a)[:200]}")
    return {"id": a["id"], "name": a.get("name") or "", "filter": a["filter"]}


def _field_id(clay, name):
    """Workspace field id by display name ('' if absent). Ids are workspace-specific, so never hard-coded."""
    d = clay(["audiences", "fields", "list", "--entity-type", "people"])
    for f in d.get("data") or []:
        if (f.get("name") or "").strip().lower() == name.lower():
            return f.get("id") or ""
    return ""


def find_in_audience(urls, audience, clay, work, batch=400, state=None, save=None):
    """-> {slug: {record_id, linkedin_url, sfdc_contact_id}} for the URLs whose profile is in the audience.

    `state` ({"done": [slugs], "found": {slug: rec}}) carries finished batches across a killed run."""
    state = state if state is not None else {}
    done, found = set(state.get("done") or []), dict(state.get("found") or {})
    by_slug = {}
    for u in urls:
        s = slug(u)
        if s and s not in by_slug:
            by_slug[s] = u
    todo = [s for s in sorted(by_slug) if s not in done]
    sfdc = _field_id(clay, "SFDC Contact ID") if todo else ""
    fpath = os.path.join(work, "audiences_check_filter.json")
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        values = sorted(set().union(*(_search_values(by_slug[s]) for s in part)))
        flt = {"type": "GroupOp", "combinationMode": "And", "items": [
            audience["filter"],
            {"type": "BinOp", "key": "linkedin_url", "dataPath": LINKEDIN_PATH, "operator": "ContainAny",
             "value": values, "entityType": "CONTACT"}]}
        with open(fpath, "w", encoding="utf-8") as fh:
            json.dump(flt, fh)
        ids, cursor = [], None
        while True:
            page = clay(["audiences", "records", "search-ids", "--entity-type", "people", "--filter",
                         to_cli_path(fpath), "--limit", "2000"] + (["--cursor", cursor] if cursor else []))
            if not isinstance(page.get("data"), list):
                raise SystemExit(f"Clay search answered without a list of ids, nothing was written: {str(page)[:300]}")
            ids += page["data"]
            cursor = page.get("cursor")
            if not cursor:
                break
        want = set(part)
        for j in range(0, len(ids), 100):
            got = clay(["audiences", "records", "get", "--entity-type", "people", "--ids",
                        ",".join(str(x) for x in ids[j:j + 100])])
            if not isinstance(got.get("data"), list):
                raise SystemExit(f"Clay records answered without a list, nothing was written: {str(got)[:300]}")
            for rec in got["data"]:
                f = rec.get("fields") or {}
                s = slug(f.get("linkedin_url"))
                if s in want and s not in found:      # the exact check: same profile slug
                    found[s] = {"record_id": rec.get("recordId"), "linkedin_url": f.get("linkedin_url") or "",
                                "sfdc_contact_id": (f.get(sfdc) or "") if sfdc else ""}
        done.update(part)
        state["done"], state["found"] = sorted(done), found
        if save:
            save(state)
        print(f"  checked {min(i + batch, len(todo)) + len(by_slug) - len(todo):,} of {len(by_slug):,} profiles, "
              f"{sum(1 for s in by_slug if s in found):,} in the audience", flush=True)
    if os.path.exists(fpath):
        os.remove(fpath)
    return {s: found[s] for s in by_slug if s in found}


def split_rows(rows, found, col):
    """-> (kept, removed, unchecked count). Pure, so tests can call it."""
    kept, removed, unchecked = [], [], 0
    for r in rows:
        s = slug(r.get(col))
        if not s:
            unchecked += 1
            kept.append(r)
        elif s in found:
            hit = found[s]
            removed.append({**r, "Removed Because": REMOVED_BECAUSE, "Audiences Record ID": hit.get("record_id"),
                            "SFDC Contact ID": hit.get("sfdc_contact_id") or ""})
        else:
            kept.append(r)
    return kept, removed, unchecked


def main(argv=None, clay=run_clay):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--people", action="append", required=True, help="a people CSV; repeat for each file")
    ap.add_argument("--audience", help="the Clay audience: its exact name or its audseg_ id")
    ap.add_argument("--icp-config", help="the ICP's icp.yaml; its clay_audience names the audience")
    ap.add_argument("--linkedin-col", default="LinkedIn URL")
    ap.add_argument("--out-dir", help="where the files go (default: next to the first --people file)")
    ap.add_argument("--batch", type=int, default=400, help="profiles per Clay search (default 400)")
    ap.add_argument("--fresh", action="store_true", help="ignore today's saved progress and ask Clay again")
    ap.add_argument("--apply", action="store_true", help="write the files (without it: counts only)")
    a = ap.parse_args(argv)

    ref = a.audience
    if not ref and a.icp_config:
        import yaml
        with open(a.icp_config, encoding="utf-8") as fh:
            ref = (yaml.safe_load(fh) or {}).get("clay_audience")
    out_dir = a.out_dir or os.path.dirname(os.path.abspath(a.people[0]))
    os.makedirs(out_dir, exist_ok=True)

    files = []
    for p in a.people:
        header, rows = read_csv(p)
        if a.linkedin_col not in header:
            raise SystemExit(f"{p}: no {a.linkedin_col!r} column (pass --linkedin-col)")
        files.append((p, header, rows))

    audience = resolve_audience(ref, clay)
    today = datetime.date.today().isoformat()
    spath = os.path.join(out_dir, "audiences_check_state.json")
    state = {}
    if os.path.exists(spath) and not a.fresh:
        with open(spath, encoding="utf-8") as fh:
            old = json.load(fh)
        if old.get("audience_id") == audience["id"] and old.get("date") == today:
            state = old
    state.update({"audience_id": audience["id"], "date": today})

    def save(st):
        tmp = spath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, spath)

    print(f"Audience: {audience['name']} ({audience['id']})")
    urls = [r.get(a.linkedin_col) for _, _, rows in files for r in rows]
    found = find_in_audience(urls, audience, clay, out_dir, batch=a.batch, state=state, save=save)

    record = {"audience": {"id": audience["id"], "name": audience["name"]}, "date": today, "files": []}
    all_removed, removed_header = [], []
    for p, header, rows in files:
        kept, removed, unchecked = split_rows(rows, found, a.linkedin_col)
        stem = os.path.splitext(os.path.basename(p))[0]
        out = os.path.join(out_dir, f"{stem} - not in audiences.csv")
        print(f"{os.path.basename(p)}: {len(rows):,} people -> {len(removed):,} already in the audience (removed), "
              f"{len(kept):,} go forward" + (f", of which {unchecked:,} have no LinkedIn profile URL (not checked)"
                                             if unchecked else ""))
        entry = {"in": p, "people": len(rows), "removed": len(removed), "kept": len(kept), "not_checked": unchecked}
        if a.apply:
            write_csv(out, header, kept)
            entry.update({"out": out, "out_sha256": sha256(out)})
        record["files"].append(entry)
        for r in removed:
            all_removed.append({**r, "Source File": os.path.basename(p)})
        removed_header += [h for h in header if h not in removed_header]
    if not a.apply:
        print("Dry run: nothing written. Add --apply.")
        return record
    rpath = os.path.join(out_dir, "audiences - removed.csv")
    write_csv(rpath, removed_header + ["Removed Because", "Audiences Record ID", "SFDC Contact ID", "Source File"],
              all_removed)
    record["removed_file"] = rpath
    with open(os.path.join(out_dir, "audiences_check.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    print(f"Wrote the '- not in audiences.csv' file(s), {os.path.basename(rpath)} and audiences_check.json in {out_dir}")
    return record


if __name__ == "__main__":
    main()
