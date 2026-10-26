import json, sys, re, collections
sys.path.insert(0, '.')
from contrast import ratio

M = json.load(open('main.json')); B = json.load(open('branch.json'))
OLD = {'#0d6efd', '#0b5ed7'}; NEW = {'#0a58ca', '#0846a2'}; BLUE = OLD | NEW
SMALL_OLD = {'#198754', '#dc3545', '#64748b', '#757575'}
SMALL_NEW = {'#146c43', '#b02a37', '#475569', '#5c636a'}

def key(screen, t): return (screen, t['sel'], t['text'])
def idx(R):
    d = {}
    for s, items in R['screens'].items():
        for t in items:
            if not t.get('nontext'): d.setdefault(key(s, t), t)
    return d
mi, bi = idx(M), idx(B)

def short(sel):
    parts = sel.split('.')
    return parts[0] + ('.' + '.'.join(parts[1:3]) if len(parts) > 1 else '')

def section(title): print('\n## ' + title)

section('Blue text: grouped by (selector, background) -- main vs branch, min ratio')
g = collections.defaultdict(lambda: [99, 99, 0, set(), ''])
for k, t in bi.items():
    m = mi.get(k)
    if not m: continue
    if m['fg'] in OLD or t['fg'] in NEW:
        gk = (short(t['sel']), m['bg'], t['bg'], m['fg'], t['fg'])
        e = g[gk]; e[0] = min(e[0], m['ratio']); e[1] = min(e[1], t['ratio']); e[2] += 1; e[3].add(k[0]); e[4] = t['text']
for (sel, mbg, bbg, mfg, bfg), (mr, br, n, scr, txt) in sorted(g.items(), key=lambda x: x[1][1]):
    print(f'{br:5.2f} <- {mr:5.2f} | {mfg}->{bfg} on {mbg}{"" if mbg==bbg else "->"+bbg} | {sel} x{n} e.g. "{txt}" | {",".join(sorted(scr))[:60]}')

section('Text on a blue fill (white on primary/hover), main vs branch')
g2 = collections.defaultdict(lambda: [99, 99, 0, ''])
for k, t in bi.items():
    m = mi.get(k)
    if not m: continue
    if m['bg'] in OLD or t['bg'] in NEW:
        gk = (short(t['sel']), m['fg'], m['bg'], t['bg'], t['disabled'])
        e = g2[gk]; e[0] = min(e[0], m['ratio']); e[1] = min(e[1], t['ratio']); e[2] += 1; e[3] = t['text']
for (sel, fg, mbg, bbg, dis), (mr, br, n, txt) in sorted(g2.items(), key=lambda x: x[1][1]):
    print(f'{br:5.2f} <- {mr:5.2f} | {fg} on {mbg}->{bbg} | {sel} x{n} "{txt}" {"(disabled)" if dis else ""}')

section('Four small items, main vs branch')
for k, t in bi.items():
    m = mi.get(k)
    if m and (m['fg'] in SMALL_OLD or m['fgEff'] in SMALL_OLD) and (t['fg'] in SMALL_NEW or t['fg'] != m['fg']):
        print(f'{t["ratio"]:5.2f} <- {m["ratio"]:5.2f} | {m["fg"]}->{t["fg"]} on {t["bg"]} | {short(t["sel"])} "{t["text"]}" | {k[0]}')

section('Non-text blue (borders, outlines, fills, strokes) on branch')
seen = {}
for s, items in B['screens'].items():
    for n in items:
        if n.get('nontext'):
            kk = (short(n['sel']), n['prop'], n['color'], n['against'], n['againstParent'])
            seen.setdefault(kk, (n['ratio'], n['ratioParent'], s))
for (sel, prop, c, ag, agp), (r, rp, s) in sorted(seen.items(), key=lambda x: x[1][0]):
    print(f'{r:5.2f} vs own bg {ag} | {rp:5.2f} vs parent {agp} | {sel} {prop} {c} | {s}')

section('Every branch text under 4.5 (not disabled), with main value')
low = []
for k, t in bi.items():
    if t['ratio'] < 4.5 and not t['disabled']:
        m = mi.get(k); low.append((t['ratio'], m['ratio'] if m else None, t, k[0]))
agg = collections.defaultdict(lambda: [99, 99, 0, '', set()])
for r, mr, t, s in low:
    gk = (short(t['sel']), t['fg'], t['bg']); e = agg[gk]
    e[0] = min(e[0], r); e[1] = min(e[1], mr if mr else 99); e[2] += 1; e[3] = t['text']; e[4].add(s)
for (sel, fg, bg), (r, mr, n, txt, scr) in sorted(agg.items(), key=lambda x: x[1][0]):
    print(f'{r:5.2f} (main {mr:5.2f}) | {fg} on {bg} | {sel} x{n} "{txt}" | {",".join(sorted(scr))[:50]}')

def rgb2hex(c):
    m = re.match(r'rgba?\(([^)]+)\)', c); p = [float(x) for x in re.split(r'[ ,/]+', m.group(1)) if x]
    return '#' + ''.join(f'{round(v):02x}' for v in p[:3]), (p[3] if len(p) > 3 else 1)

section('Hover states, branch (fg/bg as computed while hovered)')
hs = {}
for h in B['hovers']:
    fg, _ = rgb2hex(h['color']); bg, a = rgb2hex(h['bg'])
    hs.setdefault((h['sel'].split('.')[0] + '.' + '.'.join(h['sel'].split('.')[1:3]), fg, bg if a > 0 else 'transparent', h['decoration']), h['text'])
for k, v in hs.items(): print(k, '"' + v + '"')
section('Hover states, main')
hs = {}
for h in M['hovers']:
    fg, _ = rgb2hex(h['color']); bg, a = rgb2hex(h['bg'])
    hs.setdefault((h['sel'].split('.')[0] + '.' + '.'.join(h['sel'].split('.')[1:3]), fg, bg if a > 0 else 'transparent', h['decoration']), h['text'])
for k, v in hs.items(): print(k, '"' + v + '"')

section('Focus, branch: distinct focus styles reached by Tab')
fs = {}
for f in B['focus']:
    kk = (f['sel'].split('.')[0] + '.' + '.'.join(f['sel'].split('.')[1:3]), f['outlineStyle'], f['outlineWidth'], f['outlineColor'], f['borderColor'], f['boxShadow'][:60], f['focusVisible'])
    fs.setdefault(kk, (f['screen'], f['text']))
for k, v in fs.items(): print(k, v)
