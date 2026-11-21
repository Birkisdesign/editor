#!/usr/bin/env python3
"""Výsledky FBS Olomouc z ceskyflorbal.cz -> cf.json (pro editor grafik).
Během zápasu běží ve smyčce a každou minutu ukládá změny (commit + push)."""
import json, re, html, os, sys, time, subprocess, urllib.request, datetime as dt

COMP = {'zeny': '8XW1', 'muzi': '8XM2'}
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
OUT = 'cf.json'

def get(path):
    url = 'https://www.ceskyflorbal.cz' + path
    try:
        from curl_cffi import requests as cr  # TLS otisk jako Chrome (Cloudflare jinak odmítá)
        r = cr.get(url, impersonate='chrome', timeout=30, headers={'Accept-Language': 'cs-CZ,cs;q=0.9'})
        if r.status_code != 200: raise RuntimeError(f'{r.status_code} {r.headers.get("cf-mitigated", "")} {r.headers.get("server", "")}')
        return r.text
    except ImportError: pass
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'cs-CZ,cs;q=0.9'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'replace')

dec = lambda s: re.sub(r'\s+', ' ', html.unescape(s or '')).strip()

def parse_list(code):
    items = []
    for ch in re.split(r'<div class="Match(?: [^"]*)?">', get('/competition/detail/matches/' + code))[1:]:
        names = [dec(x) for x in re.findall(r'<p class="Match-teamName">([^<]*)<', ch)]
        if len(names) < 2 or not any('Olomouc' in n for n in names): continue
        g = lambda rx: (lambda m: dec(m.group(1)) if m else '')(re.search(rx, ch))
        link = re.search(r'/match/detail/default/(\d+)">\s*([^<]*?)\s*<', ch)
        cm = re.search(r'Match-centerContent[\s\S]*?</div>', ch); center = cm.group(0) if cm else ''
        items.append({'r': g(r'Match-round">(\d+)'), 'd': g(r'<time class="Match-date"[^>]*>([^<]*)<'), 'h': names[0], 'a': names[1],
                      'id': link.group(1) if link else '',
                      'sc': dec(link.group(2)) if link and 'Match-score' in center else '',
                      'time': dec(link.group(2)) if link and 'Match-startTime' in center else '',
                      'st': g(r'Match-status">([^<]*)<')})
    return items

def parse_match(mid):
    t = get('/match/detail/default/' + mid)
    sc = re.search(r'MatchHeader-score">\s*([^<]*?)\s*<', t)
    bar = re.search(r'MatchHeader-infoBar">\s*<span>\s*([^<]*?)\s*</span>\s*(?:<span class="([^"]*)">([^<]*)<)?', t)
    return {'sc': dec(sc.group(1)) if sc else '', 'per': dec(bar.group(1)) if bar else '',
            'live': bool(bar and bar.group(2) == 'live'), 'end': bool(bar and bar.group(2) == 'end'), 'st': dec(bar.group(3)) if bar else ''}

def mdate(d, tm):
    m = re.search(r'(\d+)\.\s*(\d+)\.', d or '')
    if not m: return None
    day, mon = int(m.group(1)), int(m.group(2))
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=1)))
    season = now.year if now.month >= 8 else now.year - 1
    yr = season if mon >= 8 else season + 1
    hh, mm = (map(int, tm.split(':')) if re.match(r'^\d{1,2}:\d{2}$', tm or '') else (12, 0))
    return dt.datetime(yr, mon, day, hh, mm, tzinfo=now.tzinfo)

def snapshot(old):
    data = {'zeny': {'items': parse_list(COMP['zeny'])}, 'muzi': {'items': parse_list(COMP['muzi'])}, 'match': dict(old.get('match', {}))}
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=1)))
    live = False
    for sec in COMP:
        for it in data[sec]['items']:
            start = mdate(it['d'], it['time'] or (old.get('start', {}).get(it['id'], '')))
            if it['time']: data.setdefault('start', {})[it['id']] = it['time']
            played = 'odehr' in it['st']
            inplay = bool(it['sc']) and not played
            recent = start and dt.timedelta(minutes=-20) <= now - start <= dt.timedelta(hours=4)
            if it['id'] and (inplay or recent or (played and (it['id'] not in data['match'] or not data['match'][it['id']].get('end')))):
                try: data['match'][it['id']] = parse_match(it['id'])
                except Exception as e: print('detail', it['id'], e)
                if not data['match'].get(it['id'], {}).get('end') and (inplay or recent): live = True
    data['start'] = {**old.get('start', {}), **data.get('start', {})}
    return data, live

def soon(old):
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=1)))
    for sec in COMP:
        for it in old.get(sec, {}).get('items', []):
            start = mdate(it['d'], it['time'] or old.get('start', {}).get(it['id'], ''))
            if start and dt.timedelta(minutes=-20) <= now - start <= dt.timedelta(hours=4) and 'odehr' not in it['st']: return True
    return False

def save(data):
    body = {k: v for k, v in data.items()}
    old = open(OUT).read() if os.path.exists(OUT) else ''
    cmp = lambda s: json.loads(s) if s else {}
    o = cmp(old); o.pop('updated', None)
    if o == body: return False
    body['updated'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    open(OUT, 'w').write(json.dumps(body, ensure_ascii=False, separators=(',', ':')))
    return True

def push(msg):
    if not os.environ.get('GITHUB_ACTIONS'): return
    subprocess.run(['git', 'add', OUT], check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode == 0: return
    subprocess.run(['git', '-c', 'user.name=cf-bot', '-c', 'user.email=actions@users.noreply.github.com', 'commit', '-qm', msg], check=True)
    for _ in range(3):
        if subprocess.run(['git', 'push', '-q']).returncode == 0: return
        subprocess.run(['git', 'pull', '-q', '--rebase'])

if __name__ == '__main__':
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    deadline = time.time() + 4.5 * 3600
    while True:
        data, live = snapshot(old)
        if save(data): push('Výsledky z ceskyflorbal.cz'); print('uloženo', dt.datetime.now().isoformat(timespec='seconds'))
        old = data
        if not (live or soon(old)) or time.time() > deadline: break
        time.sleep(60)
