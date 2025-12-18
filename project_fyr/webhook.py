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

from datetime import datetime, timedelta

# ... imports ...

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
    investigation_triggered_count = 0
    skipped_count = 0
    
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
        
        # --- Stateful Logic ---
        now = datetime.utcnow()
        should_investigate = False
        
        # Get current state
        state = repo.get_state(fingerprint)
        
        # Sticky Throttling: Check if we investigated recently, regardless of status.
        is_throttled = False
        if state and state.last_investigated_at:
            elapsed = now - state.last_investigated_at
            if elapsed < timedelta(hours=24):
                is_throttled = True
                logger.info(f"Alert {fingerprint} is throttled (investigated {elapsed} ago)")

        if status == "firing":
            if is_throttled:
                should_investigate = False
            elif not state or state.status == "resolved":
                # New alert or previously resolved (and not throttled) -> Trigger
                should_investigate = True
            elif state.status == "firing":
                # Already firing and not throttled (implied > 24h by is_throttled check) -> Re-trigger
                # Wait, if is_throttled is false, it means either never investigated OR > 24h.
                # So we can just say True here.
                should_investigate = True
                logger.info(f"Re-triggering investigation for persistent alert {fingerprint}")
        
        elif status == "resolved":
            should_investigate = False
            logger.info(f"Alert {fingerprint} resolved")

        # Create the alert record
        # If throttled or resolved, we mark it as 'batched=True' immediately (conceptually 'handled' or 'skipped')
        # so the batcher doesn't pick it up.
        # Ideally we'd have a separate status like 'batched' vs 'skipped', but 'batched=True' effectively hides it from batcher.
        # We can use batch_id=-1 to indicate skipping if needed, but simple boolean is enough to hide it.
        
        alert_record = repo.create_alert(
            fingerprint=fingerprint,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            labels=labels,
            annotations=annotations,
            payload=item
        )
        
        if not should_investigate:
            # Mark as processed/skipped to avoid batcher pickup
            # We can do this by updating 'batched' to True immediately (or 1 in sqlite)
            # We need to do this manually since create_alert doesn't support it yet
            # Or we could modify repo.create_alert to accept kwargs for batched.
            # For now, let's update it.
            # Optimization: could add 'batched' to create_alert arguments.
            # But adhering to minimum changes, let's do a quick update or rely on `batched` default being 0.
            # Wait, `create_alert` returns the record. We can set it?
            # No, `create_alert` commits. Use a simple update in repo? 
            # Actually, let's just use SQL.
            pass # See below, we need to modify logic to set batched=True if skipped.
            
            # Since create_alert commits, we might want to update it.
            # But cleaner is to update `repo.create_alert` to accept `batched` param?
            # Or just hack it:
            from sqlalchemy import text
            with repo.session() as s:
                s.execute(
                    text("UPDATE alerts SET batched = 1 WHERE id = :id"),
                    {"id": alert_record.id}
                )
                s.commit()
            # NOTE: raw SQL is a bit risky if we change DBs, but this is SQLite.
            # Better to add `repo.mark_as_processed(alert_record.id)` or similar.
            # Let's rely on standard practice: update the state, then handle the record.
            
            skipped_count += 1
            
        else:
            investigation_triggered_count += 1
            
        # Update State
        repo.update_state(
            fingerprint=fingerprint,
            status=status,
            now=now,
            investigated=should_investigate
        )

        saved_count += 1

    logger.info(f"Webhook processed {len(alerts)} alerts: {investigation_triggered_count} triggered, {skipped_count} skipped/throttled.")
    return {"status": "accepted", "count": saved_count, "triggered": investigation_triggered_count}
