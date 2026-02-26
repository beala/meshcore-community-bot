"""Coverage command - shows this bot's coverage score."""

import sys
from pathlib import Path

# Ensure meshcore-bot modules are importable
_bot_path = str(Path(__file__).parent.parent.parent / "meshcore-bot")
if _bot_path not in sys.path:
    sys.path.insert(0, _bot_path)

from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage


class CoverageCommand(BaseCommand):
    """Shows the bot's current coverage score from the coordinator."""

    name = "coverage"
    keywords = ["coverage", "score"]
    description = "Shows this bot's coverage score and active bots in the network"
    category = "community"

    async def execute(self, message: MeshMessage) -> bool:
        try:
            coordinator = getattr(self.bot, "coordinator", None)
            if not coordinator or not coordinator.is_configured:
                await self.send_response(message, "Coordinator not configured")
                return True

            score = coordinator.current_score
            active = coordinator.active_bots

            response = (
                f"Coverage Score: {score:.0%}\n"
                f"Active Bots: {active}"
            )
            await self.send_response(message, response)
            return True
        except Exception as e:
            self.logger.error(f"Coverage command error: {e}")
            await self.send_response(message, "Error getting coverage data")
            return False
