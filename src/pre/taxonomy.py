"""The canonical Life Dimension taxonomy.

17 dimensions derived from OECD Better Life Index, WHOQOL-BREF, SAMHSA Eight Dimensions of
Wellness, Gallup Five Elements, Bhutan GNH, PERMA, SDT, and Eurostat QoL. Full derivation in
`docs/research/life-dimension-taxonomies.md`.

Sub-dimensions are the interview coverage scaffold, not hard schema.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dimension:
    code: str
    name: str
    description: str
    sub_dimensions: tuple[str, ...]


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "physical_health",
        "Physical Health",
        "The body: fitness, fuel, rest, and medical care.",
        ("fitness", "nutrition", "sleep", "medical care", "chronic conditions"),
    ),
    Dimension(
        "mental_wellbeing",
        "Mental & Emotional Wellbeing",
        "Mood, stress, resilience, and psychological support.",
        ("mood & stress", "resilience", "therapy & support", "emotional life"),
    ),
    Dimension(
        "career",
        "Career",
        "Paid work as an employee or contractor: role, growth, mobility, standing.",
        (
            "role performance",
            "skill development",
            "job mobility",
            "professional network",
            "industry standing",
        ),
    ),
    Dimension(
        "business",
        "Business",
        "Ventures the user owns: building, selling, and running them.",
        (
            "product",
            "growth & marketing",
            "sales",
            "operations",
            "team & hiring",
            "legal & admin",
        ),
    ),
    Dimension(
        "financial",
        "Financial",
        "Money in, money out, money growing, money protected.",
        (
            "budgeting & spending",
            "saving & investing",
            "debt & credit",
            "insurance & risk",
            "taxes",
            "long-term planning",
        ),
    ),
    Dimension(
        "social",
        "Social",
        "Friendship and community outside family and romance.",
        (
            "friendships",
            "communities & groups",
            "events & gatherings",
            "online communities",
            "shared hobbies",
        ),
    ),
    Dimension(
        "relationship",
        "Relationship",
        "The romantic partnership.",
        ("communication", "shared logistics", "intimacy & connection", "milestones & planning"),
    ),
    Dimension(
        "family",
        "Family",
        "Blood and chosen family obligations and joys.",
        (
            "immediate family",
            "extended family",
            "parenting",
            "elder care",
            "household management",
        ),
    ),
    Dimension(
        "housing",
        "Housing & Home",
        "Where the user lives and keeping it working.",
        ("home operations", "maintenance & repairs", "move & search", "neighborhood & locality"),
    ),
    Dimension(
        "community_civic",
        "Community & Civic",
        "Belonging and participation beyond close ties.",
        ("local community", "volunteering", "civic engagement", "governance & rights"),
    ),
    Dimension(
        "education",
        "Education & Learning",
        "Deliberate acquisition of knowledge and skill.",
        (
            "formal learning",
            "self-directed growth",
            "skills & certifications",
            "knowledge work",
        ),
    ),
    Dimension(
        "leisure",
        "Leisure & Recreation",
        "Play, hobbies, travel, and deliberate rest.",
        ("hobbies", "fun & play", "travel", "rest & downtime"),
    ),
    Dimension(
        "environment",
        "Environment & Surroundings",
        "The physical and natural world around the user.",
        ("natural surroundings", "sustainability", "home environment quality"),
    ),
    Dimension(
        "safety",
        "Safety & Security",
        "Protection of person, data, and livelihood.",
        ("personal safety", "digital security", "economic security"),
    ),
    Dimension(
        "spirituality",
        "Spirituality & Meaning",
        "Purpose, values, faith, and the existential floor.",
        ("purpose & values", "faith & practice", "existential wellbeing"),
    ),
    Dimension(
        "reputational",
        "Reputational",
        "How the world sees the user and the record they leave.",
        ("public presence", "content & output", "mentions & reviews", "credentials & social proof"),
    ),
    Dimension(
        "autonomy_time",
        "Autonomy & Time",
        "Sovereignty over one's own hours and direction.",
        ("time sovereignty", "schedule freedom", "work-life balance", "independence"),
    ),
)

DIMENSIONS_BY_CODE: dict[str, Dimension] = {d.code: d for d in DIMENSIONS}

HORIZONS = ("immediate", "longterm")
CADENCES = ("daily", "weekly", "monthly", "long-cycle")
OPENNESS = ("low", "medium", "high")


def validate() -> None:
    """Raise ValueError if the taxonomy violates its own invariants."""
    if len(DIMENSIONS) != 17:
        raise ValueError(f"expected 17 dimensions, found {len(DIMENSIONS)}")
    codes = [d.code for d in DIMENSIONS]
    if len(set(codes)) != len(codes):
        raise ValueError("duplicate dimension codes")
    for d in DIMENSIONS:
        if not d.sub_dimensions:
            raise ValueError(f"dimension {d.code} has no sub-dimension scaffold")
        if d.code != d.code.lower():
            raise ValueError(f"dimension codes must be lowercase: {d.code}")
