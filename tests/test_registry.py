from __future__ import annotations

import copy

import pytest

import pipeline
from conftest import load_reference

STATE_NAMES = sorted(set(pipeline.US_STATES.values()))


def test_registry_loads_and_names_are_unique():
    registry = pipeline.load_registry()
    names = [source["name"] for source in registry["sources"]]
    assert len(names) == len(set(names))
    assert len(registry["sources"]) >= 100


def test_every_state_and_dc_has_exactly_one_agency():
    agencies = [source for source in pipeline.load_registry()["sources"] if source["role"] == "state_agency"]
    states = sorted(state for source in agencies for state in source["trigger"]["states"])
    assert states == STATE_NAMES
    assert all(source["tier"] == 3 for source in agencies)


def test_templates_format_with_every_supported_field():
    values = {field: "x" for field in pipeline.TEMPLATE_FIELDS}
    for source in pipeline.load_registry()["sources"]:
        query = source["query_template"].format(**values)
        assert "{" not in query and "}" not in query


def test_verifying_domains_are_not_discovery_domains():
    registry = pipeline.load_registry()
    discovery = registry["discovery_domains"]
    for source in registry["sources"]:
        if pipeline.REGISTRY_ROLES[source["role"]] is None:
            continue
        for domain in source["official_domains"]:
            assert not any(pipeline._host_matches_domain(domain, item) for item in discovery), source["name"]


def test_identity_triggers_always_require_consent():
    for source in pipeline.load_registry()["sources"]:
        trigger = source["trigger"]
        uses_identity = "identity_keywords" in trigger or any(
            field in pipeline.IDENTITY_FIELDS for field in trigger.get("true_fields", [])
        )
        if uses_identity:
            assert not trigger.get("always"), source["name"]


def bad_registry(**changes) -> dict:
    raw = copy.deepcopy(load_reference("trusted-sources.json"))
    raw["sources"] = [dict(raw["sources"][0], **changes)]
    return raw


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"role": "friend"}, "unsupported role"),
        ({"query_template": "{state.__class__}"}, "unsupported template field"),
        ({"query_template": "{unknown}"}, "unsupported template field"),
        ({"official_domains": ["not a domain"]}, "invalid domain"),
        ({"official_domains": []}, "non-empty"),
        ({"trigger": {"weather": ["sunny"]}}, "unsupported trigger keys"),
        ({"trigger": {"match": "all"}}, "at least one condition"),
        ({"trigger": {"states": ["Atlantis"]}}, "unknown states"),
        ({"trigger": {"search_modes": ["kindergarten"]}}, "unknown search modes"),
        ({"trigger": {"true_fields": ["student.location.state"]}}, "boolean"),
        ({"homepage": "https://evil.example.com/"}, "official domain"),
        ({"tier": 9}, "tier"),
    ],
)
def test_invalid_registry_entries_are_rejected(changes, message):
    with pytest.raises(ValueError, match=message):
        pipeline.validate_registry(bad_registry(**changes))


def test_duplicate_names_are_rejected():
    raw = copy.deepcopy(load_reference("trusted-sources.json"))
    raw["sources"].append(copy.deepcopy(raw["sources"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        pipeline.validate_registry(raw)


def test_legacy_single_domain_field_is_still_accepted():
    raw = bad_registry()
    entry = raw["sources"][0]
    entry["official_domain"] = entry.pop("official_domains")[0]
    assert pipeline.validate_registry(raw)["sources"][0]["official_domains"]


def test_check_registry_cli(capsys):
    assert pipeline.main(["check-registry"]) == 0
    assert '"ok": true' in capsys.readouterr().out
