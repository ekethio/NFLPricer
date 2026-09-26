# NFL Pricer — Project Notes

Context for Claude Code. The repository files are the source of truth; this file explains the model, the data, and how the owner likes to work.

## What this is

A public NFL game-pricing tool. Visitors adjust team ratings and injury inputs in their own browser, and the site prices every game: mean spread and total, then median spread and total.

The owner is an experienced sports bettor (profitable on NBA/WNBA sides and totals, mostly on early lines) with deep NFL knowledge. Their pricing approach blends three signals: market ratings, aggregated player-level ratings, and actual performance. The qualitative layer is theirs; the tooling's job is clean data, correct math, and honest statistical checks.

## Hosting and repository

- **Repository:** `ekethio/NFLPricer` on GitHub, public. Local clone at `Documents\NFL-pricer` on the owner's Windows PC (Git and Python 3.12 installed via winget).
- **Hosting:** GitHub Pages, with the source set to **GitHub Actions**. No Vercel/Render; not needed unless a server is added later (accounts, secret API keys, paid data feeds).
- **Layout:**
  ```
  .github/workflows/deploy.yml   # refresh data + publish site
  scripts/update_data.py         # pulls nflverse data, writes site/data/*.json
  site/index.html                # the whole app (HTML/CSS/JS, no build step)
  site/data/ratings.json         # owner's starting ratings (committed)
  site/data/reference.json       # 2025 league reference numbers (committed)
  site/data/schedule.json        # generated (gitignored)
  site/data/injuries.json        # generated (gitignored)
  site/data/efficiency.json      # generated (gitignored)
  site/data/meta.json            # generated (gitignored)
  ```
- **Workflow:** runs on push to `main`, every 4 hours (`cron: "0 */4 * * *"`), and manually via "Run workflow." It runs the update script, then uploads `site/` as the Pages artifact and deploys. Generated JSON is not committed back; it's built fresh on every run.
- **Local checks:** `python scripts/update_data.py`, then serve `site/` (`python -m http.server --directory site`) and open it. Node isn't installed; check the page's script in a browser instead of `node --check`.

## Data pipeline (`scripts/update_data.py`)

Standard library only (no pandas), so the Actions run needs no installs. Every section is wrapped so one failed download doesn't block publishing; failures are recorded in `meta.json` → `notes`.

nflverse release URLs (base `https://github.com/nflverse/nflverse-data/releases/download`):

| Data | Path |
|---|---|
| Schedule and scores | `schedules/games.csv` |
| Play-by-play | `pbp/play_by_play_{season}.csv.gz` |
| Official injury report | `injuries/injuries_{season}.csv` |
| Weekly player stats | `stats_player/stats_player_week_{season}.csv` (not `player_stats/...`, which stops at 2024) |
| Weekly team stats | `stats_team/stats_team_week_{season}.csv` |
| Snap counts | `snap_counts/snap_counts_{season}.csv` (exists from 2012; 2012 file is effectively empty) |
| FTN charting | `ftn_charting/ftn_charting_{season}.csv` |

What the script produces:
- **schedule.json:** season, current week (lowest week with an unplayed game), and every regular-season game as `[week, gameday, gametime, away, home, away_score, home_score, location, market_spread, market_total]`. `location` is `"Home"` or `"Neutral"`. Market lines come from nflverse's `spread_line` / `total_line` (not a named sportsbook; current line for upcoming games, closing line for played ones; usually posted about a week ahead). nflverse's `spread_line` is positive when home is favored; the script flips it to the site's convention (negative = home favored).
- **injuries.json:** the latest week of the official report per team (`[name, pos, injury, practice_status, game_status]`), falling back to the practice-report injury field when the game-report field is blank. Plus `left_game`: players whose play-by-play shows "was injured during the play" with no later "has returned to the game," for the last played week.
- **efficiency.json:** per team, offense and defense EPA/play and yards/play (pass and run plays with non-null EPA), plus league EPA, YPP, points per drive, drives per team-game, points per game, and game count.
- **meta.json:** update time, season, current week, notes.

Data gotchas learned the hard way:
- For final scores, use the play-by-play fields `total_home_score` / `total_away_score` from each game's last play. `games.csv` has lagged behind at times.
- Count drives with `fixed_drive` (nflverse's recommended drive ID), distinct per `game_id` + `posteam`.
- League points per drive × drives per team × 2 equals actual points per game exactly. That's a useful sanity check.
- Name matching across sources: strip suffixes (Jr., Sr., II, III, IV, V). Play-by-play uses `F.Lastname`; snap counts use full names; handle middle names (e.g., "Levi Drake Rodriguez" → `L.Rodriguez`).
- **The in-game injury parser has false negatives.** Zay Flowers left Week 1 of 2026 with a hamstring injury and never returned, confirmed by ESPN and news, but that game's play-by-play never used the phrase. Always label this list as incomplete.
- **ESPN's injury page** is JavaScript-rendered and can't be fetched directly. Its underlying JSON feed is unofficial, may break, and may conflict with ESPN's terms. The site deliberately doesn't use it; users set their own injury probabilities. If richer injury data is needed later, use a paid feed (Sportradar, SportsDataIO, MySportsFeeds), which requires a server to hide the API key.
- There's no public "true pressure" data (hurries are PFF-proprietary). The public proxy is (sacks + QB hits) ÷ dropbacks. Per-player dropback participation lags a season on nflverse, so player pressure rate uses total defensive snaps as the denominator, which understates it.

## The pricing model (`site/index.html`)

Ratings are per team, entered as absolute values:
- **ORTG:** points per drive against a league-average opponent. It includes all scoring (defensive and return touchdowns too), i.e. what the team would average per drive against an average team over infinite games. Internal key: `oppd`.
- **DRTG:** points per drive allowed on the same basis. Internal key: `dppd`.
- **Drives:** pace as a differential vs. league average (+1 = one more drive per game than average). Drives reflect both tempo and efficiency (bad offenses and good defenses both create more drives); that's intended.
- **Home field:** points per team; blank means the default (1.75).

Do **not** rename the internal keys `oppd`/`dppd`, or change what they store. Users' saved browser data depends on them. They hold **absolute** points per drive; since 2026-09-26 the Ratings tab **shows and edits them as differences from `league_avg_ppd`** (a setting, like `league_avg_drives`; in `ratings.json` and user settings). Saved data from before that has no `league_avg_ppd`; `migrate()` sets it to the mean of that data's 64 values, which reproduces its old prices exactly. Changing the league average in Settings shifts every `oppd`/`dppd` by the same amount, so differences stay put. The starting ratings are normalized: ORTG and DRTG differences each sum to exactly 0, so average Rating is 0.

Game math (additive, the owner's naive starting point):
```
LeagueAvgPPD = settings.league_avg_ppd (starting value 2.133). Before 2026-09-26 it was the live mean of all 64 values.
GameDrives   = LeagueAvgDrives + Drives_home + Drives_away      (summed, not averaged)
HomePPD      = ORTG_home + DRTG_away − LeagueAvgPPD
AwayPPD      = ORTG_away + DRTG_home − LeagueAvgPPD
HomePts      = HomePPD × GameDrives + HFA/2
AwayPts      = AwayPPD × GameDrives − HFA/2
MeanSpread   = AwayPts − HomePts        (negative = home favored; shown as "BUF −3.5")
MeanTotal    = HomePts + AwayPts
```
- **Home field moves the spread only, never the total.** It's split half to each side. An earlier version added all of it to the home team, which inflated every total; the owner caught it.
- **Drives are summed:** two +1 pace teams produce +2 drives, not +1.
- **"Average team"** is a selectable opponent: ORTG = DRTG = LeagueAvgPPD, Drives 0.
- **Neutral field** sets home field to 0.
- **Rating column (view only, not used in pricing):** (ORTG − DRTG) × LeagueAvgDrives.

Injuries:
- Each player has a value (points) and a chance of missing the game. Team adjustment = Σ value × chance.
- Setting `injury_mode`: `"both"` (default) takes the adjustment off the injured team's points, moving spread and total; `"spread"` shifts it half from the injured team to the opponent, so the total stays fixed.
- Adding a player from the official report pre-fills the chance from game status: Out 100%, Doubtful 75%, Questionable 25%. These are editable defaults, not a model.

**Mean → median conversion is half done** (`toMedian` at the top of the script):
- **Totals:** median = mean − 0.85 (`TOTAL_MEDIAN_SHIFT`), chosen by the owner's call on 2026-09-26. Basis: 2015–2025 regular seasons, 2,895 games, closing total as the expected total; median minus mean of (actual − expected) was −0.85 pooled, negative in 9 of 11 seasons (range −1.79 to +0.92). A linear shift in the total had slope ≈ 0 (−0.85 at 40, −0.88 at 50), so it's flat. Pre-registered held-out test (fit 2015–2022, test 2023–2025) did not show a lower absolute error for the shifted line (10.15 vs 10.12, within noise) because 2024–2025 scoring ran ~1 point above closing lines, a level miss the shift doesn't address. 2025 alone: mean 46.03, median 45.0.
- **Spreads:** not converted (median = mean). The owner has their own method; don't invent one. When spreads are done, set `MEDIAN_READY = true` to hide the on-page notice.

User data lives in `localStorage` under `nflpricer.v1` (`{ratings, settings, injuries}`), with download/load backup buttons. Nothing is sent anywhere. A returning visitor's saved ratings override `ratings.json`, so after changing starting ratings, the owner clicks "Reset ratings to the site's starting values."

Tabs: This week (all games in a selected week, priced, with market spread/total and "Model likes": market spread − model median spread, and model median total − market total), Matchup, Injuries, Ratings (editable, sortable, with 2026 EPA/play and YPP for offense and defense, an Averages panel, and an average row), Settings.

The **Averages panel** compares the ratings' implied points per drive, drives per team, and points per game (average vs. average team, no home field) with actual 2026 so far and the full 2025 season. It also flags any gap between average ORTG and average DRTG (they should match; pricing uses the midpoint). As of 2026-09-26: the starting ratings are the owner's edited set (downloaded from their browser) with every ORTG and DRTG lowered by 0.019, which cuts totals ~0.4 (0.38–0.44 by pace) and leaves spreads unchanged. They imply 2.133 points per drive and 45.6 points per game, vs. 2026 actual 2.091 / 45.1 (Weeks 1–3, 33 games) and 2025 actual 2.148 / 46.0.

## Findings from analysis (keep these in mind)

All tests were in-sample on nflverse data unless noted. The owner prefers **pre-registration**: state the hypothesis and exact test before looking at data; treat anything found while exploring as a new hypothesis needing its own test, ideally on a different time window.

- **Adjusting a player's baseline for opponent:** additive (baseline + opponent's +/- vs. league) beat multiplicative on 2025 QB pass and rush yards. Blending didn't help; the two methods' errors correlated at 0.94–0.998. For rush yards, multiplicative was less biased but noisier.
- **Mean vs. median:** single-season player skew is mostly noise. Year-over-year skew correlation for the same QB was 0.08 (1999–2024, 600 pairs). For pass yards, assume median ≈ mean; no global conversion beat that out of sample. Across 2013–2025 QB games with 30+ snaps: pass yards mean 238.6 / median 236.0; rush yards mean 15.6 / median 9.0.
- **Wins vs. losses, same QB (2022–2025, top 20 QBs by snaps):** pass yards average +12.2 in wins, but the direction depends on offensive identity (run-first teams like DET/BUF/BAL throw more in losses). 16 of 20 QBs ran for more in losses.
- **Ford Field and AT&T Stadium (2021–2025), pre-registered:** games there average +68.8 combined yards and +8.5 points vs. other domes (p < 0.0001, consistent every season). With roster controls: DET and DAL have the largest home offensive boosts in the league (points #1 and #2) and the worst home defensive yardage reversals (DAL #1, DET #4). Visiting offenses gain about +20.8 yards over a roster-adjusted expectation (p = 0.026), roughly two-thirds of the home boost. That's consistent with the owner's theory: the surface helps everyone, and familiarity helps the home team more. The defensive effect weakens on points (DET's defense bends more at home without allowing more points).
- **Comparing props to last season's median breaks when a player changed teams** (e.g., DJ Moore, CHI in 2025 → BUF in 2026). Check team changes before any prior-season comparison.

## Other tools (claude.ai, not in this repo)

- **NFL Pricing Desk:** a published claude.ai artifact with tabs for Ratings, 2025 QB props (30-snap floor, prop % over checker, game logs), 2025 defense vs. QB, 2026 efficiency, 2026 pressure (team and players above 5%, 20+ snaps), and injuries (ESPN paste, official report, in-game). Its data lives in the artifact's database and is only refreshed when Claude updates it in chat. Some of these tabs may be worth porting into the site.
- Earlier standalone HTML files (QB props, efficiency, pressure, injury tracker, team ratings sheet) are superseded by the pricing desk and this site.

## Open items

1. **Mean → median conversion for spreads.** Totals are done (flat −0.85). Get the spread method from the owner, implement it in `toMedian`, and add tests.
2. Decide whether injuries should move totals by default (currently yes).
3. Consider porting the QB props, defense vs. QB, and pressure views into the site, refreshed by the update script.
4. Keep a weekly archive (injury history, ratings snapshots) if timing of news vs. line moves matters.
5. If paid injury data or accounts are wanted: move to a host with server functions (e.g., Vercel), keeping API keys server-side.
6. Add basic tests for the pricing math (home field leaves totals unchanged; summed drives; average vs. average equals league points per game).

## How the owner likes to work

- Honest statistics: flag small samples, in-sample fits, and confounders. Don't overstate findings or present estimates as facts.
- Never fabricate data. If something isn't available (PFF grades, charted pressure), say so and offer a labeled proxy.
- State assumptions explicitly rather than silently choosing (sign conventions, which book's line, week labels).
- Explain setup steps plainly; the owner is new to GitHub and hosting.
