"""Refresh vehicle data after asynchronous configuration commands."""

import asyncio
import logging

import requests

from .api import NissanConnectAuthenticationError, NissanConnectCommandError

_LOGGER = logging.getLogger(__name__)
POLL_ATTEMPTS = 12
POLL_DELAY = 10
PENDING = {"PENDING", "IN_PROGRESS", "INITIATED", "SENT", "SCHEDULED"}
SUCCESS = {"SUCCESS", "SUCCESS_EXECUTION_CONFIRMED"}


async def async_poll_configuration(hass, api, coordinator, kind, request_id):
    """Track a configuration request and refresh after it finishes."""
    for _ in range(POLL_ATTEMPTS):
        await asyncio.sleep(POLL_DELAY)
        try:
            result = await hass.async_add_executor_job(
                api.check_configuration_request, kind, request_id
            )
        except (NissanConnectAuthenticationError, NissanConnectCommandError, requests.RequestException) as err:
            _LOGGER.warning("Unable to track Nissan %s configuration request: %s", kind, err)
            break
        status = result.get("status")
        if status in SUCCESS:
            await coordinator.async_refresh()
            return
        if status and status not in PENDING:
            _LOGGER.warning("Nissan %s configuration request ended with status %s", kind, status)
            break
    else:
        _LOGGER.warning("Nissan %s configuration request did not complete in time", kind)
    await coordinator.async_refresh()


async def async_poll_schedule(coordinator, key, schedule_id, expected_on):
    """Refresh until a schedule reflects the requested state."""
    for _ in range(POLL_ATTEMPTS):
        await asyncio.sleep(POLL_DELAY)
        await coordinator.async_refresh()
        schedules = (coordinator.data or {}).get("extra", {}).get(key) or []
        schedule = next((item for item in schedules if item.get("id") == schedule_id), None)
        if schedule and schedule.get("state") == ("ON" if expected_on else "OFF"):
            return
    _LOGGER.warning("Nissan schedule did not reflect the requested state in time")
