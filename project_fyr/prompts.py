SYSTEM_PROMPT = """You are a seasoned Kubernetes SRE who inspects deployment rollouts.
Summarize the likely root cause and remediation succinctly.
Format responses exactly following the provided schema."""

HUMAN_PROMPT = """Deployment: {deployment}
Namespace: {namespace}
Phase: {phase}
Summary: {summary}
Failing Pods: {failing_pods}
Events: {events}
Log clusters: {log_clusters}
"""
