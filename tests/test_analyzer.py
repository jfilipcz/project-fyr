from project_fyr.models import ReducedContext, Analysis
from project_fyr.analyzer import Analyzer

def test_analyzer_disabled():
    analyzer = Analyzer(model_name="gpt-4", api_key=None)
    context = ReducedContext(
        namespace="default",
        deployment="web",
        generation=1,
        summary="summary",
        phase="FAILED",
        failing_pods=[],
        log_clusters=[],
        events=[]
    )
    analysis = analyzer.analyze(context)
    assert analysis.likely_cause == "LLM disabled"
