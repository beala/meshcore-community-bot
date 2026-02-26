#!/usr/bin/env python3
"""
MeshCore Community Bot
Extended MeshCore bot with multi-bot coordination support.

Wraps the meshcore-bot project (git submodule) and adds:
- Central coordinator integration
- Coverage-based response priority
- Message/packet reporting
"""

import asyncio
import sys

from community.community_core import CommunityBot


if __name__ == "__main__":
    bot = CommunityBot()

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}")
        asyncio.run(bot.stop())
