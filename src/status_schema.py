"""Validate and reconcile only the Notion Status property."""

from typing import Any

from src.status import STATUS_OPTIONS


def status_options(property: Any) -> dict[str, tuple[str, str, str]]:
    """Return name -> (id, color, group); reject unsupported/malformed schemas."""
    if not isinstance(property, dict) or property.get("type") != "status":
        raise ValueError("Notion Status must be a status property")
    status = property.get("status")
    if not isinstance(status, dict):
        raise ValueError("Invalid Notion Status configuration")
    groups, options = status.get("groups"), status.get("options")
    if not isinstance(groups, list) or not isinstance(options, list):
        raise ValueError("Invalid Notion Status groups/options")
    membership: dict[str, str] = {}
    names: set[str] = set()
    for group in groups:
        if (
            not isinstance(group, dict) or not isinstance(group.get("name"), str)
            or not isinstance(group.get("option_ids"), list) or group["name"] in names
        ):
            raise ValueError("Invalid Notion Status group")
        names.add(group["name"])
        for option_id in group["option_ids"]:
            if not isinstance(option_id, str) or not option_id or option_id in membership:
                raise ValueError("Invalid Notion Status group membership")
            membership[option_id] = group["name"]
    if names != {"To-do", "In progress", "Complete"}:
        raise ValueError("Set Status groups to To-do, In progress, Complete in Notion")
    result: dict[str, tuple[str, str, str]] = {}
    ids: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Invalid Notion Status option")
        name, option_id, color = option.get("name"), option.get("id"), option.get("color")
        if (
            not isinstance(name, str) or not name or name in result
            or not isinstance(option_id, str) or not option_id or option_id in ids
            or not isinstance(color, str) or option_id not in membership
        ):
            raise ValueError("Invalid Notion Status option fields")
        result[name] = (option_id, color, membership[option_id])
        ids.add(option_id)
    if ids != set(membership):
        raise ValueError("Notion Status group references an unknown option")
    return result


def status_schema_matches(options: dict[str, tuple[str, str, str]]) -> bool:
    return (
        set(options) == {option["name"] for option in STATUS_OPTIONS}
        and all(options[option["name"]][1:] == (option["color"], option["group"])
                for option in STATUS_OPTIONS)
    )


def status_schema_update(options: dict[str, tuple[str, str, str]]) -> dict[str, Any]:
    # The API retains names case-insensitively but cannot rename them. Reject
    # ambiguous spelling before removing anything, rather than clearing values.
    desired_names = {option["name"] for option in STATUS_OPTIONS}
    for name in options:
        if name.strip().casefold() in desired_names and name not in desired_names:
            raise ValueError(f"Rename Status option {name!r} to lowercase in Notion first")
    desired = []
    for option in STATUS_OPTIONS:
        name = option["name"]
        fields = {"color": option["color"], "group": option["group"]}
        if name in options:
            fields["id"] = options[name][0]
        else:
            fields["name"] = name
        desired.append(fields)
    return {"Status": {"status": {"options": desired}}}
