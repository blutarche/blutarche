#!/usr/bin/env python3
"""Render README.md and docs/palette.svg.

Stats come from the GitHub GraphQL API, using $GITHUB_TOKEN or, for local runs,
`gh auth token`. The palette swatch strip is static.

    python3 scripts/build.py
"""

import json
import os
import subprocess
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
README = ROOT / "README.md"
OUT = ROOT / "docs"

LOGIN = "blutarche"
API = "https://api.github.com/graphql"
TZ = ZoneInfo("Asia/Bangkok")

TRACK_URL = "https://song.link/i/1525805459"
TRACK_LINE = "♫ Bury the Light — Casey Edwards ft. Victor Borba"

# Dark-theme tokens from blutils src/styles/tokens.css.
PALETTE = ["#0A0A0C", "#121216", "#34343F", "#8C8C94", "#F2EFEA", "#5A4FC0", "#7C70F5", "#F0A8D0"]
PALETTE_LABEL = "blutarche studio palette: " + ", ".join(PALETTE)
SWATCH, GAP = 12, 4
PALETTE_W = len(PALETTE) * SWATCH + (len(PALETTE) - 1) * GAP


class Stats(NamedTuple):
    contributions: int
    streak: int
    since: int
    releases: int
    today: date


def github_token():
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        raise SystemExit("no GITHUB_TOKEN and `gh auth token` returned nothing")
    return result.stdout.strip()


def graphql(token, query, **variables):
    request = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-readme",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.load(response)
    if body.get("errors"):
        raise SystemExit(f"GitHub GraphQL error: {body['errors']}")
    return body["data"]


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def calendar_query(first_year, last_year):
    # contributionsCollection spans at most a year, so ask for one calendar year per alias. `to` must
    # reach the end of the year: a `to` of "now" is cut at a date boundary and drops today's contributions.
    parts = [
        f'y{year}: contributionsCollection(from: "{year}-01-01T00:00:00Z", to: "{year}-12-31T23:59:59Z") '
        "{ contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } }"
        for year in range(first_year, last_year + 1)
    ]
    return "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }"


REPOS_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        releases(first: 100, orderBy: {field: CREATED_AT, direction: DESC}) {
          pageInfo { hasNextPage }
          nodes { createdAt }
        }
      }
    }
  }
}
"""


def releases_in_year(token, year):
    count, after = 0, None
    while True:
        page = graphql(token, REPOS_QUERY, login=LOGIN, after=after)["user"]["repositories"]
        for repo in page["nodes"]:
            years = [parse_time(r["createdAt"]).astimezone(TZ).year for r in repo["releases"]["nodes"]]
            count += years.count(year)
            if repo["releases"]["pageInfo"]["hasNextPage"] and years and years[-1] == year:
                raise SystemExit(f"{repo['nameWithOwner']} has over 100 releases in {year}")
        if not page["pageInfo"]["hasNextPage"]:
            return count
        after = page["pageInfo"]["endCursor"]


def fetch_stats():
    token = github_token()
    now = datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()

    created = parse_time(graphql(token, "query($login: String!) { user(login: $login) { createdAt } }",
                                 login=LOGIN)["user"]["createdAt"])
    calendars = graphql(token, calendar_query(created.year, now.year), login=LOGIN)["user"]

    contributions, days = 0, {}
    for calendar in calendars.values():
        calendar = calendar["contributionCalendar"]
        contributions += calendar["totalContributions"]
        for week in calendar["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]

    def count(d):
        return days.get(d.isoformat(), 0)

    day = today if count(today) else today - timedelta(days=1)
    streak = 0
    while count(day):
        streak += 1
        day -= timedelta(days=1)

    born = created.astimezone(TZ).date()
    return Stats(contributions, streak, born.year, releases_in_year(token, today.year), today)


def plural(n, word):
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def palette_svg():
    # Each stroke is inset so a swatch stays 12x12; it keeps the near-black and off-white
    # swatches readable on both GitHub themes.
    rects = "\n".join(
        f'<rect x="{i * (SWATCH + GAP) + 0.5:g}" y="0.5" width="{SWATCH - 1}" height="{SWATCH - 1}" '
        f'fill="{hex_}" stroke="rgba(128,128,128,0.4)"/>'
        for i, hex_ in enumerate(PALETTE)
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{PALETTE_W}" height="{SWATCH}" '
        f'viewBox="0 0 {PALETTE_W} {SWATCH}" role="img" aria-label="{PALETTE_LABEL}">\n'
        f"<title>{PALETTE_LABEL}</title>\n"
        f"{rects}\n"
        "</svg>\n"
    )


def readme(stats):
    parts = [f"{plural(stats.contributions, 'contribution')} since {stats.since}"]
    if stats.streak >= 2:
        parts.append(f"{stats.streak}-day streak")
    parts.append(f"{plural(stats.releases, 'release')} in {stats.today.year}")
    return "\n".join([
        "<!-- Generated by scripts/build.py -->",
        f"[{TRACK_LINE}]({TRACK_URL})",
        "",
        " · ".join(parts),
        "",
        f'<img src="docs/palette.svg" width="{PALETTE_W}" alt="{PALETTE_LABEL}">',
        "",
    ])


def main():
    stats = fetch_stats()
    print(f"contributions    {stats.contributions:,} since {stats.since}")
    print(f"current streak   {stats.streak}")
    print(f"releases {stats.today.year}    {stats.releases}")

    OUT.mkdir(exist_ok=True)
    (OUT / "palette.svg").write_text(palette_svg())
    README.write_text(readme(stats))
    print("docs/palette.svg\nREADME.md")


if __name__ == "__main__":
    main()
