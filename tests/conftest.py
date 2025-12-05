import pytest
from unittest.mock import MagicMock
from kubernetes import client

@pytest.fixture
def mock_core_v1():
    return MagicMock(spec=client.CoreV1Api)

@pytest.fixture
def mock_apps_v1():
    return MagicMock(spec=client.AppsV1Api)

@pytest.fixture
def mock_db_session():
    return MagicMock()
