"""Intercepts bot responses to add coordinator-based coordination.

Patches CommandManager.send_response() to check with the coordinator
before sending any response on a channel. DMs bypass coordination.
"""

import logging
import time

from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback

logger = logging.getLogger(__name__)


class MessageInterceptor:
    """Intercepts send_response to coordinate with the central coordinator."""

    def __init__(self, bot, coordinator: CoordinatorClient, fallback: CoverageFallback):
        self.bot = bot
        self.coordinator = coordinator
        self.fallback = fallback

        # Save reference to the original send_response
        self._original_send_response = bot.command_manager.send_response

        # Patch the command manager's send_response
        bot.command_manager.send_response = self._coordinated_send_response

        logger.info("Message interceptor installed on CommandManager.send_response")

    async def _coordinated_send_response(self, message, content: str) -> bool:
        """Coordinated version of send_response.

        For DMs: send immediately (no coordination needed).
        For channel messages: check with coordinator first.
        """
        # DMs always go through - only this bot received the DM
        if message.is_dm:
            return await self._original_send_response(message, content)

        # If coordinator is not configured, send immediately
        if not self.coordinator.is_configured:
            return await self._original_send_response(message, content)

        # Compute message hash for deduplication
        timestamp = message.timestamp or int(time.time())
        message_hash = CoordinatorClient.compute_message_hash(
            sender_pubkey=message.sender_pubkey or "",
            content=message.content or "",
            timestamp=timestamp,
        )

        # Ask coordinator
        content_prefix = (message.content or "").split()[0] if message.content else ""
        should_respond = await self.coordinator.should_respond(
            message_hash=message_hash,
            sender_pubkey=message.sender_pubkey or "",
            channel=message.channel,
            content_prefix=content_prefix[:50],
            is_dm=False,
            timestamp=timestamp,
        )

        if should_respond is True:
            # Coordinator says we should respond
            logger.info(f"Coordinator assigned response to us for: {content_prefix}")
            return await self._original_send_response(message, content)

        if should_respond is False:
            # Coordinator assigned to another bot
            logger.info(f"Coordinator assigned response to another bot for: {content_prefix}")
            return True  # Return True so command doesn't report failure

        # should_respond is None - coordinator unreachable, use fallback
        logger.info(f"Coordinator unreachable, using score-based fallback")
        await self.fallback.wait_before_responding()
        return await self._original_send_response(message, content)

    def restore(self):
        """Restore the original send_response method."""
        self.bot.command_manager.send_response = self._original_send_response
        logger.info("Message interceptor removed")
