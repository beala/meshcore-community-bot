"""Discord webhook integration for forwarding mesh messages.

Community-layer replacement for the old meshcore-bot discord_webhook module.
Forwards incoming and outgoing messages on configured channels to Discord webhooks.
"""

import logging
from typing import Optional

logger = logging.getLogger('CommunityBot')

# Reuse a single aiohttp session across calls
_session = None


async def _get_session():
    global _session
    import aiohttp
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def send_to_discord(webhook_url: str, sender: str, content: str, is_incoming: bool) -> bool:
    """Send a message to a Discord webhook.

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

    direction = "\u2192" if is_incoming else "\u2190"  # → or ←
    color = 0x00FF00 if is_incoming else 0x0099FF  # Green for incoming, blue for outgoing

    embed = {
        "description": content,
        "author": {"name": f"{direction} {sender}"},
        "color": color,
    }

    try:
        session = await _get_session()
        async with session.post(webhook_url, json={"embeds": [embed]}) as response:
            if response.status == 204:
                logger.debug(f"Discord webhook sent: {sender}: {content[:50]}...")
                return True
            else:
                logger.warning(f"Discord webhook returned status {response.status}")
                return False
    except Exception as e:
        logger.warning(f"Discord webhook failed: {e}")
        return False


async def close():
    """Close the shared HTTP session."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None
