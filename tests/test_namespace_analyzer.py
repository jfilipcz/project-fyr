from project_fyr.namespace_analyzer import NamespaceAnalyzer


class _FakeMessage:
    type = "ai"
    content = "analysis-ok"


class _CapturingAgent:
    def __init__(self):
        self.prompt = ""

    def invoke(self, payload, config=None):
        self.prompt = payload["messages"][0]["content"]
        return {"messages": [_FakeMessage()]}


def test_generate_ai_analysis_uses_concise_prompt():
    capturing_agent = _CapturingAgent()
    analyzer = NamespaceAnalyzer.__new__(NamespaceAnalyzer)
    analyzer.investigator = type("InvestigatorStub", (), {"_agent": capturing_agent})()

    result = analyzer._generate_ai_analysis(
        namespace="test-ns",
        deployments=[
            {
                "name": "svc-api",
                "healthy": False,
                "ready_replicas": 0,
                "desired_replicas": 1,
            }
        ],
        pods=[
            {
                "name": "svc-api-123",
                "phase": "Pending",
                "healthy": False,
                "issues": ["Container waiting: ImagePullBackOff"],
            }
        ],
        events=[
            {
                "type": "Warning",
                "reason": "Failed",
                "message": "Back-off pulling image",
            }
        ],
    )

    assert result == "analysis-ok"
    assert "IMPORTANT - PRESENTATION GUIDELINES:" not in capturing_agent.prompt
    assert "OUTPUT FORMAT - USE MARKDOWN:" not in capturing_agent.prompt
    assert "Start with a CLEAR STATUS" not in capturing_agent.prompt
    assert "Keep the response concise" in capturing_agent.prompt
