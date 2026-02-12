"""Tests for ephemeral environment (ephenv) annotation support."""

import pytest

from project_fyr.service import parse_namespace_annotations, ANNOTATION_REQUESTOR_EMAIL


def test_parse_namespace_annotations_legacy_format():
    """Test that legacy example.com/requestor-email annotation is recognized."""
    annotations = {
        "example.com/requestor-email": "user@example.com",
        "project-fyr/slack-channel": "#team-channel",
        "project-fyr/team": "Platform Team",
    }
    result = parse_namespace_annotations(annotations)
    
    assert result["metadata_json"][ANNOTATION_REQUESTOR_EMAIL] == "user@example.com"
    assert result["metadata_json"]["project-fyr/slack-channel"] == "#team-channel"
    assert result["metadata_json"]["project-fyr/team"] == "Platform Team"


def test_parse_namespace_annotations_ephenv_format():
    """Test that ephenv 'requestor' annotation is recognized."""
    annotations = {
        "requestor": "developer@example.com",
        "project-fyr/slack-channel": "#dev-notifications",
    }
    result = parse_namespace_annotations(annotations)
    
    # Should be normalized to standard key
    assert result["metadata_json"][ANNOTATION_REQUESTOR_EMAIL] == "developer@example.com"
    assert result["metadata_json"]["project-fyr/slack-channel"] == "#dev-notifications"


def test_parse_namespace_annotations_legacy_takes_precedence():
    """Test that when both annotations exist, legacy format takes precedence."""
    annotations = {
        "example.com/requestor-email": "legacy@example.com",
        "requestor": "ephenv@example.com",
    }
    result = parse_namespace_annotations(annotations)
    
    # Legacy annotation should take precedence
    assert result["metadata_json"][ANNOTATION_REQUESTOR_EMAIL] == "legacy@example.com"


def test_parse_namespace_annotations_no_requestor():
    """Test that missing requestor annotation doesn't break parsing."""
    annotations = {
        "project-fyr/slack-channel": "#alerts",
        "project-fyr/team": "SRE",
    }
    result = parse_namespace_annotations(annotations)
    
    assert ANNOTATION_REQUESTOR_EMAIL not in result["metadata_json"]
    assert result["metadata_json"]["project-fyr/slack-channel"] == "#alerts"


def test_parse_namespace_annotations_empty():
    """Test that empty annotations dict is handled gracefully."""
    result = parse_namespace_annotations({})
    
    # Should return empty or minimal structure
    assert "metadata_json" in result
    assert ANNOTATION_REQUESTOR_EMAIL not in result["metadata_json"]


def test_parse_namespace_annotations_none():
    """Test that None annotations is handled gracefully."""
    result = parse_namespace_annotations(None)
    
    # Should return empty or minimal structure
    assert "metadata_json" in result
    assert ANNOTATION_REQUESTOR_EMAIL not in result["metadata_json"]


def test_parse_namespace_annotations_whitespace_requestor():
    """Test that whitespace-only requestor email is not included."""
    annotations = {
        "requestor": "   ",
        "project-fyr/team": "Platform",
    }
    result = parse_namespace_annotations(annotations)
    
    # Empty/whitespace value should not be included
    assert ANNOTATION_REQUESTOR_EMAIL not in result["metadata_json"]


def test_parse_namespace_annotations_ephenv_real_world():
    """Test realistic ephenv namespace annotation structure."""
    # This mimics what the ephenv operator actually creates
    annotations = {
        "requestor": "jane.developer@example.com",
        "ephenv.platform.example.com/environment-name": "test-nginx",
        "ephenv.platform.example.com/expires-at": "2026-02-13T10:30:00Z",
        "project-fyr/enabled": "true",
    }
    result = parse_namespace_annotations(annotations)
    
    # Should extract requestor and project-fyr annotations
    assert result["metadata_json"][ANNOTATION_REQUESTOR_EMAIL] == "jane.developer@example.com"
    assert result["metadata_json"]["project-fyr/enabled"] == "true"
    # Ephenv-specific annotations not captured (no project-fyr/ prefix)
    assert "ephenv.platform.example.com/environment-name" not in result["metadata_json"]
