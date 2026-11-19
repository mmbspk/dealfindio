"""
Waitlist router — pre-launch email capture for dealfindio.com.au.

No authentication required; unauthenticated visitors on the landing page
submit their email to be notified at launch.
"""

import logging
import re

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.waitlist import WaitlistEmail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Waitlist"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class WaitlistSubscribeRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        if len(v) > 254:
            raise ValueError("Email address too long")
        return v


@router.post("/subscribe", status_code=status.HTTP_201_CREATED)
def subscribe(body: WaitlistSubscribeRequest, db: Session = Depends(get_db)):
    """
    Add an email address to the pre-launch waitlist.

    Idempotent: submitting an already-registered address returns 200 OK
    rather than an error so the frontend always shows a success state.
    """
    entry = WaitlistEmail(email=body.email.lower().strip())
    db.add(entry)
    try:
        db.commit()
        logger.info("waitlist.subscribe email=%s", body.email)
        return {"ok": True, "created": True}
    except IntegrityError:
        db.rollback()
        logger.debug("waitlist.subscribe duplicate email=%s", body.email)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"ok": True, "created": False},
        )
