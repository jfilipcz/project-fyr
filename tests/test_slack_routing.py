import pytest

from project_fyr.slack import build_routing_plan


def _targets(plan):
    return [(t.kind, t.value) for t in plan.primary], [(t.kind, t.value) for t in plan.fallback]


def test_requestor_dm_with_namespace_channel():
    plan = build_routing_plan(
        requestor_email="req@example.com",
        owner_channel="#owner",
        namespace_channel="#ns",
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("dm", "req@example.com"), ("channel", "#ns")]
    assert fallback == [("channel", "#default")]


def test_requestor_dm_no_namespace_owner_enabled():
    plan = build_routing_plan(
        requestor_email="req@example.com",
        owner_channel="#owner",
        namespace_channel=None,
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("dm", "req@example.com")]
    assert fallback == [("channel", "#owner"), ("channel", "#default")]


def test_requestor_dm_no_namespace_owner_disabled():
    plan = build_routing_plan(
        requestor_email="req@example.com",
        owner_channel="#owner",
        namespace_channel=None,
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=False,
    )
    primary, fallback = _targets(plan)
    assert primary == [("dm", "req@example.com")]
    assert fallback == [("channel", "#default")]


def test_no_requestor_owner_and_namespace():
    plan = build_routing_plan(
        requestor_email=None,
        owner_channel="#owner",
        namespace_channel="#ns",
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#owner"), ("channel", "#ns")]
    assert fallback == [("channel", "#default")]


def test_no_requestor_owner_only():
    plan = build_routing_plan(
        requestor_email=None,
        owner_channel="#owner",
        namespace_channel=None,
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#owner")]
    assert fallback == [("channel", "#default")]


def test_no_requestor_namespace_only():
    plan = build_routing_plan(
        requestor_email=None,
        owner_channel=None,
        namespace_channel="#ns",
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#ns")]
    assert fallback == [("channel", "#default")]


def test_no_requestor_no_channels_uses_default():
    plan = build_routing_plan(
        requestor_email=None,
        owner_channel=None,
        namespace_channel=None,
        default_channel="#default",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#default")]
    assert fallback == []


def test_requestor_present_dm_disabled_uses_owner_and_namespace():
    plan = build_routing_plan(
        requestor_email="req@example.com",
        owner_channel="#owner",
        namespace_channel="#ns",
        default_channel="#default",
        enable_requestor_dm=False,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#owner"), ("channel", "#ns")]
    assert fallback == [("channel", "#default")]


def test_dedupes_duplicate_channels():
    plan = build_routing_plan(
        requestor_email=None,
        owner_channel="#same",
        namespace_channel="#same",
        default_channel="#same",
        enable_requestor_dm=True,
        enable_owner_channel=True,
    )
    primary, fallback = _targets(plan)
    assert primary == [("channel", "#same")]
    assert fallback == []
