#!/usr/bin/env python3
"""Build the animated header of my GitHub profile: "when do I commit?"

Every commit that GitHub attributes to me on the default branch of one of my public repositories becomes one dot
on a weekday by hour grid. The bars count commits per hour and per weekday, the caption reports the peak hour with
a posterior probability, and at the end a snake eats all dots while the bars run backwards.

The output is a self-contained SVG animated with SMIL (no JavaScript), written in a dark and a light variant.
Only the Python standard library is used.

    python scripts/build_hero.py --out dist                  # fetch from the GitHub API and build
    python scripts/build_hero.py --out dist --load c.json    # build from a saved list of timestamps
    python scripts/build_hero.py --out dist --save c.json    # fetch, build and save the timestamps
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

OWNER = os.environ.get('GITHUB_REPOSITORY_OWNER') or 'HannesDiemerling'
ZONE = 'Europe/Berlin'                 # the API reports UTC; hours are shown in this time zone
MAX_DOTS = 1200                        # keeps the file small: beyond this only the most recent commits are drawn
DRAWS = 4000                           # posterior draws of the Bayesian bootstrap
W, H, T = 860, 340, 18.5               # card size in px, loop length in seconds
B0, B1 = 0.6, 7.2                      # act 1: dots appear in chronological order
S0, S1 = 7.7, 11.5                     # act 2: statistics
E0, E1 = 12.0, 16.4                    # act 3: the snake eats
FADE = T - 1.0
MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
THEMES = {
    'dark': dict(bg='#0d1117', border='#30363d', fg='#c9d1d9', muted='#8b949e', guide='#1b2129', accent='#ffa657',
                 bar='#58a6ff', dots=['#196c2e', '#2ea043', '#3fb950', '#56d364'], snake=['#a371f7', '#8957e5', '#6e40c9']),
    'light': dict(bg='#ffffff', border='#d0d7de', fg='#1f2328', muted='#656d76', guide='#eaeef2', accent='#bc4c00',
                  bar='#0969da', dots=['#8fdf9d', '#4ac26b', '#2da44e', '#1a7f37'], snake=['#8250df', '#a475f9', '#6639ba']),
}


# ---------------------------------------------------------------------------------------------- data
def api(path):
    """GET one GitHub API path and follow the pagination. Returns the concatenated list."""
    url, out = 'https://api.github.com' + path, []
    while url:
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'profile-hero'})
        if os.environ.get('GITHUB_TOKEN'):
            req.add_header('Authorization', 'Bearer ' + os.environ['GITHUB_TOKEN'])
        with urllib.request.urlopen(req, timeout=30) as res:
            out += json.load(res)
            links = res.headers.get('Link') or ''
        url = next((part.split(';')[0].strip(' <>') for part in links.split(',') if 'rel="next"' in part), None)
    return out


def fetch(owner):
    """Timestamps (in ZONE) of all non-merge commits GitHub attributes to `owner` in the owner's public repositories."""
    zone, stamps = ZoneInfo(ZONE), {}
    for repo in api(f'/users/{owner}/repos?per_page=100&type=owner'):
        try:
            commits = api(f'/repos/{owner}/{repo["name"]}/commits?author={owner}&per_page=100')
        except urllib.error.HTTPError as err:
            if err.code == 409:        # empty repository
                continue
            raise
        for c in commits:
            if len(c['parents']) < 2:
                when = dt.datetime.fromisoformat(c['commit']['author']['date'].replace('Z', '+00:00'))
                stamps[c['sha']] = when.astimezone(zone).replace(tzinfo=None)
    return sorted(stamps.values())


# ---------------------------------------------------------------------------------------------- SMIL helpers
def anim(attr, frames, spline=False):
    """<animate> that loops forever. frames = [(second, value), ...] from 0 to T with increasing times."""
    assert frames[0][0] == 0 and abs(frames[-1][0] - T) < 1e-9, (attr, frames[0], frames[-1])
    assert all(b[0] > a[0] for a, b in zip(frames, frames[1:])), (attr, frames)
    times = ';'.join(f'{t / T:.4f}' for t, _ in frames)
    values = ';'.join(str(v) for _, v in frames)
    ease = ' calcMode="spline" keySplines="' + ';'.join(['0.45 0 0.2 1'] * (len(frames) - 1)) + '"' if spline else ''
    return f'<animate attributeName="{attr}" values="{values}" keyTimes="{times}" dur="{T}s" repeatCount="indefinite"{ease}/>'


def step(attr, frames):
    """Like anim(), but the value jumps at each time instead of blending."""
    assert frames[0][0] == 0 and all(b[0] > a[0] for a, b in zip(frames, frames[1:])) and frames[-1][0] < T, (attr, frames)
    times = ';'.join(f'{t / T:.4f}' for t, _ in frames)
    values = ';'.join(str(v) for _, v in frames)
    return f'<animate attributeName="{attr}" values="{values}" keyTimes="{times}" dur="{T}s" calcMode="discrete" repeatCount="indefinite"/>'


def window(a, b, fade=0.4):
    """Opacity animation: visible from second a to second b."""
    return anim('opacity', [(0, 0), (a, 0), (a + fade, 1), (b, 1), (b + fade, 0), (T, 0)])


# ---------------------------------------------------------------------------------------------- the snake
def snake_route(points, start):
    """Greedy nearest-neighbour route with right-angle moves. Returns (svg path, distance travelled at each point)."""
    cur, left, path, dist, at = start, set(range(len(points))), [f'M{start[0]:.1f},{start[1]:.1f}'], 0.0, [0.0] * len(points)
    while left:
        j = min(left, key=lambda k: abs(points[k][0] - cur[0]) + abs(points[k][1] - cur[1]))
        left.remove(j)
        if abs(points[j][0] - cur[0]) > 0.05:
            path.append(f'H{points[j][0]:.1f}')
        if abs(points[j][1] - cur[1]) > 0.05:
            path.append(f'V{points[j][1]:.1f}')
        dist += abs(points[j][0] - cur[0]) + abs(points[j][1] - cur[1])
        at[j], cur = dist, points[j]
    return ''.join(path), at, dist


def snake(points, start, th, size=8, segments=12, steps=60):
    """SVG of the snake and its score counter, plus the second at which each point is eaten."""
    path, at, dist = snake_route(points, start)
    eaten = [E0 + (E1 - E0) * a / dist for a in at]
    lag = min((size + 1.5) / (dist / (E1 - E0)), (FADE - E1 - 0.05) / segments)
    out = ['<g>']
    for i in range(segments - 1, -1, -1):
        a, b = E0 + i * lag, E1 + i * lag
        grow = 0 if i < 3 else (i - 2) / (segments - 2) * 0.5 * (E1 - E0)       # the snake grows while it eats
        s = size if i == 0 else size - 1
        motion = (f'<animateMotion path="{path}" dur="{T}s" repeatCount="indefinite" calcMode="linear" '
                  f'keyTimes="0;{a / T:.4f};{b / T:.4f};1" keyPoints="0;0;1;1"/>')
        show = anim('opacity', [(0, 0), (a + grow, 0), (a + grow + 0.05, 1), (FADE, 1), (FADE + 0.4, 0), (T, 0)])
        fill = th['snake'][0] if i == 0 else th['snake'][1 + i % 2]
        out.append(f'<rect x="{-s / 2}" y="{-s / 2}" width="{s}" height="{s}" rx="2" fill="{fill}" opacity="0">{motion}{show}</rect>')
    out.append(f'</g><g font-family="{MONO}" text-anchor="end">')
    for k in range(steps + 1):
        a = E0 + (E1 - E0) * k / steps
        if k < steps:
            value, colour = sum(1 for e in eaten if e <= a + 1e-9), th['fg']
            show = step('opacity', [(0, 0), (a, 1), (E0 + (E1 - E0) * (k + 1) / steps, 0)])
        else:
            value, colour = len(points), th['accent']
            show = anim('opacity', [(0, 0), (a - 0.001, 0), (a, 1), (FADE, 1), (FADE + 0.4, 0), (T, 0)])
        out.append(f'<text x="{W - 30}" y="30" font-size="15" fill="{th["fg"]}" opacity="0">score '
                   f'<tspan fill="{colour}" font-weight="700">{value}</tspan>{show}</text>')
    out.append(f'<text x="{W - 30}" y="47" font-size="11.5" fill="{th["muted"]}" opacity="0">commits eaten · greedy policy'
               f'{window(E0, FADE, 0.3)}</text></g>')
    return '\n'.join(out), eaten


# ---------------------------------------------------------------------------------------------- statistics
def statistics(stamps):
    """Peak hour with its posterior probability, and the share of commits in the evening with a 90 % interval.

    Commits come in bursts, so single commits are not independent. The Bayesian bootstrap therefore reweights whole
    days (Dirichlet(1, ..., 1) weights over the days with commits) and recomputes the hourly totals for every draw.
    """
    by_hour = collections.Counter(s.hour for s in stamps)
    peak = max(range(24), key=lambda h: (by_hour[h], -h))
    days = collections.defaultdict(collections.Counter)
    for s in stamps:
        days[s.date()][s.hour] += 1
    days = [list(c.items()) for c in days.values()]
    late_hours = set(range(18, 24)) | set(range(0, 5))
    rng, wins, shares = random.Random(1), 0, []
    for _ in range(DRAWS):
        total = [0.0] * 24
        for day in days:
            weight = rng.expovariate(1)
            for hour, n in day:
                total[hour] += weight * n
        wins += max(range(24), key=total.__getitem__) == peak
        shares.append(sum(total[h] for h in late_hours) / sum(total))
    shares.sort()
    late = sum(by_hour[h] for h in late_hours) / len(stamps)
    return dict(peak=peak, p_peak=wins / DRAWS, late=late, late_lo=shares[int(0.05 * DRAWS)], late_hi=shares[int(0.95 * DRAWS)])


# ---------------------------------------------------------------------------------------------- the picture
def hour_axis(used):
    """Hours shown on the x axis: the longest run of unused hours (the night) is cut out if it is 3 h or longer."""
    best = (0, 0)
    for start in range(24):
        run = 0
        while run < 24 and (start + run) % 24 not in used:
            run += 1
        best = max(best, (run, start))
    run, start = best
    if run < 3:
        return list(range(24)), ''
    hours = [(start + run + k) % 24 for k in range(24 - run)]
    return hours, f'{start:02d} to {(start + run - 1) % 24:02d}: none'


def build(stamps, theme):
    th = THEMES[theme]
    total = len(stamps)
    stamps = stamps[-MAX_DOTS:]
    n = len(stamps)
    stats = statistics(stamps)
    by_hour = collections.Counter(s.hour for s in stamps)
    by_day = collections.Counter(s.weekday() for s in stamps)
    hours, omitted = hour_axis(set(by_hour))
    x0, y0, row = 74, 96, 31
    col = 666 / len(hours)
    x1, y1 = x0 + col * len(hours), y0 + row * 7

    # dots: sunflower packing inside each weekday/hour cell, in chronological order
    per_cell = collections.Counter((s.weekday(), s.hour) for s in stamps)
    spread = min(2.9, (row / 2 - 2.5) / math.sqrt(max(per_cell.values())))
    radius = min(2.2, spread * 0.76)
    seen, points = collections.Counter(), []
    for s in stamps:
        k = seen[(s.weekday(), s.hour)]
        seen[(s.weekday(), s.hour)] += 1
        r, a = (spread * math.sqrt(k + 0.35) if k else 0.0), k * 2.399963
        points.append((x0 + col * (hours.index(s.hour) + 0.5) + r * math.cos(a), y0 + row * (s.weekday() + 0.5) + r * math.sin(a)))
    snake_svg, eaten = snake(points, (W + 14, y0 + row * 3.5), th)
    years = sorted({s.year for s in stamps})
    shade = {y: th['dots'][max(0, len(th['dots']) - len(years) + i)] for i, y in enumerate(years)}
    dots = []
    for i, (s, (x, y)) in enumerate(zip(stamps, points)):
        a = B0 + (B1 - B0) * i / max(n - 1, 1)
        life = anim('opacity', [(0, 0), (a, 0), (a + 0.18, 1), (eaten[i] - 0.001, 1), (eaten[i], 0), (T, 0)])
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.2f}" fill="{shade[s.year]}" opacity="0">{life}</circle>')

    # frame: titles, axis labels, guides, legend
    scope = f'one dot = one commit · {ZONE} time' + (f' · last {n} of {total}' if total > n else '')
    frame = [f'<g font-family="{MONO}" fill="{th["muted"]}">',
             f'<text x="30" y="30" font-size="13" fill="{th["fg"]}">when do I commit?</text>',
             f'<text x="30" y="48" font-size="11">{scope}</text>']
    if omitted:
        frame.append(f'<text x="{W - 30}" y="{y1 + 15}" font-size="10" text-anchor="end">{omitted}</text>')
    for d, name in enumerate(WEEKDAYS):
        frame.append(f'<text x="62" y="{y0 + row * (d + 0.5) + 4:.1f}" font-size="11.5" text-anchor="end">{name}</text>')
    ticks = ''
    for c, h in enumerate(hours):
        if h % 3 == 0:
            cx = x0 + col * (c + 0.5)
            frame.append(f'<text x="{cx:.1f}" y="{y1 + 15}" font-size="11" text-anchor="middle">{h:02d}:00</text>')
            ticks += f'<line x1="{cx:.1f}" y1="{y1}" x2="{cx:.1f}" y2="{y1 + 4}"/>'
    frame.append(f'<text x="30" y="64" font-size="10">{years[0]}</text>'
                 + ''.join(f'<circle cx="{64 + 10 * i}" cy="60.5" r="2.6" fill="{shade[y]}"/>' for i, y in enumerate(years))
                 + f'<text x="{64 + 10 * len(years)}" y="64" font-size="10">{years[-1]}</text></g>')
    rows = ''.join(f'<line x1="{x0}" y1="{y0 + row * d}" x2="{x1:.1f}" y2="{y0 + row * d}"/>' for d in range(8))
    frame.append(f'<g stroke="{th["guide"]}" stroke-width="1">{rows}{ticks}</g>')

    # act 1: month and running count while the dots arrive
    ticker, steps = [f'<g font-family="{MONO}" font-size="14" fill="{th["fg"]}" text-anchor="end">'], 44
    for k in range(steps):
        a, b = B0 + (B1 - B0) * k / steps, B0 + (B1 - B0) * (k + 1) / steps
        i = min(n - 1, round((n - 1) * (k + 1) / steps))
        last = k == steps - 1
        ticker.append(f'<text x="{W - 30}" y="30" opacity="0"><tspan fill="{th["muted"]}">{stamps[i].strftime("%b %Y")} · </tspan>'
                      f'<tspan font-weight="700" fill="{th["accent"] if last else th["fg"]}">{i + 1}</tspan> commits'
                      f'{step("opacity", [(0, 0), (a, 1), (S0 - 0.3 if last else b, 0)])}</text>')
    ticker.append('</g>')

    # bars: they grow while the dots arrive and run backwards while the snake eats
    checkpoints = 16

    def course(member, scale):
        """Keyframes of one bar: count of matching commits present at each checkpoint, times `scale`."""
        match = [member(s) for s in stamps]
        frames, full = [(0, 0), (B0, 0)], sum(match)
        for k in range(1, checkpoints + 1):
            frames.append((B0 + (B1 - B0) * k / checkpoints, sum(match[:round(n * k / checkpoints)])))
        frames.append((E0, full))
        for k in range(1, checkpoints + 1):
            t = E0 + (E1 - E0) * k / checkpoints
            frames.append((t, sum(1 for m, e in zip(match, eaten) if m and e > t + 1e-9)))
        frames.append((T, 0))
        return [(t, round(v * scale, 1)) for t, v in frames]

    base, bar_h, hmax = 90, 25, max(by_hour.values())
    bars, labels = [], []
    for c, h in enumerate(hours):
        if by_hour[h]:
            frames = course(lambda s, h=h: s.hour == h, bar_h / hmax)
            x, top = x0 + col * c + col * 0.25, base - bar_h * by_hour[h] / hmax
            bars.append(f'<rect x="{x:.1f}" y="{base}" width="{col * 0.5:.1f}" height="0" rx="1.5">{anim("height", frames)}'
                        f'{anim("y", [(t, round(base - v, 1)) for t, v in frames])}</rect>')
            hot = h == stats['peak']
            if hot:
                labels.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{col * 0.5:.1f}" height="{base - top:.1f}" rx="1.5" fill="{th["accent"]}"/>')
            labels.append(f'<text x="{x0 + col * (c + 0.5):.1f}" y="{top - 4:.1f}" font-size="9.5" text-anchor="middle" '
                          f'fill="{th["accent"] if hot else th["muted"]}">{by_hour[h]}</text>')
    wx, ww, dmax = x1 + 12, 56, max(by_day.values())
    for d in range(7):
        frames = course(lambda s, d=d: s.weekday() == d, ww / dmax)
        y = y0 + row * d + 10
        bars.append(f'<rect x="{wx:.1f}" y="{y}" width="0" height="{row - 20}" rx="1.5">{anim("width", frames)}</rect>')
        labels.append(f'<text x="{wx + ww * by_day[d] / dmax + 5:.1f}" y="{y + row - 22.5}" font-size="10" fill="{th["muted"]}">{by_day[d]}</text>')
    caption = (f'<g text-anchor="end"><text x="{W - 30}" y="30" font-size="14" fill="{th["fg"]}">peak hour '
               f'<tspan fill="{th["accent"]}" font-weight="700">{stats["peak"]:02d}:00</tspan> · P(true peak | data) = '
               f'<tspan fill="{th["accent"]}" font-weight="700">{stats["p_peak"]:.2f}</tspan></text>'
               f'<text x="{W - 30}" y="48" font-size="11.5" fill="{th["muted"]}">{round(100 * stats["late"])} % after 18:00 '
               f'[{round(100 * stats["late_lo"])}, {round(100 * stats["late_hi"])}] · 90 % interval · Bayesian bootstrap over days</text></g>')
    overlay = (f'<g fill="{th["bar"]}" fill-opacity="0.5">{"".join(bars)}'
               f'{anim("opacity", [(0, 1), (FADE, 1), (FADE + 0.4, 0), (T - 0.2, 0), (T, 1)])}</g>\n'
               f'<g font-family="{MONO}" opacity="0">{"".join(labels)}{caption}{window(S0, S1)}</g>')
    over = (f'<text x="{(x0 + x1) / 2:.0f}" y="{y0 + row * 3.5 + 5:.0f}" font-family="{MONO}" font-size="14" text-anchor="middle" '
            f'fill="{th["muted"]}" opacity="0">game over · git push to respawn{window(E1 + 0.3, FADE, 0.3)}</text>')

    title = 'When do I commit? Commits by weekday and hour'
    desc = (f'{n} commits appear as dots on a grid of weekdays and hours. Bars count them per hour and per weekday. '
            f'The peak hour is {stats["peak"]:02d}:00. At the end a snake eats every dot and the score counts to {n}.')
    body = '\n'.join(frame + [overlay] + ticker + ['<g>' + ''.join(dots) + '</g>', snake_svg, over])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">\n'
            f'<title id="t">{title}</title><desc id="d">{desc}</desc>\n'
            f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{th["bg"]}" stroke="{th["border"]}"/>\n{body}\n</svg>\n'), stats


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', default='dist', help='output folder')
    parser.add_argument('--load', help='read timestamps from this JSON file instead of the GitHub API')
    parser.add_argument('--save', help='write the fetched timestamps to this JSON file')
    args = parser.parse_args()
    if args.load:
        stamps = sorted(dt.datetime.fromisoformat(s) for s in json.load(open(args.load)))
    else:
        stamps = fetch(OWNER)
    if args.save:
        json.dump([s.isoformat() for s in stamps], open(args.save, 'w'))
    if len(stamps) < 2:
        sys.exit('fewer than two commits found, nothing to draw')
    os.makedirs(args.out, exist_ok=True)
    for theme in THEMES:
        svg, stats = build(stamps, theme)
        with open(os.path.join(args.out, f'hero-{theme}.svg'), 'w', encoding='utf-8') as f:
            f.write(svg)
    print(f'{len(stamps)} commits · peak {stats["peak"]:02d}:00 with P = {stats["p_peak"]:.2f} · '
          f'{stats["late"]:.0%} after 18:00 [{stats["late_lo"]:.0%}, {stats["late_hi"]:.0%}]')


if __name__ == '__main__':
    main()
