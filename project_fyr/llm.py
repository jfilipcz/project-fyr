# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""
LLM factory — centralises the creation of LangChain chat model instances.

Supports:
  * Azure OpenAI  (when ``api_base`` and ``azure_deployment`` are set)
  * OpenAI        (when only ``api_key`` is set)
  * Any OpenAI-compatible endpoint (when ``api_base`` is set without
    ``azure_deployment``)

All call-sites should use ``create_llm(settings)`` instead of
constructing ``ChatOpenAI`` / ``AzureChatOpenAI`` directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from .config import Settings

logger = logging.getLogger(__name__)


def create_llm(
    settings: "Settings",
    *,
    temperature: float = 1.0,
) -> "BaseChatModel":
    """Return a LangChain chat model configured from *settings*.

    Parameters
    ----------
    settings:
        Application settings that carry the LLM configuration fields:
        ``langchain_model_name``, ``openai_api_key``, ``openai_api_base``,
        ``openai_api_version``, ``azure_deployment``.
    temperature:
        Sampling temperature forwarded to the model.
    """
    model_name = settings.langchain_model_name
    api_key = settings.openai_api_key
    api_base = settings.openai_api_base
    api_version = settings.openai_api_version
    azure_deployment = settings.azure_deployment

    if azure_deployment and api_base:
        from langchain_openai import AzureChatOpenAI

        logger.info(
            "Using Azure OpenAI (deployment=%s, endpoint=%s)",
            azure_deployment,
            api_base,
        )
        return AzureChatOpenAI(
            model=azure_deployment,
            azure_deployment=azure_deployment,
            temperature=temperature,
            api_key=api_key,
            azure_endpoint=api_base,
            api_version=api_version or "2024-08-01-preview",
        )

    # Vanilla OpenAI or any OpenAI-compatible endpoint
    kwargs: dict = {"model": model_name, "temperature": temperature}
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["base_url"] = api_base
        logger.info("Using OpenAI-compatible endpoint: %s", api_base)
    else:
        logger.info("Using OpenAI (model=%s)", model_name)

    return ChatOpenAI(**kwargs)
