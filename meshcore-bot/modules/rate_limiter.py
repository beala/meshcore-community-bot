#!/usr/bin/env python3
"""
Rate limiting functionality for the MeshCore Bot
Controls how often messages can be sent to prevent spam
"""

import asyncio
import time


class RateLimiter:
    """Rate limiting for message sending"""
    
    def __init__(self, seconds: int):
        self.seconds = seconds
        self.last_send = 0
    
    def can_send(self) -> bool:
        """Check if we can send a message"""
        return time.monotonic() - self.last_send >= self.seconds

    def time_until_next(self) -> float:
        """Get time until next allowed send"""
        elapsed = time.monotonic() - self.last_send
        return max(0, self.seconds - elapsed)

    def record_send(self):
        """Record that we sent a message"""
        self.last_send = time.monotonic()


class BotTxRateLimiter:
    """Rate limiting for bot transmission to prevent network overload"""
    
    def __init__(self, seconds: float = 1.0):
        self.seconds = seconds
        self.last_tx = 0
    
    def can_tx(self) -> bool:
        """Check if bot can transmit a message"""
        return time.monotonic() - self.last_tx >= self.seconds

    def time_until_next_tx(self) -> float:
        """Get time until next allowed transmission"""
        elapsed = time.monotonic() - self.last_tx
        return max(0, self.seconds - elapsed)

    def record_tx(self):
        """Record that bot transmitted a message"""
        self.last_tx = time.monotonic()
    
    async def wait_for_tx(self):
        """Wait until bot can transmit (async)"""
        while not self.can_tx():
            wait_time = self.time_until_next_tx()
            if wait_time > 0:
                await asyncio.sleep(wait_time + 0.05)  # Small buffer


class NominatimRateLimiter:
    """Rate limiting for Nominatim geocoding API requests
    
    Nominatim policy: Maximum 1 request per second
    We'll be conservative and use 1.1 seconds to ensure compliance
    """
    
    def __init__(self, seconds: float = 1.1):
        self.seconds = seconds
        self.last_request = 0
        self._lock = None  # Will be set to asyncio.Lock if needed
    
    def can_request(self) -> bool:
        """Check if we can make a Nominatim request"""
        return time.monotonic() - self.last_request >= self.seconds

    def time_until_next(self) -> float:
        """Get time until next allowed request"""
        elapsed = time.monotonic() - self.last_request
        return max(0, self.seconds - elapsed)

    def record_request(self):
        """Record that we made a Nominatim request"""
        self.last_request = time.monotonic()
    
    async def wait_for_request(self):
        """Wait until we can make a Nominatim request (async)"""
        while not self.can_request():
            wait_time = self.time_until_next()
            if wait_time > 0:
                await asyncio.sleep(wait_time + 0.05)  # Small buffer
    
    def wait_for_request_sync(self):
        """Wait until we can make a Nominatim request (synchronous)"""
        while not self.can_request():
            wait_time = self.time_until_next()
            if wait_time > 0:
                time.sleep(wait_time + 0.05)  # Small buffer
