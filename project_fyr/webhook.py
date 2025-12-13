from fastapi import APIRouter, Header, HTTPException, Request, Depends
from typing import Optional, Iterator
from datetime import datetime
import logging

from .config import settings
from .db import init_db, AlertRepo

logger = logging.getLogger(__name__)

router = APIRouter()

def get_alert_repo() -> Iterator[AlertRepo]:
    engine = init_db(settings.database_url)
    yield AlertRepo(engine)

@router.post("/webhook/alert", status_code=202)
async def receive_alert(
    request: Request,
    x_alert_token: Optional[str] = Header(None, alias="X-Alert-Token"),
    repo: AlertRepo = Depends(get_alert_repo)
):
    # Auth check
    if settings.alert_webhook_secret and x_alert_token != settings.alert_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid alert token")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Basic validation - Alertmanager/Grafana format usually has "alerts" list
    alerts = payload.get("alerts", [])
    if not isinstance(alerts, list):
        # Maybe it's a single alert? Grafana can send different shapes.
        # But let's assume standard wrapper for now.
        logger.warning(f"Received payload without 'alerts' list: {payload.keys()}")
        return {"status": "ignored", "reason": "no alerts found"}

    saved_count = 0
    for item in alerts:
        # Extract fields
        status = item.get("status", "firing")
        labels = item.get("labels", {})
        annotations = item.get("annotations", {})
        starts_at_str = item.get("startsAt")
        ends_at_str = item.get("endsAt")
        fingerprint = item.get("fingerprint") or labels.get("alertname", "unknown")
        
        # Parse times
        starts_at = datetime.utcnow()
        if starts_at_str:
            try:
                starts_at = datetime.fromisoformat(starts_at_str.replace("Z", "+00:00"))
            except ValueError:
                pass
        
        ends_at = None
        if ends_at_str and ends_at_str != "0001-01-01T00:00:00Z":
             try:
                ends_at = datetime.fromisoformat(ends_at_str.replace("Z", "+00:00"))
             except ValueError:
                pass

        repo.create_alert(
            fingerprint=fingerprint,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            labels=labels,
            annotations=annotations,
            payload=item
        )
        saved_count += 1

    logger.info(f"Webhook received {len(alerts)} alerts, saved {saved_count}")
    return {"status": "accepted", "count": saved_count}
