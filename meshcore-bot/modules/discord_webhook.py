"""Discord webhook integration for forwarding mesh messages."""

import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def send_to_discord(webhook_url: str, sender: str, content: str, is_incoming: bool) -> bool:
    """
    Send a message to a Discord webhook.

    Args:
        webhook_url: The Discord webhook URL
        sender: The sender name to display
        content: The message content
        is_incoming: True for incoming messages (green), False for bot responses (blue)

    Returns:
        True if successful, False otherwise
    """
    if not webhook_url:
        return False

    # Direction indicator and color
    direction = "→" if is_incoming else "←"
    color = 0x00ff00 if is_incoming else 0x0099ff  # Green for incoming, blue for outgoing

    embed = {
        "description": content,
        "author": {"name": f"{direction} {sender}"},
        "color": color
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json={"embeds": [embed]}) as response:
                if response.status == 204:
                    logger.debug(f"Discord webhook sent successfully: {sender}: {content[:50]}...")
                    return True
                else:
                    logger.warning(f"Discord webhook returned status {response.status}")
                    return False
    except aiohttp.ClientError as e:
        logger.warning(f"Discord webhook failed (network error): {e}")
        return False
    except Exception as e:
        logger.warning(f"Discord webhook failed: {e}")
        return False
