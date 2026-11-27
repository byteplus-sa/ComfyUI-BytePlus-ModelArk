"""Shared, top-level media sockets without changing provider request fields."""

from copy import deepcopy

from comfy_api.latest import io as comfy_io

from .nodes_shared import BytePlusException, get_text


def lift_media_inputs(combo, names):
    """Move selected children out of a combo, taking the largest Autogrow capacity."""
    shared = {}
    for option in combo.options:
        remaining = []
        for item in option.inputs:
            if item.id not in names:
                remaining.append(item)
                continue
            previous = shared.get(item.id)
            if previous is None or (
                isinstance(item, comfy_io.Autogrow.Input) and len(item.template.names) > len(previous.template.names)
            ):
                shared[item.id] = deepcopy(item)
        option.inputs = remaining
    for item in shared.values():
        item.optional = True
        if isinstance(item, comfy_io.Autogrow.Input):
            item.tooltip = (item.tooltip or "") + " The selected model's limits still apply."
    return [combo, *shared.values()]


def has_media(value):
    """Do not evaluate tensors as booleans; empty Autogrow slots are not media."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(has_media(item) for item in value.values())
    return True


def merge_media_inputs(options, **values):
    """Accept old Python callers, but never silently discard a second media source."""
    merged = dict(options) if isinstance(options, dict) else {}
    for name, value in values.items():
        if not has_media(value):
            continue
        if has_media(merged.get(name)):
            raise BytePlusException(get_text("err_media_input_conflict", name=name))
        merged[name] = value
    return merged
