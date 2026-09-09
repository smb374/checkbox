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
"""End-to-end nested test-plan validation tests.

Run this module with::

    python -m pytest plainbox/test_nested_loop_detection.py -q
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_PROVIDER_NAMES = (
    "resource",
    "base",
    "certification-client",
    "certification-server",
    "docker",
    "genio",
    "gpgpu",
    "iiotg",
    "sru",
    "tpm2",
    "tutorial",
)

SAMPLED_PROVIDER_NAMES = (
    "resource",
    "base",
    "certification-client",
    "certification-server",
    "docker",
    "genio",
    "sru",
    "tpm2",
    "tutorial",
)


def _run(command, cwd, env):
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    result.output = result.stdout + result.stderr
    return result


def _assert_result(result, returncode, required=(), forbidden=()):
    errors = []
    if result.returncode != returncode:
        errors.append(
            "expected exit status {}, got {}".format(
                returncode, result.returncode
            )
        )
    for text in required:
        if text not in result.output:
            errors.append("missing output: {!r}".format(text))
    for text in forbidden:
        if text in result.output:
            errors.append("unexpected output: {!r}".format(text))
    if errors:
        pytest.fail("{}\n\n{}".format("\n".join(errors), result.output))


def _error_count(result):
    return len(re.findall(r"^error:", result.output, re.MULTILINE))


def _cycle_count(result):
    return result.output.count("nested test-plan cycle detected")


@pytest.fixture(scope="session")
def repository_root():
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def checkbox_cli():
    path = Path(sys.executable).with_name("checkbox-cli")
    if not path.is_file():
        pytest.fail(
            "checkbox-cli is not installed next to {}".format(sys.executable)
        )
    return path


@pytest.fixture
def provider_environment(tmp_path):
    provider_path = tmp_path / "providers"
    provider_path.mkdir()
    env = os.environ.copy()
    env["PROVIDERPATH"] = str(provider_path)
    return env


def _create_provider(tmp_path, checkbox_cli, env, name, units):
    result = _run(
        [str(checkbox_cli), "startprovider", "--empty", name], tmp_path, env
    )
    _assert_result(result, 0)
    provider_dir = tmp_path / name
    units_dir = provider_dir / "units"
    units_dir.mkdir()
    (units_dir / "test-plans.yaml").write_text(units)
    return provider_dir


def _validate(provider_dir, env):
    return _run([sys.executable, "manage.py", "validate"], provider_dir, env)


def _develop(provider_dir, env):
    result = _run(
        [sys.executable, "manage.py", "develop", "-f"], provider_dir, env
    )
    _assert_result(result, 0)


@pytest.fixture(scope="session")
def repository_provider_environment(tmp_path_factory, repository_root):
    provider_path = tmp_path_factory.mktemp("repository-providers")
    env = os.environ.copy()
    env["PROVIDERPATH"] = str(provider_path)
    for name in REPOSITORY_PROVIDER_NAMES:
        provider_dir = repository_root / "providers" / name
        result = _run(
            [sys.executable, "manage.py", "develop", "-f"], provider_dir, env
        )
        _assert_result(result, 0)
    return env


@pytest.mark.integration
@pytest.mark.parametrize("provider_name", SAMPLED_PROVIDER_NAMES)
def test_existing_provider_is_valid(
    repository_root, repository_provider_environment, provider_name
):
    result = _run(
        [sys.executable, "manage.py", "validate"],
        repository_root / "providers" / provider_name,
        repository_provider_environment,
    )

    _assert_result(
        result,
        0,
        required=("The provider seems to be valid",),
        forbidden=(
            "nested test-plan cycle detected",
            "Traceback",
            "RecursionError",
            "TypeError",
        ),
    )
    assert _error_count(result) == 0, result.output


@pytest.mark.integration
def test_valid_shared_child_dag(tmp_path, checkbox_cli, provider_environment):
    provider_dir = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        "2026.com.example:valid-nested",
        """\
unit: test plan
id: shared
name: Shared plan
include: []
---
unit: test plan
id: left
name: Left plan
include: []
nested_part:
  - shared
---
unit: test plan
id: right
name: Right plan
include: []
nested_part:
  - shared
---
unit: test plan
id: root
name: Valid root plan
include: []
nested_part:
  - left
  - right
""",
    )

    result = _validate(provider_dir, provider_environment)

    _assert_result(result, 0, required=("The provider seems to be valid",))
    assert _error_count(result) == 0, result.output


@pytest.mark.integration
def test_same_provider_cycles_are_rejected(
    tmp_path, checkbox_cli, provider_environment
):
    provider_dir = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        "2026.com.example:cyclic-nested",
        """\
unit: test plan
id: self-cycle
name: Direct cycle
include: []
nested_part:
  - self-cycle
---
unit: test plan
id: cycle-a
name: Cycle A
include: []
nested_part:
  - cycle-b
---
unit: test plan
id: cycle-b
name: Cycle B
include: []
nested_part:
  - cycle-a
""",
    )

    result = _validate(provider_dir, provider_environment)

    _assert_result(
        result,
        1,
        required=(
            "2026.com.example::self-cycle -> " "2026.com.example::self-cycle",
            "2026.com.example::cycle-a -> "
            "2026.com.example::cycle-b -> "
            "2026.com.example::cycle-a",
        ),
        forbidden=("Traceback", "RecursionError", "TypeError"),
    )
    assert _cycle_count(result) == 2, result.output


@pytest.mark.integration
@pytest.mark.parametrize(
    "nested_part",
    (
        "nested_part: child\n",
        "nested_part: 1\n",
        "nested_part: {child: true}\n",
        "nested_part:\n  - child\n  - 1\n",
    ),
)
def test_malformed_nested_part_is_rejected_cleanly(
    tmp_path, checkbox_cli, provider_environment, nested_part
):
    provider_dir = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        "2026.com.example:malformed-nested",
        "unit: test plan\nid: malformed\nname: Malformed plan\ninclude: []\n"
        + nested_part,
    )

    result = _validate(provider_dir, provider_environment)

    _assert_result(
        result,
        1,
        required=("expected a list of test-plan identifiers",),
        forbidden=(
            "setup_include",
            "bootstrap_include",
            "TypeError",
            "Traceback",
            "RecursionError",
        ),
    )
    assert _error_count(result) == 1, result.output


@pytest.mark.integration
def test_cross_provider_cycle_is_rejected(
    tmp_path, checkbox_cli, provider_environment
):
    first = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        "2026.com.example.crossa:nested",
        """\
unit: test plan
id: root
name: Cross-provider root
include: []
nested_part:
  - 2026.com.example.crossb::part
""",
    )
    second = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        "2026.com.example.crossb:nested",
        """\
unit: test plan
id: part
name: Cross-provider part
include: []
nested_part:
  - 2026.com.example.crossa::root
""",
    )
    _develop(first, provider_environment)
    _develop(second, provider_environment)
    expected_path = (
        "2026.com.example.crossa::root -> "
        "2026.com.example.crossb::part -> "
        "2026.com.example.crossa::root"
    )

    for provider_dir in (first, second):
        result = _validate(provider_dir, provider_environment)
        _assert_result(
            result,
            1,
            required=(expected_path,),
            forbidden=("Traceback", "RecursionError", "TypeError"),
        )
        assert _cycle_count(result) == 1, result.output


@pytest.mark.integration
@pytest.mark.parametrize(
    "provider_name,plan_id",
    (
        ("com.canonical.plainbox:categories", "local-categories"),
        ("com.canonical.plainbox:manifest", "local-manifest"),
    ),
)
def test_authoring_provider_can_replace_builtin(
    tmp_path, checkbox_cli, provider_environment, provider_name, plan_id
):
    provider_dir = _create_provider(
        tmp_path,
        checkbox_cli,
        provider_environment,
        provider_name,
        "unit: test plan\nid: {}\nname: Local provider\ninclude: []\n".format(
            plan_id
        ),
    )

    result = _validate(provider_dir, provider_environment)

    _assert_result(result, 0, required=("The provider seems to be valid",))
    assert _error_count(result) == 0, result.output


@pytest.mark.integration
def test_local_provider_overrides_registered_same_name_provider(
    tmp_path, checkbox_cli, provider_environment
):
    installed_parent = tmp_path / "installed"
    local_parent = tmp_path / "local"
    installed_parent.mkdir()
    local_parent.mkdir()
    name = "2026.com.example.shadow:nested"
    installed = _create_provider(
        installed_parent,
        checkbox_cli,
        provider_environment,
        name,
        """\
unit: test plan
id: first
name: Installed first plan
include: []
nested_part:
  - second
---
unit: test plan
id: second
name: Installed second plan
include: []
""",
    )
    local = _create_provider(
        local_parent,
        checkbox_cli,
        provider_environment,
        name,
        """\
unit: test plan
id: first
name: Local first plan
include: []
nested_part:
  - second
---
unit: test plan
id: second
name: Local second plan
include: []
nested_part:
  - first
""",
    )
    _develop(installed, provider_environment)

    result = _validate(local, provider_environment)

    _assert_result(
        result,
        1,
        required=(
            "2026.com.example.shadow::first -> "
            "2026.com.example.shadow::second -> "
            "2026.com.example.shadow::first",
        ),
        forbidden=("Traceback", "RecursionError", "TypeError"),
    )
    assert _cycle_count(result) == 1, result.output
