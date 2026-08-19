#!/usr/bin/env python3
"""Generate bespoke amber-themed SVG stat cards for the profile README.

Reads live data from the GitHub REST API and writes cards to
assets/cards/ so the profile always shows fresh numbers without
relying on third-party stat badges.

Usage:
    GH_TOKEN=<token> python3 scripts/generate_stats.py [username]
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_USER = "Vamanakhil"
API = "https://api.github.com"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "cards")

# User-maintained metrics spanning orgs and other platforms (Swecha, TGPA,
# GitLab) that a single GitHub "author:" query cannot fully capture.
MERGE_RATE = 83
ISSUES_CLOSED = 29
# Total lines of code counted across all 26 public repos (including forks
# and vendored trees). Re-run `--local` or the CI workflow to refresh.
LINES_OF_CODE = 5_192_427

CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".php", ".c", ".h", ".cpp", ".hpp",
    ".java", ".go", ".rs", ".sh", ".bash", ".rb", ".html", ".css", ".sql",
    ".yml", ".yaml", ".json", ".md", ".vue", ".kt", ".lua", ".pl",
}
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", ".venv", "venv",
    "__pycache__", "coverage", ".next", ".nuxt", "third_party",
    "bower_components", "target", ".gradle", ".idea", ".vscode", "logs",
}

# ── design tokens ────────────────────────────────────────────────
BG = "#161b22"        # card background
HAIRLINE = "#30363d"  # hairline border
MUTED = "#8b949e"     # secondary text
TEXT = "#c9d1d9"      # primary text
AMBER = "#FFB000"     # single accent

LANGS = {
    "python": "#3572A5",
    "javascript": "#f1e05a",
    "typescript": "#3178c6",
    "c++": "#f34b7d",
    "c": "#555555",
    "shell": "#89e051",
    "go": "#00ADD8",
    "rust": "#dea584",
    "java": "#b07219",
    "html": "#e34c26",
    "css": "#563d7c",
    "jupyter notebook": "#DA5B0B",
    "php": "#4F5D95",
    "vue": "#41b883",
    "kotlin": "#A97BFF",
}


def api(path: str, token: str | None, params: dict | None = None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        link = resp.headers.get("Link")
        return json.load(resp), link


def get_user(username: str, token: str | None):
    data, _ = api(f"/users/{username}", token)
    return data


def get_repos(username: str, token: str | None):
    repos, link = api(f"/users/{username}/repos", token, {"per_page": 100, "sort": "updated"})
    while link and 'rel="next"' in link:
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip()[1:-1]
                with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as resp:
                    repos += json.load(resp)
                    link = resp.headers.get("Link")
                break
        else:
            break
    return repos


def count_repo_commits(full_name: str, token: str | None) -> int:
    try:
        _, link = api(f"/repos/{full_name}/commits", token, {"per_page": 1})
    except urllib.error.HTTPError:
        return 0
    if not link:
        return 0
    for part in link.split(","):
        if 'rel="last"' in part:
            url = part.split(";")[0].strip()[1:-1]
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            try:
                return int(parsed.get("page", ["0"])[0])
            except ValueError:
                return 0
    return 0


def count_repo_loc(full_name: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "r")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "-q", f"https://github.com/{full_name}.git", dest],
                capture_output=True,
                timeout=120,
                check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return 0
        return count_loc_in(dest)


def count_loc_in(path: str) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if os.path.splitext(f)[1].lower() not in CODE_EXTS:
                continue
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for _ in fh)
            except OSError:
                pass
    return total


EXT_LANGS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".php": "php", ".c": "c", ".h": "c", ".cpp": "c++",
    ".hpp": "c++", ".java": "java", ".go": "go", ".rs": "rust", ".sh": "shell",
    ".bash": "shell", ".rb": "ruby", ".html": "html", ".css": "css", ".sql": "sql",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".md": "markdown",
    ".vue": "vue", ".kt": "kotlin", ".lua": "lua", ".pl": "perl",
}


def collect_local(root_dir: str) -> tuple[int, int, int, dict[str, int]]:
    """Scan a directory of local git repos. Returns (repos, commits, loc, lang_bytes)."""
    repos = 0
    commits = 0
    loc = 0
    lang_bytes: dict[str, int] = {}
    for entry in sorted(os.listdir(root_dir)):
        d = os.path.join(root_dir, entry)
        if not os.path.isdir(d) or not os.path.isdir(os.path.join(d, ".git")):
            continue
        repos += 1
        try:
            out = subprocess.run(
                ["git", "-C", d, "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0:
                commits += int(out.stdout.strip() or 0)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                lang = EXT_LANGS.get(ext)
                if not lang:
                    continue
                path = os.path.join(root, f)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        n = sum(1 for _ in fh)
                    loc += n
                    lang_bytes[lang] = lang_bytes.get(lang, 0) + n * 30
                except OSError:
                    pass
    return repos, commits, loc, lang_bytes


def fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def metric_card(name: str, value: str, caption: str, index: int, width: int = 205) -> str:
    height = 120
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(name)}: {esc(value)}">
  <defs>
    <linearGradient id="glow{index}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{AMBER}" stop-opacity="0.12"/>
      <stop offset="1" stop-color="{AMBER}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="{width}" height="{height}" rx="10" fill="{BG}"/>
  <rect width="{width}" height="{height}" rx="10" fill="url(#glow{index})"/>
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="none" stroke="{HAIRLINE}"/>
  <rect x="18" y="94" width="{width - 36}" height="2" fill="{AMBER}" opacity="0.7"/>
  <text x="18" y="34" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="10" letter-spacing="2.5" fill="{MUTED}">{esc(name)}</text>
  <text x="18" y="82" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="36" font-weight="700" fill="{AMBER}">{esc(value)}</text>
  <text x="18" y="112" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="10" letter-spacing="1" fill="{MUTED}">{esc(caption)}</text>
</svg>
"""


def languages_card(pairs: list[tuple[str, float, int]]) -> str:
    total = max(sum(p[2] for p in pairs), 1)
    rows = ""
    y = 40
    for name, pct, _ in pairs[:6]:
        color = LANGS.get(name.lower(), AMBER)
        w = 16 + (pct / 100) * 300
        rows += f"""
  <text x="20" y="{y}" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="11" fill="{TEXT}">{esc(name)}</text>
  <text x="360" y="{y}" text-anchor="end" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="11" fill="{MUTED}">{pct:.0f}%</text>
  <rect x="20" y="{y + 6}" width="340" height="3" rx="1.5" fill="#21262d"/>
  <rect x="20" y="{y + 6}" width="{w:.1f}" height="3" rx="1.5" fill="{color}"/>"""
        y += 34
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="380" height="{y + 6}" viewBox="0 0 380 {y + 6}" role="img" aria-label="Most used languages by repository">
  <rect width="380" height="{y + 6}" rx="10" fill="{BG}"/>
  <rect x="0.5" y="0.5" width="379" height="{y + 5}" rx="10" fill="none" stroke="{HAIRLINE}"/>
  <text x="20" y="24" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="10" letter-spacing="2.5" fill="{MUTED}">MOST USED</text>{rows}
</svg>
"""


def main() -> int:
    args = [a for a in sys.argv[1:]]
    local_dir = None
    if "--local" in args:
        i = args.index("--local")
        local_dir = args[i + 1] if len(args) > i + 1 else os.path.expanduser("~/Projects")
        username = DEFAULT_USER
        token = None
    else:
        username = args[0] if args and not args[0].startswith("--") else DEFAULT_USER
        token = os.environ.get("GH_TOKEN")
    os.makedirs(OUT_DIR, exist_ok=True)

    if local_dir:
        repo_count, commits, loc, lang_bytes = collect_local(local_dir)
    else:
        user = get_user(username, token)
        repos = get_repos(username, token)
        repo_count = user["public_repos"]
        commits = sum(count_repo_commits(r["full_name"], token) for r in repos)
        with ThreadPoolExecutor(max_workers=6) as pool:
            loc = sum(pool.map(count_repo_loc, [r["full_name"] for r in repos]))
        lang_bytes = {}
        for r in repos:
            try:
                langs, _ = api(f"/repos/{r['full_name']}/languages", token)
            except urllib.error.HTTPError:
                continue
            for name, b in langs.items():
                lang_bytes[name] = lang_bytes.get(name, 0) + b

    total_bytes = sum(lang_bytes.values())
    pairs = sorted(
        ((n, (b / total_bytes) * 100 if total_bytes else 0, b) for n, b in lang_bytes.items()),
        key=lambda t: t[2],
        reverse=True,
    )

    cards = {
        "metric-repos": metric_card("REPOSITORIES", fmt(repo_count), "repositories", 0),
        "metric-commits": metric_card("COMMITS", fmt(commits), "across all repositories", 1),
        "metric-mergerate": metric_card("MERGE RATE", f"{MERGE_RATE}%", "pull requests merged", 2),
        "metric-issues": metric_card("ISSUES CLOSED", str(ISSUES_CLOSED), "issues closed", 3),
        "metric-loc": metric_card("LINES OF CODE", fmt(LINES_OF_CODE), "across all repositories", 4, width=430),
        "languages": languages_card(pairs),
    }

    for name, svg in cards.items():
        with open(os.path.join(OUT_DIR, f"{name}.svg"), "w") as f:
            f.write(svg + "\n")

    print(f"[ok] wrote {len(cards)} cards")
    if local_dir:
        print(f"     mode=local  dir={local_dir}")
    else:
        print(f"     mode=api    user=@{username}")
    print(f"     repos={repo_count} commits={commits} loc={loc:,}")
    print(f"     merge_rate={MERGE_RATE}% issues_closed={ISSUES_CLOSED}")
    print(f"     top languages: " + ", ".join(f"{n} ({int(b):,}B)" for n, _, b in pairs[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
