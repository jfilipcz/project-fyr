# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Iterator
from project_fyr import utcnow
import logging

from .db import init_db, RolloutRepo, AlertRepo
from .config import settings
from .webhook import router as webhook_router

logger = logging.getLogger(__name__)

# Simple in-memory cache for insights
insights_cache = {
    "data": None,
    "timestamp": None,
    "hours": None
}

app = FastAPI(title="Project Fyr Dashboard")

@app.on_event("startup")
async def startup_event():
    """Initialize database tables and create default admin user if needed."""
    if settings.auth_enabled:
        from sqlalchemy import create_engine
        from .auth.models import Base as AuthBase, User
        from .auth.utils import hash_password
        from sqlalchemy.orm import Session

        # Create auth tables
        engine = create_engine(settings.database_url)
        AuthBase.metadata.create_all(engine)
        logger.info("Auth database tables initialized")

        # Create default admin user if it doesn't exist
        if settings.admin_password:
            with Session(engine) as session:
                admin = session.query(User).filter_by(username=settings.admin_username).first()
                if not admin:
                    admin = User(
                        username=settings.admin_username,
                        email=settings.admin_email,
                        hashed_password=hash_password(settings.admin_password),
                        is_admin=True,
                        is_active=True
                    )
                    session.add(admin)
                    session.commit()
                    logger.info(f"Created default admin user: {settings.admin_username}")
                else:
                    logger.info(f"Admin user already exists: {settings.admin_username}")

# Setup authentication if enabled
if settings.auth_enabled:
    from .auth import AuthenticationMiddleware, auth_router
    from .auth.providers.entra import EntraIDProvider
    from .auth.local_auth import LocalAuthProvider

    logger.info(f"Authentication enabled (mode: {settings.auth_mode})")

    # Initialize authentication providers
    sso_provider = None
    local_provider = None

    # Setup SSO provider if configured
    if settings.auth_mode in ["sso", "hybrid"]:
        if settings.sso_provider == "entra":
            if settings.sso_tenant_id and settings.sso_client_id:
                sso_provider = EntraIDProvider(
                    tenant_id=settings.sso_tenant_id,
                    client_id=settings.sso_client_id
                )
                logger.info("Entra ID (Azure AD) authentication enabled")
            else:
                logger.warning("Entra ID configured but tenant_id or client_id missing")
        elif settings.sso_provider in ("generic_oidc", "okta"):
            from .auth.providers.oidc import GenericOIDCProvider
            if settings.oidc_issuer or settings.oidc_jwks_uri:
                sso_provider = GenericOIDCProvider(
                    issuer=settings.oidc_issuer,
                    jwks_uri=settings.oidc_jwks_uri,
                    audience=settings.oidc_audience,
                    client_id=settings.sso_client_id,
                )
                logger.info("Generic OIDC authentication enabled (provider: %s)", settings.sso_provider)
            else:
                logger.warning("OIDC configured but oidc_issuer and oidc_jwks_uri are both missing")
        else:
            logger.warning(f"Unknown SSO provider: {settings.sso_provider}")

    # Setup local auth provider - ALWAYS needed for session token validation
    # Even in SSO mode, we need local provider to validate session tokens created after SSO login
    if settings.local_auth_enabled:
        local_provider = LocalAuthProvider(
            jwt_secret=settings.local_auth_jwt_secret,
            jwt_expiry_hours=settings.local_auth_jwt_expiry_hours
        )
        logger.info("Local authentication provider enabled for session token validation")
    else:
        logger.warning("Local auth disabled - session tokens will not work!")

    # Parse excluded paths
    exclude_paths = [p.strip() for p in settings.auth_exclude_paths.split(",")]
    # Always exclude auth endpoints and login page to avoid loops
    exclude_paths.extend(["/auth/", "/login"])

    # Add authentication middleware
    app.add_middleware(
        AuthenticationMiddleware,
        sso_provider=sso_provider,
        local_provider=local_provider,
        exclude_paths=exclude_paths,
        redirect_to_login=True
    )

    # Include auth endpoints
    app.include_router(auth_router)
else:
    logger.info("Authentication disabled")

# Include webhook router
app.include_router(webhook_router)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Mount static files for favicon and assets
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Shared database engine (created once, reused across requests)
_engine = None

def _get_engine():
    global _engine
    if _engine is None:
        _engine = init_db(settings.database_url)
    return _engine

# Dependency
def get_repo() -> Iterator[RolloutRepo]:
    yield RolloutRepo(_get_engine(), annotation_prefix=settings.annotation_prefix)

def get_alert_repo() -> Iterator[AlertRepo]:
    yield AlertRepo(_get_engine())

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render login page."""
    if not settings.auth_enabled:
        # If auth is disabled, redirect to home
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")

    return templates.TemplateResponse("login.html", {
        "request": request,
        "local_auth_enabled": settings.local_auth_enabled,
        "sso_enabled": settings.auth_mode in ["sso", "hybrid"] and settings.sso_provider is not None
    })

@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)."""
    return {"status": "healthy"}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, hours: int = 24, repo: RolloutRepo = Depends(get_repo)):
    stats = repo.get_stats(hours=hours)

    return templates.TemplateResponse("overview.html", {
        "request": request,
        "stats": stats,
        "hours": hours,
        "show_ai_summary": settings.overview_show_ai_summary,
    })

@app.get("/rollouts", response_class=HTMLResponse)
async def rollouts_list(request: Request, status: str = None, namespace: str = None, requestor: str = None):
    """Render rollouts page immediately without blocking on data."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "current_status": status,
        "current_namespace": namespace,
        "current_requestor": requestor,
    })

@app.get("/api/rollouts")
async def get_rollouts_data(
    status: str = None,
    namespace: str = None,
    requestor: str = None,
    include_system: bool = False,
    repo: RolloutRepo = Depends(get_repo),
):
    """API endpoint to fetch rollouts data asynchronously."""
    exclude_system = not include_system

    # Filter by status and/or namespace if provided
    if status and namespace:
        rollouts = repo.list_by_status_and_namespace(status, namespace, limit=50, requestor=requestor)
    elif status:
        rollouts = repo.list_by_status(status, limit=50, exclude_system=exclude_system, requestor=requestor)
    elif namespace:
        rollouts = repo.list_by_namespace(namespace, limit=50, requestor=requestor)
    else:
        rollouts = repo.list_recent(limit=50, exclude_system=exclude_system, requestor=requestor)

    return {
        "rollouts": [
            {
                "id": r.id,
                "cluster": r.cluster,
                "namespace": r.namespace,
                "deployment": r.deployment,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "team": r.team
            }
            for r in rollouts
        ],
        "count": len(rollouts)
    }

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
        if record and record.analysis:
            # analysis is stored as a dict, Jinja2 can access it
            analysis_data = record.analysis

    return templates.TemplateResponse("detail.html", {"request": request, "rollout": rollout, "analysis": analysis_data, "annotation_prefix": settings.annotation_prefix})

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
        logger.error(f"Investigation failed for {namespace}/{deployment}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Investigation failed. Check server logs for details.")

@app.get("/investigate", response_class=HTMLResponse)
async def investigate_page(request: Request):
    """Render investigate page immediately without blocking on k8s queries."""
    return templates.TemplateResponse("investigate.html", {"request": request})

@app.get("/api/investigate/deployments")
async def get_deployments_data(include_system: bool = False):
    """API endpoint to fetch deployment data asynchronously."""
    from kubernetes import client, config
    from .config import settings

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()

    v1 = client.AppsV1Api()
    core = client.CoreV1Api()

    namespaces = [
        ns.metadata.name for ns in core.list_namespace().items
        if include_system or ns.metadata.name not in settings.system_namespaces
    ]
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

    return {
        "namespaces": namespaces,
        "deployments": deployments,
        "deployment_statuses": deployment_statuses
    }


@app.post("/api/investigate/namespace/{namespace}")
async def analyze_namespace(namespace: str):
    """
    Perform on-demand AI analysis of a namespace.

    Returns comprehensive analysis including:
    - Deployment health
    - Pod issues
    - Recent events
    - AI-powered recommendations
    """
    from kubernetes import client, config
    from .namespace_analyzer import NamespaceAnalyzer

    logger.info(f"Starting namespace analysis for: {namespace}")

    try:
        try:
            config.load_incluster_config()
        except Exception as e:
            logger.debug(f"Failed to load in-cluster config, trying kubeconfig: {e}")
            config.load_kube_config()
    except Exception as e:
        logger.error(f"Failed to load Kubernetes config: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect to Kubernetes cluster")

    try:
        core_v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
    except Exception as e:
        logger.error(f"Failed to create Kubernetes API clients: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize Kubernetes clients")

    # Create analyzer
    try:
        analyzer = NamespaceAnalyzer(
            model_name=settings.langchain_model_name,
            api_key=settings.openai_api_key,
            api_base=settings.openai_api_base,
            api_version=settings.openai_api_version,
            azure_deployment=settings.azure_deployment,
        )
    except Exception as e:
        logger.error(f"Failed to create NamespaceAnalyzer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to initialize namespace analyzer")

    # Perform analysis
    try:
        result = analyzer.analyze_namespace(
            namespace=namespace,
            core_v1=core_v1,
            apps_v1=apps_v1,
        )
        logger.info(f"Successfully completed namespace analysis for: {namespace}")
        return result
    except Exception as e:
        logger.error(f"Error analyzing namespace {namespace}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Namespace analysis failed. Check server logs for details.")


@app.post("/api/investigate/chat")
async def chat_with_agent(request: dict):
    """
    Interactive chat with the K8s investigation agent.

    Request body:
    {
        "namespace": "namespace-name",
        "deployment": "deployment-name" (optional),
        "messages": [
            {"role": "user", "content": "message"},
            {"role": "assistant", "content": "response"},
            ...
        ]
    }

    Returns the agent's response with updated message history.
    """
    from kubernetes import config
    from .agent import InvestigatorAgent

    namespace = request.get("namespace")
    deployment = request.get("deployment")
    messages = request.get("messages", [])

    if not namespace:
        raise HTTPException(status_code=400, detail="namespace is required")

    if not messages or not messages[-1].get("content"):
        raise HTTPException(status_code=400, detail="messages with content required")

    logger.info(f"Chat request for namespace: {namespace}, deployment: {deployment}")

    # Load k8s config
    try:
        try:
            config.load_incluster_config()
        except Exception as e:
            logger.debug(f"Failed to load in-cluster config, trying kubeconfig: {e}")
            config.load_kube_config()
    except Exception as e:
        error_msg = f"Failed to load Kubernetes config: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

    # Create investigator agent
    try:
        investigator = InvestigatorAgent(
            model_name=settings.langchain_model_name,
            api_key=settings.openai_api_key,
            api_base=settings.openai_api_base,
            api_version=settings.openai_api_version,
            azure_deployment=settings.azure_deployment,
        )
    except Exception as e:
        error_msg = f"Failed to create InvestigatorAgent: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)

    # Check if agent is available
    if not hasattr(investigator, '_agent') or not investigator._agent:
        raise HTTPException(
            status_code=503,
            detail="Agent not available - OpenAI API may not be configured"
        )

    # Add context to the conversation - always include system message with namespace info
    formatted_messages = []

    # Always start with system context so agent knows which namespace/deployment
    context = f"You are investigating Kubernetes resources in namespace '{namespace}'"
    if deployment:
        context += f", focusing on deployment '{deployment}'"
    context += (
        ". Use your READ-ONLY k8s tools to help answer questions and investigate issues. "
        "You CANNOT and MUST NOT make any changes to the cluster. "
        "Explain findings in clear, simple terms suitable for developers and non-SRE users. "
        "Format your responses in Markdown with headings, bullet points, and code formatting for better readability."
    )

    formatted_messages.append({
        "role": "system",
        "content": context
    })

    # Add all conversation messages
    for msg in messages:
        formatted_messages.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", "")
        })

    # Invoke agent with conversation history
    try:
        logger.info(f"Invoking agent with {len(formatted_messages)} messages")
        result = investigator._agent.invoke({"messages": formatted_messages})

        # Extract response
        result_messages = result.get("messages", [])
        if result_messages:
            last_message = result_messages[-1]
            response_text = last_message.content if hasattr(last_message, 'content') else str(last_message)

            # Count iterations for logging
            iteration_count = sum(1 for msg in result_messages if hasattr(msg, 'type') and msg.type == 'ai')
            logger.info(f"Chat response generated in {iteration_count} iterations")

            return {
                "response": response_text,
                "status": "success"
            }
        else:
            raise HTTPException(status_code=500, detail="No response from agent")

    except Exception as e:
        error_msg = f"Error in chat interaction: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)


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

    from sqlalchemy import select
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


@app.get("/overview", response_class=HTMLResponse)
async def overview_legacy(request: Request, hours: int = 24, include_system: bool = False, repo: RolloutRepo = Depends(get_repo)):
    """Legacy route - redirects to main page"""
    exclude_system = not include_system
    stats = repo.get_stats(hours=hours, exclude_system=exclude_system)

    return templates.TemplateResponse("overview.html", {
        "request": request,
        "stats": stats,
        "hours": hours
    })


@app.get("/api/overview/insights")
async def get_overview_insights(hours: int = 24, include_system: bool = False, repo: RolloutRepo = Depends(get_repo)):
    """Get AI-aggregated insights for recent failures (with caching)."""
    from .aggregator import IssueAggregator

    exclude_system = not include_system

    # Check cache
    now = utcnow()
    cache_valid = (
        insights_cache["data"] is not None and
        insights_cache["timestamp"] is not None and
        insights_cache["hours"] == hours and
        (now - insights_cache["timestamp"]).total_seconds() < settings.insights_cache_ttl_minutes * 60
    )

    if cache_valid:
        logger.debug(f"Returning cached insights (age: {(now - insights_cache['timestamp']).total_seconds():.0f}s)")
        return insights_cache["data"]

    # Get recent failures with analysis
    failures = repo.get_recent_failures(limit=50, hours=hours, exclude_system=exclude_system)

    if not failures:
        result = {
            "top_issues": [],
            "summary": "No failures detected in the selected time window (or no analysis available)."
        }
    else:
        aggregator = IssueAggregator(
            model_name=settings.langchain_model_name,
            api_key=settings.openai_api_key,
            api_base=settings.openai_api_base,
            api_version=settings.openai_api_version,
            azure_deployment=settings.azure_deployment
        )
        result = aggregator.aggregate_issues(failures)

    # Update cache
    insights_cache["data"] = result
    insights_cache["timestamp"] = now
    insights_cache["hours"] = hours
    logger.info(f"Cached new insights for {hours}h window")

    return result
