#!/usr/bin/env python3
"""
Test command for the MeshCore Bot.
Responds to 'test' or 't' with connection info and optional path distance estimates.
"""

import asyncio
import re
from typing import Optional

from .base_command import BaseCommand
from ..models import MeshMessage
from ..repeater_location_mixin import RepeaterLocationMixin
from ..utils import calculate_distance


class TestCommand(RepeaterLocationMixin, BaseCommand):
    """Responds to 'test' or 't' with connection info.

    Supports an optional phrase and geographic path distance estimation
    using the shared RepeaterLocationMixin.
    """

    # Plugin metadata
    name = "test"
    keywords = ['test', 't']
    description = "Responds to 'test' or 't' with connection info"
    category = "basic"

    # Documentation
    short_description = "Get test response with connection info"
    usage = "test [phrase]"
    examples = ["test", "t hello world"]

    def __init__(self, bot):
        super().__init__(bot)
        self.test_enabled = self.get_config_value(
            'Test_Command', 'enabled', fallback=True, value_type='bool'
        )
        # Shared geo config (weights, bot location, star bias, age filter, etc.)
        self._init_location_config()

    def can_execute(self, message: MeshMessage) -> bool:
        if not self.test_enabled:
            return False
        return super().can_execute(message)

    def get_help_text(self) -> str:
        return self.translate('commands.test.help')

    # ------------------------------------------------------------------
    # Keyword matching
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_content(content: str) -> str:
        """Remove control characters and normalise whitespace."""
        cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', content)
        return ' '.join(cleaned.split())

    def matches_keyword(self, message: MeshMessage) -> bool:
        content = self._clean_content(message.content)
        if content.startswith('!'):
            content = content[1:].strip()
        lower = content.lower()

        if lower == 'test' or lower == 't':
            return True
        if lower.startswith('test ') and len(content) > 5 and content[5:].strip():
            return True
        if lower.startswith('t ') and len(content) > 2 and content[2:].strip():
            return True
        return False

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def get_response_format(self) -> Optional[str]:
        if self.bot.config.has_section('Keywords'):
            fmt = self.bot.config.get('Keywords', 'test', fallback=None)
            return self._strip_quotes_from_config(fmt) if fmt else None
        return None

    def _extract_phrase(self, message: MeshMessage) -> str:
        """Extract optional phrase from the message content."""
        content = self._clean_content(message.content)
        if content.startswith('!'):
            content = content[1:].strip()
        lower = content.lower()

        if lower == 'test' or lower == 't':
            return ""
        if lower.startswith('test '):
            return content[5:].strip()
        if lower.startswith('t '):
            return content[2:].strip()
        return ""

    def format_response(self, message: MeshMessage, response_format: str) -> str:
        phrase = self._extract_phrase(message)

        try:
            connection_info = self.build_enhanced_connection_info(message)
            timestamp = self.format_timestamp(message)
            phrase_part = f": {phrase}" if phrase else ""

            # Calculate distances on the calling thread (sync helpers)
            path_distance = self._calculate_path_distance_sync(message)
            firstlast_distance = self._calculate_firstlast_distance_sync(message)

            return response_format.format(
                sender=message.sender_id or self.translate('common.unknown_sender'),
                phrase=phrase,
                phrase_part=phrase_part,
                connection_info=connection_info,
                path=message.path or self.translate('common.unknown_path'),
                timestamp=timestamp,
                snr=message.snr or self.translate('common.unknown'),
                path_distance=path_distance or "",
                firstlast_distance=firstlast_distance or "",
            )
        except (KeyError, ValueError) as e:
            self.logger.warning(f"Error formatting test response: {e}")
            return response_format

    # ------------------------------------------------------------------
    # Distance calculations (sync — run via to_thread from execute)
    # ------------------------------------------------------------------

    def _calculate_path_distance_sync(self, message: MeshMessage) -> str:
        """Sum of segment distances between consecutive repeaters with locations."""
        node_ids = self.extract_path_node_ids(message.path)
        if len(node_ids) < 2:
            if not message.path or "Direct" in message.path or "0 hops" in message.path:
                return "N/A"
            return ""

        locations = []
        for nid in node_ids:
            loc = self._lookup_repeater_location_sync(nid, path_context=node_ids)
            if loc:
                locations.append((nid, loc))

        skipped = len(node_ids) - len(locations)
        if len(locations) < 2:
            return ""

        total = 0.0
        for i in range(len(locations) - 1):
            total += calculate_distance(
                locations[i][1][0], locations[i][1][1],
                locations[i + 1][1][0], locations[i + 1][1][1],
            )
        segs = len(locations) - 1

        if skipped > 0:
            return f"{total:.1f}km ({segs} segs, {skipped} no-loc)"
        return f"{total:.1f}km ({segs} segs)"

    def _calculate_firstlast_distance_sync(self, message: MeshMessage) -> str:
        """Straight-line distance between first and last repeater."""
        node_ids = self.extract_path_node_ids(message.path)
        if len(node_ids) < 2:
            if not message.path or "Direct" in message.path or "0 hops" in message.path:
                return "N/A"
            return ""

        first = self._lookup_repeater_location_sync(node_ids[0], path_context=node_ids)
        last = self._lookup_repeater_location_sync(node_ids[-1], path_context=node_ids)
        if not first or not last:
            return ""

        d = calculate_distance(first[0], first[1], last[0], last[1])
        return f"{d:.1f}km"

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, message: MeshMessage) -> bool:
        # Store message ref for sender location lookups inside sync helpers.
        # We pass it through to_thread so the sync code can access it.
        self._current_message = message
        try:
            return await self.handle_keyword_match(message)
        finally:
            self._current_message = None
