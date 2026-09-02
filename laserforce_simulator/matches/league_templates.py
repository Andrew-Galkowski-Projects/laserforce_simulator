"""CRE-01 — League template constants.

A server-side bundle of curated create-League presets surfaced by the
``league_create`` chooser view. NOT user-savable, NOT persisted, and carries
NO ``League`` back-reference — each ``LeagueTemplate`` resolves to a set of
``CreateLeagueForm`` field values (via ``_template_to_form_data``) so the
chooser reuses the Advanced validation + phase-composer parse verbatim.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LeagueTemplate:
    """One curated create-League preset.

    ``phases`` is a phase_composer wire string (the same comma-separated
    ``type[:config]`` tokens the Advanced composer serialises). The remaining
    fields map straight onto ``CreateLeagueForm`` field values.
    """

    key: str
    label: str
    num_teams: int
    phases: str
    finance_enabled: bool = False
    challenge_fired_luxury_tax: bool = False
    mean: int = 50
    std_dev: int = 15
    map_mode: str = "none"  # 3-zone fallback


LEAGUE_TEMPLATES: tuple[LeagueTemplate, ...] = (
    LeagueTemplate("4_team_quick", "4-Team Quick", 4, "round_robin,tournament"),
    LeagueTemplate("8_team_classic", "8-Team Classic", 8, "round_robin,tournament"),
    LeagueTemplate(
        "8_team_career",
        "8-Team Career",
        8,
        "round_robin,tournament",
        finance_enabled=True,
    ),
    LeagueTemplate(
        "8_team_double_rr",
        "8-Team Double-RR",
        8,
        "round_robin:double_round_robin,tournament",
    ),
    LeagueTemplate(
        "8_team_member_nights",
        "8-Team Member Nights",
        8,
        "round_robin,member_night,tournament",
    ),
)

LEAGUE_TEMPLATES_BY_KEY: dict[str, LeagueTemplate] = {
    t.key: t for t in LEAGUE_TEMPLATES
}
