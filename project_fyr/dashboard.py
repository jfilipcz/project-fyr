from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from typing import Iterator

from .db import init_db, RolloutRepo, Rollout, AnalysisRecord
from .config import settings

app = FastAPI(title="Project Fyr Dashboard")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Dependency
def get_repo() -> Iterator[RolloutRepo]:
    engine = init_db(settings.database_url)
    yield RolloutRepo(engine)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, repo: RolloutRepo = Depends(get_repo)):
    rollouts = repo.list_recent(limit=50)
    return templates.TemplateResponse("index.html", {"request": request, "rollouts": rollouts})

@app.get("/rollout/{rollout_id}", response_class=HTMLResponse)
async def detail(request: Request, rollout_id: int, repo: RolloutRepo = Depends(get_repo)):
    rollout = repo.get_by_id(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")
    
    # We also want the analysis if it exists.
    # Since we don't have a direct relationship loaded eagerly or a separate method for analysis,
    # we might need to fetch it. But Rollout has analysis_id.
    # Let's add get_analysis method to repo as well? Or just rely on lazy loading if session was open?
    # Session is closed after get_by_id returns.
    # So we need a way to fetch analysis.
    
    analysis_data = None
    if rollout.analysis_id:
        record = repo.get_analysis(rollout.analysis_id)
        if record:
            analysis_data = record.analysis

    return templates.TemplateResponse("detail.html", {"request": request, "rollout": rollout, "analysis": analysis_data})
