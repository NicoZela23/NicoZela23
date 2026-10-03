"""Render the neofetch-style profile card (dark_mode.svg + light_mode.svg).

    GH_TOKEN=<token> python scripts/build.py

Pulls live stats from the GitHub API, merges them with the static profile
below and the ASCII portrait in assets/ascii.txt. Standard library only.
Without GH_TOKEN the stats rows render as "—" (handy for layout tweaks).
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
USER = "NicoZela23"
CAREER_START = dt.date(2023, 3, 1)

# ── profile ──────────────────────────────────────────────────────────────────
# ("title", text) | ("section", text) | ("kv", key, value) | ("blank",) | ("stats", name)
# value: str or list of (text, css_class) segments
PROFILE = [
    ("title", "nico@zelaya"),
    ("kv", "OS", "Sucre, Bolivia (UTC-4)"),
    ("kv", "Uptime", "{uptime}"),
    ("kv", "Status", [("● ", "ok pulse"), ("Open to work · Remote", "v")]),
    ("kv", "Host", "Full Stack AI Engineer"),
    ("kv", "Kernel", "Backend · System Design · DevOps"),
    ("kv", "Shell", "TypeScript, Python, Go, SQL, Bash"),
    ("kv", "Languages.Real", "Spanish, English"),
    ("blank",),
    ("section", "Stack"),
    ("kv", "AI", "Anthropic, Gemini, OpenAI, AI SDK, MCP"),
    ("kv", "Backend", "Node.js, NestJS, FastAPI, Go (Gin)"),
    ("kv", "Frontend", "React, Next.js, Tailwind CSS"),
    ("kv", "Cloud", "GCP, AWS, Vercel, Supabase, Docker"),
    ("kv", "Data", "PostgreSQL, MongoDB, Firestore"),
    ("kv", "Community", "AWS User Group Sucre (Lead), GDG"),
    ("blank",),
    ("section", "Contact"),
    ("kv", "Email", "nzelaya785@gmail.com"),
    ("kv", "LinkedIn", "in/nicozela"),
    ("kv", "TikTok", "@nico_zela1"),
    ("blank",),
    ("section", "GitHub Stats"),
    ("stats", "repos"),
    ("stats", "commits"),
    ("stats", "loc"),
]

# ── layout ───────────────────────────────────────────────────────────────────
FONT_SIZE = 15
CHAR_W = 9.0        # forced per line via textLength, so any monospace font lines up
LINE_H = 20
PAD_X = 22
TITLE_BAR = 34
ASCII_GAP = 3       # columns between portrait and card
CARD_COLS = 58
PROMPT = "nico@zelaya:~$ "

THEMES = {
    "dark": dict(bg="#0d1117", bar="#161b22", border="#30363d", text="#c9d1d9", art="#8b949e",
                 key="#ffa657", value="#a5d6ff", dots="#484f58", user="#7ee787", path="#79c0ff",
                 add="#3fb950", rm="#f85149", title="#8b949e"),
    "light": dict(bg="#ffffff", bar="#f6f8fa", border="#d0d7de", text="#24292f", art="#57606a",
                  key="#953800", value="#0a3069", dots="#afb8c1", user="#116329", path="#0550ae",
                  add="#1a7f37", rm="#cf222e", title="#57606a"),
}


# ── GitHub API ───────────────────────────────────────────────────────────────
def request(url, token, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers={
        "Authorization": f"bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USER}-profile-card",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return r.status, json.loads(raw) if raw else None


def graphql(token, query, **variables):
    _, data = request("https://api.github.com/graphql", token, {"query": query, "variables": variables})
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def repo_lists(token):
    """Owned non-fork repos (with stars) and repos contributed to, paginated."""
    query = """
    query($login: String!, $owned: String, $contrib: String) {
      user(login: $login) {
        createdAt
        repositories(first: 100, after: $owned, ownerAffiliations: OWNER, isFork: false) {
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner stargazerCount }
        }
        repositoriesContributedTo(first: 100, after: $contrib, includeUserRepositories: false,
                                  contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) {
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner isFork }
        }
      }
    }"""
    owned, contrib, created = [], [], None
    cursors = {"owned": None, "contrib": None}
    more = {"owned": True, "contrib": True}
    while any(more.values()):
        user = graphql(token, query, login=USER, **cursors)["user"]
        created = user["createdAt"]
        for key, field, bucket in (("owned", "repositories", owned), ("contrib", "repositoriesContributedTo", contrib)):
            if more[key]:
                page = user[field]
                bucket.extend(page["nodes"])
                more[key] = page["pageInfo"]["hasNextPage"]
                cursors[key] = page["pageInfo"]["endCursor"]
    contrib = [r for r in contrib if not r["isFork"]]
    return owned, contrib, dt.datetime.fromisoformat(created.replace("Z", "+00:00"))


def contribution_totals(token, created):
    """Commits and PRs summed year by year (the API caps a window at one year)."""
    now = dt.datetime.now(dt.timezone.utc)
    windows, start = [], created
    while start < now:
        end = min(start.replace(year=start.year + 1), now)
        windows.append((start, end))
        start = end
    parts = [
        f'y{i}: contributionsCollection(from: "{a.isoformat()}", to: "{b.isoformat()}") '
        "{ totalCommitContributions totalPullRequestContributions }"
        for i, (a, b) in enumerate(windows)
    ]
    user = graphql(token, "query($login: String!) { user(login: $login) { %s } }" % " ".join(parts), login=USER)["user"]
    return (sum(y["totalCommitContributions"] for y in user.values()),
            sum(y["totalPullRequestContributions"] for y in user.values()))


def lines_of_code(token, repos):
    """Sum this user's additions/deletions via /stats/contributors (GitHub computes it lazily -> 202)."""
    added = removed = 0
    pending = list(repos)
    for attempt in range(6):
        retry = []
        for name in pending:
            try:
                status, data = request(f"https://api.github.com/repos/{name}/stats/contributors", token)
            except urllib.error.HTTPError:
                continue  # no access / empty repo
            if status == 202:
                retry.append(name)
                continue
            for c in data or []:
                if (c.get("author") or {}).get("login", "").lower() == USER.lower():
                    added += sum(w["a"] for w in c["weeks"])
                    removed += sum(w["d"] for w in c["weeks"])
        pending = retry
        if not pending:
            break
        time.sleep(5 * (attempt + 1))
    if pending:
        print(f"stats still computing for {len(pending)} repos: {', '.join(pending)}")
    return added, removed


def fetch_stats(token):
    owned, contrib, created = repo_lists(token)
    commits, prs = contribution_totals(token, created)
    names = {r["nameWithOwner"] for r in owned} | {r["nameWithOwner"] for r in contrib}
    added, removed = lines_of_code(token, sorted(names))
    return dict(repos=len(owned), contributed=len(contrib), stars=sum(r["stargazerCount"] for r in owned),
                commits=commits, prs=prs, added=added, removed=removed)


# ── rendering ────────────────────────────────────────────────────────────────
def uptime(today):
    years = today.year - CAREER_START.year
    months = today.month - CAREER_START.month
    days = today.day - CAREER_START.day
    if days < 0:
        months -= 1
        prev_month_end = today.replace(day=1) - dt.timedelta(days=1)
        days += prev_month_end.day
    if months < 0:
        years -= 1
        months += 12
    plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
    return f"{plural(years, 'year')}, {plural(months, 'month')}, {plural(days, 'day')}"


def fmt(n):
    return "—" if n is None else f"{n:,}"


def kv_segments(key, value, width):
    """`key: ....... value` padded to exactly `width` chars."""
    value = [(value, "v")] if isinstance(value, str) else value
    vlen = sum(len(t) for t, _ in value)
    dots = max(2, width - len(key) - 2 - 1 - vlen)
    return [(key, "k"), (": ", ""), ("." * dots, "d"), (" ", "")] + value


def stats_segments(name, s):
    if name == "repos":
        left = kv_segments("Repos", [(fmt(s.get("repos")), "v"), (" {", ""), ("Contributed", "k"), (": ", ""),
                                     (fmt(s.get("contributed")), "v"), ("}", "")], 31)
        right = kv_segments("Stars", fmt(s.get("stars")), CARD_COLS - 34)
    elif name == "commits":
        left = kv_segments("Commits", fmt(s.get("commits")), 31)
        right = kv_segments("PRs", fmt(s.get("prs")), CARD_COLS - 34)
    else:
        a, r = s.get("added"), s.get("removed")
        net = None if a is None else a - r
        return kv_segments("Lines of Code", [(fmt(net), "v"), (" ( ", ""), (f"{fmt(a)}++", "add"), (", ", ""),
                                             (f"{fmt(r)}--", "rm"), (" )", "")], CARD_COLS)
    return left + [(" | ", "")] + right


def card_lines(stats, today):
    lines = []
    for row in PROFILE:
        kind = row[0]
        if kind == "title":
            lines.append([(row[1], "u"), (" ", ""), ("─" * (CARD_COLS - len(row[1]) - 1), "d")])
        elif kind == "section":
            lines.append([("- ", "d"), (row[1], "u"), (" ", ""), ("─" * (CARD_COLS - len(row[1]) - 3), "d")])
        elif kind == "blank":
            lines.append([])
        elif kind == "kv":
            value = row[2].replace("{uptime}", uptime(today)) if isinstance(row[2], str) else row[2]
            lines.append(kv_segments(row[1], value, CARD_COLS))
        else:
            lines.append(stats_segments(row[1], stats))
    return lines


def text_el(x, y, segments):
    n = sum(len(t) for t, _ in segments)
    if not n:
        return ""
    spans = "".join(f'<tspan class="{c}">{escape(t)}</tspan>' if c else escape(t) for t, c in segments)
    return (f'<text x="{x:.1f}" y="{y}" textLength="{n * CHAR_W:.1f}" '
            f'lengthAdjust="spacingAndGlyphs">{spans}</text>')


def render(theme, art, card):
    c = THEMES[theme]
    art_cols = max(len(l) for l in art)
    body_rows = max(len(art), len(card))
    art_top = (body_rows - len(art)) // 2
    width = PAD_X * 2 + (art_cols + ASCII_GAP + CARD_COLS) * CHAR_W
    # rows: prompt, blank, body..., blank, prompt
    height = TITLE_BAR + 18 + (body_rows + 4) * LINE_H
    y0 = TITLE_BAR + 18 + LINE_H - 5
    card_x = PAD_X + (art_cols + ASCII_GAP) * CHAR_W

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height}" '
        f'viewBox="0 0 {width:.0f} {height}" xml:space="preserve" role="img" '
        f'aria-label="Nico Zelaya - Full Stack AI Engineer - terminal profile card">',
        "<style>",
        f"text{{font-family:ConsolasFallback,Consolas,'SF Mono',Menlo,'DejaVu Sans Mono','Courier New',monospace;"
        f"font-size:{FONT_SIZE}px;fill:{c['text']};white-space:pre}}",
        f".art{{fill:{c['art']}}}.k{{fill:{c['key']}}}.v{{fill:{c['value']}}}.d{{fill:{c['dots']}}}"
        f".u{{fill:{c['user']};font-weight:bold}}.p{{fill:{c['path']};font-weight:bold}}"
        f".add,.ok{{fill:{c['add']}}}.rm{{fill:{c['rm']}}}.t{{fill:{c['title']};font-size:13px}}",
        ".cursor{animation:blink 1.1s steps(1) infinite}.pulse{animation:pulse 2s ease-in-out infinite}",
        "@keyframes blink{50%{opacity:0}}@keyframes pulse{50%{opacity:.35}}",
        "</style>",
        f'<rect x="0.5" y="0.5" width="{width - 1:.0f}" height="{height - 1}" rx="10" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>',
        f'<path d="M0.5 {TITLE_BAR}V10.5a10 10 0 0 1 10-10H{width - 10.5:.0f}a10 10 0 0 1 10 10V{TITLE_BAR}Z" '
        f'fill="{c["bar"]}"/>',
        f'<line x1="0.5" y1="{TITLE_BAR}" x2="{width - 0.5:.0f}" y2="{TITLE_BAR}" stroke="{c["border"]}"/>',
        '<circle cx="20" cy="17" r="6" fill="#ff5f57"/><circle cx="40" cy="17" r="6" fill="#febc2e"/>'
        '<circle cx="60" cy="17" r="6" fill="#28c840"/>',
        f'<text class="t" x="{width / 2:.0f}" y="22" text-anchor="middle">nico@zelaya: ~ — zsh</text>',
    ]

    def prompt(row, cmd):
        segs = [("nico@zelaya", "u"), (":", ""), ("~", "p"), ("$ ", ""), (cmd, "")]
        return text_el(PAD_X, y0 + row * LINE_H, segs)

    out.append(prompt(0, "neofetch"))
    for i, line in enumerate(art):
        out.append(text_el(PAD_X, y0 + (2 + art_top + i) * LINE_H, [(line.ljust(art_cols), "art")]))
    for i, segs in enumerate(card):
        out.append(text_el(card_x, y0 + (2 + i) * LINE_H, segs))
    last = 2 + body_rows + 1
    out.append(prompt(last, ""))
    cx = PAD_X + len(PROMPT) * CHAR_W
    out.append(f'<rect class="cursor" x="{cx:.1f}" y="{y0 + last * LINE_H - 14}" width="{CHAR_W:.0f}" '
               f'height="17" fill="{c["text"]}"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    token = os.environ.get("GH_TOKEN")
    stats = fetch_stats(token) if token else {}
    print(json.dumps(stats))
    art = (ROOT / "assets" / "ascii.txt").read_text().rstrip("\n").split("\n")
    card = card_lines(stats, dt.date.today())
    for theme in THEMES:
        (ROOT / f"{theme}_mode.svg").write_text(render(theme, art, card))


if __name__ == "__main__":
    main()
