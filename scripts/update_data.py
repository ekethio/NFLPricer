"""Refresh the site's data files from nflverse.

Runs on GitHub's scheduler. Uses only the Python standard library.
Writes site/data/schedule.json, injuries.json, efficiency.json and meta.json.
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


def flt(v):
    try:
        return float(v)
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
    # nflverse spread_line is positive when the home team is favored; the site uses the
    # opposite sign (negative = home favored), so it is flipped here.
    market_spread = lambda r: None if flt(r.get("spread_line")) is None else -flt(r["spread_line"]) + 0.0
    save("schedule.json", {
        "season": season,
        "current_week": current_week,
        "games": [[int(r["week"]), r["gameday"], r["gametime"], r["away_team"], r["home_team"],
                   num(r["away_score"]), num(r["home_score"]), r.get("location", ""),
                   market_spread(r), flt(r.get("total_line"))]
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

# ---------- play-by-play: 2026 efficiency + players who left a game ----------
left = {"week": None, "teams": {}}
eff = {"season": season, "weeks": [], "league": {}, "teams": {}}
try:
    pbp = fetch_csv(f"{BASE}/pbp/play_by_play_{season}.csv.gz", gz=True)
    pbp = [r for r in pbp if r["season_type"] == "REG"]

    # EPA/play and yards/play, offense and defense (pass + run plays only)
    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    agg = {}
    tot = [0.0, 0.0, 0]
    for r in pbp:
        if r["play_type"] not in ("pass", "run"):
            continue
        epa, yds = fnum(r["epa"]), fnum(r["yards_gained"]) or 0.0
        if epa is None or not r["posteam"] or not r["defteam"]:
            continue
        for team, side in ((r["posteam"], "o"), (r["defteam"], "d")):
            a = agg.setdefault(team, {"o": [0.0, 0.0, 0], "d": [0.0, 0.0, 0]})[side]
            a[0] += epa; a[1] += yds; a[2] += 1
        tot[0] += epa; tot[1] += yds; tot[2] += 1
    for team, a in agg.items():
        eff["teams"][team] = {
            "o_epa": round(a["o"][0] / a["o"][2], 3), "o_ypp": round(a["o"][1] / a["o"][2], 2), "o_plays": a["o"][2],
            "d_epa": round(a["d"][0] / a["d"][2], 3), "d_ypp": round(a["d"][1] / a["d"][2], 2), "d_plays": a["d"][2],
        }
    if tot[2]:
        eff["league"] = {"epa": round(tot[0] / tot[2], 4), "ypp": round(tot[1] / tot[2], 3)}

    # Actual points per drive and drives per game (all points, every drive)
    drives, final = {}, {}
    for r in pbp:
        g = r["game_id"]
        if r["posteam"] and r["fixed_drive"] not in ("", "NA"):
            drives.setdefault((g, r["posteam"]), set()).add(r["fixed_drive"])
        pid = fnum(r["play_id"]) or 0
        if g not in final or pid > final[g][0]:
            final[g] = (pid, r["home_team"], r["away_team"], fnum(r["total_home_score"]) or 0, fnum(r["total_away_score"]) or 0)
    pts = n_drv = team_games = 0
    for g, (_, home, away, hs, as_) in final.items():
        for team, p in ((home, hs), (away, as_)):
            d = len(drives.get((g, team), ()))
            if d:
                pts += p; n_drv += d; team_games += 1
    if n_drv:
        eff["league"].update({
            "ppd": round(pts / n_drv, 4),
            "drives_per_team_game": round(n_drv / team_games, 3),
            "points_per_game": round(2 * pts / team_games, 2),
            "games": team_games // 2,
        })
    eff["weeks"] = sorted({int(r["week"]) for r in pbp})

    last = max(eff["weeks"])
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
    notes.append(f"Play-by-play update failed: {e}")
    left["error"] = str(e)
    eff["error"] = str(e)

save("efficiency.json", eff)
save("injuries.json", {"report": report, "left_game": left})
save("meta.json", {
    "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "season": season, "current_week": current_week, "notes": notes,
})
print("done", season, current_week, notes)
