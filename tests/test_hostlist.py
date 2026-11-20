"""The host list's schema and `wl-check-hosts`.

The fixtures have the shape of the machine-classes spec's section 6.2 examples
(an absent `deploys`, an empty one, an exception) with placeholder values; this
repository is public.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wl_manifest.cli import check_hosts_path, hosts_main
from wl_manifest.hostlist import HostEntry, check_hosts, load_hosts
from wl_manifest.hosts import HostSelector

DWS0 = {
    "name": "wl-dws0", "selector": "dws", "owner": "wl", "os": "os-a",
    "hardware": "desktop", "user": "PI",
    "stacks": ["stack-a", "stack-b"], "develops": "all",
    "status": "planned", "notes": "example",
}
WH_DWS0 = {
    "name": "wh-dws0", "selector": "dws", "owner": "wh", "os": "os-b",
    "hardware": "x86_64 desktop", "user": "PI",
    "stacks": ["stack-c"], "develops": [], "status": "active",
    "exceptions": [{
        "rule": "no lab licences on a wh- host",
        "what": "a licensed package installed before the standard",
        "why": "example",
        "until": "example",
    }],
}
MWS1 = {
    "name": "wl-mws1", "selector": "mws", "owner": "wl", "os": "os-a",
    "hardware": "laptop", "user": "trainee-01",
    "stacks": ["stack-d"], "develops": [], "deploys": [],
    "status": "planned",
}
SPEC_EXAMPLES = [DWS0, WH_DWS0, MWS1]


def write(tmp_path: Path, data) -> Path:
    p = tmp_path / "hosts.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def codes(tmp_path: Path, entries) -> list[str]:
    return [f.code for f in check_hosts(write(tmp_path, entries))]


def test_the_spec_examples_have_no_findings(tmp_path):
    assert check_hosts(write(tmp_path, SPEC_EXAMPLES)) == []


def test_deploys_absent_and_empty_are_different(tmp_path):
    a, b = load_hosts(write(tmp_path, [{**DWS0}, {**MWS1}]))
    assert a.deploys is None and b.deploys == []


def test_the_exception_loads(tmp_path):
    entry = load_hosts(write(tmp_path, [WH_DWS0]))[0]
    assert entry.exceptions[0].until == "example"


def test_develops_accepts_all_or_a_list(tmp_path):
    a, b = load_hosts(
        write(tmp_path, [{**DWS0}, {**DWS0, "name": "wl-dws1", "develops": ["wl-style"]}])
    )
    assert a.develops == "all" and b.develops == ["wl-style"]
    assert codes(tmp_path, [{**DWS0, "develops": "some"}]) == ["H001"]


def test_host_selector_property(tmp_path):
    e = load_hosts(write(tmp_path, [{**DWS0, "name": "wl-rig-eye0", "selector": "rig/eye"}]))[0]
    assert e.host_selector == HostSelector("rig", "eye")
    assert isinstance(e, HostEntry)
    with pytest.raises(ValueError):
        HostEntry.model_validate({**DWS0, "selector": "nope"}).host_selector


def test_prefix_must_match_owner(tmp_path):
    assert codes(tmp_path, [{**DWS0, "name": "wh-dws1"}]) == ["H003"]


def test_name_must_match_selector(tmp_path):
    e = {**DWS0, "name": "wl-serv-gpu0", "selector": "serv/mem"}
    assert codes(tmp_path, [e]) == ["H005"]


def test_a_matching_role_name_is_clean(tmp_path):
    e = {**DWS0, "name": "wl-serv-gpu0", "selector": "serv/gpu"}
    assert codes(tmp_path, [e]) == []


def test_a_bare_class_name_must_have_a_bare_selector(tmp_path):
    assert codes(tmp_path, [{**DWS0, "selector": "dws/x"}]) == ["H005"]


def test_an_unparseable_selector_is_h004(tmp_path):
    assert codes(tmp_path, [{**DWS0, "selector": "dws/a/b"}]) == ["H004"]


def test_duplicate_names_are_an_error(tmp_path):
    assert "H006" in codes(tmp_path, [DWS0, {**DWS0}])


def test_a_retired_class_name_is_rejected_with_the_known_classes(tmp_path):
    f = check_hosts(write(tmp_path, [{**DWS0, "name": "wl-rws0", "selector": "dws"}]))
    assert f[0].code == "H002" and "'kiosk'" in f[0].message


def test_a_malformed_name_is_h002(tmp_path):
    assert codes(tmp_path, [{**DWS0, "name": "dws0"}]) == ["H002"]


def test_role_before_class_is_rejected(tmp_path):
    e = {**DWS0, "name": "wl-sync-rig0", "selector": "rig/sync"}
    assert codes(tmp_path, [e]) == ["H002"]


def test_an_exception_needs_until(tmp_path):
    bad = {k: v for k, v in WH_DWS0["exceptions"][0].items() if k != "until"}
    assert codes(tmp_path, [{**WH_DWS0, "exceptions": [bad]}]) == ["H001"]


def test_not_a_list_is_h001(tmp_path):
    assert codes(tmp_path, {"name": "wl-dws0"}) == ["H001"]


def test_not_yaml_is_h001(tmp_path):
    p = tmp_path / "hosts.yaml"
    p.write_text("a: [unclosed")
    assert [f.code for f in check_hosts(p)] == ["H001"]


def test_load_hosts_raises_with_file_and_index(tmp_path):
    p = write(tmp_path, [DWS0, {**DWS0, "status": "bogus"}])
    with pytest.raises(ValueError, match=r"hosts\.yaml: entry 1:"):
        load_hosts(p)


def test_every_finding_is_reported(tmp_path):
    e = {**DWS0, "name": "wh-dws1"}
    assert sorted(codes(tmp_path, [e, {**e}])) == ["H003", "H003", "H006"]


def test_cli_exit_codes(tmp_path, capsys):
    clean = write(tmp_path, SPEC_EXAMPLES)
    assert check_hosts_path(clean) == 0
    assert capsys.readouterr().out == f"{clean}: no findings\n"

    bad = write(tmp_path, [{**DWS0, "name": "wh-dws1"}])
    assert check_hosts_path(bad) == 1
    assert capsys.readouterr().out.startswith("error   H003  ")

    missing = tmp_path / "nope.yaml"
    assert check_hosts_path(missing) == 2
    assert capsys.readouterr().err == f"no host list at {missing}\n"


def test_a_directory_is_no_host_list_and_exits_2(tmp_path, capsys):
    assert check_hosts_path(tmp_path) == 2
    assert capsys.readouterr().err == f"no host list at {tmp_path}\n"


def test_hosts_main_defaults_to_hosts_yaml_in_the_cwd(tmp_path, monkeypatch):
    write(tmp_path, SPEC_EXAMPLES)
    monkeypatch.chdir(tmp_path)
    assert hosts_main([]) == 0
    monkeypatch.chdir(tmp_path / "..")
    assert hosts_main([]) == 2


def test_an_unquoted_yaml_date_in_until_is_accepted(tmp_path):
    p = tmp_path / "hosts.yaml"
    p.write_text(
        yaml.safe_dump([WH_DWS0]).replace("until: example", "until: 2027-01-01")
    )
    assert "until: 2027-01-01" in p.read_text()  # unquoted, so YAML makes a date
    assert check_hosts(p) == []
    assert load_hosts(p)[0].exceptions[0].until == "2027-01-01"


@pytest.mark.parametrize("name", ["wl-dws01", "wl-dws00", "wl-serv-nas01"])
def test_leading_zeros_in_the_number_are_h002(tmp_path, name):
    sel = "serv/nas" if "nas" in name else "dws"
    assert codes(tmp_path, [{**DWS0, "name": name, "selector": sel}]) == ["H002"]


def test_hosts_main_takes_a_path(tmp_path):
    assert hosts_main([str(write(tmp_path, SPEC_EXAMPLES))]) == 0


def test_a_bad_entry_does_not_hide_findings_on_the_others(tmp_path):
    entries = [DWS0, {**DWS0, "status": "bogus"},
               {**DWS0, "name": "wl-rws0", "selector": "dws"}]
    # entry 1 fails its schema (H001) and repeats entry 0's name (H006);
    # wl-rws0 still gets its H002.
    assert codes(tmp_path, entries) == ["H001", "H006", "H002"]


def test_a_typo_in_a_key_is_h007(tmp_path):
    thin = {k: v for k, v in MWS1.items() if k != "deploys"} | {"deploy": []}
    f = check_hosts(write(tmp_path, [thin]))
    assert [x.code for x in f] == ["H007"]
    assert "wl-mws1" in f[0].message and "'deploy'" in f[0].message
    assert load_hosts(write(tmp_path, [thin]))[0].deploys is None


def test_an_unknown_key_in_an_exception_is_h007(tmp_path):
    exc = {**WH_DWS0["exceptions"][0], "untill": "never"}
    assert codes(tmp_path, [{**WH_DWS0, "exceptions": [exc]}]) == ["H007"]
