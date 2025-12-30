from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from typing import Iterator

from .db import init_db, RolloutRepo, Rollout, AnalysisRecord, AlertRepo
from .config import settings
from .webhook import router as webhook_router

app = FastAPI(title="Project Fyr Dashboard")
app.include_router(webhook_router)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Dependency
def get_repo() -> Iterator[RolloutRepo]:
    engine = init_db(settings.database_url)
    yield RolloutRepo(engine)

def get_alert_repo() -> Iterator[AlertRepo]:
    engine = init_db(settings.database_url)
    yield AlertRepo(engine)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, status: str = None, namespace: str = None, repo: RolloutRepo = Depends(get_repo)):
    # Filter by status and/or namespace if provided
    if status and namespace:
        rollouts = repo.list_by_status_and_namespace(status, namespace, limit=50)
    elif status:
        rollouts = repo.list_by_status(status, limit=50)
    elif namespace:
        rollouts = repo.list_by_namespace(namespace, limit=50)
    else:
        rollouts = repo.list_recent(limit=50)
    
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "rollouts": rollouts,
        "current_status": status,
        "current_namespace": namespace
    })

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

@app.post("/api/investigate")
async def investigate(request: Request):
    data = await request.json()
    deployment = data.get("deployment")
    namespace = data.get("namespace")
    
    if not deployment or not namespace:
        raise HTTPException(status_code=400, detail="Missing deployment or namespace")
        
    from .agent import InvestigatorAgent
    
    agent = InvestigatorAgent(
        model_name=settings.langchain_model_name,
        api_key=settings.openai_api_key,
        api_base=settings.openai_api_base,
        api_version=settings.openai_api_version,
        azure_deployment=settings.azure_deployment
    )
    
    try:
        analysis = agent.investigate(deployment, namespace)
        return analysis.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/investigate", response_class=HTMLResponse)
async def investigate_page(request: Request):
    from kubernetes import client, config
    try:
        config.load_incluster_config()
    except:
        config.load_kube_config()
        
    v1 = client.AppsV1Api()
    core = client.CoreV1Api()
    
    namespaces = [ns.metadata.name for ns in core.list_namespace().items]
    deployments = {}
    deployment_statuses = {}
    
    for ns in namespaces:
        deps = v1.list_namespaced_deployment(ns).items
        if deps:
            deployments[ns] = []
            for d in deps:
                dep_name = d.metadata.name
                deployments[ns].append(dep_name)
                
                # Check if deployment is healthy
                ready_replicas = d.status.ready_replicas or 0
                desired_replicas = d.spec.replicas or 0
                is_failing = ready_replicas < desired_replicas
                
                deployment_statuses[f"{ns}/{dep_name}"] = {
                    "failing": is_failing,
                    "ready": ready_replicas,
                    "desired": desired_replicas
                }
            
    return templates.TemplateResponse("investigate.html", {
        "request": request, 
        "namespaces": namespaces, 
        "deployments": deployments,
        "deployment_statuses": deployment_statuses
    })

@app.get("/alerts", response_class=HTMLResponse)
async def alerts_index(request: Request, repo: AlertRepo = Depends(get_alert_repo)):
    # We need to fetch batches. AlertRepo doesn't have list_batches yet.
    # Let's add it ad-hoc or assume we added it.
    # Wait, I didn't add list_batches to AlertRepo in previous step.
    # I should add it now or use direct session.
    # Let's use direct session for now to avoid another file edit if possible, 
    # but cleaner to add to Repo.
    # Actually, I can just add the method to AlertRepo in db.py first?
    # Or just do a query here.
    
    from sqlalchemy import select, desc
    from .db import AlertBatchRecord
    
    stmt = select(AlertBatchRecord).order_by(AlertBatchRecord.created_at.desc()).limit(50)
    with repo.session() as s:
        batches = s.scalars(stmt).all()
        
    return templates.TemplateResponse("alerts.html", {"request": request, "batches": batches})

@app.get("/alerts/{batch_id}", response_class=HTMLResponse)
async def alert_detail(request: Request, batch_id: int, repo: AlertRepo = Depends(get_alert_repo)):
    batch = repo.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
        
    alerts = repo.get_batch_alerts(batch_id)
    
    # Get job status
    # We need to find the job for this batch
    from .db import InvestigationJob
    from sqlalchemy import select
    
    stmt = select(InvestigationJob).where(InvestigationJob.alert_batch_id == batch_id)
    with repo.session() as s:
        job = s.scalars(stmt).first()
        
    return templates.TemplateResponse("alert_detail.html", {
        "request": request, 
        "batch": batch, 
        "alerts": alerts,
        "job": job
    })

