"""Rail OTHER SOURCES copy (21 Nov 2026): OS file names, OS run folders, people already in the Rail CE review lists
excluded (never delivered twice), and the 9 portal bodies (qe_portal.jsonl, dl_portal.py) added as their own rows.

Rail Competitive Events (19 Nov 2026, copied from scrapers_submarine/_run_v2/other_sources/people/deepline): Deepline people (qe_people*.jsonl from dl_search.py) -> post filter -> merged with Clay's list.
Adapted from scrapers_climatech/_buyside/people/deepline/dl_build_people.py (ClimaTech OS / buy-side precedent).

    python dl_build.py translate   # distinct non-English titles -> title_en.json (free Google endpoint, titles only)
    python dl_build.py build       # -> dl_people.csv -> post_filter -> "Rail CE Deepline - *.csv"
    python dl_build.py merge       # Clay final lists + Deepline -> ../final/"Rail CE - email list (Clay + Deepline).csv"
                                   #   and "... - LinkedIn only (Clay + Deepline).csv", column "Person Source"

Kept: a real title + full name + LinkedIn profile, at the scope company (Deepline's company_url match also returns
lookalike domains), one row per profile, not already found by Clay (final lists, incl. dropped and duplicates). Each person goes on
the company's search row for that title set: 'sell' -> the Sell Side row (the also_sell row for carriers / cable
owners), 'buy: <group>' -> the Buy Side row; so post_filter judges them against the same side's rules as Clay's.
Country: Deepline ISO2 -> the country name Clay uses (iso map from this run's Clay results), else pycountry.
Titles: non-English ones translated first (original kept in "Job Title (Original)"). As in the ClimaTech runs a
Deepline title still unreadable after translation is DROPPED (its title match is loose: words anywhere).
"""
import collections, csv, glob, json, os, re, subprocess, sys, time

import pycountry
import requests

csv.field_size_limit(10**9)
HERE = os.path.dirname(os.path.abspath(__file__))
PEOPLE = os.path.dirname(HERE)
FINAL = os.path.join(PEOPLE, 'final')
SKILL = r'C:\Users\qasim\terrapinn\clay-icp-pipeline\.claude\skills\event-icp-clay-workflow\scripts'
CFG = r'C:\Users\qasim\terrapinn\clay-icp-pipeline\config\icps\rail'
sys.path.insert(0, SKILL)
import collect as C  # noqa: E402

PRE = os.path.join(HERE, 'Rail OS Deepline')
CE_FINAL = os.path.join(PEOPLE, '..', '..', 'people', 'final')
CACHE = os.path.join(HERE, 'title_en.json')
URL = 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=en&dt=t'
ENGLISH_CC = {'GB', 'UK', 'US', 'AU', 'NZ', 'IE', 'CA', 'SG', 'IN', 'ZA', 'NG', 'KE', 'GH', 'PH', 'MY'}


def na(v):
    v = (v or '').strip() if isinstance(v, str) else ''
    return '' if v in ('N/A', 'n/a') else v


def rd(p):
    with open(p, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def wr(p, rows, cols):
    with open(p, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def records():
    for f in sorted(glob.glob(os.path.join(HERE, 'qe_people*.jsonl'))):
        for line in open(f, encoding='utf-8'):
            yield json.loads(line)


def needs_translation(title, cc):
    return any(ord(c) > 127 for c in title) or cc not in ENGLISH_CC


def tr(batch):
    for attempt in range(5):
        try:
            r = requests.post(URL, data={'q': '\n'.join(batch)}, timeout=30)
            if r.status_code == 200:
                lines = ''.join(seg[0] or '' for seg in r.json()[0]).split('\n')
                if len(lines) == len(batch):
                    return dict(zip(batch, (x.strip() for x in lines)))
                break
            time.sleep(30 * (attempt + 1))
        except Exception:
            time.sleep(10)
    if len(batch) == 1:
        return {}
    h = len(batch) // 2
    return {**tr(batch[:h]), **tr(batch[h:])}


def cmd_translate():
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    todo = sorted({t for x in records() for t in [' '.join(na(x.get('title')).split())]
                   if t and needs_translation(t, na(x.get('country_code')).upper()) and t not in cache})
    print(len(todo), 'titles to translate', flush=True)
    batches, cur, size = [], [], 0
    for t in todo:
        if cur and (size + len(t) + 1 > 3000 or len(cur) >= 80):
            batches.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t) + 1
    if cur:
        batches.append(cur)
    for i, b in enumerate(batches, 1):
        cache.update(tr(b))
        time.sleep(0.8)
        if i % 20 == 0 or i == len(batches):
            json.dump(cache, open(CACHE + '.tmp', 'w', encoding='utf-8'), ensure_ascii=False)
            os.replace(CACHE + '.tmp', CACHE)
            print(i, '/', len(batches), len(cache), flush=True)
    print('TRANSLATED', len(cache), flush=True)


# 9 portal bodies (21 Nov 2026): Deepline query B = portal domain + department keywords. Kept only when the employer
# is that government / ministry; Quebec's people are the Ministere des Transports du Quebec (not Mobilite Infra Quebec),
# so they are labelled as the Ministere and flagged for the reviewer in Title Check.
PORTAL_EMPLOYER = {
    'act.gov.au': re.compile(r'ACT Government|Transport Canberra', re.I),
    'ap.gov.in': re.compile(r'Andhra Pradesh', re.I),
    'gouv.qc.ca': re.compile(r'Minist.{1,4}re des Transports', re.I),
    'guanajuato.gob.mx': re.compile(r'Guanajuato', re.I),
    'sarawak.gov.my': re.compile(r'Sarawak', re.I), 'gouv.cd': re.compile(r'Transport', re.I),
    'beijing.gov.cn': re.compile(r'Beijing', re.I), 'sh.gov.cn': re.compile(r'Shanghai', re.I),
    'huangshi.gov.cn': re.compile(r'Huangshi', re.I),
}
PORTAL_NAME = {'gouv.qc.ca': 'Ministere des Transports et de la Mobilite durable du Quebec (listed: Mobilite Infra Quebec)'}


def portal_records():
    p = os.path.join(HERE, 'qe_portal.jsonl')
    return [json.loads(l) for l in open(p, encoding='utf-8')] if os.path.exists(p) else []


def portal_rows():
    sys.path.insert(0, os.path.join(PEOPLE, '..', '..', 'people'))
    from build_people_feed import rid
    lab = {r['Key']: r for r in rd(os.path.join(PEOPLE, '..', 'classify', 'classified_os.csv'))}
    out = {}
    feed = {r['company_id']: r for r in rd(os.path.join(PEOPLE, '..', 'classify', 'feed_portal.csv'))}
    import glob as G
    res = {}
    for f in G.glob(os.path.join(PEOPLE, '..', 'classify', 'runs_portal', 'results', '*.jsonl')):
        for l in open(f, encoding='utf-8'):
            d = json.loads(l)
            so = (d.get('result') or {}).get('structuredOutputs') or {}
            res[d['id']] = (so.get('Side'), so.get('Classification'))
    for r in feed.values():
        side, cl = res[r['Key']]
        dom = r['Portal Domain']
        out[dom] = {'Row ID': rid(dom, side, cl), 'Company Name': PORTAL_NAME.get(dom, r['Company Name']),
                    'Domain': dom, 'Side': side, 'Classification': cl, 'Search': 'portal + department keywords (Deepline)',
                    'Countries Override': '', 'Events': r['Primary Offerings'].replace('Listed in: ', ''), 'Editions': ''}
    return out


def iso_map():
    people = [p for d in ('runs/people', 'runs/sen_b1')
              for rec in C.load_results(os.path.join(PEOPLE, d, 'results')).values() for p in C.people_of(rec)[0]]
    return C.iso_names(people)


def country_name(cc, iso):
    if not cc:
        return ''
    if cc in ('UK',):
        cc = 'GB'
    if cc in iso:
        return iso[cc]
    c = pycountry.countries.get(alpha_2=cc)
    return (getattr(c, 'common_name', None) or c.name) if c else cc


def cmd_build():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    feed = rd(os.path.join(PEOPLE, 'people_feed.csv'))
    comp = {c['Domain']: c for c in rd(os.path.join(PEOPLE, 'companies.csv'))}
    rowfor = {}
    for r in feed:
        g = 'sell' if r['Side'] == 'Sell Side' else 'buy: ' + r['Classification']
        if r['Classification'] == 'All other industries':
            g += ' | rail bio'
        rowfor[(r['Domain'], g)] = r
    have = {C.slug(r['LinkedIn URL']) for f in ('Rail OS - email list.csv', 'Rail OS - LinkedIn only.csv',
                                                'Rail OS - email list - cross-side duplicates.csv',
                                                'Rail OS - LinkedIn only - cross-side duplicates.csv',
                                                'Rail OS - dropped by post filter.csv') for r in rd(os.path.join(FINAL, f))}
    # people already in the Rail CE review lists: never delivered twice (user answer 1, 21 Nov 2026)
    ce = {C.slug(r['LinkedIn URL']) for f in ('Rail CE - review.csv', 'Rail CE - review - LinkedIn only.csv')
          for r in rd(os.path.join(CE_FINAL, f)) if r.get('LinkedIn URL')}
    en = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    iso = iso_map()
    cols = ['Row ID', 'Company Name', 'Domain', 'Side', 'Classification', 'Search', 'Countries Override', 'Event', 'Editions',
            'Description'] + C.PERSON_COLS + ['Capped', 'Outcome', 'Job Title (Original)', 'Person Source']
    seen, rows, st = set(), [], collections.Counter()
    portal = portal_rows()
    for x in list(records()) + portal_records():
        st['records'] += 1
        dom = (x.get('company_url') or '').lower()
        r = portal.get(x['body']) if x.get('body') else rowfor.get((dom, x.get('group')))
        if x.get('body') and r and not PORTAL_EMPLOYER[x['body']].search(x.get('company_name') or ''):
            st['portal: other employer'] += 1
            continue
        if x.get('body') and r:
            dom = r['Domain']
        if not r:
            st['lookalike domain returned'] += 1
            continue
        first, last, title, li = na(x.get('first_name')), na(x.get('last_name')), na(x.get('title')), na(x.get('employee_linkedin'))
        s = C.slug(li)
        if not title or not (first and last):
            st['no title or name'] += 1
            continue
        if not s:
            st['no LinkedIn profile'] += 1
            continue
        if (s, x['group']) in seen:
            st['duplicate profile'] += 1
            continue
        seen.add((s, x['group']))
        if s in have:
            st['already found by Clay'] += 1
            continue
        if s in ce:
            st['already in another list'] += 1
            continue
        c = comp.get(dom, {})
        title = ' '.join(title.split())
        t_en = en.get(title, '')
        row = {'Row ID': r['Row ID'], 'Company Name': r['Company Name'], 'Domain': dom, 'Side': r['Side'],
               'Classification': r['Classification'], 'Search': r['Search'], 'Countries Override': r['Countries Override'],
               'Event': c.get('Event', r.get('Events', '')), 'Editions': r.get('Editions', ''), 'Description': c.get('Description', ''),
               'Person Name': f'{first} {last}', 'First Name': first, 'Last Name': last, 'Job Title': title,
               'LinkedIn URL': li, 'Mailing City': na(x.get('city')), 'Mailing State Province': '',
               'Mailing Country': country_name(na(x.get('country_code')).upper(), iso), 'Capped': '',
               'Outcome': 'found (Deepline)', 'Person Source': 'Deepline'}
        if t_en and t_en.lower() != title.lower():
            row.update({'Job Title': t_en, 'Job Title (Original)': title})
            st['title translated'] += 1
        if x.get('body'):
            row['Event'] = r['Events']
            row['Title Check'] = ('Quebec: employer is the Ministere des Transports, listed body was Mobilite Infra Quebec; '
                                  if x['body'] == 'gouv.qc.ca' else '') + 'found on the government portal by department keywords'
        rows.append(row)
    wr(os.path.join(HERE, 'dl_people.csv'), rows, cols + ['Title Check'])
    print(dict(st))
    print(f"{len(rows):,} people at {len({r['Domain'] for r in rows}):,} companies -> post filter", flush=True)
    subprocess.run([sys.executable, os.path.join(SKILL, 'post_filter.py'), '--people', os.path.join(HERE, 'dl_people.csv'),
                    '--icp', os.path.join(CFG, 'icp.yaml'), '--rules', os.path.join(CFG, 'post_filter.yaml'),
                    '--out-prefix', PRE], check=True)
    data = rd(PRE + ' - email list.csv')
    keep = [r for r in data if not (r.get('Title Check') or '').startswith('non-English')]
    moved = [{**r, 'Dropped Because': 'title not readable as English after translation (Deepline match is loose)'}
             for r in data if (r.get('Title Check') or '').startswith('non-English')]
    wr(PRE + ' - email list.csv', keep, list(data[0].keys()))
    d = rd(PRE + ' - dropped by post filter.csv')
    wr(PRE + ' - dropped by post filter.csv', d + moved, list((d or data)[0].keys()) + ['Dropped Because'])
    print(f'Deepline email list {len(keep):,} | {len(moved):,} unreadable titles moved to dropped')
    # 19 Nov 2026: Deepline's bio_li filter matches the COMPANY's LinkedIn bio, not the person's (LMW, Polyvantis
    # returned every person with or without it), so it can't make "All other industries" narrow the way Clay's
    # per-person profile_keywords did. User: AOI must be "very very narrow" -> a Deepline AOI person is kept only when
    # their own job title carries a rail word; the rest go to dropped (kept on file).
    for kind in ('email list', 'LinkedIn only'):
        path = PRE + f' - {kind}.csv'
        data = rd(path)
        if not data:
            continue
        ok = [r for r in data if r['Classification'] != 'All other industries'
              or RAIL_RE.search(r['Job Title'] + ' ' + r.get('Job Title (Original)', ''))]
        out = [{**r, 'Dropped Because': 'All other industries: no rail word in the title (Deepline bio filter is company-level)'}
               for r in data if r not in ok]
        wr(path, ok, list(data[0].keys()))
        d = rd(PRE + ' - dropped by post filter.csv')
        cols = list(d[0].keys()) if d else list(data[0].keys()) + ['Dropped Because']
        wr(PRE + ' - dropped by post filter.csv', d + out, cols + [c for c in data[0] if c not in cols])
        print(f'{kind}: {len(out):,} All other industries people without a rail title moved to dropped; {len(ok):,} kept')


# Cap (18 Nov 2026): Deepline returned 100s at a few big companies Clay had found 0-2 at (Telia 1,233, T-Mobile 1,130,
# Power Grid India 719 ...). Clay never gives more than 20 per search row (10 + 10 heads / managers), so Deepline is
# capped to 20 per search row INCLUDING the Clay people already there, most senior first (ClimaTech dl_cap.py ranking),
# Bookers before Stand staff. Over the cap -> "... (Deepline over the cap).csv", not deleted.
CAP = 20
RAIL_RE = re.compile(r'\b(rail\w*|railroad\w*|rolling stock|locomotives?|trams?|tramways?|signall?ing|rail transit|'
                     r'mass transit|rapid transit|subway|trains?|bogies?|catenary|traction|metro rail|ferrovi\w*|bahn\w*)\b', re.I)
R1 = re.compile(r'\b(owner|founder|co-founder|ceo|coo|cfo|cto|cco|cmo|chief|president|chairman|managing director|md|'
                r'general manager|gm|director general|managing partner)\b', re.I)
R2 = re.compile(r'\b(vp|svp|evp|vice president|director|head|partner)\b', re.I)
R3 = re.compile(r'\b(manager|lead|leader|responsible|supervisor|principal)\b', re.I)


def rank(r):
    t = r['Job Title']
    lvl = 1 if R1.search(t) and not re.search(r'\b(assistant|deputy|executive assistant)\b', t, re.I) else \
        2 if R2.search(t) else 3 if R3.search(t) else 4
    return (lvl, 0 if r.get('Persona') == 'Booker' else 1)


def cmd_merge():
    for kind in ('email list', 'LinkedIn only'):
        clay = rd(os.path.join(FINAL, f'Rail OS - {kind}.csv'))
        dl = rd(PRE + f' - {kind}.csv') if os.path.exists(PRE + f' - {kind}.csv') else []
        for r in clay:
            r['Person Source'] = 'Clay'
        cols = list(clay[0].keys()) + [c for c in (dl[0].keys() if dl else []) if c not in clay[0]]
        if 'Person Source' not in cols:
            cols.append('Person Source')
        seen, out, dup = set(), [], 0
        for r in clay + dl:                       # one row per (profile, company search row); Clay first
            k = (C.slug(r['LinkedIn URL']), r['Row ID'])
            if k in seen:
                dup += 1
                continue
            seen.add(k)
            out.append(r)
        n = collections.Counter(r['Row ID'] for r in out if r['Person Source'] == 'Clay')
        cand = collections.defaultdict(list)
        for i, r in enumerate(out):
            if r['Person Source'] == 'Deepline':
                cand[r['Row ID']].append((rank(r), i))
        over_idx = set()
        for rid, items in cand.items():
            room = max(0, CAP - n[rid])
            over_idx |= {i for _, i in sorted(items)[room:]}
        keep = [r for i, r in enumerate(out) if i not in over_idx]
        over = [r for i, r in enumerate(out) if i in over_idx]
        wr(os.path.join(FINAL, f'Rail OS - {kind} (Clay + Deepline).csv'), keep, cols)
        wr(os.path.join(FINAL, f'Rail OS - {kind} (Deepline over the cap).csv'), over, cols)
        src = collections.Counter(r['Person Source'] for r in keep)
        print(f"{kind}: Clay {len(clay):,} + Deepline {len(dl):,} - {dup} duplicates - {len(over):,} over the cap of {CAP}"
              f" = {len(keep):,} people ({dict(src)}) at {len({r['Domain'] for r in keep}):,} companies")


if __name__ == '__main__':
    {'translate': cmd_translate, 'build': cmd_build, 'merge': cmd_merge}[sys.argv[1]]()
