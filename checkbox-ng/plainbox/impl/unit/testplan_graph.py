# This file is part of Checkbox.
#
# Copyright 2026 Canonical Ltd.
#
# Checkbox is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# Checkbox is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

"""Utilities for validating nested test-plan dependency graphs."""

import collections
import logging

from plainbox.impl.unit import get_array_field_qualify

__all__ = ["find_nested_test_plan_cycles"]


logger = logging.getLogger("plainbox.unit.testplan_graph")


NestedTestPlanCycle = collections.namedtuple(
    "NestedTestPlanCycle", "root path"
)


def find_nested_test_plan_cycles(provider_list, root_list):
    """
    Find cycles reachable from *root_list* of nested test plans.

    Only unambiguous references to test-plan units form graph edges. Missing
    and ambiguous references retain the runtime resolver's existing behavior
    and are outside this cycle-only check.
    """
    id_map = collections.defaultdict(list)
    for provider in provider_list:
        for unit in provider.unit_list:
            if hasattr(unit, "id") and unit.id is not None:
                id_map[unit.id].append(unit)
    for unit in root_list:
        if hasattr(unit, "id") and unit.id is not None:
            id_map[unit.id] = [unit]

    adjacency = {}

    def get_children(unit):
        if unit.id not in adjacency:
            children = []
            nested_part = unit.nested_part
            if not isinstance(nested_part, (str, list)):
                adjacency[unit.id] = children
                return children
            if isinstance(nested_part, list) and not all(
                isinstance(unit_id, str) for unit_id in nested_part
            ):
                adjacency[unit.id] = children
                return children
            for unit_id in get_array_field_qualify(
                nested_part, "nested_part", unit.qualify_id, logger
            ):
                candidates = id_map.get(unit_id, ())
                if (
                    len(candidates) == 1
                    and candidates[0].Meta.name == "test plan"
                ):
                    children.append(candidates[0])
            adjacency[unit.id] = sorted(children, key=lambda child: child.id)
        return adjacency[unit.id]

    roots = sorted(
        (
            unit
            for unit in root_list
            if (
                unit.Meta.name == "test plan"
                and unit.id is not None
                and unit.nested_part is not None
            )
        ),
        key=lambda unit: unit.id,
    )
    states = {}
    seen_cycles = set()
    cycles = []

    for root in roots:
        if states.get(root.id) == "finished":
            continue
        states[root.id] = "visiting"
        trail = [root]
        stack = [(root, iter(get_children(root)))]
        while stack:
            unit, child_iter = stack[-1]
            try:
                child = next(child_iter)
            except StopIteration:
                states[unit.id] = "finished"
                stack.pop()
                trail.pop()
                continue
            state = states.get(child.id)
            if state is None:
                states[child.id] = "visiting"
                trail.append(child)
                stack.append((child, iter(get_children(child))))
            elif state == "visiting":
                start = next(
                    index
                    for index, trail_unit in enumerate(trail)
                    if trail_unit.id == child.id
                )
                path = tuple(trail_unit.id for trail_unit in trail[start:])
                path = _canonicalize_cycle(path)
                if path not in seen_cycles:
                    seen_cycles.add(path)
                    cycles.append(NestedTestPlanCycle(root, path + (path[0],)))
    return cycles


def _canonicalize_cycle(path):
    """Return *path* rotated so that it has a stable starting point."""
    return min(path[index:] + path[:index] for index in range(len(path)))
