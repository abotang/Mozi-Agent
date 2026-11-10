"""Startup trigger for the blue-side opportunity-target engagement setting.

The rule runs once per saved scenario identity and again if simulation time
rewinds, which indicates a fresh opening of the same scenario.
"""


def opening_engagement_needed(state: dict, *, rewound: bool = False) -> bool:
    return rewound or state.get("opportunity_targets_scenario") != state.get("scenario")
