"""Refresh the site's data files from nflverse.

Runs on GitHub's scheduler. Uses only the Python standard library.
Writes site/data/schedule.json, site/data/injuries.json and site/data/meta.json.
If a download fails, that file is written with an error note so the site still publishes.
"""
import csv, gzip, io, json, re, urllib.request, datetime, os

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
OUT = os.path.join(os.path.dirname(__file__), "..", "site", "data")
os.makedirs(OUT, exist_ok=True)


def fetch_csv(url, gz=False):
    req = urllib.request.Request(url, headers={"User-Agent": "nfl-pricer-updater"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    if gz:
        raw = gzip.decompress(raw)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def save(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, separators=(",", ":"))


notes = []

# ---------- schedule ----------
season, current_week, games = None, None, []
try:
    rows = fetch_csv(f"{BASE}/schedules/games.csv")
    reg = [r for r in rows if r["game_type"] == "REG"]
    season = max(int(r["season"]) for r in reg)
    games = [r for r in reg if int(r["season"]) == season]
    unplayed = [int(r["week"]) for r in games if r["home_score"] in ("", "NA")]
    current_week = min(unplayed) if unplayed else max(int(r["week"]) for r in games)
    save("schedule.json", {
        "season": season,
        "current_week": current_week,
        "games": [[int(r["week"]), r["gameday"], r["gametime"], r["away_team"], r["home_team"],
                   num(r["away_score"]), num(r["home_score"]), r.get("location", "")]
                  for r in sorted(games, key=lambda r: (int(r["week"]), r["gameday"], r["gametime"]))],
    })
except Exception as e:
    notes.append(f"Schedule update failed: {e}")
    save("schedule.json", {"season": None, "current_week": None, "games": [], "error": str(e)})

# ---------- official injury report (latest week) ----------
try:
    rows = fetch_csv(f"{BASE}/injuries/injuries_{season}.csv")
    rows = [r for r in rows if r["season_type"] == "REG"]
    latest = max(int(r["week"]) for r in rows)
    teams = {}
    for r in sorted((r for r in rows if int(r["week"]) == latest), key=lambda r: r["full_name"]):
        injury = " / ".join(x for x in (r["report_primary_injury"], r["report_secondary_injury"]) if x and x != "NA")
        if not injury:
            injury = " / ".join(x for x in (r["practice_primary_injury"], r["practice_secondary_injury"]) if x and x != "NA")
        clean = lambda v: "" if v in (None, "NA") else v
        teams.setdefault(r["team"], []).append(
            [r["full_name"], clean(r["position"]), injury, clean(r["practice_status"]), clean(r["report_status"])])
    report = {"week": latest, "teams": teams}
except Exception as e:
    notes.append(f"Injury report update failed: {e}")
    report = {"week": None, "teams": {}, "error": str(e)}

# ---------- players who left a game and didn't return (last played week) ----------
left = {"week": None, "teams": {}}
try:
    pbp = fetch_csv(f"{BASE}/pbp/play_by_play_{season}.csv.gz", gz=True)
    pbp = [r for r in pbp if r["season_type"] == "REG"]
    last = max(int(r["week"]) for r in pbp)
    hurt = re.compile(r"([A-Z]{2,3})-(\d+)-([A-Za-z.'\-]+) was injured during the play")
    back = re.compile(r"([A-Z]{2,3})-(\d+)-([A-Za-z.'\-]+) has returned to the game")
    seen = {}
    for r in sorted((r for r in pbp if int(r["week"]) == last), key=lambda r: (r["game_id"], float(r["play_id"] or 0))):
        for m in hurt.finditer(r["desc"] or ""):
            seen.setdefault((m.group(1), m.group(3)), [False, False])[0] = True
        for m in back.finditer(r["desc"] or ""):
            seen.setdefault((m.group(1), m.group(3)), [False, False])[1] = True
    for (team, name), (h, b) in seen.items():
        if h and not b:
            left["teams"].setdefault(team, []).append(name)
    left["week"] = last
except Exception as e:
    notes.append(f"In-game injury update failed: {e}")
    left["error"] = str(e)

save("injuries.json", {"report": report, "left_game": left})
save("meta.json", {
    "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "season": season, "current_week": current_week, "notes": notes,
})
print("done", season, current_week, notes)
