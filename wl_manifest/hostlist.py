"""The host list: one entry per machine the lab has, plans and retirements included.

The file itself (`hosts.yaml`) lives in wl-stack, which owns host identity. The
schema and its checker live here so that `wl-orchestrator` can parse the list
without importing wl-stack, the same split as `wl.yaml` and the selector
grammar (wl-stack's machine-classes design, section 6.2).

Like `check.py`, `check_hosts` never raises: every fault is a `CheckFinding`.
It reads only the one file it is given.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError

from wl_manifest.check import CheckFinding
from wl_manifest.hosts import HOST_CLASSES, HostSelector
from wl_manifest.models import _Tolerant

# `<org>-<class><n>` or `<org>-<class>-<role><n>`. Class comes before role, so
# `wl-sync-rig0` parses with class `sync` and is rejected as an unknown class.
HOST_NAME = re.compile(r"^(wl|wh)-([a-z]+)(?:-([a-z]+))?([0-9]+)$")


class HostException(_Tolerant):
    """A deliberate, recorded breach of a standard. `until` is what ends it."""

    rule: str
    what: str
    why: str
    until: str


class HostEntry(_Tolerant):
    """One machine.

    `deploys` distinguishes absent from empty: absent means everything the
    class's `runs_on` software implies, `[]` means nothing (a thin client), a
    list names packages.
    """

    name: str
    selector: str
    owner: Literal["wl", "wh"]
    os: str
    hardware: str | None = None
    user: str | None = None  # a role or an opaque ID, never a person's name
    stacks: list[str] = []
    develops: Literal["all"] | list[str] = []
    deploys: list[str] | None = None
    status: Literal["planned", "active", "retired"]
    notes: str | None = None
    exceptions: list[HostException] = []

    @property
    def host_selector(self) -> HostSelector:
        return HostSelector.parse(self.selector)


def _read_list(path: Path) -> list:
    """The file's top-level list, or `ValueError` saying what is wrong with it."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(
            f"{path} must be a list of hosts at the top level, "
            f"found {type(data).__name__}"
        )
    return data


def _detail(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '(entry)'}: {e['msg']}"
        for e in exc.errors()
    )


def _entries(path: Path) -> tuple[list[HostEntry], list[str]]:
    """Every entry that validates, and a message for every one that does not."""
    entries: list[HostEntry] = []
    errors: list[str] = []
    for i, raw in enumerate(_read_list(path)):
        try:
            entries.append(HostEntry.model_validate(raw))
        except ValidationError as exc:
            errors.append(f"{path}: entry {i}: {_detail(exc)}")
    return entries, errors


def load_hosts(path: Path) -> list[HostEntry]:
    """Parse the host list. Raises `ValueError` naming the file and entry index."""
    entries, errors = _entries(path)
    if errors:
        raise ValueError("\n".join(errors))
    return entries


def check_hosts(path: Path) -> list[CheckFinding]:
    """Check one host list. Never raises; reports every finding it can.

    Only a fault in the file as a whole (H001: unreadable, not YAML, not a
    list) stops the checking. An entry that fails its schema is an H001 of its
    own, and the entries that did load are still checked. Its name, if it has
    one, still counts for H006, since a duplicate is a duplicate whatever else
    is wrong with the second copy.
    """
    try:
        raw_entries = _read_list(path)
    except ValueError as exc:
        return [CheckFinding("error", "H001", str(exc))]

    findings: list[CheckFinding] = []

    def err(code: str, message: str) -> None:
        findings.append(CheckFinding("error", code, message))

    seen: set[str] = set()

    def note_name(name: object) -> None:
        if isinstance(name, str):
            if name in seen:
                err("H006", f"duplicate host name {name!r}")
            seen.add(name)

    for i, raw in enumerate(raw_entries):
        try:
            e = HostEntry.model_validate(raw)
        except ValidationError as exc:
            err("H001", f"{path}: entry {i}: {_detail(exc)}")
            note_name(raw.get("name") if isinstance(raw, dict) else None)
            continue
        note_name(e.name)

        for key in sorted(e.model_extra or {}):
            err("H007", f"{e.name}: unknown key {key!r}")
        for exc_entry in e.exceptions:
            for key in sorted(exc_entry.model_extra or {}):
                err("H007", f"{e.name}: unknown key {key!r} in an exception")

        m = HOST_NAME.match(e.name)
        name_ok = False
        if m is None:
            err("H002", f"{e.name!r} is not <wl|wh>-<class>[-<role>]<n>")
        elif m.group(2) not in HOST_CLASSES:
            err(
                "H002",
                f"{e.name!r} has class {m.group(2)!r}, "
                f"which is not one of {sorted(HOST_CLASSES)}",
            )
        else:
            name_ok = True
            if m.group(1) != e.owner:
                err(
                    "H003",
                    f"{e.name!r} has prefix {m.group(1)!r} but owner is {e.owner!r}",
                )

        try:
            sel = e.host_selector
        except ValueError as exc:
            err("H004", f"{e.name!r}: {exc}")
            continue
        if name_ok and (m.group(2), m.group(3)) != (sel.klass, sel.role):
            named = m.group(2) if m.group(3) is None else f"{m.group(2)}/{m.group(3)}"
            err("H005", f"{e.name!r} names {named!r} but its selector is {str(sel)!r}")
    return findings
