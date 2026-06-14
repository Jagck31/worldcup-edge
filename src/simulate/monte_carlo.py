from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import csv
import random
from typing import Callable, Mapping

import pandas as pd


GROUPS_2026 = tuple("ABCDEFGHIJKL")
THIRD_PLACE_SLOTS = ("1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L")

ROUND_OF_32_FIXED = [
    ("M73", "2A", "2B"),
    ("M74", "1E", "3A/B/C/D/F"),
    ("M75", "1F", "2C"),
    ("M76", "1C", "2F"),
    ("M77", "1I", "3C/D/F/G/H"),
    ("M78", "2E", "2I"),
    ("M79", "1A", "3C/E/F/H/I"),
    ("M80", "1L", "3E/H/I/J/K"),
    ("M81", "1D", "3B/E/F/I/J"),
    ("M82", "1G", "3A/E/H/I/J"),
    ("M83", "2K", "2L"),
    ("M84", "1H", "2J"),
    ("M85", "1B", "3E/F/G/I/J"),
    ("M86", "1J", "2H"),
    ("M87", "1K", "3D/E/I/J/L"),
    ("M88", "2D", "2G"),
]

# Knockout pairings after the Round of 32, keyed by stage. Each tuple is
# (match_id, source_match_a, source_match_b) where the sources are the match ids
# whose winners meet. NOTE: verify against the official 2026 match schedule before
# trusting bracket-dependent markets — this is the published structure as of build
# time and is labelled DEMO in the dashboard until confirmed.
KNOCKOUT_BRACKET: dict[str, list[tuple[str, str, str]]] = {
    "R16": [
        ("M89", "M74", "M77"),
        ("M90", "M73", "M75"),
        ("M91", "M76", "M78"),
        ("M92", "M79", "M80"),
        ("M93", "M83", "M84"),
        ("M94", "M81", "M82"),
        ("M95", "M86", "M88"),
        ("M96", "M85", "M87"),
    ],
    "QF": [
        ("M97", "M89", "M90"),
        ("M98", "M93", "M94"),
        ("M99", "M91", "M92"),
        ("M100", "M95", "M96"),
    ],
    "SF": [
        ("M101", "M97", "M98"),
        ("M102", "M99", "M100"),
    ],
    "F": [
        ("M104", "M101", "M102"),
    ],
}

# Labels for the team set produced by winning each stage (last-N still alive).
STAGE_OUTPUT_LABEL = {"R32": "last_16", "R16": "last_8", "QF": "last_4", "SF": "finalist", "F": "champion"}


@dataclass(frozen=True)
class GroupStanding:
    group: str
    team: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    fair_play_points: int = 0

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


@dataclass(frozen=True)
class QualifiedTeam:
    group: str
    team: str
    group_rank: int
    standing: GroupStanding


def rank_group(
    standings: list[GroupStanding],
    results: pd.DataFrame | None = None,
    rng: random.Random | None = None,
) -> list[GroupStanding]:
    """Rank a group using the FIFA 2026 tiebreaker order.

    1. Overall points, then goal difference, then goals for.
    2. Among teams still level, head-to-head points / GD / goals-for computed
       from ``results`` (the group's match results).
    3. Fair-play points.
    4. Drawing of lots: a random pick from ``rng`` if provided, otherwise the
       team name for deterministic output.

    ``results`` and ``rng`` are optional so unit tests can rank on the overall
    criteria alone, but the simulator always supplies both.
    """
    overall = sorted(
        standings,
        key=lambda item: (-item.points, -item.goal_difference, -item.goals_for),
    )
    ranked: list[GroupStanding] = []
    start = 0
    while start < len(overall):
        end = start + 1
        while (
            end < len(overall)
            and overall[end].points == overall[start].points
            and overall[end].goal_difference == overall[start].goal_difference
            and overall[end].goals_for == overall[start].goals_for
        ):
            end += 1
        cluster = overall[start:end]
        ranked.extend(cluster if len(cluster) == 1 else _break_level_tie(cluster, results, rng))
        start = end
    return ranked


def _head_to_head_table(
    teams: list[str], results: pd.DataFrame | None
) -> dict[str, tuple[int, int, int]]:
    """Return {team: (points, goal_difference, goals_for)} over matches among ``teams`` only."""
    tally = {team: {"points": 0, "gf": 0, "ga": 0} for team in teams}
    if results is None or results.empty:
        return {team: (0, 0, 0) for team in teams}
    teamset = set(teams)
    for _, row in results.iterrows():
        home, away = str(row["home_team"]), str(row["away_team"])
        if home not in teamset or away not in teamset:
            continue
        home_score, away_score = int(row["home_score"]), int(row["away_score"])
        tally[home]["gf"] += home_score
        tally[home]["ga"] += away_score
        tally[away]["gf"] += away_score
        tally[away]["ga"] += home_score
        if home_score > away_score:
            tally[home]["points"] += 3
        elif home_score < away_score:
            tally[away]["points"] += 3
        else:
            tally[home]["points"] += 1
            tally[away]["points"] += 1
    return {team: (vals["points"], vals["gf"] - vals["ga"], vals["gf"]) for team, vals in tally.items()}


def _break_level_tie(
    cluster: list[GroupStanding],
    results: pd.DataFrame | None,
    rng: random.Random | None,
) -> list[GroupStanding]:
    h2h = _head_to_head_table([item.team for item in cluster], results)
    lots = {item.team: (rng.random() if rng is not None else 0.0) for item in cluster}

    def key(item: GroupStanding) -> tuple:
        h2h_points, h2h_gd, h2h_gf = h2h[item.team]
        return (-h2h_points, -h2h_gd, -h2h_gf, item.fair_play_points, lots[item.team], item.team)

    return sorted(cluster, key=key)


def select_knockout_qualifiers(groups: Mapping[str, list[GroupStanding]]) -> list[QualifiedTeam]:
    if set(groups.keys()) != set(GROUPS_2026):
        missing = set(GROUPS_2026) - set(groups.keys())
        extra = set(groups.keys()) - set(GROUPS_2026)
        raise ValueError(f"Expected groups A-L, missing={sorted(missing)}, extra={sorted(extra)}")

    qualifiers: list[QualifiedTeam] = []
    thirds: list[QualifiedTeam] = []
    for group in GROUPS_2026:
        ranked = rank_group(list(groups[group]))
        if len(ranked) != 4:
            raise ValueError(f"Group {group} must contain four teams")
        qualifiers.append(QualifiedTeam(group, ranked[0].team, 1, ranked[0]))
        qualifiers.append(QualifiedTeam(group, ranked[1].team, 2, ranked[1]))
        thirds.append(QualifiedTeam(group, ranked[2].team, 3, ranked[2]))

    best_thirds = sorted(
        thirds,
        key=lambda item: (
            -item.standing.points,
            -item.standing.goal_difference,
            -item.standing.goals_for,
            -item.standing.wins,
            item.standing.fair_play_points,
            item.group,
        ),
    )[:8]
    return qualifiers + best_thirds


def load_third_place_assignment_table(path: Path) -> dict[str, dict[str, str]]:
    """Load official Annex C mappings keyed by sorted eight-group combinations.

    The CSV must include columns: qualified_groups, 1A, 1B, 1D, 1E, 1G, 1I, 1K, 1L.
    qualified_groups is the eight-letter string, for example CDEFGHIJ.
    Values are source group letters without the "3" prefix.
    """
    table: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"qualified_groups", *THIRD_PLACE_SLOTS}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Third-place assignment table missing columns: {sorted(missing)}")
        for row in reader:
            key = "".join(sorted(str(row["qualified_groups"]).strip().upper()))
            table[key] = {slot: str(row[slot]).strip().upper() for slot in THIRD_PLACE_SLOTS}
    return table


def assign_third_place_slots(
    qualified_third_groups: list[str],
    assignment_table: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    key = "".join(sorted(group.upper() for group in qualified_third_groups))
    if key not in assignment_table:
        raise KeyError(
            "Missing official third-place assignment for groups "
            f"{key}. Load the FIFA Annex C table before simulating bracket-dependent markets."
        )
    return {slot: f"3{group}" for slot, group in assignment_table[key].items()}


def build_group_standings(group: str, teams: list[str], results: pd.DataFrame) -> list[GroupStanding]:
    records = {
        team: {"played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0}
        for team in teams
    }
    for _, row in results.iterrows():
        home = str(row["home_team"])
        away = str(row["away_team"])
        if home not in records or away not in records:
            continue
        home_score = int(row["home_score"])
        away_score = int(row["away_score"])
        _add_result(records[home], home_score, away_score)
        _add_result(records[away], away_score, home_score)
    return [GroupStanding(group=group, team=team, **values) for team, values in records.items()]


def _add_result(record: dict[str, int], goals_for: int, goals_against: int) -> None:
    record["played"] += 1
    record["goals_for"] += goals_for
    record["goals_against"] += goals_against
    if goals_for > goals_against:
        record["wins"] += 1
    elif goals_for == goals_against:
        record["draws"] += 1
    else:
        record["losses"] += 1


ThirdPlaceResolver = Callable[[list[str]], dict[str, str]]


def deterministic_third_place_resolver(qualified_third_groups: list[str]) -> dict[str, str]:
    """DEMO resolver: map the 8 qualified third-place groups to winner slots in order.

    This is NOT the official FIFA Annex C mapping. It exists only so the dashboard can
    show bracket-dependent probabilities for a demo draw. For real markets, build a
    resolver from ``load_third_place_assignment_table`` + ``assign_third_place_slots``.
    """
    ordered_groups = sorted(group.upper() for group in qualified_third_groups)
    return {slot: group for slot, group in zip(THIRD_PLACE_SLOTS, ordered_groups)}


def official_third_place_resolver(table: Mapping[str, Mapping[str, str]]) -> ThirdPlaceResolver:
    """Build a resolver backed by an official Annex C assignment table."""

    def resolver(qualified_third_groups: list[str]) -> dict[str, str]:
        assigned = assign_third_place_slots(qualified_third_groups, table)
        return {slot: value[-1] for slot, value in assigned.items()}

    return resolver


@dataclass
class TournamentOutcome:
    group_winners: dict[str, str]
    group_runners_up: dict[str, str]
    group_rankings: dict[str, list[GroupStanding]]
    qualified_third_groups: list[str]
    reached: dict[str, set[str]]  # stage label -> teams alive at that depth
    champion: str
    bracket: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # round -> [(a, b, winner)]


# Stage labels in tournament order, from group exit through the trophy.
STAGE_ORDER = ["advanced", "last_16", "last_8", "last_4", "finalist", "champion"]


def _is_third_placeholder(slot: str) -> bool:
    return slot.startswith("3") and "/" in slot


class TournamentSimulator:
    def __init__(
        self,
        scoreline_sampler: Callable[[str, str], tuple[int, int]],
        knockout_win_probability: Callable[[str, str], float],
        third_place_assignment_table: Mapping[str, Mapping[str, str]] | None = None,
        third_place_resolver: ThirdPlaceResolver | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.scoreline_sampler = scoreline_sampler
        self.knockout_win_probability = knockout_win_probability
        self.third_place_assignment_table = third_place_assignment_table
        if third_place_resolver is not None:
            self.third_place_resolver = third_place_resolver
        elif third_place_assignment_table is not None:
            self.third_place_resolver = official_third_place_resolver(third_place_assignment_table)
        else:
            self.third_place_resolver = deterministic_third_place_resolver
        self.rng = rng or random.Random()
        self.last_bracket: dict[str, list[tuple[str, str, str]]] = {}

    def _simulate_group_results(self, teams: list[str], fixtures: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for _, row in fixtures.iterrows():
            home = str(row["home_team"])
            away = str(row["away_team"])
            if not pd.isna(row.get("home_score")) and not pd.isna(row.get("away_score")):
                home_score = int(row["home_score"])
                away_score = int(row["away_score"])
            else:
                home_score, away_score = self.scoreline_sampler(home, away)
            rows.append(
                {"home_team": home, "away_team": away, "home_score": home_score, "away_score": away_score}
            )
        return pd.DataFrame(rows)

    def simulate_group_once(self, group: str, teams: list[str], fixtures: pd.DataFrame) -> list[GroupStanding]:
        return build_group_standings(group, teams, self._simulate_group_results(teams, fixtures))

    def rank_simulated_group(self, group: str, teams: list[str], fixtures: pd.DataFrame) -> list[GroupStanding]:
        results = self._simulate_group_results(teams, fixtures)
        standings = build_group_standings(group, teams, results)
        return rank_group(standings, results=results, rng=self.rng)

    def choose_knockout_winner(self, team_a: str, team_b: str) -> str:
        probability_a = self.knockout_win_probability(team_a, team_b)
        return team_a if self.rng.random() < probability_a else team_b

    def simulate_once(self, groups: Mapping[str, Mapping[str, object]]) -> TournamentOutcome:
        """Run one full tournament: 12 groups -> R32 -> R16 -> QF -> SF -> Final.

        ``groups`` maps each group letter A-L to ``{"teams": [...4 teams...],
        "fixtures": DataFrame}``. Locked results in ``fixtures`` (non-null scores) are
        respected; remaining matches are sampled from ``scoreline_sampler``.
        """
        if set(groups.keys()) != set(GROUPS_2026):
            raise ValueError("simulate_once requires exactly groups A-L")

        winners: dict[str, str] = {}
        runners: dict[str, str] = {}
        rankings: dict[str, list[GroupStanding]] = {}
        thirds: list[tuple[str, GroupStanding]] = []
        for group in GROUPS_2026:
            spec = groups[group]
            ranked = self.rank_simulated_group(group, list(spec["teams"]), spec["fixtures"])  # type: ignore[index]
            rankings[group] = ranked
            winners[group] = ranked[0].team
            runners[group] = ranked[1].team
            thirds.append((group, ranked[2]))

        best_thirds = sorted(
            thirds,
            key=lambda gt: (
                -gt[1].points,
                -gt[1].goal_difference,
                -gt[1].goals_for,
                self.rng.random(),
            ),
        )[:8]
        third_team_by_group = {group: standing.team for group, standing in best_thirds}
        qualified_third_groups = sorted(third_team_by_group.keys())
        assignment = self.third_place_resolver(qualified_third_groups)

        advanced = set(winners.values()) | set(runners.values()) | set(third_team_by_group.values())

        def resolve_concrete(slot: str) -> str:
            seed, group = slot[0], slot[1]
            return winners[group] if seed == "1" else runners[group]

        # Round of 32.
        bracket: dict[str, list[tuple[str, str, str]]] = {}
        current: dict[str, str] = {}
        r32_matches: list[tuple[str, str, str]] = []
        for match_id, slot_a, slot_b in ROUND_OF_32_FIXED:
            team_a = resolve_concrete(slot_a)
            if _is_third_placeholder(slot_b):
                source_group = assignment[slot_a]
                team_b = third_team_by_group[source_group]
            else:
                team_b = resolve_concrete(slot_b)
            winner = self.choose_knockout_winner(team_a, team_b)
            current[match_id] = winner
            r32_matches.append((team_a, team_b, winner))
        bracket["R32"] = r32_matches

        reached: dict[str, set[str]] = {"advanced": advanced, "last_16": set(current.values())}
        for stage in ("R16", "QF", "SF", "F"):
            nxt: dict[str, str] = {}
            stage_matches: list[tuple[str, str, str]] = []
            for match_id, source_a, source_b in KNOCKOUT_BRACKET[stage]:
                team_a, team_b = current[source_a], current[source_b]
                winner = self.choose_knockout_winner(team_a, team_b)
                nxt[match_id] = winner
                stage_matches.append((team_a, team_b, winner))
            bracket[stage] = stage_matches
            current = nxt
            reached[STAGE_OUTPUT_LABEL[stage]] = set(current.values())

        champion = next(iter(reached["champion"]))
        return TournamentOutcome(
            group_winners=winners,
            group_runners_up=runners,
            group_rankings=rankings,
            qualified_third_groups=qualified_third_groups,
            reached=reached,
            champion=champion,
            bracket=bracket,
        )

    # ---- Fast path (plain Python, no per-sim DataFrames) used by simulate_many ----

    def _prepare_groups(self, groups: Mapping[str, Mapping[str, object]]) -> dict[str, tuple]:
        """Convert the per-group fixtures DataFrames to plain (teams, match-tuples) ONCE,
        so the per-sim loop never touches pandas. ``match`` = (home, away, hs|None, as|None)."""
        prepared: dict[str, tuple] = {}
        for letter, spec in groups.items():
            teams = [str(t) for t in spec["teams"]]  # type: ignore[index]
            matches: list[tuple[str, str, int | None, int | None]] = []
            for _, row in spec["fixtures"].iterrows():  # type: ignore[index]
                hs, away_score = row.get("home_score"), row.get("away_score")
                if pd.isna(hs) or pd.isna(away_score):
                    matches.append((str(row["home_team"]), str(row["away_team"]), None, None))
                else:
                    matches.append((str(row["home_team"]), str(row["away_team"]), int(hs), int(away_score)))
            prepared[letter] = (teams, matches)
        return prepared

    def _break_tie_fast(self, cluster: list[str], played: list[tuple]) -> list[str]:
        teamset = set(cluster)
        h2h = {t: [0, 0, 0] for t in cluster}  # points, gd, gf among the tied teams
        for home, away, hs, away_score in played:
            if home in teamset and away in teamset:
                h2h[home][2] += hs
                h2h[away][2] += away_score
                h2h[home][1] += hs - away_score
                h2h[away][1] += away_score - hs
                if hs > away_score:
                    h2h[home][0] += 3
                elif hs < away_score:
                    h2h[away][0] += 3
                else:
                    h2h[home][0] += 1
                    h2h[away][0] += 1
        lots = {t: self.rng.random() for t in cluster}
        return sorted(cluster, key=lambda t: (-h2h[t][0], -h2h[t][1], -h2h[t][2], lots[t], t))

    def _rank_fast(self, teams: list[str], standings: dict[str, list[int]], played: list[tuple]) -> list[str]:
        # standings[t] = [points, gf, ga, wins]. FIFA order: pts, GD, GF, then H2H, then lots.
        ordered = sorted(teams, key=lambda t: (-standings[t][0], -(standings[t][1] - standings[t][2]), -standings[t][1]))
        ranked: list[str] = []
        i, n = 0, len(ordered)
        while i < n:
            si = standings[ordered[i]]
            gd_i = si[1] - si[2]
            j = i + 1
            while j < n:
                sj = standings[ordered[j]]
                if sj[0] == si[0] and (sj[1] - sj[2]) == gd_i and sj[1] == si[1]:
                    j += 1
                else:
                    break
            if j - i == 1:
                ranked.append(ordered[i])
            else:
                ranked.extend(self._break_tie_fast(ordered[i:j], played))
            i = j
        return ranked

    def simulate_once_fast(self, prepared: dict[str, tuple]) -> tuple[dict, dict, str, dict]:
        winners: dict[str, str] = {}
        runners: dict[str, str] = {}
        thirds: list[tuple[str, str, int, int, int]] = []  # group, team, pts, gd, gf
        for group in GROUPS_2026:
            teams, matches = prepared[group]
            standings = {t: [0, 0, 0, 0] for t in teams}  # points, gf, ga, wins
            played: list[tuple] = []
            for home, away, hs, away_score in matches:
                if hs is None:
                    hs, away_score = self.scoreline_sampler(home, away)
                played.append((home, away, hs, away_score))
                sh, sa = standings[home], standings[away]
                sh[1] += hs
                sh[2] += away_score
                sa[1] += away_score
                sa[2] += hs
                if hs > away_score:
                    sh[0] += 3
                    sh[3] += 1
                elif hs < away_score:
                    sa[0] += 3
                    sa[3] += 1
                else:
                    sh[0] += 1
                    sa[0] += 1
            ranked = self._rank_fast(teams, standings, played)
            winners[group] = ranked[0]
            runners[group] = ranked[1]
            third = ranked[2]
            st = standings[third]
            thirds.append((group, third, st[0], st[1] - st[2], st[1]))

        best = sorted(thirds, key=lambda x: (-x[2], -x[3], -x[4], self.rng.random()))[:8]
        third_team_by_group = {group: team for group, team, *_ in best}
        assignment = self.third_place_resolver(sorted(third_team_by_group.keys()))
        advanced = set(winners.values()) | set(runners.values()) | set(third_team_by_group.values())

        def resolve(slot: str) -> str:
            return winners[slot[1]] if slot[0] == "1" else runners[slot[1]]

        bracket: dict[str, list[tuple[str, str, str]]] = {}
        current: dict[str, str] = {}
        r32: list[tuple[str, str, str]] = []
        for match_id, slot_a, slot_b in ROUND_OF_32_FIXED:
            team_a = resolve(slot_a)
            team_b = third_team_by_group[assignment[slot_a]] if _is_third_placeholder(slot_b) else resolve(slot_b)
            winner = self.choose_knockout_winner(team_a, team_b)
            current[match_id] = winner
            r32.append((team_a, team_b, winner))
        bracket["R32"] = r32

        reached: dict[str, set[str]] = {"advanced": advanced, "last_16": set(current.values())}
        for stage in ("R16", "QF", "SF", "F"):
            nxt: dict[str, str] = {}
            stage_matches: list[tuple[str, str, str]] = []
            for match_id, source_a, source_b in KNOCKOUT_BRACKET[stage]:
                team_a, team_b = current[source_a], current[source_b]
                winner = self.choose_knockout_winner(team_a, team_b)
                nxt[match_id] = winner
                stage_matches.append((team_a, team_b, winner))
            bracket[stage] = stage_matches
            current = nxt
            reached[STAGE_OUTPUT_LABEL[stage]] = set(current.values())

        return winners, reached, next(iter(reached["champion"])), bracket

    def sample_market_outcomes(
        self, groups: Mapping[str, Mapping[str, object]], n_sims: int
    ) -> list[dict[str, str]]:
        """Per-sim realised outcomes for the tradeable markets, for portfolio payoff sampling.

        Returns a list of ``{"champion": team, "group:A": team, ...}`` — one dict per simulated
        tournament — so a bet's payoff in every sim can be read off directly (captures the full
        correlation structure: same-group mutual exclusivity, champion vs group-winner, etc.)."""
        if set(groups.keys()) != set(GROUPS_2026):
            raise ValueError("sample_market_outcomes requires exactly groups A-L")
        prepared = self._prepare_groups(groups)
        out: list[dict[str, str]] = []
        for _ in range(n_sims):
            winners, _reached, champion, _bracket = self.simulate_once_fast(prepared)
            rec: dict[str, str] = {"champion": champion}
            for group, team in winners.items():
                rec[f"group:{group}"] = team
            out.append(rec)
        return out

    def simulate_many(
        self,
        groups: Mapping[str, Mapping[str, object]],
        n_sims: int,
        on_progress: Callable[[int, int, dict, dict], None] | None = None,
        progress_every: int = 25,
    ) -> pd.DataFrame:
        """Aggregate ``n_sims`` tournaments into per-team sub-market probabilities.

        Uses the plain-Python fast path (no per-sim DataFrames). If ``on_progress`` is
        given it is called every ``progress_every`` sims (and once at the end) with
        ``(done, total, counts, sample_bracket)`` so a UI can stream the convergence.
        """
        if set(groups.keys()) != set(GROUPS_2026):
            raise ValueError("simulate_many requires exactly groups A-L")
        prepared = self._prepare_groups(groups)
        teams = [team for spec in groups.values() for team in spec["teams"]]  # type: ignore[index]
        counts = {team: {"win_group": 0, **{stage: 0 for stage in STAGE_ORDER}} for team in teams}
        for index in range(n_sims):
            winners, reached, _champion, bracket = self.simulate_once_fast(prepared)
            self.last_bracket = bracket
            for team in winners.values():
                counts[team]["win_group"] += 1
            for stage in STAGE_ORDER:
                for team in reached[stage]:
                    counts[team][stage] += 1
            if on_progress is not None and ((index + 1) % progress_every == 0 or index + 1 == n_sims):
                on_progress(index + 1, n_sims, counts, bracket)

        rows = []
        for team, tally in counts.items():
            row = {"team": team}
            for key, value in tally.items():
                row[f"p_{key}"] = value / n_sims
            rows.append(row)
        return pd.DataFrame(rows).sort_values("p_champion", ascending=False).reset_index(drop=True)
