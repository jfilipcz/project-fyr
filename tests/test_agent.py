from unittest.mock import MagicMock, patch
from project_fyr.agent import InvestigatorAgent

@patch("project_fyr.agent.ChatOpenAI")
@patch("project_fyr.agent.AgentExecutor")
@patch("project_fyr.agent.create_openai_tools_agent")
def test_investigate(mock_create_agent, mock_agent_executor, mock_chat):
    # Mock the agent that create_openai_tools_agent returns
    mock_agent_runnable = MagicMock()
    mock_create_agent.return_value = mock_agent_runnable
    
    # Mock the AgentExecutor
    mock_executor_instance = MagicMock()
    mock_agent_executor.return_value = mock_executor_instance
    
    # Mock the invoke response with messages format
    mock_message = MagicMock()
    mock_message.content = "Root cause: Misconfiguration"
    mock_message.type = "ai"
    mock_executor_instance.invoke.return_value = {"messages": [mock_message]}
    
    agent = InvestigatorAgent(api_key="fake")
    analysis = agent.investigate("dep", "ns")
    
    assert analysis.summary == "Agent Investigation for dep"
    assert "Root cause: Misconfiguration" in analysis.likely_cause
    assert analysis.severity == "medium"

def test_investigate_disabled():
    agent = InvestigatorAgent(api_key=None)
    analysis = agent.investigate("dep", "ns")
    assert analysis.summary == "Agent disabled"

def test_investigate_mock():
    agent = InvestigatorAgent(model_name="mock")
    analysis = agent.investigate("dep", "ns")
    assert "[MOCK AGENT]" in analysis.summary
