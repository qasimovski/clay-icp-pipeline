"""The marketing-manager gate between Find People and the paid email workflow.

    python approval.py review --people people.csv --out "<Series> - people for review.csv"
        a copy for the marketing manager + a summary of what is in it (counts per classification, title word, country)
    python approval.py apply  --people people.csv --changes changes.yaml --approved-by "<name>" \
        --out "people - approved.csv" [--notes "..."]
        applies the manager's removals, writes the approved list, the removed rows, and approval.json

No email is looked up, found or validated before `apply` has run: the email workflow's bulk run refuses a feed whose
sha256 is not the one approval.json recorded (bulk_run.py --approval approval.json). Editing the approved file after
approval breaks the hash on purpose - re-run `apply`.

changes.yaml (every key optional; matching is case-insensitive):
    remove_title_keywords: [intern, assistant]      # drop people whose Job Title contains the word/phrase
    remove_countries: [India]                       # drop people whose Mailing Country is one of these
    keep_only_countries: [United Kingdom, Ireland]  # drop everyone else
    remove_classifications: [Consultancies]         # by ICP category
    remove_domains: [acme.com]                      # whole companies
    remove_linkedin: [https://www.linkedin.com/in/x] # single people
    add_title_keywords: [...]                       # NOT applied here: additions need a new Find People run (Phase 6)
    add_countries: [...]                            # idem
"""

import argparse
import collections
import csv
import datetime
import hashlib
import json
import os
import re


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


def _norm(v):
    return re.sub(r"\s+", " ", (v or "").strip().lower())


def _slug(url):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", (url or "").lower())
    return m.group(1) if m else _norm(url)


def _has_word(text, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(_norm(term)) + r"(?![a-z0-9])", _norm(text)) is not None


def apply_changes(rows, changes):
    """-> (kept, removed rows each with a 'Removed Because'). Pure, so tests can call it."""
    ch = {k: [x for x in (v or []) if str(x).strip()] for k, v in (changes or {}).items()}
    countries = {_norm(c) for c in ch.get("remove_countries", [])}
    only = {_norm(c) for c in ch.get("keep_only_countries", [])}
    classes = {_norm(c) for c in ch.get("remove_classifications", [])}
    domains = {_norm(d).removeprefix("www.") for d in ch.get("remove_domains", [])}
    people = {_slug(u) for u in ch.get("remove_linkedin", [])}
    kept, removed = [], []
    for r in rows:
        why = None
        title = r.get("Job Title") or ""
        hit = next((t for t in ch.get("remove_title_keywords", []) if _has_word(title, t)), None)
        if hit:
            why = f"title contains '{hit}'"
        elif countries and _norm(r.get("Mailing Country")) in countries:
            why = f"country {r.get('Mailing Country')}"
        elif only and _norm(r.get("Mailing Country")) not in only:
            why = f"country {r.get('Mailing Country') or '(blank)'} not in keep-only list"
        elif classes and _norm(r.get("Classification")) in classes:
            why = f"classification {r.get('Classification')}"
        elif domains and _norm(r.get("Domain")).removeprefix("www.") in domains:
            why = f"company {r.get('Domain')}"
        elif people and _slug(r.get("LinkedIn URL")) in people:
            why = "person removed by name"
        if why:
            removed.append({**r, "Removed Because": why})
        else:
            kept.append(r)
    return kept, removed


def cmd_review(a):
    header, rows = read_csv(a.people)
    write_csv(a.out, header, rows)
    words = collections.Counter(w for r in rows for w in set(re.findall(r"[a-z][a-z&]+", _norm(r.get("Job Title")))) if len(w) > 2)
    print(f"{len(rows)} people -> {a.out}")
    for col in ("Side", "Classification", "Mailing Country"):
        if col in header:
            print(f"by {col}:", dict(collections.Counter(r.get(col) or "(blank)" for r in rows).most_common(12)))
    print("common title words:", dict(words.most_common(15)))
    print("Send this file to the marketing manager. Nothing is enriched until their changes go through `approval.py apply`.")


def cmd_apply(a):
    import yaml

    header, rows = read_csv(a.people)
    changes = yaml.safe_load(open(a.changes, encoding="utf-8")) if a.changes else {}
    unknown = set(changes or {}) - {"remove_title_keywords", "remove_countries", "keep_only_countries", "remove_classifications",
                                     "remove_domains", "remove_linkedin", "add_title_keywords", "add_countries"}
    if unknown:
        raise SystemExit(f"unknown keys in {a.changes}: {sorted(unknown)}")
    kept, removed = apply_changes(rows, changes)
    write_csv(a.out, header, kept)
    removed_path = a.out[:-4] + " - removed in review.csv"
    write_csv(removed_path, header + ["Removed Because"], removed)
    record = {"approved_by": a.approved_by, "approved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
              "notes": a.notes or "", "source_file": os.path.abspath(a.people), "source_sha256": sha256(a.people),
              "approved_file": os.path.abspath(a.out), "approved_sha256": sha256(a.out),
              "rows_before": len(rows), "rows_approved": len(kept), "rows_removed": len(removed),
              "removed_by_reason": dict(collections.Counter(r["Removed Because"].split(" '")[0].split(" ")[0] for r in removed)),
              "changes": changes or {}}
    path = os.path.join(os.path.dirname(os.path.abspath(a.out)), a.record)
    json.dump(record, open(path, "w", encoding="utf-8"), indent=1)
    print(f"approved {len(kept)} of {len(rows)} people ({len(removed)} removed -> {removed_path})")
    print(f"approval record -> {path}")
    adds = {k: v for k, v in (changes or {}).items() if k.startswith("add_") and v}
    if adds:
        print(f"NOT applied (needs a new Find People run for the affected companies, priced first): {adds}")


def verify(approval_path, feed_path):
    """Raise SystemExit unless feed_path is byte-identical to the list the manager approved."""
    rec = json.load(open(approval_path, encoding="utf-8"))
    got = sha256(feed_path)
    if got != rec.get("approved_sha256"):
        raise SystemExit(f"{feed_path} is not the approved list recorded in {approval_path} "
                         f"(sha256 {got[:12]}… vs {str(rec.get('approved_sha256'))[:12]}…). Re-run approval.py apply.")
    return rec


def main():
    ap = argparse.ArgumentParser(description="Marketing-manager approval gate before the email workflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review")
    r.add_argument("--people", required=True)
    r.add_argument("--out", required=True)
    p = sub.add_parser("apply")
    p.add_argument("--people", required=True)
    p.add_argument("--changes", help="changes.yaml from the manager's feedback; omit when approved as-is")
    p.add_argument("--approved-by", required=True, help="who approved (the marketing manager's name)")
    p.add_argument("--notes")
    p.add_argument("--out", required=True)
    p.add_argument("--record", default="approval.json")
    a = ap.parse_args()
    {"review": cmd_review, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    main()
