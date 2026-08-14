#!/usr/bin/env python3
"""
MeApp Daily Standup — render the standup page.

    python3 render_standup.py standup.json standup.html

Keeps the LLM step dumb: the daily routine only *gathers* and dumps raw-ish JSON;
every derivation (hours, sizing rule, Jira-vs-GitHub drift, grouping, HTML) happens
here, deterministically, so the page cannot drift in format from one day to the next.

INPUT CONTRACT
{
  "window":    "Thu 13 Aug 08:30 - Fri 14 Aug 08:30 IST",   # the worked-time window
  "generated": "Fri 14 Aug 2026, 08:34 IST",
  "sprints":   [{"id": 6141, "name": "MOB DEV Sprint 33"}],
  "branch_keys": ["MOB-1530"],    # MOB keys with a branch, any of the three repos
  "merged_keys": ["MOB-2225"],    # MOB keys with a MERGED PR
  "openpr_keys": ["MOB-1878"],    # MOB keys with an OPEN PR
  "people": [{
    "name": "Kaviya", "role": "Android", "tester": false,
    "worked": [{"key","summary","seconds","total_seconds","oe_seconds","sp"}],
    "issues": [{"key","summary","status","oe_seconds","sp"}],
    "prs":    [{"repo","number","title","url","branch","conflict",
                "review": "APPROVED|CHANGES_REQUESTED|REVIEW_REQUIRED|null",
                "keys": ["MOB-2174"]}]
  }]
}
Every field is required except oe_seconds / sp / review, which may be null.
"""
import json, os, sys, html

STATUS_ORDER = ['In Progress', 'In Review', 'In QA', 'Blocked', 'Triage', 'To Do', 'Backlog']
OPEN_BY_DEFAULT = {'In Progress', 'In Review', 'Blocked', 'Triage'}
PR_ORDER = [('conflict', 'Conflict'), ('changes-req', 'Changes requested'),
            ('needs-review', 'Needs review'), ('approved', 'Approved')]
PR_TAG = {'conflict': 't-crit', 'changes-req': 't-warn',
          'needs-review': 't-gold', 'approved': 't-ok'}
BROWSE = 'https://greatergoods.atlassian.net/browse/'
BOARD = 'https://greatergoods.atlassian.net/jira/software/c/projects/MOB/boards/1088'
e = html.escape


def hrs(v):
    """Hours as the team writes them: 4h, 1h 30m, 20m, em-dash when unset."""
    if v is None:
        return '—'
    total = round(v * 60)
    h, m = divmod(total, 60)
    if h and m:
        return f'{h}h {m}m'
    if h:
        return f'{h}h'
    return f'{m}m' if m else '0h'


def sec_h(v):
    return (v / 3600) if v else None


def prstate(p):
    if p.get('conflict'):
        return 'conflict'
    r = p.get('review') or ''
    return 'changes-req' if r == 'CHANGES_REQUESTED' else 'approved' if r == 'APPROVED' else 'needs-review'


def derive(D):
    """Compute hours, the sizing rule, and Jira-vs-GitHub drift onto the raw input."""
    branch = set(D.get('branch_keys') or [])
    merged = set(D.get('merged_keys') or [])
    openpr = set(D.get('openpr_keys') or [])
    for p in D['people']:
        for w in p['worked']:
            w['hours'] = sec_h(w['seconds']) or 0
            w['total'] = sec_h(w.get('total_seconds'))
            oe, sp = sec_h(w.get('oe_seconds')), w.get('sp')
            # Sizing rule: Original Estimate, else Story Points x 2h, else unset.
            w['oe'] = oe if oe else (sp * 2 if sp else None)
            w['oe_derived'] = not oe and bool(sp)
        p['worked'].sort(key=lambda x: int(x['key'].split('-')[1]))
        p['logged'] = round(sum(w['hours'] for w in p['worked']), 2)

        for pr in p['prs']:
            pr['state'] = prstate(pr)
        for i in p['issues']:
            k, st = i['key'], i['status']
            i['oe'], i['sp'] = sec_h(i.get('oe_seconds')), i.get('sp')
            # Precedence merged > open PR > branch; flag only when work is AHEAD of status.
            if k in merged and st not in ('In QA', 'Done'):
                i['mismatch'] = 'PR merged, not In QA/Done'
            elif k in openpr and st in ('To Do', 'In Progress', 'Blocked'):
                i['mismatch'] = 'PR raised, not In Review'
            elif k in branch and st in ('To Do', 'Blocked'):
                i['mismatch'] = 'branch exists, not In Progress'
            else:
                i['mismatch'] = None
            i['unsized'] = st in ('To Do', 'In Progress', 'In QA') and not i['oe'] and not i['sp']
            i['prs'] = [q for q in p['prs'] if k in (q.get('keys') or [])]
        p['issues'].sort(key=lambda x: int(x['key'].split('-')[1]))
    return D


def render(D):
    people = D['people']
    T = {
        'logged': sum(p['logged'] for p in people),
        'issues': sum(len(p['issues']) for p in people),
        'mismatch': sum(1 for p in people for i in p['issues'] if i['mismatch']),
        'unsized': sum(1 for p in people for i in p['issues'] if i['unsized']),
        'prs': sum(len(p['prs']) for p in people),
        'conflict': sum(1 for p in people for q in p['prs'] if q['state'] == 'conflict'),
        'worked': sum(len(p['worked']) for p in people),
    }
    out = []
    w = out.append

    w(f'''<title>MeApp Daily Standup</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg:#F6F7F9; --surface:#FFFFFF; --surface-2:#FBFCFD; --raise:#F1F4F8;
  --ink:#0F172A; --ink-2:#3D4759; --muted:#6B7689;
  --line:#E2E6EE; --line-strong:#CBD2DF;
  --link:#1D4ED8; --accent:#14304F;
  --ok:#157F4B;   --ok-bg:#E6F4EC;   --ok-line:#B7DFC8;
  --warn:#B45309; --warn-bg:#FBF0E2; --warn-line:#EDCFA4;
  --gold:#8A6A00; --gold-bg:#FAF3DC; --gold-line:#E4D19A;
  --crit:#C0322B; --crit-bg:#FBECEA; --crit-line:#F0C0BA;
  --shadow:0 1px 2px rgba(15,23,42,.05), 0 1px 3px rgba(15,23,42,.04);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0C1016; --surface:#141922; --surface-2:#171D27; --raise:#1D242F;
    --ink:#E8ECF4; --ink-2:#B4BDCC; --muted:#8B96A8;
    --line:#232B37; --line-strong:#394352;
    --link:#89ADFF; --accent:#C9D6E8;
    --ok:#5CD196;   --ok-bg:#10241B;   --ok-line:#1F5138;
    --warn:#F0B357; --warn-bg:#271B0E; --warn-line:#5A421F;
    --gold:#DDC062; --gold-bg:#231E0D; --gold-line:#4E441E;
    --crit:#FF8B84; --crit-bg:#2A1414; --crit-line:#5E2B27;
    --shadow:0 1px 2px rgba(0,0,0,.4);
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0C1016; --surface:#141922; --surface-2:#171D27; --raise:#1D242F;
  --ink:#E8ECF4; --ink-2:#B4BDCC; --muted:#8B96A8;
  --line:#232B37; --line-strong:#394352;
  --link:#89ADFF; --accent:#C9D6E8;
  --ok:#5CD196;   --ok-bg:#10241B;   --ok-line:#1F5138;
  --warn:#F0B357; --warn-bg:#271B0E; --warn-line:#5A421F;
  --gold:#DDC062; --gold-bg:#231E0D; --gold-line:#4E441E;
  --crit:#FF8B84; --crit-bg:#2A1414; --crit-line:#5E2B27;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}}

*,*::before,*::after {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,"Helvetica Neue",sans-serif;
  font-size:14px; line-height:1.5; -webkit-font-smoothing:antialiased;
}}
.mono {{ font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Roboto Mono",monospace; }}
.num {{ font-variant-numeric:tabular-nums; }}
a {{ color:var(--link); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
a:focus-visible, button:focus-visible, input:focus-visible, summary:focus-visible {{
  outline:2px solid var(--link); outline-offset:2px; border-radius:3px;
}}
.wrap {{ max-width:1180px; margin:0 auto; padding:0 20px; }}

header {{ background:var(--surface); border-bottom:1px solid var(--line); }}
.head {{ padding:26px 0 20px; display:flex; flex-direction:column; gap:14px; }}
.eyebrow {{
  font-size:11px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--muted); font-weight:600; display:flex; flex-wrap:wrap; gap:8px; align-items:center;
}}
.eyebrow .dot {{ color:var(--line-strong); }}
h1 {{ margin:0; font-size:26px; font-weight:680; letter-spacing:-.021em; text-wrap:balance; }}
.sub {{ color:var(--ink-2); font-size:13.5px; margin:0; }}
.sub b {{ color:var(--ink); font-weight:620; }}

.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(132px,1fr)); gap:10px; }}
.tile {{
  background:var(--surface-2); border:1px solid var(--line); border-radius:9px;
  padding:11px 13px; display:flex; flex-direction:column; gap:3px;
}}
.tile .k {{ font-size:10.5px; letter-spacing:.075em; text-transform:uppercase; color:var(--muted); font-weight:640; }}
.tile .v {{ font-size:23px; font-weight:660; letter-spacing:-.02em; line-height:1.15; }}
.tile.crit .v {{ color:var(--crit); }} .tile.warn .v {{ color:var(--warn); }}

.controls {{
  position:sticky; top:0; z-index:20; background:var(--surface);
  border-bottom:1px solid var(--line); box-shadow:var(--shadow);
}}
.cbar {{ padding:11px 0; display:flex; flex-direction:column; gap:9px; }}
.crow {{ display:flex; flex-wrap:wrap; gap:7px; align-items:center; }}
.clabel {{
  font-size:10.5px; letter-spacing:.075em; text-transform:uppercase;
  color:var(--muted); font-weight:640; min-width:52px;
}}
.chip {{
  font:inherit; font-size:12.5px; font-weight:540;
  background:var(--surface-2); color:var(--ink-2);
  border:1px solid var(--line); border-radius:999px;
  padding:4px 11px; cursor:pointer; display:inline-flex; align-items:center; gap:6px;
  transition:background .12s, border-color .12s, color .12s;
}}
.chip:hover {{ border-color:var(--line-strong); }}
.chip .c {{ font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; }}
.chip[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:var(--surface); }}
.chip[aria-pressed="true"] .c {{ color:inherit; opacity:.72; }}
.chip.f-crit[aria-pressed="true"] {{ background:var(--crit); border-color:var(--crit); color:#fff; }}
.chip.f-warn[aria-pressed="true"] {{ background:var(--warn); border-color:var(--warn); color:#fff; }}
.chip.f-ok[aria-pressed="true"]   {{ background:var(--ok);   border-color:var(--ok);   color:#fff; }}
input[type="search"] {{
  font:inherit; font-size:13px; color:var(--ink); background:var(--surface-2);
  border:1px solid var(--line); border-radius:7px; padding:6px 11px;
  min-width:230px; flex:1 1 230px; max-width:340px;
}}
input[type="search"]::placeholder {{ color:var(--muted); }}
.reset {{ font:inherit; font-size:12.5px; background:none; border:none; color:var(--link); cursor:pointer; padding:4px 6px; }}
.hits {{ font-size:12px; color:var(--muted); margin-left:auto; font-variant-numeric:tabular-nums; }}

main {{ padding:22px 0 60px; display:flex; flex-direction:column; gap:16px; }}
.person {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); overflow:hidden; }}
.phead {{
  padding:14px 16px; display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center;
  border-bottom:1px solid var(--line); background:var(--surface-2);
}}
.pname {{ font-size:16px; font-weight:660; letter-spacing:-.012em; }}
.role {{
  font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; font-weight:660;
  color:var(--muted); border:1px solid var(--line-strong); border-radius:5px; padding:2px 6px;
}}
.pstats {{ display:flex; flex-wrap:wrap; gap:6px; margin-left:auto; }}
.stat {{
  font-size:12px; color:var(--ink-2); background:var(--raise); border:1px solid var(--line);
  border-radius:6px; padding:3px 8px; font-variant-numeric:tabular-nums;
}}
.stat b {{ font-weight:660; color:var(--ink); }}
.stat.crit {{ color:var(--crit); background:var(--crit-bg); border-color:var(--crit-line); }}
.stat.crit b {{ color:var(--crit); }}
.stat.warn {{ color:var(--warn); background:var(--warn-bg); border-color:var(--warn-line); }}
.stat.warn b {{ color:var(--warn); }}

.block {{ border-top:1px solid var(--line); }}
.person > .block:first-of-type {{ border-top:none; }}
.btitle {{
  font-size:10.5px; letter-spacing:.09em; text-transform:uppercase; font-weight:660;
  color:var(--muted); padding:12px 16px 7px;
}}

.tscroll {{ overflow-x:auto; padding:0 16px 14px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th {{
  text-align:left; font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--muted); font-weight:640; padding:5px 10px 5px 0;
  border-bottom:1px solid var(--line); white-space:nowrap;
}}
td {{ padding:6px 10px 6px 0; border-bottom:1px solid var(--line); vertical-align:top; }}
tr:last-child td {{ border-bottom:none; }}
th.r, td.r {{ text-align:right; padding:5px 0 5px 18px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
table td.mono {{ white-space:nowrap; padding-right:14px; }}
td.sum {{ color:var(--ink-2); width:100%; }}
tfoot td {{ font-weight:660; border-top:1px solid var(--line-strong); border-bottom:none; }}
.derived {{ color:var(--muted); font-size:11px; }}

details.grp {{ border-top:1px dashed var(--line); }}
details.grp:first-of-type {{ border-top:none; }}
summary.gsum {{
  cursor:pointer; padding:8px 16px; display:flex; align-items:center; gap:9px;
  font-size:12.5px; font-weight:600; color:var(--ink-2); list-style:none;
}}
summary.gsum::-webkit-details-marker {{ display:none; }}
summary.gsum::before {{
  content:"\\203A"; font-size:15px; line-height:1; color:var(--muted);
  transition:transform .15s; display:inline-block; width:9px;
}}
details[open] > summary.gsum::before {{ transform:rotate(90deg); }}
.gcount {{ color:var(--muted); font-weight:520; font-variant-numeric:tabular-nums; }}
.rows {{ padding:0 16px 10px; display:flex; flex-direction:column; }}
.row {{ display:flex; flex-wrap:wrap; gap:5px 9px; align-items:baseline; padding:5px 0; border-top:1px solid var(--line); }}
.rows > .row:first-child {{ border-top:none; }}
.row .key {{ font-size:12.5px; font-weight:600; white-space:nowrap; }}
.row .txt {{ color:var(--ink-2); flex:1 1 300px; min-width:0; font-size:12.5px; }}
.row .meta {{ display:flex; flex-wrap:wrap; gap:5px; align-items:center; margin-left:auto; }}

.tag {{ font-size:10.5px; font-weight:620; letter-spacing:.02em; border-radius:5px; padding:1.5px 6px; white-space:nowrap; border:1px solid transparent; }}
.t-crit {{ color:var(--crit); background:var(--crit-bg); border-color:var(--crit-line); }}
.t-warn {{ color:var(--warn); background:var(--warn-bg); border-color:var(--warn-line); }}
.t-gold {{ color:var(--gold); background:var(--gold-bg); border-color:var(--gold-line); }}
.t-ok   {{ color:var(--ok);   background:var(--ok-bg);   border-color:var(--ok-line); }}
.t-mute {{ color:var(--muted); background:var(--raise); border-color:var(--line); }}
.prlink {{ font-size:11.5px; }}

.empty {{ padding:2px 16px 14px; color:var(--muted); font-size:12.5px; font-style:italic; }}
.nohits {{ display:none; padding:40px 16px; text-align:center; color:var(--muted); font-size:13.5px; }}
.person.hide, .block.hide, .row.hide, .row-w.hide, details.grp.hide {{ display:none; }}

footer {{ border-top:1px solid var(--line); padding:18px 0 40px; color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap; gap:6px 14px; }}
@media (max-width:640px) {{
  .pstats {{ margin-left:0; width:100%; }}
  .row .meta {{ margin-left:0; }}
  h1 {{ font-size:22px; }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>

<header><div class="wrap head">
  <div class="eyebrow">
    <span>{" <span class='dot'>&middot;</span> ".join(e(s['name']) for s in D.get('sprints', []))}</span>
    <span class="dot">&middot;</span><span>Board 1088</span>
  </div>
  <h1>MeApp Daily Standup</h1>
  <p class="sub">Work logged in the window <b>{e(D['window'])}</b>, plus live sprint workload,
     Jira&nbsp;&harr;&nbsp;GitHub status drift, sizing gaps and open pull requests across
     meApp, SageApp and meAppTest.</p>
  <div class="tiles">
    <div class="tile"><span class="k">Logged</span><span class="v num">{hrs(T['logged'])}</span></div>
    <div class="tile"><span class="k">Sprint issues</span><span class="v num">{T['issues']}</span></div>
    <div class="tile crit"><span class="k">Status &ne; GitHub</span><span class="v num">{T['mismatch']}</span></div>
    <div class="tile warn"><span class="k">Missing SP/OE</span><span class="v num">{T['unsized']}</span></div>
    <div class="tile"><span class="k">Open PRs</span><span class="v num">{T['prs']}</span></div>
    <div class="tile crit"><span class="k">PR conflicts</span><span class="v num">{T['conflict']}</span></div>
  </div>
</div></header>

<div class="controls"><div class="wrap cbar">
  <div class="crow">
    <input type="search" id="q" placeholder="Search MOB key or summary&hellip;" aria-label="Search issues and pull requests">
    <button class="chip f-crit" data-flag="mismatch" aria-pressed="false">Status &ne; GitHub <span class="c">{T['mismatch']}</span></button>
    <button class="chip f-warn" data-flag="unsized" aria-pressed="false">Missing SP/OE <span class="c">{T['unsized']}</span></button>
    <button class="chip f-crit" data-flag="conflict" aria-pressed="false">PR conflicts <span class="c">{T['conflict']}</span></button>
    <button class="chip" data-flag="worked" aria-pressed="false">Logged time <span class="c">{T['worked']}</span></button>
    <button class="reset" id="reset" type="button">Reset</button>
    <span class="hits" id="hits"></span>
  </div>
  <div class="crow"><span class="clabel">Person</span>''')

    for p in people:
        w(f'<button class="chip" data-person="{e(p["name"])}" aria-pressed="false">'
          f'{e(p["name"])} <span class="c">{len(p["issues"])}</span></button>')

    w('</div>\n  <div class="crow"><span class="clabel">Status</span>')
    scount = {}
    for p in people:
        for i in p['issues']:
            scount[i['status']] = scount.get(i['status'], 0) + 1
    for s in STATUS_ORDER + sorted(k for k in scount if k not in STATUS_ORDER):
        if s in scount:
            w(f'<button class="chip" data-status="{e(s)}" aria-pressed="false">{e(s)} <span class="c">{scount[s]}</span></button>')

    w('</div>\n  <div class="crow"><span class="clabel">PR state</span>')
    pcount = {}
    for p in people:
        for q in p['prs']:
            pcount[q['state']] = pcount.get(q['state'], 0) + 1
    cls = {'conflict': 'f-crit', 'changes-req': 'f-warn', 'approved': 'f-ok', 'needs-review': ''}
    for kk, lbl in PR_ORDER:
        if kk in pcount:
            w(f'<button class="chip {cls[kk]}" data-pr="{kk}" aria-pressed="false">{lbl} <span class="c">{pcount[kk]}</span></button>')
    w('</div>\n</div></div>\n\n<main class="wrap">')

    for p in people:
        nm = e(p['name'])
        mis = sum(1 for i in p['issues'] if i['mismatch'])
        uns = sum(1 for i in p['issues'] if i['unsized'])
        w(f'''<section class="person" data-person="{nm}">
  <div class="phead">
    <span class="pname">{nm}</span><span class="role">{e(p.get('role', ''))}</span>
    <div class="pstats">
      <span class="stat">Logged <b>{hrs(p['logged'])}</b></span>
      <span class="stat">Sprint <b>{len(p['issues'])}</b></span>''')
        if mis:
            w(f'<span class="stat crit">Status &ne; GitHub <b>{mis}</b></span>')
        if uns:
            w(f'<span class="stat warn">Unsized <b>{uns}</b></span>')
        w(f'<span class="stat">Open PRs <b>{len(p["prs"])}</b></span></div></div>')

        w('<div class="block" data-block="worked"><div class="btitle">Worked in window</div>')
        if p['worked']:
            w('<div class="tscroll"><table><thead><tr><th>Issue</th><th>Summary</th>'
              '<th class="r">Logged</th><th class="r">Total</th><th class="r">Est</th></tr></thead><tbody>')
            for it in p['worked']:
                oe = hrs(it['oe']) + (' <span class="derived">SP</span>' if it['oe'] and it['oe_derived'] else '')
                w(f'''<tr class="row-w" data-person="{nm}" data-text="{e((it['key'] + ' ' + (it.get('summary') or '')).lower())}">
            <td class="mono"><a href="{BROWSE}{it['key']}">{it['key']}</a></td>
            <td class="sum">{e(it.get('summary') or '')}</td>
            <td class="r num">{hrs(it['hours'])}</td><td class="r num">{hrs(it['total'])}</td>
            <td class="r num">{oe}</td></tr>''')
            w(f'</tbody><tfoot><tr><td colspan="2">Total</td><td class="r num">{hrs(p["logged"])}</td>'
              '<td></td><td></td></tr></tfoot></table></div>')
        else:
            w('<div class="empty">No time logged in this window.</div>')
        w('</div>')

        w('<div class="block" data-block="issues"><div class="btitle">Sprint workload</div>')
        order = STATUS_ORDER + sorted({i['status'] for i in p['issues']} - set(STATUS_ORDER))
        for st in order:
            grp = [i for i in p['issues'] if i['status'] == st]
            if not grp:
                continue
            w(f'<details class="grp" data-status="{e(st)}"{" open" if st in OPEN_BY_DEFAULT else ""}>'
              f'<summary class="gsum">{e(st)} <span class="gcount">{len(grp)}</span></summary><div class="rows">')
            for i in grp:
                flags = [f for f, on in (('mismatch', i['mismatch']), ('unsized', i['unsized'])) if on]
                meta = []
                if i['mismatch']:
                    meta.append(f'<span class="tag t-crit">{e(i["mismatch"])}</span>')
                if i['unsized']:
                    meta.append('<span class="tag t-warn">no SP/OE</span>')
                elif i['oe']:
                    meta.append(f'<span class="tag t-mute">{hrs(i["oe"])}</span>')
                elif i['sp']:
                    meta.append(f'<span class="tag t-mute">{i["sp"]:g} SP</span>')
                for q in i['prs']:
                    meta.append(f'<a class="prlink mono" href="{q["url"]}">{q["repo"]}#{q["number"]}</a>')
                w(f'''<div class="row" data-person="{nm}" data-status="{e(st)}" data-flags="{' '.join(flags)}"
             data-text="{e((i['key'] + ' ' + (i.get('summary') or '')).lower())}">
            <span class="key mono"><a href="{BROWSE}{i['key']}">{i['key']}</a></span>
            <span class="txt">{e(i.get('summary') or '')}</span>
            <span class="meta">{''.join(meta)}</span></div>''')
            w('</div></details>')
        w('</div>')

        w('<div class="block" data-block="prs"><div class="btitle">Open pull requests</div>')
        if p['prs']:
            w('<div class="rows">')
            rank = {k: n for n, (k, _) in enumerate(PR_ORDER)}
            for q in sorted(p['prs'], key=lambda x: (rank[x['state']], x['repo'], -x['number'])):
                extra = ('<span class="tag t-warn">changes requested</span>'
                         if q['state'] == 'conflict' and q.get('review') == 'CHANGES_REQUESTED' else '')
                keys = ' '.join(f'<a class="mono prlink" href="{BROWSE}{k}">{k}</a>' for k in (q.get('keys') or []))
                w(f'''<div class="row" data-person="{nm}" data-pr="{q['state']}"
             data-text="{e((q['title'] + ' ' + q.get('branch', '') + ' ' + ' '.join(q.get('keys') or [])).lower())}">
            <span class="key mono"><a href="{q['url']}">{q['repo']}#{q['number']}</a></span>
            <span class="txt">{e(q['title'])}</span>
            <span class="meta">{keys}{extra}<span class="tag {PR_TAG[q['state']]}">{dict(PR_ORDER)[q['state']]}</span></span></div>''')
            w('</div>')
        else:
            w('<div class="empty">No open pull requests.</div>')
        w('</div></section>')

    w(f'''</main>
<div class="wrap"><div class="nohits" id="nohits">Nothing matches these filters.</div></div>
<footer class="wrap">
  <span>Generated {e(D.get('generated', ''))}</span>
  <span>&middot;</span><span>Refreshes every weekday at 08:30 IST</span>
  <span>&middot;</span><a href="{BOARD}">MOB board</a>
  <span>&middot;</span><span>Sizing rule: Est = Original Estimate, else Story Points &times; 2h</span>
</footer>

<script>
(function () {{
  var q = document.getElementById('q'), hits = document.getElementById('hits');
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var people = Array.prototype.slice.call(document.querySelectorAll('.person'));

  function on(attr) {{
    return chips.filter(function (c) {{
      return c.hasAttribute('data-' + attr) && c.getAttribute('aria-pressed') === 'true';
    }}).map(function (c) {{ return c.getAttribute('data-' + attr); }});
  }}

  function apply() {{
    var text = q.value.trim().toLowerCase();
    var fPerson = on('person'), fStatus = on('status'), fFlag = on('flag'), fPr = on('pr');
    var wantWorked = fFlag.indexOf('worked') > -1;
    var issueFlags = fFlag.filter(function (f) {{ return f === 'mismatch' || f === 'unsized'; }});
    var wantConflict = fFlag.indexOf('conflict') > -1;
    // A PR-state question hides issues; a status/flag question hides PRs. Search alone hides nothing.
    var onlyPrs = (fPr.length > 0 || wantConflict) && !issueFlags.length && !fStatus.length && !wantWorked;
    var onlyIssues = (fStatus.length > 0 || issueFlags.length > 0) && !fPr.length && !wantConflict && !wantWorked;
    var onlyWorked = wantWorked && !fPr.length && !wantConflict && !issueFlags.length && !fStatus.length;
    var filtering = !!(text || fStatus.length || fFlag.length || fPr.length);
    var shown = 0;

    people.forEach(function (sec) {{
      var pOk = !fPerson.length || fPerson.indexOf(sec.getAttribute('data-person')) > -1;
      var visible = 0;

      sec.querySelectorAll('.block').forEach(function (blk) {{
        var kind = blk.getAttribute('data-block');
        var allowed = pOk && (
          (kind === 'issues' && !onlyPrs && !onlyWorked) ||
          (kind === 'prs' && !onlyIssues && !onlyWorked) ||
          (kind === 'worked' && !onlyPrs && !onlyIssues));
        var count = 0;

        blk.querySelectorAll('.row, .row-w').forEach(function (row) {{
          var ok = allowed;
          if (ok && text) ok = (row.getAttribute('data-text') || '').indexOf(text) > -1;
          if (ok && kind === 'issues') {{
            if (fStatus.length && fStatus.indexOf(row.getAttribute('data-status')) < 0) ok = false;
            if (ok && issueFlags.length) {{
              var have = (row.getAttribute('data-flags') || '').split(' ');
              ok = issueFlags.some(function (f) {{ return have.indexOf(f) > -1; }});
            }}
          }}
          if (ok && kind === 'prs') {{
            if (fPr.length && fPr.indexOf(row.getAttribute('data-pr')) < 0) ok = false;
            if (ok && wantConflict && row.getAttribute('data-pr') !== 'conflict') ok = false;
          }}
          row.classList.toggle('hide', !ok);
          if (ok) count++;
        }});

        blk.querySelectorAll('details.grp').forEach(function (g) {{
          var live = g.querySelectorAll('.row:not(.hide)').length;
          g.classList.toggle('hide', live === 0);
          if (filtering && live > 0) g.open = true;
        }});

        blk.classList.toggle('hide', !allowed || count === 0);
        visible += count;
      }});

      sec.classList.toggle('hide', !pOk || visible === 0);
      shown += visible;
    }});

    document.getElementById('nohits').style.display = shown ? 'none' : 'block';
    hits.textContent = (filtering || fPerson.length)
      ? shown + ' matching ' + (shown === 1 ? 'entry' : 'entries') : '';
  }}

  chips.forEach(function (c) {{
    c.addEventListener('click', function () {{
      c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
      apply();
    }});
  }});
  q.addEventListener('input', apply);
  document.getElementById('reset').addEventListener('click', function () {{
    q.value = '';
    chips.forEach(function (c) {{ c.setAttribute('aria-pressed', 'false'); }});
    apply();
  }});
  apply();
}})();
</script>''')
    return '\n'.join(out)


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: render_standup.py <standup.json> <out.html>")
    src, dst = sys.argv[1], sys.argv[2]
    D = derive(json.load(open(src)))
    open(dst, 'w').write(render(D))
    n = sum(len(p['issues']) for p in D['people'])
    print(f"wrote {dst} ({os.path.getsize(dst)/1024:.0f} KB, {len(D['people'])} people, {n} issues)")


if __name__ == '__main__':
    main()
