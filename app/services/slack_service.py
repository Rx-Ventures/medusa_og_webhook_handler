import logging
from typing import Any
from datetime import datetime, timezone

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

class SlackService:
    def __init__(self):
        self.slack_url = settings.SLACK_ALERTS_URL

    async def _execute_query(
        self,
        endpoint: str,
        payload: dict[str, Any]
    ) -> dict[str, Any]:

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                
                # Slack webhooks return plain text "ok" on success, not JSON
                response_text = response.text.strip()
                if response_text:
                    # Try to parse as JSON if possible, otherwise return text response
                    try:
                        result = response.json()
                    except ValueError:
                        result = {"status": "ok", "message": response_text}
                else:
                    result = {"status": "ok", "message": "Message sent successfully"}

                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Http error: {e}")
            raise Exception(f"Slack API error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise Exception(f"Slack API error: {e}") from e

    async def send_critical_alert(
        self,
        title: str,
        alert: str,
        platform: str | None = None,
    ) -> dict[str, Any]:
        timestamp = datetime.now(timezone.utc).strftime("%b %d, %Y at %I:%M %p UTC")
        env = settings.ENVIRONMENT.title()

        detail_blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": alert,
                },
            },
            {"type": "divider"},
        ]

        fields = [
            {"type": "mrkdwn", "text": f"*Severity*\n🔴 Critical"},
            {"type": "mrkdwn", "text": f"*Environment*\n{env}"},
        ]
        if platform:
            fields.append({"type": "mrkdwn", "text": f"*Platform*\n{platform}"})
        fields.append({"type": "mrkdwn", "text": f"*Timestamp*\n{timestamp}"})

        detail_blocks.append({"type": "section", "fields": fields})

        payload: dict[str, Any] = {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"🚨  {title}",
                        "emoji": True,
                    },
                },
            ],
            "attachments": [
                {
                    "color": "#E01E5A",
                    "blocks": detail_blocks,
                }
            ],
        }

        return await self._execute_query(
            endpoint=self.slack_url,
            payload=payload,
        )


slack_service = SlackService()
