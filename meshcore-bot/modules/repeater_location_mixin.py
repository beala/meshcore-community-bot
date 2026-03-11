#!/usr/bin/env python3
"""
Shared mixin for repeater geographic location lookup and proximity-based selection.

Used by PathCommand and TestCommand to resolve 2-char hex node IDs to repeater
locations, with recency-weighted scoring and path-aware proximity selection.
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .utils import calculate_distance


# Minimum recency score to consider a repeater (~55 hours old)
MIN_RECENCY_THRESHOLD = 0.01


class RepeaterLocationMixin:
    """Mixin providing repeater location lookup and proximity selection.

    Requires the consuming class to have:
        self.bot          - bot instance with db_manager and config
        self.logger       - logging instance
        self.bot_latitude / self.bot_longitude - optional bot coords
        self.geographic_guessing_enabled - bool
        self.recency_weight / self.proximity_weight - floats summing to 1.0
        self.star_bias_multiplier - float >= 1.0
    """

    # ------------------------------------------------------------------
    # Initialisation helper (call from subclass __init__)
    # ------------------------------------------------------------------

    def _init_location_config(self):
        """Read shared geo config from [Path_Command] and [Bot] sections."""
        bot = self.bot
        self.geographic_guessing_enabled = False
        self.bot_latitude = None
        self.bot_longitude = None

        # Weights
        recency_weight = bot.config.getfloat('Path_Command', 'recency_weight', fallback=0.4)
        self.recency_weight = max(0.0, min(1.0, recency_weight))
        self.proximity_weight = 1.0 - self.recency_weight

        # Star bias
        self.star_bias_multiplier = max(
            1.0, bot.config.getfloat('Path_Command', 'star_bias_multiplier', fallback=2.5)
        )

        # Max repeater age (days); 0 = no limit
        self.max_repeater_age_days = bot.config.getint('Path_Command', 'max_repeater_age_days', fallback=14)

        # Max proximity range (km); 0 = no limit
        self.max_proximity_range = bot.config.getfloat('Path_Command', 'max_proximity_range', fallback=200.0)

        # Bot location
        try:
            if bot.config.has_section('Bot'):
                lat = bot.config.getfloat('Bot', 'bot_latitude', fallback=None)
                lon = bot.config.getfloat('Bot', 'bot_longitude', fallback=None)
                if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                    self.bot_latitude = lat
                    self.bot_longitude = lon
                    self.geographic_guessing_enabled = True
                    self.logger.debug(
                        f"Geographic proximity enabled: {lat:.4f}, {lon:.4f}"
                    )
        except Exception as e:
            self.logger.warning(f"Error reading bot location from config: {e}")

    # ------------------------------------------------------------------
    # Path node extraction
    # ------------------------------------------------------------------

    @staticmethod
    def extract_path_node_ids(path: Optional[str]) -> List[str]:
        """Extract 2-char hex node IDs from a path string.

        Returns an empty list for direct connections or missing paths.
        """
        if not path:
            return []
        if "Direct" in path or "0 hops" in path:
            return []

        path_string = path
        # Strip route type suffix
        if " via ROUTE_TYPE_" in path_string:
            path_string = path_string.split(" via ROUTE_TYPE_")[0]
        # Strip hop-count parenthetical
        if '(' in path_string:
            path_string = path_string.split('(')[0].strip()

        if ',' not in path_string:
            return []

        parts = path_string.split(',')
        valid = []
        for part in parts:
            part = part.strip()
            if len(part) == 2 and all(c in '0123456789abcdefABCDEF' for c in part):
                valid.append(part.upper())
        return valid

    # ------------------------------------------------------------------
    # Recency scoring
    # ------------------------------------------------------------------

    @staticmethod
    def calculate_recency_weighted_scores(
        repeaters: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Score repeaters by recency (0.0-1.0, higher = more recent).

        Checks last_heard, last_advert_timestamp, and last_seen fields.
        Returns list sorted by score descending.
        """
        scored = []
        now = datetime.now()

        for repeater in repeaters:
            most_recent = None
            for field in ('last_heard', 'last_advert_timestamp', 'last_seen'):
                val = repeater.get(field)
                if not val:
                    continue
                try:
                    dt = datetime.fromisoformat(val.replace('Z', '+00:00')) if isinstance(val, str) else val
                    if most_recent is None or dt > most_recent:
                        most_recent = dt
                except Exception:
                    pass

            if most_recent is None:
                score = 0.1
            else:
                hours_ago = (now - most_recent).total_seconds() / 3600.0
                score = max(0.0, min(1.0, math.exp(-hours_ago / 12.0)))

            scored.append((repeater, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Location lookups (sync — call via asyncio.to_thread in async code)
    # ------------------------------------------------------------------

    def _lookup_repeater_location_sync(
        self, node_id: str, path_context: Optional[List[str]] = None
    ) -> Optional[Tuple[float, float]]:
        """Look up repeater location by node ID prefix (sync).

        When multiple candidates match, uses path context and proximity
        to select the best one.
        """
        try:
            if not hasattr(self.bot, 'db_manager'):
                return None

            age_filter = ""
            if self.max_repeater_age_days > 0:
                age_filter = f"""
                    AND (
                        (last_advert_timestamp IS NOT NULL AND last_advert_timestamp >= datetime('now', '-{self.max_repeater_age_days} days'))
                        OR (last_advert_timestamp IS NULL AND last_heard >= datetime('now', '-{self.max_repeater_age_days} days'))
                    )
                """

            query = f'''
                SELECT latitude, longitude, public_key, name,
                       last_advert_timestamp, last_heard, advert_count, is_starred
                FROM complete_contact_tracking
                WHERE public_key LIKE ? AND role IN ('repeater', 'roomserver')
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
                {age_filter}
            '''

            results = self.bot.db_manager.execute_query(query, (f"{node_id}%",))
            if not results:
                return None

            repeaters = [
                {
                    'latitude': row.get('latitude'),
                    'longitude': row.get('longitude'),
                    'public_key': row.get('public_key'),
                    'name': row.get('name'),
                    'last_advert_timestamp': row.get('last_advert_timestamp'),
                    'last_heard': row.get('last_heard'),
                    'advert_count': row.get('advert_count', 0),
                    'is_starred': bool(row.get('is_starred', 0)),
                }
                for row in results
            ]

            if len(repeaters) == 1:
                r = repeaters[0]
                return (float(r['latitude']), float(r['longitude']))

            # Multiple candidates — try path proximity
            if path_context and len(path_context) > 1:
                sender_loc = self._get_sender_location_sync()
                selected = self._select_by_path_proximity_internal(
                    repeaters, node_id, path_context, sender_loc
                )
                if selected:
                    return (float(selected['latitude']), float(selected['longitude']))

            # Fall back to most recent
            scored = self.calculate_recency_weighted_scores(repeaters)
            if scored:
                best = scored[0][0]
                return (float(best['latitude']), float(best['longitude']))

            return None
        except Exception as e:
            self.logger.debug(f"Error looking up repeater location for {node_id}: {e}")
            return None

    def _get_node_location_simple_sync(self, node_id: str) -> Optional[Tuple[float, float]]:
        """Simple location lookup without proximity selection (sync)."""
        try:
            if not hasattr(self.bot, 'db_manager'):
                return None

            age_filter = ""
            if self.max_repeater_age_days > 0:
                age_filter = f"""
                    AND (
                        (last_advert_timestamp IS NOT NULL AND last_advert_timestamp >= datetime('now', '-{self.max_repeater_age_days} days'))
                        OR (last_advert_timestamp IS NULL AND last_heard >= datetime('now', '-{self.max_repeater_age_days} days'))
                    )
                """

            query = f'''
                SELECT latitude, longitude
                FROM complete_contact_tracking
                WHERE public_key LIKE ? AND role IN ('repeater', 'roomserver')
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
                {age_filter}
                ORDER BY is_starred DESC, COALESCE(last_advert_timestamp, last_heard) DESC
                LIMIT 1
            '''

            results = self.bot.db_manager.execute_query(query, (f"{node_id}%",))
            if results:
                row = results[0]
                lat, lon = row.get('latitude'), row.get('longitude')
                if lat is not None and lon is not None:
                    return (float(lat), float(lon))
            return None
        except Exception as e:
            self.logger.debug(f"Error in simple location lookup for {node_id}: {e}")
            return None

    def _get_sender_location_sync(self, sender_pubkey: Optional[str] = None) -> Optional[Tuple[float, float]]:
        """Look up sender location from DB (sync).

        If sender_pubkey is None, tries self._current_message.sender_pubkey.
        """
        try:
            if sender_pubkey is None:
                msg = getattr(self, '_current_message', None)
                if not msg:
                    return None
                sender_pubkey = msg.sender_pubkey
            if not sender_pubkey:
                return None

            query = '''
                SELECT latitude, longitude
                FROM complete_contact_tracking
                WHERE public_key = ?
                AND latitude IS NOT NULL AND longitude IS NOT NULL
                AND latitude != 0 AND longitude != 0
                ORDER BY COALESCE(last_advert_timestamp, last_heard) DESC
                LIMIT 1
            '''
            results = self.bot.db_manager.execute_query(query, (sender_pubkey,))
            if results:
                row = results[0]
                return (row['latitude'], row['longitude'])
            return None
        except Exception as e:
            self.logger.debug(f"Error getting sender location: {e}")
            return None

    # ------------------------------------------------------------------
    # Proximity selection internals
    # ------------------------------------------------------------------

    def _select_by_path_proximity_internal(
        self,
        repeaters: List[Dict[str, Any]],
        node_id: str,
        path_context: List[str],
        sender_location: Optional[Tuple[float, float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Select repeater using path-aware proximity (no confidence score)."""
        try:
            scored = self.calculate_recency_weighted_scores(repeaters)
            recent = [r for r, s in scored if s >= MIN_RECENCY_THRESHOLD]
            if not recent:
                return None

            current_index = path_context.index(node_id) if node_id in path_context else -1
            if current_index == -1:
                return None

            prev_location = None
            next_location = None

            if current_index > 0:
                prev_location = self._get_node_location_simple_sync(path_context[current_index - 1])
            if current_index < len(path_context) - 1:
                next_location = self._get_node_location_simple_sync(path_context[current_index + 1])

            # First repeater: prioritise sender location
            if current_index == 0 and sender_location:
                return self._select_by_single_proximity_internal(recent, sender_location, "sender")

            # Last repeater: prioritise bot location
            if current_index == len(path_context) - 1 and self.geographic_guessing_enabled:
                if self.bot_latitude is not None and self.bot_longitude is not None:
                    return self._select_by_single_proximity_internal(
                        recent, (self.bot_latitude, self.bot_longitude), "bot"
                    )

            # Middle repeaters
            if prev_location and next_location:
                return self._select_by_dual_proximity_internal(recent, prev_location, next_location)
            elif prev_location:
                return self._select_by_single_proximity_internal(recent, prev_location, "previous")
            elif next_location:
                return self._select_by_single_proximity_internal(recent, next_location, "next")
            return None
        except Exception as e:
            self.logger.debug(f"Error in path proximity selection: {e}")
            return None

    def _select_by_dual_proximity_internal(
        self,
        repeaters: List[Dict[str, Any]],
        prev_location: Tuple[float, float],
        next_location: Tuple[float, float],
    ) -> Optional[Dict[str, Any]]:
        """Select repeater closest to both neighbours."""
        scored = self.calculate_recency_weighted_scores(repeaters)
        scored = [(r, s) for r, s in scored if s >= MIN_RECENCY_THRESHOLD]
        if not scored:
            return None

        best, best_score = None, 0.0
        for repeater, recency_score in scored:
            prev_d = calculate_distance(prev_location[0], prev_location[1], repeater['latitude'], repeater['longitude'])
            next_d = calculate_distance(next_location[0], next_location[1], repeater['latitude'], repeater['longitude'])
            avg_d = (prev_d + next_d) / 2
            proximity_score = 1.0 - min(avg_d / 1000.0, 1.0)
            combined = (recency_score * self.recency_weight) + (proximity_score * self.proximity_weight)
            if repeater.get('is_starred', False):
                combined *= self.star_bias_multiplier
            if combined > best_score:
                best_score = combined
                best = repeater
        return best

    def _select_by_single_proximity_internal(
        self,
        repeaters: List[Dict[str, Any]],
        reference: Tuple[float, float],
        direction: str = "unknown",
    ) -> Optional[Dict[str, Any]]:
        """Select repeater closest to a single reference point."""
        scored = self.calculate_recency_weighted_scores(repeaters)
        scored = [(r, s) for r, s in scored if s >= MIN_RECENCY_THRESHOLD]
        if not scored:
            return None

        # First/last hops: 100% proximity
        if direction in ("bot", "sender"):
            p_weight, r_weight = 1.0, 0.0
        else:
            p_weight, r_weight = self.proximity_weight, self.recency_weight

        best, best_score = None, 0.0
        for repeater, recency_score in scored:
            d = calculate_distance(reference[0], reference[1], repeater['latitude'], repeater['longitude'])
            proximity_score = 1.0 - min(d / 1000.0, 1.0)
            combined = (recency_score * r_weight) + (proximity_score * p_weight)
            if repeater.get('is_starred', False):
                combined *= self.star_bias_multiplier
            if combined > best_score:
                best_score = combined
                best = repeater
        return best
