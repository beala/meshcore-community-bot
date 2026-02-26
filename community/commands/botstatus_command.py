"""Bot status command - shows coordinator connection status."""

import sys
import time
from pathlib import Path

# Ensure meshcore-bot modules are importable
_bot_path = str(Path(__file__).parent.parent.parent / "meshcore-bot")
if _bot_path not in sys.path:
    sys.path.insert(0, _bot_path)

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class BotstatusCommand(BaseCommand):
    """Shows the bot's coordinator connection status."""

    name = "botstatus"
    keywords = ["botstatus", "botstat"]
    description = "Shows coordinator connection status and network info"
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            coordinator = getattr(self.bot, "coordinator", None)
            fallback = getattr(self.bot, "coverage_fallback", None)

            if not coordinator or not coordinator.is_configured:
                await self.send_response(message, "Running standalone (no coordinator)")
                return True

            status = "Connected" if coordinator.is_registered else "Not registered"
            score = coordinator.current_score
            active = coordinator.active_bots
            uptime = int(time.time() - self.bot.start_time)
            hours = uptime // 3600
            mins = (uptime % 3600) // 60

            parts = [
                f"Status: {status}",
                f"Score: {score:.0%}",
                f"Network: {active} bots",
                f"Uptime: {hours}h {mins}m",
            ]

            if fallback:
                delay = fallback.compute_delay_ms()
                parts.append(f"Fallback delay: {delay}ms")

            await self.send_response(message, "\n".join(parts))
            return True
        except Exception as e:
            self.logger.error(f"Botstatus command error: {e}")
            await self.send_response(message, "Error getting bot status")
            return False
