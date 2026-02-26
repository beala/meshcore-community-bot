"""Extended MeshCoreBot with coordinator integration.

Inherits from MeshCoreBot and adds:
- Coordinator registration and heartbeat
- Message coordination (who should respond)
- Packet/message reporting to central service
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

# Add meshcore-bot submodule to path
_bot_path = str(Path(__file__).parent.parent / "meshcore-bot")
if _bot_path not in sys.path:
    sys.path.insert(0, _bot_path)

from modules.core import MeshCoreBot

from .config import CoordinatorConfig
from .coordinator_client import CoordinatorClient
from .coverage_fallback import CoverageFallback
from .message_interceptor import MessageInterceptor
from .packet_reporter import PacketReporter

logger = logging.getLogger(__name__)


class CommunityBot(MeshCoreBot):
    """MeshCoreBot extended with multi-bot coordination."""

    def __init__(self, config_file: str = "config.ini"):
        # Initialize the base bot
        super().__init__(config_file)

        # Load coordinator config
        self.coordinator_config = CoordinatorConfig.from_env_and_config(self.config)

        # Initialize coordinator client
        self.coordinator = CoordinatorClient(
            base_url=self.coordinator_config.url,
            timeout_ms=self.coordinator_config.coordination_timeout_ms,
            data_dir=str(self.bot_root / "data"),
        )

        # Initialize fallback
        self.coverage_fallback = CoverageFallback()

        # Initialize packet reporter
        self.packet_reporter = PacketReporter(
            coordinator=self.coordinator,
            batch_interval=self.coordinator_config.batch_interval_seconds,
            batch_max_size=self.coordinator_config.batch_max_size,
        )

        # Install message interceptor (patches send_response)
        self.message_interceptor = MessageInterceptor(
            bot=self,
            coordinator=self.coordinator,
            fallback=self.coverage_fallback,
        )

        # Background tasks
        self._coordinator_tasks: list[asyncio.Task] = []

        self.logger.info("Community bot initialized with coordinator support")

    async def start(self):
        """Start the bot with coordinator integration."""
        self.logger.info("Starting Community Bot...")

        # Register with coordinator (non-blocking - bot works without it)
        await self._register_with_coordinator()

        # Start coordinator background tasks
        self._start_coordinator_tasks()

        # Start the base bot (connects to radio, starts event loop)
        await super().start()

    async def stop(self):
        """Stop the bot and cleanup coordinator resources."""
        # Cancel coordinator tasks
        for task in self._coordinator_tasks:
            task.cancel()
        self._coordinator_tasks.clear()

        # Restore original send_response
        if hasattr(self, "message_interceptor"):
            self.message_interceptor.restore()

        # Close coordinator client
        if hasattr(self, "coordinator"):
            await self.coordinator.close()

        # Stop base bot
        await super().stop()

    async def _register_with_coordinator(self):
        """Register this bot with the coordinator."""
        if not self.coordinator.is_configured:
            self.logger.info("No coordinator URL configured, running standalone")
            return

        # Get radio public key after connection
        public_key = ""
        if self.meshcore and hasattr(self.meshcore, "self_info"):
            try:
                info = self.meshcore.self_info
                if info and hasattr(info, "public_key"):
                    public_key = info.public_key or ""
            except Exception:
                pass

        # If no public key yet, use a placeholder - will re-register after connect
        if not public_key:
            public_key = self.config.get("Bot", "bot_name", fallback="unknown")

        bot_name = self.config.get("Bot", "bot_name", fallback="CommunityBot")
        lat = self.config.getfloat("Bot", "latitude", fallback=None)
        lon = self.config.getfloat("Bot", "longitude", fallback=None)
        conn_type = self.config.get("Connection", "connection_type", fallback="serial")

        # Get loaded command names
        capabilities = list(self.command_manager.commands.keys())

        success = await self.coordinator.register(
            bot_name=bot_name,
            public_key=public_key,
            latitude=lat,
            longitude=lon,
            connection_type=conn_type,
            capabilities=capabilities,
            version="0.1.0",
            mesh_region=self.coordinator_config.mesh_region,
        )

        if success:
            self.logger.info(f"Registered with coordinator (bot_id={self.coordinator.bot_id})")
        else:
            self.logger.warning("Could not register with coordinator, running standalone")

    def _start_coordinator_tasks(self):
        """Start background tasks for coordinator communication."""
        if not self.coordinator.is_configured:
            return

        # Heartbeat loop
        task = asyncio.create_task(self._heartbeat_loop())
        self._coordinator_tasks.append(task)

        # Packet reporter loop
        task = asyncio.create_task(self.packet_reporter.run())
        self._coordinator_tasks.append(task)

        self.logger.info("Coordinator background tasks started")

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to the coordinator."""
        while True:
            try:
                uptime = int(time.time() - self.start_time)
                contact_count = 0
                channel_count = 0

                if self.meshcore:
                    if hasattr(self.meshcore, "contacts") and self.meshcore.contacts:
                        contact_count = len(self.meshcore.contacts)

                success = await self.coordinator.heartbeat(
                    uptime_seconds=uptime,
                    connected=self.connected,
                    contact_count=contact_count,
                    channel_count=channel_count,
                )

                if success:
                    # Update fallback score
                    self.coverage_fallback.update_score(self.coordinator.current_score)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.debug(f"Heartbeat error: {e}")

            await asyncio.sleep(self.coordinator.heartbeat_interval)
