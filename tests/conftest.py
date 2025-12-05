import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from project_fyr.db import Base, RolloutRepo

from sqlalchemy.pool import StaticPool

@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine

@pytest.fixture
def repo(engine):
    return RolloutRepo(engine)

@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
