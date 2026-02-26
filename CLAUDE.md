# CLAUDE.md

## Overview

MeshCore Community Bot - Extended MeshCore mesh radio bot with multi-bot coordination. Wraps the meshcore-bot project (git submodule) and adds coordinator integration for coordinated response priority across multiple bot instances.

## Architecture

- **Base bot:** meshcore-bot (git submodule at `meshcore-bot/`) - untouched
- **Extension:** `community/` package adds coordinator client, message interceptor, packet reporter
- **Entry point:** `community_bot.py` → `community/community_core.py:CommunityBot` (extends `MeshCoreBot`)

## Key Integration Point

**`CommandManager.send_response()`** at `meshcore-bot/modules/command_manager.py:552` is patched by `MessageInterceptor`. This single method captures ALL bot responses. The interceptor:
1. Lets DMs through immediately (no coordination needed)
2. Checks with coordinator for channel messages
3. Falls back to score-based delay if coordinator is unreachable

All 20+ existing commands work unchanged - they call `BaseCommand.send_response()` which delegates to `CommandManager.send_response()`.

## Project Structure

```
community_bot.py                    # Entry point
community/
├── community_core.py              # CommunityBot extends MeshCoreBot
├── coordinator_client.py          # httpx client for coordinator API
├── message_interceptor.py         # Patches send_response for coordination
├── packet_reporter.py             # Background batch reporter
├── coverage_fallback.py           # Score-based delay when coordinator down
├── config.py                      # Coordinator config from env/ini
└── commands/
    ├── coverage_command.py        # "coverage" - show bot's score
    └── botstatus_command.py       # "botstatus" - coordinator status
meshcore-bot/                      # Git submodule (DO NOT MODIFY)
```

## Adding New Community Commands

Same pattern as meshcore-bot - create a file in `community/commands/`:

```python
from modules.commands.base_command import BaseCommand
from modules.models import MeshMessage

class MyCommand(BaseCommand):
    name = "mycommand"
    keywords = ["mycommand"]
    description = "Does something"

    async def execute(self, message: MeshMessage) -> bool:
        await self.send_response(message, "Hello!")
        return True
```

**Note:** Community commands need `meshcore-bot` on sys.path. See existing commands for the path setup pattern.

## Configuration

Config via environment variables (`.env`) mapped to `config.ini` by `docker/entrypoint.sh`:
- `COORDINATOR_URL` - Central coordinator API URL
- `MESH_REGION` - Region code (e.g., DEN)
- `DISCORD_BOT_WEBHOOK_URL` - Discord webhook for #bot channel
- `DISCORD_EMERGENCY_WEBHOOK_URL` - Discord webhook for #emergency
- `MESHCORE_*` - All standard meshcore-bot settings
- See `.env.example` for full list

## Development

```bash
# Clone with submodule
git clone --recurse-submodules <repo-url>

# Local dev
pip install -r requirements.txt
python3 community_bot.py

# Docker
cp .env.example .env  # Edit with your values
docker compose up -d
docker compose logs -f
```

## Coordination Flow

1. Bot receives channel message matching a command
2. `MessageInterceptor` computes message hash (sha256 of sender + content + time bucket)
3. Asks coordinator `POST /should-respond` (100ms timeout)
4. If coordinator says yes → respond normally
5. If coordinator says no → suppress response (another bot handles it)
6. If coordinator unreachable → wait score-based delay, then respond

## Deployment

- Community members: clone, edit `.env`, `docker compose up -d`
- Auto-release: push tag `v*` for Docker image + GitHub release
- Coordinator URL defaults to `https://coordinator.denvermc.com`
