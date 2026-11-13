# Nissan Connect Canada for Home Assistant

A modern, standalone Home Assistant integration for **Nissan Connect Canada** (`MyNISSAN Canada` / `login.nissan.ca`).

Built to support modern Nissan Canada telematics, WSO2 OAuth2 Single Sign-On, and Nissan cloud APIs, including GraphQL and REST.

The maintainer reports successful tests on an **ARIYA** and a **2026 LEAF SL**. A tester also confirmed remote engine stop on a **2026 Rogue**. These tests do not certify every model year, feature, or Home Assistant configuration: availability and behavior depend on the vehicle, its subscriptions, and the Nissan backend.

> **Vehicle Compatibility Note:** This integration requires the modern **MyNISSAN Canada** cloud platform (`login.nissan.ca`). Older generation LEAF models (e.g. 2011–2017 using legacy Carwings / 2G-3G NissanConnect EV portals) use an older telematics backend and are not compatible with this integration.

---

## Key Features

* **Full Climate Control (`climate`)**:
  * Home Assistant climate entity displaying the temperature reported by Nissan's climate status.
  * Adjust target temperature (18 °C to 28 °C) in 1 °C increments. Higher targets were not applied reliably on a tested LEAF, and the app's "HI" setting is not exposed.
  * The selected target is retained across updates and Home Assistant restarts; if no target was restored, the integration uses Nissan's saved default when available, otherwise 21 °C.
  * Turn heating/cooling on (`HEAT_COOL`) or off (`OFF`) directly from dashboards or automations.
  * Changing the target while climate is running saves it for the next start without adjusting the active session. When climate is off, supported Nissan defaults are synchronized when the target changes.
  * Instant action feedback and follow-up request polling. While Nissan reports the remote request as `PENDING`, the MyNISSAN app may be unable to adjust, start or stop the climate until the vehicle answers (observed on a LEAF; likely Nissan-side behavior).
  * Debug logs for `custom_components.nissan_connect_ca` identify the selected target, its source, Nissan's default, and the temperature sent in `StartClimate`. Review and redact debug logs before sharing: they may contain vehicle and account information.
* **Real Door Lock Entity (`lock`)**:
  * Standard Home Assistant lock entity reflecting the latest door lock state reported by Nissan.
  * Lock and unlock doors with a single stateful tile.
* **Remote Action Buttons (`button`)**:
  * **Start / Stop Engine** (`button.*_start_engine_remotely` / `button.*_stop_engine_remotely`): Remote engine controls appear for non-electric vehicles when Nissan reports the service as requested and an engine ID is available.
  * **Start / Stop Charging** (`button.*_start_charging` / `button.*_stop_charging`): Remotely trigger or stop EV charging on demand (ideal for solar surplus or off-peak electricity automations).
  * **Locate Vehicle & Flash Lights** (`button.*_flash_lights`): Flash vehicle exterior lights to easily locate the car in dark parking areas.
  * **Honk Horn & Flash Lights** (`button.*_honk_horn_and_flash_lights`): Trigger both horn and lights for quick vehicle location.
  * **Start / Stop Climate** (`button.*_start_climate` / `button.*_stop_climate`): Quick-action buttons for pre-conditioning cabin temperature.
  * **Request vehicle status update**: Ask a supported vehicle to report fresh status to Nissan; this differs from the existing button that only reads the cloud data. The vehicle position (`device_tracker`) is the last one uploaded by the vehicle's telemetry and may lag, for example after parking; this button can force an update. Use it sparingly, as repeated requests can drain the 12 V battery.
* **Complete Electric Vehicle (EV) Metrics**:
  * **Battery Level** (`sensor.*_battery_level` in %).
  * **Remaining Autonomy / Range** (`sensor.*_remaining_range` in km with accurate metric conversion).
  * **Remaining Charge Time** (`sensor.*_remaining_charge_time` in minutes).
  * **Charging Active Binary Sensor** (`binary_sensor.*_charging`).
  * **Charger Cable Plugged-In Binary Sensor** (`binary_sensor.*_plugged_in`).
* **Adaptive Polling**:
  * **Idle Polling:** 8 hours default (configurable from 1h to 24h) to reduce requests and the risk of account rate limits.
  * **Charging Polling:** Automatically accelerates to 30 minutes (configurable 5m to 120m) while the vehicle is actively charging.
  * **Climate Polling:** Automatically accelerates to 10 minutes (configurable 2m to 60m) while cabin heating/cooling is active.
  * **Forced Battery Refresh:** While an EV is plugged in or charging, the integration sends Nissan's `refreshBatteryStatus` request every 2 hours by default (configurable 1h to 8h), since the cloud does not always receive battery updates during a charge. A "Refresh battery status" button triggers it manually.
* **Multi-Vehicle Session Sharing**:
  * Having multiple Nissan vehicles (e.g. Ariya + Leaf) on the same account shares the exact same authenticated HTTP session, cookies, and OAuth tokens.
  * Session sharing reduces redundant authentications but cannot guarantee that Nissan will not apply account limits.
  * Coordinated token renewal with thread locking across all configured vehicles.
* **Diagnostics & Health Indicators**:
  * **Schedules (EV)**: One diagnostic count sensor and one configuration switch per charge/climate schedule reported by Nissan (the switch enables or disables the schedule).
  * **Charge limit (EV, BaseElectric2)**: A `number` entity reads and sets the target charge limit (50–100 %, steps of 10).
  * **Charge history and V2L (EV)**: Sensors for the latest month's charge sessions, energy and duration, plus V2L state and minimum level, created only when Nissan returns data. When available, a V2L slider sets the minimum level from 10% to 95% in 5% increments.
  * **Configuration refresh**: Changing a charge limit, V2L limit or schedule refreshes the vehicle data; the integration also follows the request or polls the schedule until the change is reported.
  * **Remote climate accessories**: When Nissan reports supported accessories, configuration switches select steering wheel heat, defrost and front or rear seat heat for the next remote start. The switches store local preferences; on vehicles exposing accessory defaults, the integration reads and, when needed, persistently updates Nissan's climate defaults to match the selected temperature and accessories before starting climate. Changing the climate target while climate is off also updates those defaults. This can affect subsequent starts from the MyNISSAN app, but does not edit saved schedules. Without an override, Nissan's saved default is preserved; an unknown default remains unknown. On vehicles reporting `ElectricVehicle` without accessory defaults, such as the tested ARIYA, the target is retained in Home Assistant and sent with each start, but is not saved as a Nissan default. Nissan's acceptance of the command does not confirm that the vehicle applies the requested temperature.
  * **Alerts**: Diagnostic counters for enabled boundary, curfew and speed alerts, and the valet alert status (read-only; locations are not exposed).
  * **Tire pressures**: Read GraphQL tire pressures in PSI when available, falling back to RTS pressure values and units. Tire sensors are added when readings first become available; availability depends on the vehicle and Nissan backend.
  * **Last Communication** (`sensor.*_last_communication`): Timestamp when the car last uploaded telemetry to Nissan.
  * **Service Due Mileage** (`sensor.*_service_due_mileage`): Next maintenance milestone.
  * **12V Auxiliary Battery Alert** (`binary_sensor.*_12v_battery_alert`): Warns you before a weak 12V battery prevents starting.
  * **Vehicle Warning Light** (`binary_sensor.*_vehicle_warning`): Single aggregate indicator for instrument cluster warnings (ABS, airbags, brakes, power steering), with full warning messages provided in attributes.
  * **Openings**: Engine hood (`binary_sensor.*_hood`) and trunk/hatch (`binary_sensor.*_trunk`).
* **Secure Encrypted Credential Storage**:
  * Automatic unattended token renewal requires login capability because Nissan Canada WSO2 SSO refresh tokens expire after 7 days.
  * The integration encrypts the stored password using **Fernet (AES-128-CBC + HMAC-SHA256)** with a key in `.storage/nissan_connect_ca.key` (created with permission mode `0600`). The password must be decrypted for authentication; access to both the key and encrypted credentials allows recovery. Protect Home Assistant files and backups accordingly.

---

## Installation via HACS

Nissan Connect Canada is available in the default HACS catalog; no custom repository is needed.

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance.
2. In Home Assistant, open **HACS** > **Integrations**.
3. Search for **Nissan Connect Canada** and click **Download**.
4. Restart Home Assistant.

---

## Configuration

1. In Home Assistant, navigate to **Settings** > **Devices & Services**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **Nissan Connect Canada**.
4. Enter your Canadian Nissan Connect username (email) and password.
5. If you have multiple vehicles, select which one to add. Repeat the process to add your other vehicles—they will automatically link and share the same authenticated session.

### Integration Options
Click **Configure** on any configured vehicle entry to customize the polling rates:
* **Normal update interval:** 1 to 24 hours (default: `8h`).
* **Charging update interval:** 5 to 120 minutes (default: `30m`).
* **Active climate update interval:** 2 to 60 minutes (default: `10m`).
* **Battery refresh interval:** 1 to 8 hours (default: `2h`).

---

## Disclaimer

> This integration is an independent open-source project and is **not** affiliated with, maintained, authorized, or endorsed by Nissan Motor Co., Ltd., Nissan Canada, or any of their affiliates or subsidiaries.
>
> Use this integration at your own risk. The author is not responsible for any account suspension, battery drain, vehicle modifications, or misuse of the Nissan APIs.
