"""Flatten the people-search results into the deliverable CSV.

One row per PERSON, carrying the company context it was found through, so the
file can be handed on without a join. The company columns come from
`Highways CE - people feed.csv` (which is the classified deliverable, filtered
to searchable rows); the person columns come from the routine results.

The September `ce` search set `identifiers_only`, so its people are exactly
name, job title and profile URL. The `ce_fit` searches return full profiles,
which also carry the first/last name and location the Salesforce file needs
(First Name, Last Name, Mailing City / State Province / Country); those columns
are blank for an identifiers-only result. Full profiles cap a search at 10, so
`ce_fit_20` lays the seniority-split searches (A + B, up to 20) over the
companies that hit that cap — see build_ce_fit_split_feed.py.

THE EXCLUSION LIST IS RE-APPLIED HERE, and that is not belt-and-braces. Clay's
`job_title_exclude_keywords` is a SOFT filter: the terms were sent on every call
and measurably shrank the candidate pool (419 -> 383 on a control company), but
113 of the first 16,586 rows still came back with an excluded term in the title
— "Accounting Manager", "Freelance Graphic Designer", "Virtual Assistant". The
list is the user's rule for every query, so it is enforced locally where it can
be enforced exactly: whole-word, case-insensitive, against `Job Title`.

Dropped rows are WRITTEN OUT, never just discarded — `--dropped-out` names the
file, and it is produced by default beside the deliverable. A filter nobody can
inspect is how a good person quietly disappears and nobody notices.

COMPANIES THAT RETURNED NOBODY ARE NOT DROPPED SILENTLY. They are reported in
the summary and, with --include-empty, written as a row with the person columns
blank, so a reader can tell "we looked and found nobody" apart from "we never
looked". Measured on the fleet, ~30% return nothing and virtually all of those
are "No Profile Found" — the domain does not resolve to a LinkedIn company.

    python export_ce_people.py                  # write the CSV + report
    python export_ce_people.py --keep-excluded  # do NOT re-apply the exclusions
    python export_ce_people.py --include-empty
    python export_ce_people.py --event "Big 5 Construct Saudi"
"""

import argparse
import collections
import csv
import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(SCRIPT_DIR, "ce_classification_exports")
FEED = os.path.join(OUT_DIR, "Highways CE - people feed.csv")
RESULT_DIR = os.path.join(SCRIPT_DIR, "ce_people_results")

# Other Sources runs the same workflow over a different feed, so it exports
# through the same rules and the same three-file contract. Only the paths and
# the deliverable's name differ; `--scope` switches them.
SCOPES = {
    "ce": ("Highways CE - people feed.csv", "ce_people_results",
           "Highways CE - people.csv"),
    "other_sources": ("Highways Other Sources - people feed.csv",
                      "other_sources_people_results",
                      "Highways Other Sources - people.csv"),
    # CE re-run through the `Fit Keep?` gate (build_ce_fit_people_feed.py,
    # ce_people_rollout.py --scope ce_fit[_pilot]).
    "ce_fit_pilot": ("Highways CE - people feed - fit - pilot.csv",
                     "ce_fit_pilot_people_results",
                     "Highways CE - people - fit pilot.csv"),
    "ce_fit": ("Highways CE - people feed - fit.csv", "ce_fit_people_results",
               "Highways CE - people - fit.csv"),
    "ce_fit_20": ("Highways CE - people feed - fit.csv", "ce_fit_people_results",
                  "Highways CE - people - fit 20.csv"),
    # Other Sources re-run on workflow v7, and with the seniority split laid
    # over its capped companies.
    "other_sources_v7": ("Highways Other Sources - people feed.csv",
                         "other_sources_v7_people_results",
                         "Highways Other Sources - people - v7.csv"),
    "other_sources_v7_20": ("Highways Other Sources - people feed.csv",
                            "other_sources_v7_people_results",
                            "Highways Other Sources - people - v7 20.csv"),
}
# Per scope: result dirs whose people REPLACE the base result for each Domain
# they cover, merged in order. Every listed dir must cover the same Domains —
# half a split would silently ship one seniority half as the whole company.
SPLITS = {
    "ce_fit_20": ("ce_fit_sen_a_people_results", "ce_fit_sen_b_people_results"),
    "other_sources_v7_20": ("other_sources_v7_sen_a_people_results",
                            "other_sources_v7_sen_b_people_results"),
}

import people_export_rules as rules  # noqa: E402

COMPANY_COLS = ("Company Name", "Domain", "Side", "Classification",
                "Company Event Name", "Exhibitor/Sponsor", "Country", "Website")
PERSON_COLS = ("Person Name", "First Name", "Last Name", "Job Title",
               "LinkedIn URL", "Mailing City", "Mailing State Province",
               "Mailing Country")


def person_cells(p):
    """PERSON_COLS for one result person. Location is the person's own, from
    the full profile — not the company's `Country`."""
    loc = p.get("structured_location") or {}
    country = loc.get("country") or ""
    # A country-only profile repeats the country as its city/state
    # ("Saudi Arabia" as Mailing City); that is no city at all.
    city, state = ((v if v != country else "")
                   for v in (loc.get("city") or "", loc.get("state") or ""))
    return [p.get("name", ""), p.get("first_name") or "", p.get("last_name") or "",
            p.get("title", "") or "", p.get("url", ""), city, state, country]


NO_PERSON = [""] * len(PERSON_COLS)




def asked_patterns():
    """The EXACT titles that may overturn an exclusion, per side.

    Exact titles only, from the shared rules module — see rule 2 there. Rescuing
    on the full asked-for list let generic function words (`Operations`,
    `Content`, `Compliance`) save almost anything: on the buy-side P&S export it
    rescued 528 of 528. Restricted to exact titles it rescues 10 here, which is
    precisely the senior compound titles the user approved.
    """
    return (rules.asked_patterns(rules.rescue_titles("highways")),
            rules.asked_patterns(rules.rescue_titles("highways_buyside")))


def load_results(result_dir=None):
    """{domain: (people, note)} across every result file on disk."""
    result_dir = result_dir or RESULT_DIR
    out = {}
    if not os.path.isdir(result_dir):
        raise SystemExit(f"no results at {result_dir} — run ce_people_rollout.py")
    for name in sorted(os.listdir(result_dir)):
        if not name.endswith(".jsonl"):
            continue
        for line in open(os.path.join(result_dir, name), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            res = rec.get("result") or {}
            inner = res.get("result") or {}
            note = ""
            if rec.get("status") != "complete":
                note = "run " + str(rec.get("status"))
            elif not (inner.get("people") or []):
                # The action's own words, kept verbatim rather than paraphrased:
                # "No Profile Found" means the DOMAIN did not resolve to a
                # company, which is a different failure from a company whose
                # staff simply do not match the persona.
                note = str(res.get("textPreview") or "no people").replace("❌", "").strip()
            out[rec["id"]] = (inner.get("people") or [], note)
    return out


def apply_splits(results, split_dirs):
    """Replace each split-covered Domain's people with the halves' people
    concatenated. A Domain that is incomplete in any half stops the export: a
    failed half is not "nobody at that level", and the base result it would
    replace is the only full-list one we have."""
    halves = [load_results(os.path.join(SCRIPT_DIR, d)) for d in split_dirs]
    domains = set(halves[0])
    if any(set(h) != domains for h in halves[1:]):
        raise SystemExit(f"split result dirs cover different Domains: {split_dirs}")
    bad = sorted(d for d in domains if any(h[d][1].startswith("run ") for h in halves))
    if bad:
        raise SystemExit(f"{len(bad)} Domain(s) have an incomplete split search, "
                         f"e.g. {bad[:3]} — re-run them before exporting")
    for d in domains:
        people = [p for h in halves for p in h[d][0]]
        results[d] = (people, "" if people else
                      next((h[d][1] for h in halves if h[d][1]), "no people"))
    print(f"seniority split: {len(domains):,} companies re-searched in "
          f"{len(split_dirs)} halves")
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", default="ce", choices=sorted(SCOPES))
    ap.add_argument("--feed")
    ap.add_argument("--event", help="limit to one event")
    ap.add_argument("--include-empty", action="store_true",
                    help="also write a blank-person row for companies with none")
    ap.add_argument("--keep-excluded", action="store_true",
                    help="skip the local exclusion pass (Clay's soft filter only)")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="keep every row even when the same LinkedIn profile "
                         "already appeared under another company")
    ap.add_argument("--strict-exclusions", action="store_true",
                    help="an excluded term removes the person even when the "
                         "title also matches a title the ICP asked for")
    ap.add_argument("--dropped-out",
                    help="where to write the excluded rows (default: beside the "
                         "deliverable, '... - excluded.csv')")
    ap.add_argument("--out")
    args = ap.parse_args()

    global RESULT_DIR
    feed_name, result_dir, out_name = SCOPES[args.scope]
    args.feed = args.feed or os.path.join(OUT_DIR, feed_name)
    RESULT_DIR = os.path.join(SCRIPT_DIR, result_dir)

    if not os.path.exists(args.feed):
        raise SystemExit(f"no feed at {args.feed}")
    companies = list(csv.DictReader(open(args.feed, encoding="utf-8-sig")))
    if args.event:
        companies = [c for c in companies if c["Company Event Name"] == args.event]
    results = load_results()
    if args.scope in SPLITS:
        results = apply_splits(results, SPLITS[args.scope])

    header = list(COMPANY_COLS) + list(PERSON_COLS) + ["Outcome"]
    name = args.out or (out_name[:-4]
                        + (f" - {args.event}" if args.event else "") + ".csv")
    path = os.path.join(OUT_DIR, name)
    os.makedirs(OUT_DIR, exist_ok=True)

    patterns = [] if args.keep_excluded else rules.exclusion_patterns()
    sell_asked, buy_asked = (([], []) if args.keep_excluded or args.strict_exclusions
                             else asked_patterns())
    dropped, drop_terms = [], collections.Counter()
    rescued, rescue_terms = [], collections.Counter()
    counts = collections.Counter()
    per_cat = collections.defaultdict(lambda: [0, 0, 0])   # cos, with people, people
    per_event = collections.defaultdict(lambda: [0, 0])
    seen_people = collections.Counter()
    seen_slugs = {}                       # profile key -> the Domain that claimed it
    duped, dup_kind = [], collections.Counter()
    written = 0

    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for c in companies:
            # Fit-stamped feeds only. The workflow already stopped these at
            # `Fit Keep?`; skipping here too means no stale result on disk can
            # put a dropped company's people back into the deliverable.
            if (c.get("Fit") or "").strip().upper() == "DROP":
                counts["dropped by fit filter"] += 1
                continue
            base = [c.get(k, "") for k in COMPANY_COLS]
            cat = c.get("Classification", "")
            per_cat[cat][0] += 1
            per_event[c.get("Company Event Name", "")][0] += 1

            if c["Domain"] not in results:
                counts["not searched yet"] += 1
                if args.include_empty:
                    w.writerow(base + NO_PERSON + ["not searched yet"])
                    written += 1
                continue

            people, note = results[c["Domain"]]
            if not people:
                counts[note or "no people"] += 1
                if args.include_empty:
                    w.writerow(base + NO_PERSON + [note or "no people"])
                    written += 1
                continue

            asked = (sell_asked if c.get("Side") == "Sell Side"
                     else buy_asked)
            kept = []
            n_excluded = n_duped = 0
            for p in people:
                title = p.get("title", "") or ""
                hit = next((t for t, rx in patterns if rx.search(title)), None)
                if hit:
                    # ICP TITLE WINS, per the user (2026-09-13). A "Chief
                    # Executive Officer - Software" is not stopped being a CEO
                    # by the word after the dash, and these compound titles skew
                    # senior — exclusion-wins removed 37 rows of which most were
                    # C-suite, VP or Director. The exclusion still decides on
                    # its own (Virtual Assistant, Inventory Controller), which
                    # is the case it was written for. --strict-exclusions
                    # restores the blunt rule.
                    rescue = (None if args.strict_exclusions else
                              next((t for t, rx in asked if rx.search(title)), None))
                    if rescue:
                        rescued.append((title, rescue, hit))
                        rescue_terms[hit] += 1
                    else:
                        dropped.append(base + person_cells(p) + ["excluded: " + hit])
                        drop_terms[hit] += 1
                        n_excluded += 1
                        continue
                # ONE ROW PER HUMAN, across the whole file (user, 2026-09-13).
                # 117 profiles came back twice: 62 as exact repeats inside a
                # single search result, the rest because a firm entered the feed
                # under two domains (progress.group/progress-m.com) or through a
                # parent and a subsidiary (Bluebeam/Nemetschek, Autodesk/
                # Payapps). Either way it is one person, and the downstream
                # email waterfall charges per row.
                #
                # First occurrence in FEED ORDER wins — stable, explainable, and
                # it does not silently prefer one event over another on a
                # tie-break nobody chose. The loser is written out, not binned.
                slug = rules.profile_key(p.get("url", ""))
                if not args.keep_duplicates and slug and slug in seen_slugs:
                    first = seen_slugs[slug]
                    duped.append(base + person_cells(p)
                                 + [f"duplicate of row for {first}"])
                    dup_kind[("same company" if first == c["Domain"]
                              else "other company")] += 1
                    n_duped += 1
                    continue
                if slug:
                    seen_slugs[slug] = c["Domain"]
                kept.append(p)

            if not kept:
                # Every person this company returned was excluded, or was
                # already counted elsewhere. Its own outcome rather than folded
                # into "no people": we DID find people here.
                why = ("all people excluded" if n_excluded and not n_duped else
                       "all people already counted elsewhere"
                       if n_duped and not n_excluded else
                       "all people excluded or already counted")
                counts[why] += 1
                if args.include_empty:
                    w.writerow(base + NO_PERSON + [why])
                    written += 1
                continue

            counts["found"] += 1
            per_cat[cat][1] += 1
            per_cat[cat][2] += len(kept)
            per_event[c.get("Company Event Name", "")][1] += len(kept)
            for p in kept:
                url = p.get("url", "")
                seen_people[url] += 1
                w.writerow(base + person_cells(p) + ["found"])
                written += 1

    searched = sum(v for k, v in counts.items() if k != "not searched yet")
    found = counts["found"]
    people_rows = sum(t[2] for t in per_cat.values())
    print(f"companies in feed   : {len(companies):,}")
    print(f"  searched          : {searched:,}")
    print(f"  returned people   : {found:,}" +
          (f"  ({found / searched:.0%})" if searched else ""))
    for k, v in counts.most_common():
        if k != "found":
            print(f"      {v:5}  {k}")
    print(f"\npeople rows written : {people_rows:,}"
          + (f"  (avg {people_rows / found:.1f} per company)" if found else ""))
    rep = sum(1 for v in seen_people.values() if v > 1)
    print(f"distinct profiles   : {len(seen_people):,}"
          f"  ({rep} appear at more than one company)")

    print(f"\nby classification (companies / with people / people):")
    for cat, (n, h, p) in sorted(per_cat.items(), key=lambda x: -x[1][2])[:15]:
        print(f"   {cat[:44]:46} {n:5} {h:5} {p:6}")

    print(f"\ntop events by people:")
    for ev, (n, p) in sorted(per_event.items(), key=lambda x: -x[1][1])[:10]:
        print(f"   {ev[:44]:46} {n:5} companies {p:6} people")

    if dropped:
        dpath = args.dropped_out or path[:-4] + " - excluded.csv"
        with open(dpath, "w", newline="", encoding="utf-8-sig") as fh:
            dw = csv.writer(fh)
            dw.writerow(header)
            dw.writerows(dropped)
        # The denominator is every person the search returned, DUPLICATES
        # INCLUDED. Omitting them understated it, so the printed total did not
        # match `delivered + excluded + duplicates` and the two sidecars looked
        # like they failed to add up against the deliverable.
        found = len(dropped) + len(duped) + people_rows
        share = len(dropped) / found
        print(f"\nexcluded by the title rule: {len(dropped):,} of "
              f"{found:,} people found ({share:.2%})")
        for t, n in drop_terms.most_common(12):
            print(f"      {n:4}  {t}")
        if len(drop_terms) > 12:
            print(f"      … {len(drop_terms) - 12} more term(s)")
        print(f"   -> {dpath}")
    elif not args.keep_excluded:
        print("\nexcluded by the title rule: none")

    if duped:
        ppath = path[:-4] + " - duplicates.csv"
        with open(ppath, "w", newline="", encoding="utf-8-sig") as fh:
            pw = csv.writer(fh)
            pw.writerow(header)
            pw.writerows(duped)
        print(f"\nduplicate profile rows removed: {len(duped):,} "
              f"({len(duped) / (len(duped) + people_rows):.2%} of people kept)")
        for kind, n in dup_kind.most_common():
            print(f"      {n:4}  the first row was at the {kind}")
        print(f"   -> {ppath}")
    elif not args.keep_duplicates:
        print("\nduplicate profile rows removed: none")

    if rescued:
        print(f"\nkept despite carrying an excluded term, because the title also "
              f"matches one the ICP asked for: {len(rescued):,}")
        for t, n in rescue_terms.most_common(8):
            print(f"      {n:4}  {t}")
        for title, ask, hit in rescued[:6]:
            print(f"        e.g. {title[:48]:50} ICP:{ask} / excluded:{hit}")

    print(f"\n{written:,} rows -> {path}")


if __name__ == "__main__":
    main()
