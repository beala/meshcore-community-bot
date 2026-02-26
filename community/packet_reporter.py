"""Background task that batches and reports messages/packets to the coordinator."""

import asyncio
import logging
import time
from typing import Optional

from .coordinator_client import CoordinatorClient

logger = logging.getLogger(__name__)


class PacketReporter:
    """Collects messages and packets and sends them to the coordinator in batches."""

    def __init__(
        self,
        coordinator: CoordinatorClient,
        batch_interval: int = 5,
        batch_max_size: int = 50,
    ):
        self.coordinator = coordinator
        self.batch_interval = batch_interval
        self.batch_max_size = batch_max_size

        self._message_queue: list[dict] = []
        self._packet_queue: list[dict] = []
        self._lock = asyncio.Lock()

    async def add_message(
        self,
        message_hash: str,
        sender_pubkey: str = "",
        sender_name: str = "",
        channel: Optional[str] = None,
        content: str = "",
        is_dm: bool = False,
        hops: Optional[int] = None,
        path: Optional[str] = None,
        snr: Optional[float] = None,
        rssi: Optional[int] = None,
        timestamp: int = 0,
        was_command: bool = False,
        command_name: Optional[str] = None,
        bot_responded: bool = False,
    ):
        """Queue a message for reporting."""
        async with self._lock:
            self._message_queue.append({
                "message_hash": message_hash,
                "sender_pubkey": sender_pubkey,
                "sender_name": sender_name,
                "channel": channel,
                "content": content,
                "is_dm": is_dm,
                "hops": hops,
                "path": path,
                "snr": snr,
                "rssi": rssi,
                "timestamp": timestamp,
                "was_command": was_command,
                "command_name": command_name,
                "bot_responded": bot_responded,
            })

        # Flush if batch is full
        if len(self._message_queue) >= self.batch_max_size:
            await self._flush()

    async def add_packet(
        self,
        packet_hash: str = "",
        raw_hex: str = "",
        packet_type: Optional[int] = None,
        route_type: Optional[int] = None,
        snr: Optional[float] = None,
        rssi: Optional[int] = None,
        timestamp: int = 0,
    ):
        """Queue a packet for reporting."""
        async with self._lock:
            self._packet_queue.append({
                "packet_hash": packet_hash,
                "raw_hex": raw_hex,
                "packet_type": packet_type,
                "route_type": route_type,
                "snr": snr,
                "rssi": rssi,
                "timestamp": timestamp,
            })

    async def _flush(self):
        """Send queued messages and packets to the coordinator."""
        async with self._lock:
            messages = self._message_queue.copy()
            packets = self._packet_queue.copy()
            self._message_queue.clear()
            self._packet_queue.clear()

        if not messages and not packets:
            return

        success = await self.coordinator.report_batch(
            messages=messages,
            packets=packets,
        )

        if success:
            logger.debug(
                f"Reported batch: {len(messages)} messages, {len(packets)} packets"
            )
        else:
            logger.debug("Failed to report batch, data dropped")

    async def run(self):
        """Run the reporter loop - flushes batches periodically."""
        logger.info(
            f"Packet reporter started (interval={self.batch_interval}s, "
            f"max_batch={self.batch_max_size})"
        )
        while True:
            await asyncio.sleep(self.batch_interval)
            try:
                await self._flush()
            except Exception as e:
                logger.debug(f"Reporter flush error: {e}")
