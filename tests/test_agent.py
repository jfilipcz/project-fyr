from unittest.mock import MagicMock, patch
from project_fyr.agent import InvestigatorAgent

@patch("project_fyr.agent.ChatOpenAI")
@patch("project_fyr.agent.create_openai_tools_agent")
@patch("project_fyr.agent.AgentExecutor")
def test_investigate(mock_executor_cls, mock_create_agent, mock_chat):
    mock_executor = MagicMock()
    mock_executor_cls.return_value = mock_executor
    
    mock_executor.invoke.return_value = {"output": "Root cause: Misconfiguration"}
    
    agent = InvestigatorAgent(api_key="fake")
    analysis = agent.investigate("dep", "ns")
    
    assert analysis.summary == "Agent Investigation for dep"
    assert analysis.likely_cause == "Root cause: Misconfiguration"
    assert analysis.severity == "medium"

def test_investigate_disabled():
    agent = InvestigatorAgent(api_key=None)
    analysis = agent.investigate("dep", "ns")
    assert analysis.summary == "Agent disabled"

def test_investigate_mock():
    agent = InvestigatorAgent(model_name="mock")
    analysis = agent.investigate("dep", "ns")
    assert "[MOCK AGENT]" in analysis.summary
