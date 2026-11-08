"""Tests for refreshes following remote configuration commands."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.nissan_connect_ca.command_refresh import (
    async_poll_configuration,
    async_poll_schedule,
)
from custom_components.nissan_connect_ca.api import NissanConnectCommandError


async def executor(func, *args):
    return func(*args)


@pytest.mark.parametrize("kind", ["charge", "v2l"])
async def test_configuration_refreshes_after_pending_and_success(kind):
    hass = Mock(async_add_executor_job=executor)
    api = Mock()
    api.check_configuration_request.side_effect = [{"status": "SENT"}, {"status": "SUCCESS"}]
    coordinator = Mock(async_refresh=AsyncMock())
    with patch("custom_components.nissan_connect_ca.command_refresh.asyncio.sleep", new=AsyncMock()):
        await async_poll_configuration(hass, api, coordinator, kind, "request")
    assert api.check_configuration_request.call_count == 2
    api.check_configuration_request.assert_called_with(kind, "request")
    coordinator.async_refresh.assert_awaited_once()


async def test_configuration_failure_is_logged_and_refreshes(caplog):
    hass = Mock(async_add_executor_job=executor)
    api = Mock()
    api.check_configuration_request.return_value = {"status": "FAILED"}
    coordinator = Mock(async_refresh=AsyncMock())
    with patch("custom_components.nissan_connect_ca.command_refresh.asyncio.sleep", new=AsyncMock()):
        await async_poll_configuration(hass, api, coordinator, "charge", "request")
    assert "FAILED" in caplog.text
    coordinator.async_refresh.assert_awaited_once()


async def test_schedule_refreshes_until_requested_state():
    coordinator = Mock()
    coordinator.data = {"extra": {"chargeSchedules": [{"id": "1", "state": "OFF"}]}}

    async def refresh():
        if coordinator.async_refresh.await_count == 2:
            coordinator.data["extra"]["chargeSchedules"][0]["state"] = "ON"

    coordinator.async_refresh = AsyncMock(side_effect=refresh)
    with patch("custom_components.nissan_connect_ca.command_refresh.asyncio.sleep", new=AsyncMock()):
        await async_poll_schedule(coordinator, "chargeSchedules", "1", True)
    assert coordinator.async_refresh.await_count == 2


async def test_configuration_tracking_error_still_refreshes(caplog):
    hass = Mock(async_add_executor_job=executor)
    api = Mock()
    api.check_configuration_request.side_effect = NissanConnectCommandError("unavailable")
    coordinator = Mock(async_refresh=AsyncMock())
    with patch("custom_components.nissan_connect_ca.command_refresh.asyncio.sleep", new=AsyncMock()):
        await async_poll_configuration(hass, api, coordinator, "v2l", "request")
    assert "unavailable" in caplog.text
    coordinator.async_refresh.assert_awaited_once()


async def test_configuration_timeout_still_refreshes(caplog):
    hass = Mock(async_add_executor_job=executor)
    api = Mock()
    api.check_configuration_request.return_value = {"status": "PENDING"}
    coordinator = Mock(async_refresh=AsyncMock())
    with (
        patch("custom_components.nissan_connect_ca.command_refresh.asyncio.sleep", new=AsyncMock()),
        patch("custom_components.nissan_connect_ca.command_refresh.POLL_ATTEMPTS", 2),
    ):
        await async_poll_configuration(hass, api, coordinator, "v2l", "request")
    assert "did not complete in time" in caplog.text
    coordinator.async_refresh.assert_awaited_once()
