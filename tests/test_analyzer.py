from unittest.mock import MagicMock, patch
from project_fyr.analyzer import Analyzer
from project_fyr.models import ReducedContext, Analysis

def test_analyzer_disabled():
    analyzer = Analyzer(model_name="gpt-4", api_key=None)
    context = ReducedContext(
        namespace="default",
        deployment="test-dep",
        generation=1,
        summary="test summary",
        phase="FAILED",
        failing_pods=[],
        log_clusters=[],
        events=[]
    )
    analysis = analyzer.analyze(context)
    assert analysis.likely_cause == "LLM disabled"

@patch("project_fyr.analyzer.ChatOpenAI")
def test_analyzer_enabled(mock_chat_openai):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = Analysis(
        summary="Analyzed summary",
        likely_cause="Test cause",
        recommended_steps=["Step 1"],
        severity="high"
    )
    
    # Mock the chain creation
    with patch("project_fyr.analyzer.ChatPromptTemplate") as mock_prompt:
        with patch("project_fyr.analyzer.PydanticOutputParser") as mock_parser:
             # We need to mock the chain construction: prompt | model | parser
            mock_prompt_instance = mock_prompt.from_messages.return_value
            mock_model_instance = mock_chat_openai.return_value
            mock_parser_instance = mock_parser.return_value
            
            # Mock the chain behavior
            mock_prompt_instance.__or__.return_value.__or__.return_value = mock_chain

            analyzer = Analyzer(model_name="gpt-4", api_key="sk-test")
            
            context = ReducedContext(
                namespace="default",
                deployment="test-dep",
                generation=1,
                summary="test summary",
                phase="FAILED",
                failing_pods=[],
                log_clusters=[],
                events=[]
            )
            analysis = analyzer.analyze(context)
            
            assert analysis.summary == "Analyzed summary"
            assert analysis.likely_cause == "Test cause"
