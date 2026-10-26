"""Judge companies against the ICP's written rules with fresh evidence, before paying to search them.

Two uses, one tool (Mining, Oct 2026):
  * RE-CHECK  companies whose old type maps to a new label, but the new version's rules are
              stricter ("a bank with no mining focus", "a distributor of generic industrial
              parts"). Mining: 1,339 queued, 201 OUT, 42 RELABEL; every OUT is a company whose
              people would otherwise have been searched and paid for.
  * CLASSIFY  companies the old run left unknown (remap_prior.py's classify_queue.csv), when the
              user wants them typed locally rather than through the paid Clay classifier.

Agents (Claude subagents, or you) read the batches with a written BRIEF (template:
assets/recheck_brief.example.md, the Mining one) and write one verdict row per company. `check`
validates every output file; `remap_prior.py --verdicts recheck/out/*.csv` applies them.

    python recheck.py queue   --feed remap/feed_companies.csv --scope scope.yaml --out recheck/queue.csv
    python recheck.py queue   --unknown remap/classify_queue.csv --out recheck/queue.csv   # CLASSIFY use
    python recheck.py fetch   --queue recheck/queue.csv --cache recheck/lookups.jsonl     # free homepage fetch
    python recheck.py batches --queue recheck/queue.csv --cache recheck/lookups.jsonl --dir recheck [--size 100]
    python recheck.py check   --dir recheck --icp icp.yaml --scope scope.yaml [r_000 ... | --all]

scope.yaml `recheck:` (all optional):
    labels:          {<label>: "<why it is queued>"}   which kept labels are re-checked, and why
    out_codes:       [OIL_GAS, NOT_A_SUBLEVEL, ...]    the reasons an OUT may give
    extra_verdicts:  [RENTAL]                          verdicts that keep the label but list the
                                                       company for the user (a decision for them)

Output files (`<dir>/out/<batch>.csv`, utf-8-sig) have exactly the header
    domain,verdict,side,classification,reason,confidence,note
verdict: KEEP | RELABEL | OUT | <extra> for a labelled company; CLASSIFIED | OUT | UNKNOWN for an
unknown one. confidence: H / M / L.
"""

import argparse
import collections
import csv
import glob
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import yaml

csv.field_size_limit(10**9)
OUT_COLS = ["domain", "verdict", "side", "classification", "reason", "confidence", "note"]
IN_COLS = ["domain", "name", "events", "country", "current_side", "current_label", "prior_type",
           "why_queued", "web_evidence"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"}


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames or []), list(rd)


def write(path, cols, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def recheck_cfg(scope):
    return ((yaml.safe_load(open(scope, encoding="utf-8")) or {}).get("recheck") or {}) if scope else {}


def cmd_queue(a):
    rows = []
    if a.unknown:
        for r in read(a.unknown)[1]:
            rows.append(dict(r, **{"Why queued": "type unknown in the old run"}))
    if a.feed:
        why = recheck_cfg(a.scope).get("labels") or {}
        if not why:
            raise SystemExit("scope.yaml has no recheck.labels - say which labels to re-check (ask the user)")
        for r in read(a.feed)[1]:
            if r.get("Classification") in why:
                rows.append(dict(r, **{"Why queued": why[r["Classification"]]}))
    seen, out = set(), []
    for r in rows:
        if r["Domain"] and r["Domain"] not in seen:
            seen.add(r["Domain"])
            out.append(r)
    cols = ["Company Name", "Domain", "Website", "Side", "Classification", "Events", "Country", "Why queued"]
    write(a.out, cols, out)
    print(f"{len(out):,} companies queued -> {a.out}")
    for k, n in collections.Counter(r["Why queued"] for r in out).most_common():
        print(f"  {n:6,}  {k}")


def cache(path):
    out = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                d = json.loads(line)
                out[d["domain"]] = d
    return out


def page_text(raw):
    """(title, meta description, visible text) without needing BeautifulSoup."""
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    desc = re.search(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*'
                     r'content=["\']([^"\']*)', raw, re.I)
    body = re.sub(r"(?is)<(script|style|noscript|nav|header|footer)\b.*?</\1>", " ", raw)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    clean = lambda s: " ".join(html.unescape(s or "").split())  # noqa: E731
    return clean(title.group(1) if title else "")[:200], clean(desc.group(1) if desc else "")[:400], \
        " ".join(body.split())[:900]


def cmd_fetch(a):
    import requests
    import warnings
    warnings.filterwarnings("ignore")
    done = cache(a.cache)
    todo = {q["Domain"]: q.get("Website") or "http://" + q["Domain"] for q in read(a.queue)[1]
            if q["Domain"] and q["Domain"] not in done}
    print(f"fetch: {len(todo):,} homepages (free)", flush=True)

    def work(item):
        dom, url = item
        rec = {"domain": dom, "url": url}
        try:
            r = requests.get(url, headers=UA, timeout=(8, 15), verify=False, allow_redirects=True)
            rec["final_url"] = r.url
            if r.status_code >= 400:
                rec["error"] = f"http {r.status_code}"
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding
            rec["title"], rec["description"], rec["text"] = page_text(r.text[:400000])
        except requests.RequestException as ex:
            rec["error"] = type(ex).__name__
        except Exception as ex:  # noqa: BLE001 - one bad page must not stop the run
            rec["error"] = f"parse {type(ex).__name__}"
        return rec

    with ThreadPoolExecutor(max_workers=16) as ex, open(a.cache, "a", encoding="utf-8") as f:
        for n, rec in enumerate(ex.map(work, todo.items()), 1):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if n % 250 == 0:
                print(f"  {n:,}/{len(todo):,}", flush=True)
    print("fetch done (re-run to continue after an interruption)")


def cmd_batches(a):
    ev = cache(a.cache)
    rows = []
    for q in read(a.queue)[1]:
        f = ev.get(q["Domain"], {})
        if f and not f.get("error") and (f.get("title") or f.get("text")):
            web = (f"homepage {f.get('final_url') or f['url']}: title={f.get('title', '')} | "
                   f"desc={f.get('description', '')} | text={f.get('text', '')}")
        elif f:
            web = f"homepage {f['url']}: unreachable ({f.get('error', 'no text')})"
        else:
            web = "homepage not fetched"
        rows.append({"domain": q["Domain"], "name": q["Company Name"], "events": q.get("Events", ""),
                     "country": q.get("Country", ""), "current_side": q.get("Side", ""),
                     "current_label": q.get("Classification", ""), "prior_type": q.get("Prior Type", ""),
                     "why_queued": q.get("Why queued", ""), "web_evidence": web})
    for i in range(0, len(rows), a.size):
        write(os.path.join(a.dir, "in", f"r_{i // a.size:03d}.csv"), IN_COLS, rows[i:i + a.size])
    print(f"{len(rows):,} companies -> {(len(rows) + a.size - 1) // a.size} batches in {a.dir}/in "
          f"(homepage read for {sum(': title=' in r['web_evidence'] for r in rows):,})")


def cmd_check(a):
    icp = yaml.safe_load(open(a.icp, encoding="utf-8")) or {}
    labels = {("Sell Side", k) for k in (icp.get("sell") or {}).get("categories") or {}} | \
             {("Buy Side", k) for k in (icp.get("buy") or {}).get("categories") or {}}
    cfg = recheck_cfg(a.scope)
    codes, extra = set(cfg.get("out_codes") or []), set(cfg.get("extra_verdicts") or [])
    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(a.dir, "in", "*.csv"))) \
        if a.all or not a.names else a.names
    ok = True
    for name in names:
        _, want = read(os.path.join(a.dir, "in", name + ".csv"))
        pout = os.path.join(a.dir, "out", name + ".csv")
        if not os.path.exists(pout):
            print(f"{name}: MISSING output")
            ok = False
            continue
        hdr, got = read(pout)
        errs = []
        if hdr != OUT_COLS:
            errs.append(f"header must be exactly {','.join(OUT_COLS)}")
        if [w["domain"] for w in want] != [g.get("domain") for g in got]:
            errs.append(f"rows differ from input ({len(got)} vs {len(want)}; one per input row, same order)")
        for w, g in zip(want, got):
            cur, lab, v = (w["current_side"], w["current_label"]), (g.get("side"), g.get("classification")), g.get("verdict")
            known = bool(w["current_label"])
            if v == "OUT":
                if codes and g.get("reason") not in codes:
                    errs.append(f"OUT needs a reason from {sorted(codes)} ({g['domain']})")
                if any(lab):
                    errs.append(f"OUT needs a blank label ({g['domain']})")
            elif known and v in ({"KEEP"} | extra) and lab != cur:
                errs.append(f"{v} must repeat the current label ({g['domain']})")
            elif known and v == "RELABEL" and (lab not in labels or lab == cur):
                errs.append(f"RELABEL needs a different valid label ({g['domain']}: {lab})")
            elif not known and v == "CLASSIFIED" and lab not in labels:
                errs.append(f"CLASSIFIED needs a valid label ({g['domain']}: {lab})")
            elif v not in ({"KEEP", "RELABEL"} | extra if known else {"CLASSIFIED", "UNKNOWN"}):
                errs.append(f"bad verdict {v!r} for a {'labelled' if known else 'unknown'} company ({g['domain']})")
            if g.get("confidence") not in ("H", "M", "L"):
                errs.append(f"confidence must be H/M/L ({g['domain']})")
            if not (g.get("note") or "").strip():
                errs.append(f"empty note ({g['domain']})")
        if errs:
            ok = False
            print(f"{name}: FAIL {len(errs)} problems: " + " | ".join(errs[:6]))
        else:
            mix = collections.Counter(g["verdict"] + (":" + g["reason"] if g["verdict"] == "OUT" else "") for g in got)
            print(f"{name}: OK {len(got)}/{len(want)} " + ", ".join(f"{k}={n}" for k, n in mix.most_common()))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--feed", help="remap_prior.py feed_companies.csv (re-check the labels scope.yaml lists)")
    q.add_argument("--unknown", help="remap_prior.py classify_queue.csv (classify the unknowns)")
    q.add_argument("--scope")
    q.add_argument("--out", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--queue", required=True)
    f.add_argument("--cache", required=True)
    b = sub.add_parser("batches")
    b.add_argument("--queue", required=True)
    b.add_argument("--cache", required=True)
    b.add_argument("--dir", required=True)
    b.add_argument("--size", type=int, default=100)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    c.add_argument("--icp", required=True)
    c.add_argument("--scope")
    c.add_argument("--all", action="store_true")
    c.add_argument("names", nargs="*")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "queue" and not (a.feed or a.unknown):
        raise SystemExit("queue needs --feed and/or --unknown")
    return {"queue": cmd_queue, "fetch": cmd_fetch, "batches": cmd_batches, "check": cmd_check}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
