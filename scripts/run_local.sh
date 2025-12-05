#!/bin/bash
set -e

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

export PROJECT_FYR_DATABASE_URL="sqlite:///./project_fyr.db"
export PROJECT_FYR_K8S_CLUSTER_NAME="local-cluster"
export PROJECT_FYR_ROLLOUT_TIMEOUT_SECONDS=60
export PROJECT_FYR_LOG_TAIL_SECONDS=60
export PROJECT_FYR_LANGCHAIN_MODEL_NAME="mock"
export PROJECT_FYR_OPENAI_API_KEY="dummy"
export PROJECT_FYR_SLACK_DEFAULT_CHANNEL="#local-test"
export PROJECT_FYR_SLACK_MOCK_LOG_FILE="slack_messages.log"

echo "Starting Watcher and Analyzer services..."

# Run watcher in background
python -m project_fyr.watcher_service &
WATCHER_PID=$!

# Run analyzer in background
python -m project_fyr.analyzer_service &
ANALYZER_PID=$!

echo "Services started. Press Ctrl+C to stop."

trap "kill $WATCHER_PID $ANALYZER_PID; exit" SIGINT SIGTERM

wait
