"""Regression tests for vehicle configuration."""

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.nissan_connect_ca.config_flow import ConfigFlow
from custom_components.nissan_connect_ca.const import CONF_VEHICLE_ID


@pytest.mark.parametrize("selection", ["single", "multiple"])
async def test_vehicle_entry_sets_unique_id_before_checking_duplicates(selection):
    flow = ConfigFlow()
    vehicle = {"vehicle_id": "vehicle-id", "vin": "vin", "name": "Nissan"}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock(
        side_effect=lambda: flow.async_set_unique_id.assert_awaited_once_with("vehicle-id")
    )
    flow.async_create_entry = Mock(return_value={"type": "create_entry"})

    if selection == "single":
        flow._async_login = AsyncMock(return_value=({}, [vehicle]))
        flow._async_current_ids = Mock(return_value=set())
        flow._async_current_entries = Mock(return_value=[])
        result = await flow.async_step_user({"username": "user", "password": "password"})
    else:
        flow._pending_data = {}
        flow._pending_vehicles = [vehicle]
        result = await flow.async_step_select_vehicle({CONF_VEHICLE_ID: "vehicle-id"})

    assert result == {"type": "create_entry"}
    flow._abort_if_unique_id_configured.assert_called_once_with()
    assert flow.async_create_entry.call_args.kwargs["data"][CONF_VEHICLE_ID] == "vehicle-id"
