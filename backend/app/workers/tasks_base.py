"""
Base task class with error handling, logging, and retry logic.

Provides a reusable foundation for all Celery tasks with:
- Automatic retry on network errors
- Comprehensive error logging
- Slack notifications for critical failures
- Task lifecycle hooks for monitoring
"""

import logging

from celery import Task

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """
    Task class with built-in error handling, retry logic, and monitoring.

    All tasks should inherit from this class to get consistent:
    - Automatic retries on transient failures
    - Error logging and Slack notifications
    - Task lifecycle callbacks for observability
    """

    # Retry configuration
    autoretry_for = (Exception,)
    max_retries = 3
    default_retry_delay = 60  # seconds

    # Don't ack task until it completes
    acks_late = True

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """
        Called when task is being retried.

        Args:
            exc: The exception that triggered the retry
            task_id: Unique task ID
            args: Positional arguments
            kwargs: Keyword arguments
            einfo: Exception info object with traceback
        """
        logger.warning(
            f"Task {self.name} (ID: {task_id}) retrying after error",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "exception": str(exc),
                "task_args": str(args)[:100],
                "task_kwargs": str(kwargs)[:100],
                "retry_count": self.request.retries,
            },
        )

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        Called when task fails permanently after all retries.

        Args:
            exc: The exception that caused failure
            task_id: Unique task ID
            args: Positional arguments
            kwargs: Keyword arguments
            einfo: Exception info object with traceback
        """
        logger.error(
            f"Task {self.name} (ID: {task_id}) failed permanently after {self.request.retries} retries",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "exception": str(exc),
                "task_args": str(args)[:100],
                "task_kwargs": str(kwargs)[:100],
                "traceback": str(einfo)[:500],
            },
        )

        # Send Slack notification for critical task failures
        self._send_slack_alert(task_id, exc, args, kwargs)

    def on_success(self, result, task_id, args, kwargs):
        """
        Called when task completes successfully.

        Args:
            result: Task return value
            task_id: Unique task ID
            args: Positional arguments
            kwargs: Keyword arguments
        """
        logger.info(
            f"Task {self.name} (ID: {task_id}) completed successfully",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "result": str(result)[:100],
            },
        )

    def _send_slack_alert(self, task_id: str, exc: Exception, args: tuple, kwargs: dict) -> None:
        """
        Send Slack notification for task failure.

        Args:
            task_id: Unique task ID
            exc: The exception that caused failure
            args: Task arguments
            kwargs: Task keyword arguments
        """
        from app.config import get_settings

        settings = get_settings()
        if not settings.SLACK_WEBHOOK_URL:
            return

        try:
            import requests

            # Format task arguments for display
            args_str = str(args)[:100] if args else "none"
            kwargs_str = str(kwargs)[:100] if kwargs else "none"

            # Build Slack message
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "Celery Task Failed",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Task*\n{self.name}"},
                        {"type": "mrkdwn", "text": f"*ID*\n{task_id}"},
                        {"type": "mrkdwn", "text": f"*Error*\n{str(exc)[:100]}"},
                        {"type": "mrkdwn", "text": f"*Retries*\n{self.request.retries}/{self.max_retries}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```Args: {args_str}\nKwargs: {kwargs_str}```",
                    },
                },
            ]

            requests.post(
                settings.SLACK_WEBHOOK_URL,
                json={"blocks": blocks},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")

    def apply_async(self, *args, **kwargs):
        """
        Override apply_async to add logging for task queueing.
        """
        logger.debug(
            f"Queueing task {self.name}",
            extra={
                "task_name": self.name,
                "task_args": str(args)[:100],
                "task_kwargs": str(kwargs)[:100],
            },
        )
        return super().apply_async(*args, **kwargs)
