#!/usr/bin/env python3
"""
Message scheduler functionality for the MeshCore Bot
Handles scheduled messages and timing as an asyncio task
"""

import asyncio
import time
import datetime
import pytz
import sqlite3
import json
from typing import Dict, Tuple, Optional


class MessageScheduler:
    """Manages scheduled messages and timing as an asyncio task"""

    def __init__(self, bot):
        self.bot = bot
        self.logger = bot.logger
        self.scheduled_messages: Dict[str, Tuple[str, str, Optional[datetime.datetime]]] = {}
        self._task: Optional[asyncio.Task] = None

    def get_current_time(self):
        """Get current time in configured timezone"""
        timezone_str = self.bot.config.get('Bot', 'timezone', fallback='')

        if timezone_str:
            try:
                tz = pytz.timezone(timezone_str)
                return datetime.datetime.now(tz)
            except pytz.exceptions.UnknownTimeZoneError:
                self.logger.warning(f"Invalid timezone '{timezone_str}', using system timezone")
                return datetime.datetime.now()
        else:
            return datetime.datetime.now()

    def _get_timezone(self):
        """Get the configured timezone object, or None for system default"""
        timezone_str = self.bot.config.get('Bot', 'timezone', fallback='')
        if timezone_str:
            try:
                return pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                return None
        return None

    def setup_scheduled_messages(self):
        """Setup scheduled messages from config"""
        if self.bot.config.has_section('Scheduled_Messages'):
            self.logger.info("Found Scheduled_Messages section")
            for time_str, message_info in self.bot.config.items('Scheduled_Messages'):
                self.logger.info(f"Processing scheduled message: '{time_str}' -> '{message_info}'")
                try:
                    if not self._is_valid_time_format(time_str):
                        self.logger.warning(f"Invalid time format '{time_str}' for scheduled message: {message_info}")
                        continue

                    channel, message = message_info.split(':', 1)
                    hour = int(time_str[:2])
                    minute = int(time_str[2:])

                    # Calculate next run time
                    next_run = self._calculate_next_run(hour, minute)

                    self.scheduled_messages[time_str] = (channel.strip(), message.strip(), next_run)
                    self.logger.info(f"Scheduled message: {hour:02d}:{minute:02d} -> {channel.strip()}: {message.strip()} (next: {next_run})")
                except ValueError:
                    self.logger.warning(f"Invalid scheduled message format: {message_info}")
                except Exception as e:
                    self.logger.warning(f"Error setting up scheduled message '{time_str}': {e}")

        self.setup_interval_advertising()

    def _calculate_next_run(self, hour: int, minute: int) -> datetime.datetime:
        """Calculate the next run datetime for a scheduled time"""
        now = self.get_current_time()
        tz = self._get_timezone()

        # Build today's target time
        if tz:
            target = tz.localize(datetime.datetime(now.year, now.month, now.day, hour, minute))
        else:
            target = datetime.datetime(now.year, now.month, now.day, hour, minute)

        # If target time already passed today, schedule for tomorrow
        if target <= now:
            target += datetime.timedelta(days=1)

        return target

    def setup_interval_advertising(self):
        """Setup interval-based advertising from config"""
        try:
            advert_interval_hours = self.bot.config.getint('Bot', 'advert_interval_hours', fallback=0)
            if advert_interval_hours > 0:
                self.logger.info(f"Setting up interval-based advertising every {advert_interval_hours} hours")
                if not hasattr(self.bot, 'last_advert_time') or self.bot.last_advert_time is None:
                    self.bot.last_advert_time = time.time()
            else:
                self.logger.info("Interval-based advertising disabled (advert_interval_hours = 0)")
        except Exception as e:
            self.logger.warning(f"Error setting up interval advertising: {e}")

    def _is_valid_time_format(self, time_str: str) -> bool:
        """Validate time format (HHMM)"""
        try:
            if len(time_str) != 4:
                return False
            hour = int(time_str[:2])
            minute = int(time_str[2:])
            return 0 <= hour <= 23 and 0 <= minute <= 59
        except ValueError:
            return False

    def start(self) -> asyncio.Task:
        """Start the scheduler as an asyncio task"""
        self._task = asyncio.create_task(self.run_scheduler())
        return self._task

    def stop(self):
        """Stop the scheduler task"""
        if self._task and not self._task.done():
            self._task.cancel()

    async def run_scheduler(self):
        """Run the scheduler as an asyncio task in the main event loop"""
        self.logger.info("Scheduler task started")
        last_log_time = 0
        last_feed_poll_time = 0
        last_channel_ops_time = 0
        last_message_queue_time = 0

        try:
            while self.bot.connected:
                now = time.time()

                # Log current time every 5 minutes for debugging
                if now - last_log_time > 300:
                    current_time = self.get_current_time()
                    self.logger.info(f"Scheduler running - Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                    last_log_time = now

                # Check for due scheduled messages
                try:
                    await self._check_scheduled_messages()
                except Exception as e:
                    self.logger.error(f"Error checking scheduled messages: {e}")

                # Check for interval-based advertising
                try:
                    await self.check_interval_advertising()
                except Exception as e:
                    self.logger.error(f"Error checking interval advertising: {e}")

                # Poll feeds every 60 seconds
                if now - last_feed_poll_time >= 60:
                    if (hasattr(self.bot, 'feed_manager') and self.bot.feed_manager and
                        hasattr(self.bot.feed_manager, 'enabled') and self.bot.feed_manager.enabled and
                        self.bot.connected):
                        try:
                            await self.bot.feed_manager.poll_all_feeds()
                            self.logger.debug("Feed polling cycle completed")
                        except Exception as e:
                            self.logger.error(f"Error in feed polling cycle: {e}")
                    last_feed_poll_time = now

                # Process pending channel operations from web viewer (every 5 seconds)
                if now - last_channel_ops_time >= 5:
                    if (hasattr(self.bot, 'channel_manager') and self.bot.channel_manager and
                        self.bot.connected):
                        try:
                            await self._process_channel_operations()
                        except Exception as e:
                            self.logger.error(f"Error processing channel operations: {e}")
                    last_channel_ops_time = now

                # Process feed message queue (every 2 seconds)
                if now - last_message_queue_time >= 2:
                    if (hasattr(self.bot, 'feed_manager') and self.bot.feed_manager and
                        self.bot.connected):
                        try:
                            await self.bot.feed_manager.process_message_queue()
                        except Exception as e:
                            self.logger.error(f"Error processing message queue: {e}")
                    last_message_queue_time = now

                await asyncio.sleep(1)

        except asyncio.CancelledError:
            self.logger.info("Scheduler task cancelled")
        except Exception as e:
            self.logger.error(f"Scheduler task error: {e}")
        finally:
            self.logger.info("Scheduler task stopped")

    async def _check_scheduled_messages(self):
        """Check if any scheduled messages are due and send them"""
        if not self.scheduled_messages:
            return

        now = self.get_current_time()

        for time_str, (channel, message, next_run) in list(self.scheduled_messages.items()):
            if next_run is None:
                continue

            if now >= next_run:
                self.logger.info(f"Sending scheduled message at {now.strftime('%H:%M:%S')} to {channel}: {message}")
                try:
                    await self.bot.command_manager.send_channel_message(channel, message)
                except Exception as e:
                    self.logger.error(f"Error sending scheduled message: {e}")

                # Calculate next run (tomorrow at same time)
                hour = int(time_str[:2])
                minute = int(time_str[2:])
                next_run = self._calculate_next_run(hour, minute)
                self.scheduled_messages[time_str] = (channel, message, next_run)
                self.logger.debug(f"Next run for {time_str}: {next_run}")

    async def check_interval_advertising(self):
        """Check if it's time to send an interval-based advert"""
        try:
            advert_interval_hours = self.bot.config.getint('Bot', 'advert_interval_hours', fallback=0)
            if advert_interval_hours <= 0:
                return

            current_time = time.time()

            if not hasattr(self.bot, 'last_advert_time') or self.bot.last_advert_time is None:
                self.bot.last_advert_time = current_time
                return

            time_since_last_advert = current_time - self.bot.last_advert_time
            interval_seconds = advert_interval_hours * 3600

            if time_since_last_advert >= interval_seconds:
                self.logger.info(f"Time for interval-based advert (every {advert_interval_hours} hours)")
                await self.send_interval_advert()
                self.bot.last_advert_time = current_time

        except Exception as e:
            self.logger.error(f"Error checking interval advertising: {e}")

    async def send_interval_advert(self):
        """Send an interval-based advert"""
        current_time = self.get_current_time()
        self.logger.info(f"Sending interval-based flood advert at {current_time.strftime('%H:%M:%S')}")
        try:
            await self.bot.meshcore.commands.send_advert(flood=True)
            self.logger.info("Interval-based flood advert sent successfully")
        except Exception as e:
            self.logger.error(f"Error sending interval-based advert: {e}")

    async def _process_channel_operations(self):
        """Process pending channel operations from the web viewer"""
        try:
            db_path = self.bot.db_manager.db_path

            # Get pending operations (run in thread to avoid blocking)
            def _get_pending_ops():
                with sqlite3.connect(db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT id, operation_type, channel_idx, channel_name, channel_key_hex
                        FROM channel_operations
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT 10
                    ''')
                    return [dict(row) for row in cursor.fetchall()]

            operations = await asyncio.to_thread(_get_pending_ops)

            if not operations:
                return

            self.logger.info(f"Processing {len(operations)} pending channel operation(s)")

            for op in operations:
                op_id = op['id']
                op_type = op['operation_type']
                channel_idx = op['channel_idx']
                channel_name = op['channel_name']
                channel_key_hex = op['channel_key_hex']

                try:
                    success = False
                    error_msg = None

                    if op_type == 'add':
                        if channel_key_hex:
                            channel_secret = bytes.fromhex(channel_key_hex)
                            success = await self.bot.channel_manager.add_channel(
                                channel_idx, channel_name, channel_secret=channel_secret
                            )
                        else:
                            success = await self.bot.channel_manager.add_channel(
                                channel_idx, channel_name
                            )

                        if success:
                            self.logger.info(f"Successfully processed channel add operation: {channel_name} at index {channel_idx}")
                        else:
                            error_msg = "Failed to add channel"

                    elif op_type == 'remove':
                        success = await self.bot.channel_manager.remove_channel(channel_idx)

                        if success:
                            self.logger.info(f"Successfully processed channel remove operation: index {channel_idx}")
                        else:
                            error_msg = "Failed to remove channel"

                    # Update operation status (run in thread)
                    def _update_status(s, em, oid):
                        with sqlite3.connect(db_path) as conn:
                            cursor = conn.cursor()
                            if s:
                                cursor.execute('''
                                    UPDATE channel_operations
                                    SET status = 'completed',
                                        processed_at = CURRENT_TIMESTAMP,
                                        result_data = ?
                                    WHERE id = ?
                                ''', (json.dumps({'success': True}), oid))
                            else:
                                cursor.execute('''
                                    UPDATE channel_operations
                                    SET status = 'failed',
                                        processed_at = CURRENT_TIMESTAMP,
                                        error_message = ?
                                    WHERE id = ?
                                ''', (em or 'Unknown error', oid))
                            conn.commit()

                    await asyncio.to_thread(_update_status, success, error_msg, op_id)

                except Exception as e:
                    self.logger.error(f"Error processing channel operation {op_id}: {e}")
                    try:
                        def _mark_failed(oid, err):
                            with sqlite3.connect(db_path) as conn:
                                cursor = conn.cursor()
                                cursor.execute('''
                                    UPDATE channel_operations
                                    SET status = 'failed',
                                        processed_at = CURRENT_TIMESTAMP,
                                        error_message = ?
                                    WHERE id = ?
                                ''', (str(err), oid))
                                conn.commit()

                        await asyncio.to_thread(_mark_failed, op_id, e)
                    except Exception as update_error:
                        self.logger.error(f"Error updating operation status: {update_error}")

        except Exception as e:
            self.logger.error(f"Error in _process_channel_operations: {e}")
