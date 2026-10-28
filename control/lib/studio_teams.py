"""Group studio agents and route rows by team."""
import re

ROLE_RX = r"(?:^|-)(architect|main|messenger|worker-\d+)$"
_ROLE = re.compile(ROLE_RX)
_ORDER = {"architect": 0, "main": 1, "messenger": 2}


def team_of(name):
    """Return the team and role for a role-suffixed name, or None."""
    if not isinstance(name, str):
        return None
    match = _ROLE.search(name)
    if not match:
        return None
    return name[:match.start()], match.group(1)


def group(agents):
    """Group agents by team, sorted by team and role."""
    teams = {}
    for agent in agents:
        parsed = team_of(agent.get("name"))
        if parsed is not None:
            team, role = parsed
            teams.setdefault(team, []).append((role, agent))
    result = []
    for team in sorted(teams):
        members = teams[team]
        members.sort(key=lambda item: (_ORDER.get(item[0], 3),
                                       int(item[0][7:]) if item[0].startswith("worker-") else 0))
        rows = [agent for _, agent in members]
        kinds = {agent.get("agent", "?") for agent in rows}
        result.append({"team": team, "kind": next(iter(kinds)) if len(kinds) == 1 else "mixed",
                       "idle": not any(a.get("agent_status", a.get("status")) in ("working", "blocked") for a in rows),
                       "agents": rows})
    return result


def team_of_route(row):
    """Return the sender's team, falling back to the recipient's team."""
    for key in ("from", "to"):
        parsed = team_of(row.get(key))
        if parsed is not None:
            return parsed[0]
    return None
