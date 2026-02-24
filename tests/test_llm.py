"""Tests for project_fyr.llm — LLM factory."""

from unittest.mock import MagicMock, patch

from project_fyr.llm import create_llm


def _make_settings(**overrides):
    """Build a minimal Settings-like object for create_llm."""
    defaults = {
        "langchain_model_name": "gpt-4o",
        "openai_api_key": "sk-test-key",
        "openai_api_base": None,
        "openai_api_version": None,
        "azure_deployment": None,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


@patch("project_fyr.llm.ChatOpenAI")
def test_create_llm_openai(mock_cls):
    """Default path returns a ChatOpenAI instance."""
    settings = _make_settings()
    create_llm(settings, temperature=0.5)

    mock_cls.assert_called_once_with(
        model="gpt-4o",
        temperature=0.5,
        api_key="sk-test-key",
    )


@patch("project_fyr.llm.ChatOpenAI")
def test_create_llm_compatible_endpoint(mock_cls):
    """When api_base is set without azure_deployment, uses base_url."""
    settings = _make_settings(openai_api_base="http://localhost:11434/v1")
    create_llm(settings)

    call_kwargs = mock_cls.call_args[1]
    assert call_kwargs["base_url"] == "http://localhost:11434/v1"
    assert call_kwargs["model"] == "gpt-4o"


@patch("langchain_openai.AzureChatOpenAI")
def test_create_llm_azure(mock_azure_cls):
    """When both azure_deployment and api_base are set, returns AzureChatOpenAI."""
    settings = _make_settings(
        azure_deployment="gpt-4o-dep",
        openai_api_base="https://my-resource.openai.azure.com",
        openai_api_version="2024-08-01-preview",
    )
    create_llm(settings, temperature=0.7)

    mock_azure_cls.assert_called_once()
    call_kwargs = mock_azure_cls.call_args[1]
    assert call_kwargs["azure_deployment"] == "gpt-4o-dep"
    assert call_kwargs["azure_endpoint"] == "https://my-resource.openai.azure.com"
    assert call_kwargs["temperature"] == 0.7


@patch("project_fyr.llm.ChatOpenAI")
def test_create_llm_no_api_key(mock_cls):
    """When no api_key is provided, it is omitted from kwargs."""
    settings = _make_settings(openai_api_key=None)
    create_llm(settings)

    call_kwargs = mock_cls.call_args[1]
    assert "api_key" not in call_kwargs
