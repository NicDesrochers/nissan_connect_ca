"""Translate Nissan climate defaults to mutation input parameters."""

CLIMATE_OVERRIDES = "climate_accessory_overrides"
ACCESSORIES = {
    "climate_steering_wheel": ("steeringWheelHeaterState", None, "ON"),
    "climate_defrost": ("defrostAndDeicerState", None, "ON"),
    "climate_driver_seat": ("seatsClimate", "frontDriverState", "HEAT"),
    "climate_passenger_seat": ("seatsClimate", "frontPassengerState", "HEAT"),
    "climate_rear_left_seat": ("seatsClimate", "rearLeftPassengerState", "HEAT"),
    "climate_rear_right_seat": ("seatsClimate", "rearRightPassengerState", "HEAT"),
}
REAR_SEAT_CAPABILITIES = {
    "climate_rear_left_seat": "secondLeftSeat",
    "climate_rear_right_seat": "secondRightSeat",
}
SEAT_FIELDS = (
    "frontDriverState", "frontPassengerState", "rearLeftPassengerState",
    "rearRightPassengerState", "rearCenterPassengerState", "thirdLeftState",
    "thirdRightState",
)


def climate_accessory_supported(snapshot, name):
    """Check rear seat heat capability when a Nissan default is unavailable."""
    capability = REAR_SEAT_CAPABILITIES.get(name)
    if not capability:
        return False
    data = (snapshot or {}).get("extra", {}).get("capabilities") or {}
    heater = (data.get("accessoriesDetails") or {}).get("seatHeater") or {}
    return (
        heater.get("enabled") is True
        and (heater.get("accessories") or {}).get(capability) in ("HEATING", "HEATING_AND_COOLING")
    )


def climate_parameters(snapshot, overrides=None):
    """Keep supported defaults, omitting GraphQL metadata and null values."""
    defaults = ((snapshot or {}).get("climateDefaults") or {}).get("parameters") or {}
    parameters = {}
    for key in ("steeringWheelHeaterState", "defrostAndDeicerState"):
        if defaults.get(key) in ("ON", "OFF"):
            parameters[key] = defaults[key]
    seats = defaults.get("seatsClimate") or {}
    seat_parameters = {
        key: seats[key] for key in SEAT_FIELDS if seats.get(key) in ("HEAT", "COOL", "OFF")
    }
    if seat_parameters:
        parameters["seatsClimate"] = seat_parameters
    for name, enabled in (overrides or {}).items():
        if name not in ACCESSORIES or not isinstance(enabled, bool):
            continue
        key, seat, on_state = ACCESSORIES[name]
        if seat:
            if seat in seat_parameters or climate_accessory_supported(snapshot, name):
                seat_parameters[seat] = on_state if enabled else "OFF"
                parameters["seatsClimate"] = seat_parameters
        elif key in parameters:
            parameters[key] = on_state if enabled else "OFF"
    return parameters
