#!/bin/bash

# Script to create Slack secrets for Fyr
# Usage: ./create-slack-secrets.sh

set -e

NAMESPACE="project-fyr"
SECRET_NAME="fyr-slack-secrets"

echo "Creating Slack secrets for Fyr..."
echo ""

# Check if tokens are provided as files or prompt for them
if [ -f "../project-fyr-deployment/slack-bot-token.txt" ]; then
    SLACK_BOT_TOKEN=$(cat ../project-fyr-deployment/slack-bot-token.txt)
    echo "✓ Found slack-bot-token.txt"
else
    echo -n "Enter SLACK_BOT_TOKEN (xoxb-...): "
    read -r SLACK_BOT_TOKEN
fi

if [ -f "../project-fyr-deployment/slack-app-token.txt" ]; then
    SLACK_APP_TOKEN=$(cat ../project-fyr-deployment/slack-app-token.txt)
    echo "✓ Found slack-app-token.txt"
else
    echo -n "Enter SLACK_APP_TOKEN (xapp-...): "
    read -r SLACK_APP_TOKEN
fi

if [ -f "../project-fyr-deployment/slack-signing-secret.txt" ]; then
    SLACK_SIGNING_SECRET=$(cat ../project-fyr-deployment/slack-signing-secret.txt)
    echo "✓ Found slack-signing-secret.txt"
else
    echo -n "Enter SLACK_SIGNING_SECRET: "
    read -r SLACK_SIGNING_SECRET
fi

echo ""
echo "Validating tokens..."

# Basic validation
if [[ ! $SLACK_BOT_TOKEN =~ ^xoxb- ]]; then
    echo "❌ Error: SLACK_BOT_TOKEN should start with 'xoxb-'"
    exit 1
fi

if [[ ! $SLACK_APP_TOKEN =~ ^xapp- ]]; then
    echo "❌ Error: SLACK_APP_TOKEN should start with 'xapp-'"
    exit 1
fi

if [ ${#SLACK_SIGNING_SECRET} -lt 32 ]; then
    echo "❌ Error: SLACK_SIGNING_SECRET seems too short"
    exit 1
fi

echo "✓ Tokens validated"
echo ""

# Check if secret already exists
if kubectl get secret $SECRET_NAME -n $NAMESPACE &>/dev/null; then
    echo "Secret '$SECRET_NAME' already exists."
    echo -n "Do you want to delete and recreate it? (y/N): "
    read -r answer
    if [[ $answer =~ ^[Yy]$ ]]; then
        kubectl delete secret $SECRET_NAME -n $NAMESPACE
        echo "✓ Deleted existing secret"
    else
        echo "Aborting."
        exit 1
    fi
fi

# Create the secret
kubectl create secret generic $SECRET_NAME \
    -n $NAMESPACE \
    --from-literal=slack-bot-token="$SLACK_BOT_TOKEN" \
    --from-literal=slack-app-token="$SLACK_APP_TOKEN" \
    --from-literal=slack-signing-secret="$SLACK_SIGNING_SECRET"

echo ""
echo "✅ Secret '$SECRET_NAME' created successfully!"
echo ""
echo "Next steps:"
echo "1. Update Helm values to enable Slack integration"
echo "2. Upgrade Helm release to mount the secrets"
echo "3. Deploy the Slack handler pod"
