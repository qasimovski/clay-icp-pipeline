"""Rail OTHER SOURCES copy (21 Nov 2026, user "Yes to both"): scope = OS people_feed.csv companies with 0-2 people in
../full/"Rail OS - email list.csv" + "... - LinkedIn only.csv" (first pass). Otherwise unchanged from the CE script below.

Rail Competitive Events: Deepline FREE people search (quickenrich_contact_search, "Cost: Free") for thin companies.
Copied from scrapers_submarine/_run_v2/other_sources/people/deepline/dl_search.py (19 Nov 2026).

User, 19 Nov 2026: "For the companies where we didn't get many people, I want you to run that through DeepLine as
well." Scope (Submarine precedent): every searched company (../people_feed.csv) with 0, 1 or 2 people in
../final/"Rail CE - email list.csv" + "... - LinkedIn only.csv".
Rail difference: "All other industries" rows (either side) also send bio_li include = the rail words of
../workflow/rail_kw.py (user: "very very narrow" for that label; Clay used profile_keywords for the same words).

Adapted from scrapers_climatech/_buyside/people/deepline/dl_quickenrich.py (ClimaTech OS / buy side precedent):
direct CLI calls (a Play would add compute charges), 25 company domains a call, title filter in chunks of 50
(Deepline matches title words loosely and takes at most 50), up to 5 pages of 100, a batch whose total is over 500
is split. Title sets = the Clay search's: Sell Side rows (main + also_sell) -> icp.yaml sell.titles; Buy Side rows ->
that buyer group's icp.yaml titles. No country filter here: post_filter.py applies the sheet's geography (and
Germany LinkedIn-only, sanctioned countries) to Deepline people exactly as to Clay's.

    python dl_search.py scope                        -> scope.csv (Domain, Company Name, Side, Classification, Group, People Found)
    python dl_search.py run [--shard i --shards n] [--limit N]
        -> qe_people[_i].jsonl (raw records + "group", "title_chunk"), qe_state[_i].json (done "group|domain")
Resumable: re-run the same command.
"""
import argparse, csv, hashlib, json, os, subprocess, sys, time
from collections import Counter

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'people', 'workflow'))
from rail_kw import RAIL  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PEOPLE = os.path.dirname(HERE)
ICP = r"C:\Users\qasim\terrapinn\clay-icp-pipeline\config\icps\rail\icp.yaml"
BATCH = 25


def rd(p):
    with open(p, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def title_sets():
    y = yaml.safe_load(open(ICP, encoding='utf-8'))
    sets = {'sell': y['sell']['titles']}
    for k, v in y['buy']['categories'].items():
        sets['buy: ' + k] = v.get('titles') or []

    def words(t):
        return t.lower().replace('-', ' ').split()

    def sub(a, b):
        a, b = words(a), words(b)
        return a != b and any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))
    out = {}
    for k, T in sets.items():
        T = list(dict.fromkeys(T))
        R = [t for t in T if not any(sub(o, t) for o in T)]   # "Network Engineer" is covered by "Network" ...
        out[k] = [R[i:i + 50] for i in range(0, len(R), 50)]
    return out


def scope():
    n = Counter()
    for f in ('Rail OS - email list.csv', 'Rail OS - LinkedIn only.csv'):
        for r in rd(os.path.join(PEOPLE, 'full', f)):
            n[r['Domain']] += 1
    rows, seen = [], set()
    for r in rd(os.path.join(PEOPLE, 'people_feed.csv')):
        if n[r['Domain']] > 2:
            continue
        g = 'sell' if r['Side'] == 'Sell Side' else 'buy: ' + r['Classification']
        if r['Classification'] == 'All other industries':
            g += ' | rail bio'          # same titles, plus the rail-word bio filter
        if (g, r['Domain']) in seen:
            continue
        seen.add((g, r['Domain']))
        rows.append({'Domain': r['Domain'], 'Company Name': r['Company Name'], 'Side': r['Side'],
                     'Classification': r['Classification'], 'Group': g, 'People Found': n[r['Domain']]})
    with open(os.path.join(HERE, 'scope.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len({r['Domain'] for r in rows}):,} companies, {len(rows):,} (group, company) searches:",
          dict(Counter(r['Group'] for r in rows)))


def call(inp, domains, page, tchunk, bio=None):
    q = {'company_url': {'include': domains}, 'title': {'include': tchunk}, 'per_page': 100, 'page': page}
    if bio:
        q['bio_li'] = {'include': bio}
    with open(inp, 'w', encoding='utf-8') as fh:
        json.dump(q, fh, ensure_ascii=False)
    for attempt in range(4):
        p = subprocess.run(f'deepline tools execute quickenrich_contact_search --input "@{inp}" --json --timeout 3m',
                           shell=True, capture_output=True, text=True, encoding='utf-8', errors='replace')
        try:
            raw = json.loads(p.stdout)['toolResponse']['rawV2']
            if raw.get('success') and isinstance(raw.get('data'), list):
                return raw['data'], raw.get('meta') or {}
        except Exception:
            pass
        print('  retry', attempt, (p.stdout or p.stderr)[-300:].replace('\n', ' '), flush=True)
        time.sleep(20 * (attempt + 1))
    raise SystemExit('quickenrich failed 4 times: stopping (state is saved; re-run the same command)')


def run(a):
    sets = title_sets()
    sfx = f'_{a.shard}' if a.shards > 1 else ''
    STATE, OUT, INP = (os.path.join(HERE, f) for f in (f'qe_state{sfx}.json', f'qe_people{sfx}.jsonl', f'qe_input{sfx}.json'))
    rows = [r for r in rd(os.path.join(HERE, 'scope.csv'))
            if int(hashlib.md5(r['Domain'].encode()).hexdigest(), 16) % a.shards == a.shard]
    state = json.load(open(STATE)) if os.path.exists(STATE) else {'done': [], 'capped': []}
    done = set(state['done'])
    queue = []
    for g in sorted({r['Group'] for r in rows}):
        doms = [r['Domain'] for r in rows if r['Group'] == g and f"{g}|{r['Domain']}" not in done]
        queue += [(g, doms[i:i + BATCH]) for i in range(0, len(doms), BATCH)]
    if a.limit:
        queue = queue[:a.limit]
    print(f'{len(rows):,} searches in this shard, {len(queue)} batches to run', flush=True)
    n = 0
    while queue:
        g, part = queue.pop(0)
        recs, split = [], False
        bio = RAIL if g.endswith(' | rail bio') else None
        for ti, tc in enumerate(sets[g.replace(' | rail bio', '')]):
            data, meta = call(INP, part, 1, tc, bio)
            total = int(meta.get('total') or len(data))
            if total > 500 and len(part) > 1:
                queue[:0] = [(g, part[:len(part) // 2]), (g, part[len(part) // 2:])]
                split = True
                break
            page = 1
            while meta.get('has_more') and page < 5:
                page += 1
                more, meta = call(INP, part, page, tc, bio)
                data += more
            if total > 500:
                state['capped'].append(f'{g}|{part[0]}|{ti}')
            recs += [dict(r, group=g, title_chunk=ti) for r in data]
        if split:
            continue
        with open(OUT, 'a', encoding='utf-8') as fh:
            for rec in recs:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        done.update(f'{g}|{d}' for d in part)
        state['done'] = sorted(done)
        json.dump(state, open(STATE + '.tmp', 'w'))
        os.replace(STATE + '.tmp', STATE)
        n += 1
        if n % 10 == 0:
            print(f'  {n} batches done, {len(queue)} left', flush=True)
    print('QE DONE', flush=True)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['scope', 'run'])
    ap.add_argument('--shard', type=int, default=0)
    ap.add_argument('--shards', type=int, default=1)
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    scope() if a.cmd == 'scope' else run(a)


if __name__ == '__main__':
    main()
