"""HTTP client for the MeshCore Coordinator API."""

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


class CoordinatorClient:
    """Client for communicating with the central MeshCore Coordinator."""

    def __init__(self, base_url: str, timeout_ms: int = 100, data_dir: str = "data"):
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.bot_id: Optional[str] = None
        self.bot_token: str = ""
        self.current_score: float = 0.5
        self.active_bots: int = 0
        self.heartbeat_interval: int = 30
        self._last_score_update: float = 0.0

        # Load saved token
        self._load_token()

        # HTTP client with connection pooling
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(5.0, connect=2.0),
        )

    @property
    def is_configured(self) -> bool:
        """Check if the coordinator URL is configured."""
        return bool(self.base_url)

    @property
    def is_registered(self) -> bool:
        """Check if this bot is registered with the coordinator."""
        return bool(self.bot_id and self.bot_token)

    def _token_path(self) -> Path:
        return self.data_dir / ".bot_token"

    def _botid_path(self) -> Path:
        return self.data_dir / ".bot_id"

    def _load_token(self):
        """Load saved bot token and ID from disk."""
        try:
            if self._token_path().exists():
                self.bot_token = self._token_path().read_text().strip()
            if self._botid_path().exists():
                self.bot_id = self._botid_path().read_text().strip()
        except Exception as e:
            logger.warning(f"Failed to load saved token: {e}")

    def _save_token(self):
        """Save bot token and ID to disk."""
        try:
            self._token_path().write_text(self.bot_token)
            self._token_path().chmod(0o600)
            self._botid_path().write_text(self.bot_id or "")
        except Exception as e:
            logger.warning(f"Failed to save token: {e}")

    def _auth_headers(self) -> dict:
        """Get authorization headers."""
        if self.bot_token:
            return {"Authorization": f"Bearer {self.bot_token}"}
        return {}

    async def register(
        self,
        bot_name: str,
        public_key: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        connection_type: str = "serial",
        capabilities: Optional[list[str]] = None,
        version: str = "0.1.0",
        mesh_region: str = "",
    ) -> bool:
        """Register this bot with the coordinator."""
        if not self.is_configured:
            logger.info("No coordinator URL configured, running standalone")
            return False

        payload = {
            "bot_name": bot_name,
            "public_key": public_key,
            "connection_type": connection_type,
            "capabilities": capabilities or [],
            "version": version,
            "mesh_region": mesh_region,
        }
        if latitude is not None and longitude is not None:
            payload["location"] = {
                "latitude": latitude,
                "longitude": longitude,
            }

        try:
            resp = await self._client.post(
                "/api/v1/bots/register",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            self.bot_id = data["bot_id"]
            self.bot_token = data["bot_token"]
            self.heartbeat_interval = data.get("heartbeat_interval_seconds", 30)
            self._save_token()

            logger.info(f"Registered with coordinator as {bot_name} ({self.bot_id})")
            return True
        except Exception as e:
            logger.warning(f"Failed to register with coordinator: {e}")
            return False

    async def heartbeat(
        self,
        uptime_seconds: int = 0,
        messages_processed: int = 0,
        messages_responded: int = 0,
        connected: bool = True,
        contact_count: int = 0,
        channel_count: int = 0,
    ) -> bool:
        """Send heartbeat to coordinator."""
        if not self.is_registered:
            return False

        try:
            resp = await self._client.post(
                "/api/v1/bots/heartbeat",
                json={
                    "bot_id": self.bot_id,
                    "uptime_seconds": uptime_seconds,
                    "messages_processed": messages_processed,
                    "messages_responded": messages_responded,
                    "connected": connected,
                    "contact_count": contact_count,
                    "channel_count": channel_count,
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            self.current_score = data.get("your_score", 0.5)
            self.active_bots = data.get("active_bots", 0)
            self.heartbeat_interval = data.get("next_heartbeat_seconds", 30)
            self._last_score_update = time.time()

            return True
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")
            return False

    async def should_respond(
        self,
        message_hash: str,
        sender_pubkey: str = "",
        channel: Optional[str] = None,
        content_prefix: str = "",
        is_dm: bool = False,
        timestamp: int = 0,
    ) -> Optional[bool]:
        """Ask coordinator if this bot should respond to a message.

        Returns:
            True if should respond, False if should not, None if coordinator unreachable.
        """
        if not self.is_registered:
            return None

        try:
            resp = await self._client.post(
                "/api/v1/coordination/should-respond",
                json={
                    "bot_id": self.bot_id,
                    "message_hash": message_hash,
                    "sender_pubkey": sender_pubkey,
                    "channel": channel or "",
                    "content_prefix": content_prefix,
                    "is_dm": is_dm,
                    "timestamp": timestamp,
                },
                headers=self._auth_headers(),
                timeout=httpx.Timeout(self.timeout_ms / 1000.0),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("should_respond", True)
        except Exception as e:
            logger.debug(f"Coordination check failed: {e}")
            return None  # Unreachable - caller should use fallback

    async def report_batch(
        self,
        messages: Optional[list[dict]] = None,
        packets: Optional[list[dict]] = None,
    ) -> bool:
        """Report a batch of messages and packets to the coordinator."""
        if not self.is_registered:
            return False

        try:
            resp = await self._client.post(
                "/api/v1/messages/batch",
                json={
                    "bot_id": self.bot_id,
                    "messages": messages or [],
                    "packets": packets or [],
                },
                headers=self._auth_headers(),
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Batch report failed: {e}")
            return False

    async def heartbeat_loop(self, bot):
        """Run heartbeat in a loop."""
        while True:
            try:
                uptime = int(time.time() - bot.start_time)
                contact_count = 0
                if bot.meshcore and hasattr(bot.meshcore, "contacts"):
                    contact_count = len(bot.meshcore.contacts) if bot.meshcore.contacts else 0

                await self.heartbeat(
                    uptime_seconds=uptime,
                    connected=bot.connected,
                    contact_count=contact_count,
                )
            except Exception as e:
                logger.debug(f"Heartbeat loop error: {e}")

            await asyncio.sleep(self.heartbeat_interval)

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    @staticmethod
    def compute_message_hash(
        sender_pubkey: str, content: str, timestamp: int
    ) -> str:
        """Compute a deterministic hash for message deduplication.

        Uses 10-second time buckets so bots that receive the same message
        at slightly different times produce the same hash.
        """
        bucket = timestamp // 10
        raw = f"{sender_pubkey}:{content}:{bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()
