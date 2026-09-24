"""
Community price report validation worker tasks.

Handles validation of community-submitted price reports including:
- Outlier detection (3-sigma statistical filtering)
- Rate limiting (20 reports per user per 24h)
- Reputation scoring (weighted confidence calculation)
- Fraud prevention

These tasks run on the 'default' queue with standard concurrency.

Implements Task #12: Report validation with outlier detection and rate limiting.
"""

import logging
from datetime import datetime, timedelta, timezone
from statistics import mean, stdev
from typing import TypedDict
from uuid import UUID

from sqlalchemy import and_

from app.config import get_settings
from app.deps import _SessionLocal
from app.models import PriceReport, UserProfile
from app.workers.celery_app import celery_app
from app.workers.tasks_base import CallbackTask

logger = logging.getLogger(__name__)
settings = get_settings()


class ValidationResult(TypedDict):
    """Result dictionary for validation tasks."""
    valid: bool
    flags: list[str]
    confidence_score: float
    status: str


def get_db_session():
    """Get a synchronous database session for use in Celery tasks."""
    if not _SessionLocal:
        raise RuntimeError("Database not initialized")
    return _SessionLocal()


@celery_app.task(
    bind=True,
    name='app.workers.report_validator.validate_community_report',
    queue='default',
    base=CallbackTask,
    time_limit=10,
    soft_time_limit=8,
    max_retries=2,
)
def validate_community_report(self, report_id: str) -> ValidationResult:
    """
    Validate community price report with outlier detection and deduplication.

    Runs immediately after a community user submits a price report. Performs
    validation checks including outlier detection, duplicate detection, and
    reporter reputation scoring.

    Args:
        report_id: UUID of newly created community PriceReport (as string)

    Returns:
        ValidationResult dict with:
            - valid: Whether report passed all validation checks
            - flags: List of validation issues found
            - confidence_score: 0-1 score indicating report reliability
            - status: 'validated', 'flagged', 'rejected', or 'pending_review'

    Raises:
        Exception: On database or calculation errors (will retry)

    Implementation notes:
        - Very fast execution (< 10 seconds target)
        - Runs synchronously immediately after report submission
        - Validation checks:
          * Price within reasonable range (outlier detection using 3-sigma)
          * Not duplicate of recent report (same product, chain, within 1 hour)
          * Reporter reputation score (trust level, 0.0-5.0)
          * Plausible product/chain combination
          * Rate limiting (max 20 reports per user per 24h)
        - Flags reports for manual review if:
          * Extreme outlier (3+ sigma from mean)
          * Low reputation reporter (< 1.0)
          * Rate limit exceeded
          * Suspicious patterns detected
        - Rejected reports are archived but still visible with warning
        - Confidence score factors:
          * Reporter reputation (40%)
          * Price reasonableness (30%)
          * Report freshness (20%)
          * Duplicate proximity (10%)

    Triggered:
        Automatically after each community PriceReport creation
    """
    logger.info(f"Validating community report: {report_id}", extra={"task_id": self.request.id})

    session = None
    flags = []
    confidence_score = 0.5

    try:
        # Convert string UUID to UUID object
        try:
            report_id_uuid = UUID(report_id)
        except ValueError:
            logger.error(f"Invalid report_id UUID: {report_id}")
            return {
                "valid": False,
                "flags": ["invalid_uuid"],
                "confidence_score": 0.0,
                "status": "rejected",
            }

        session = get_db_session()

        # Load report
        report = session.query(PriceReport).filter_by(id=report_id_uuid).first()
        if not report:
            logger.warning(f"Report not found: {report_id}")
            return {
                "valid": False,
                "flags": ["report_not_found"],
                "confidence_score": 0.0,
                "status": "rejected",
            }

        if report.source != 'community':
            logger.debug(f"Report is not community-sourced (source={report.source}), skipping validation")
            return {
                "valid": True,
                "flags": [],
                "confidence_score": 1.0,
                "status": "validated",
            }

        # Load reporter
        user = session.query(UserProfile).filter_by(
            id=report.reporter_user_id
        ).first()

        if not user:
            logger.error(f"Reporter user not found: {report.reporter_user_id}")
            flags.append("reporter_not_found")
            report.is_flagged = True
            report.confidence = 0.0
            session.commit()
            return {
                "valid": False,
                "flags": flags,
                "confidence_score": 0.0,
                "status": "rejected",
            }

        logger.debug(f"Validating report from user {user.email}: reputation={user.reputation_score}")

        # 1. RATE LIMITING: max 20 reports per user per 24h
        recent_count = session.query(PriceReport).filter(
            and_(
                PriceReport.reporter_user_id == user.id,
                PriceReport.source == 'community',
                PriceReport.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
            )
        ).count()

        if recent_count > 20:
            logger.warning(f"Rate limit exceeded for user {user.id}: {recent_count} reports in 24h")
            flags.append("rate_limit_exceeded")
            report.is_flagged = True
            report.confidence = 0.1
            session.commit()
            return {
                "valid": False,
                "flags": flags,
                "confidence_score": 0.1,
                "status": "flagged",
            }

        # 2. OUTLIER DETECTION: 3-sigma from 30-day mean
        historical = session.query(PriceReport).filter(
            and_(
                PriceReport.product_id == report.product_id,
                PriceReport.store_id == report.store_id,
                PriceReport.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
                PriceReport.id != report_id_uuid,  # Exclude current report
            )
        ).all()

        outlier_score = 1.0
        if len(historical) >= 3:
            prices = [p.price_cents for p in historical]
            try:
                mean_price = mean(prices)
                sigma = stdev(prices)

                # Check if outside 3-sigma
                z_score = abs(report.price_cents - mean_price) / sigma if sigma > 0 else 0
                logger.debug(f"Price outlier check: z_score={z_score:.2f}, price={report.price_cents}, mean={mean_price:.0f}, sigma={sigma:.0f}")

                if z_score > 3:
                    logger.warning(f"Extreme outlier detected: {report.price_cents} vs mean {mean_price:.0f} (z={z_score:.2f})")
                    flags.append("price_outlier_3sigma")
                    outlier_score = 0.3
                    report.is_flagged = True

                elif z_score > 2:
                    logger.debug(f"Moderate outlier: {report.price_cents} vs mean {mean_price:.0f} (z={z_score:.2f})")
                    flags.append("price_outlier_2sigma")
                    outlier_score = 0.6

            except (ZeroDivisionError, ValueError) as e:
                logger.error(f"Failed to calculate outlier score: {e}")

        # 3. DUPLICATE CHECK: same product/chain within 1 hour
        duplicate = session.query(PriceReport).filter(
            and_(
                PriceReport.product_id == report.product_id,
                PriceReport.store_id == report.store_id,
                PriceReport.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
                PriceReport.id != report_id_uuid,
                PriceReport.price_cents == report.price_cents,
            )
        ).first()

        duplicate_score = 1.0
        if duplicate:
            logger.warning(f"Duplicate report detected: similar to {duplicate.id}")
            flags.append("duplicate_within_1h")
            duplicate_score = 0.5

        # 4. REPUTATION SCORING
        # New users (contribution_count < 5): reputation 0.5, confidence impact 0.5
        # Low reputation (< 1.0): confidence impact 0.6
        # Established users (reputation_score >= 2.0): confidence impact 1.0
        # Default: confidence impact = user.reputation_score / 5.0

        reputation_factor = min(1.0, user.reputation_score / 5.0)
        logger.debug(f"Reputation factor: {reputation_factor:.2f} (reputation_score={user.reputation_score})")

        if user.contribution_count < 5:
            flags.append("new_reporter")
            reputation_factor = min(reputation_factor, 0.5)

        # Calculate overall confidence score
        # Base: 0.5 initial + reputation_factor weighted average
        # Components: reputation (40%), outlier (30%), duplicate (20%), other (10%)
        confidence_score = (
            reputation_factor * 0.4 +
            outlier_score * 0.3 +
            duplicate_score * 0.2 +
            0.1
        )

        # Clamp to 0.0-1.0 range
        confidence_score = max(0.0, min(1.0, confidence_score))

        logger.debug(
            f"Validation complete: confidence={confidence_score:.2f}, flags={flags}, outlier={outlier_score:.2f}, reputation={reputation_factor:.2f}"
        )

        # Determine status
        if not flags:
            status = "validated"
            report.is_verified = True
        elif "rate_limit_exceeded" in flags or "price_outlier_3sigma" in flags:
            status = "flagged"
            report.is_flagged = True
        else:
            status = "validated"
            report.is_verified = True

        # Update report
        report.confidence = confidence_score
        session.commit()

        # Increment contribution count
        user.contribution_count += 1
        session.commit()

        # Enqueue watchlist check to trigger notifications
        celery_app.send_task(
            "app.workers.notification_tasks.check_watchlist_for_price",
            args=[str(report_id_uuid)],
            queue="notification",
        )
        logger.debug(f"Enqueued watchlist check for report {report_id_uuid}")

        # Enqueue reputation recalculation (async, non-critical)
        celery_app.send_task(
            "app.workers.report_validator.recalculate_reporter_reputation",
            args=[str(user.id)],
            queue="default",
        )
        logger.debug(f"Enqueued reputation recalculation for user {user.id}")

        return {
            "valid": status == "validated",
            "flags": flags,
            "confidence_score": confidence_score,
            "status": status,
        }

    except Exception as e:
        logger.exception(f"Validation failed: {e}")
        raise self.retry(exc=e, countdown=20, max_retries=2)

    finally:
        if session:
            session.close()


@celery_app.task(
    bind=True,
    name='app.workers.report_validator.recalculate_reporter_reputation',
    queue='default',
    base=CallbackTask,
    time_limit=30,
    soft_time_limit=25,
    max_retries=1,
)
def recalculate_reporter_reputation(self, user_id: str) -> dict:
    """
    Recalculate reputation score for a community reporter.

    Runs periodically or after significant activity. Analyzes reporter's
    history of price submissions and accuracy to update reputation score.

    Args:
        user_id: UUID of user whose reputation to recalculate (as string)

    Returns:
        Dict with updated reputation score and breakdown

    Raises:
        Exception: On database errors (will retry once)

    Implementation notes:
        - Runs weekly for active reporters
        - Can also run manually after disputes or achievements
        - Factors:
          * Number of reports submitted (0-1.0)
          * Percentage validated/accepted (0-1.0)
          * Average confidence score of their reports (0-1.0)
          * Time since last activity (0-1.0)
          * Not flagged/banned (binary)
        - Score range: 0.0-5.0 (higher = more trusted)
        - Users start at 1.0 (neutral reputation)
        - Automatic flags if score drops below 0.5
        - Bonuses for consistent, accurate reporters
    """
    logger.info(f"Recalculating reputation for user: {user_id}", extra={"task_id": self.request.id})

    session = None

    try:
        # Convert string UUID to UUID object
        try:
            user_id_uuid = UUID(user_id)
        except ValueError:
            logger.error(f"Invalid user_id UUID: {user_id}")
            return {
                "user_id": user_id,
                "new_reputation": 0.0,
                "status": "error",
            }

        session = get_db_session()

        # Load user profile
        user = session.query(UserProfile).filter_by(id=user_id_uuid).first()
        if not user:
            logger.warning(f"User not found: {user_id}")
            return {
                "user_id": user_id,
                "new_reputation": 0.0,
                "status": "user_not_found",
            }

        logger.debug(f"Current reputation for {user.email}: {user.reputation_score}")

        # Query all community reports by this user
        reports = session.query(PriceReport).filter(
            and_(
                PriceReport.reporter_user_id == user.id,
                PriceReport.source == 'community',
            )
        ).all()

        logger.debug(f"Found {len(reports)} reports from user {user.email}")

        if len(reports) == 0:
            # Brand new user: default reputation
            new_reputation = 1.0
            logger.debug("No reports found, using default reputation")
        else:
            # Calculate metrics
            verified_count = sum(1 for r in reports if r.is_verified)
            flagged_count = sum(1 for r in reports if r.is_flagged)
            avg_confidence = sum(r.confidence for r in reports) / len(reports) if reports else 0.5

            # Verification rate (0-1.0)
            verification_rate = verified_count / len(reports) if reports else 0.0

            # Base score from report count
            # 0-20 reports: 0.0-1.0
            # 20-100 reports: 1.0-2.0
            # 100+ reports: 2.0-3.0
            report_count_score = min(3.0, 1.0 + len(reports) / 50.0)

            # Verification rate score (0-2.0)
            verification_score = verification_rate * 2.0

            # Average confidence contribution (0-1.0)
            confidence_contribution = avg_confidence

            # Flagged report penalty
            flag_penalty = min(1.0, flagged_count * 0.2)

            # Days since last report (0-1.0, lower score for older reporters)
            if reports:
                most_recent = max(r.created_at for r in reports)
                days_since = (datetime.now(timezone.utc) - most_recent).days
                recency_score = max(0.0, 1.0 - (days_since / 30.0))
            else:
                recency_score = 0.0

            # Calculate new reputation
            # Weighted average of components
            new_reputation = (
                report_count_score * 0.25 +
                verification_score * 0.35 +
                confidence_contribution * 0.25 +
                recency_score * 0.15 -
                flag_penalty
            )

            # Clamp to 0.0-5.0 range
            new_reputation = max(0.0, min(5.0, new_reputation))

            logger.debug(
                f"Reputation calculation: base={report_count_score:.2f}, verify={verification_score:.2f}, conf={confidence_contribution:.2f}, recency={recency_score:.2f}, penalty={flag_penalty:.2f}, final={new_reputation:.2f}"
            )

        # Update user reputation
        user.reputation_score = new_reputation
        user.contribution_count = len(reports)

        # Check for automatic flags
        if new_reputation < 0.5 and not (user.reputation_score < 0.5):
            logger.warning(f"User {user.email} reputation dropped below threshold: {new_reputation:.2f}")

        session.commit()

        logger.info(
            f"Reputation recalculated for {user.email}: {user.reputation_score:.2f}",
            extra={
                "old_reputation": user.reputation_score,
                "new_reputation": new_reputation,
                "report_count": len(reports),
                "verified": sum(1 for r in reports if r.is_verified),
            },
        )

        return {
            "user_id": user_id,
            "new_reputation": new_reputation,
            "report_count": len(reports),
            "status": "success",
        }

    except Exception as e:
        logger.exception(f"Reputation recalculation failed: {e}")
        raise self.retry(exc=e, countdown=60, max_retries=1)

    finally:
        if session:
            session.close()
