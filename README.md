# Nissan Connect Canada for Home Assistant

A modern, standalone Home Assistant integration for **Nissan Connect Canada** (`MyNISSAN Canada` / `login.nissan.ca`).

Built from the ground up to support modern Nissan Canada telematics, WSO2 OAuth2 Single Sign-On, and Nissan Hades Cloud GraphQL APIs. Tested and confirmed on both **Nissan ARIYA** (2023–2026) and **Nissan LEAF** (all trims including Leaf SL), as well as internal combustion Nissan vehicles.

---

## Key Features

* **Full Climate Control (`climate`)**:
  * Real Home Assistant climate entity with cabin temperature display.
  * Adjust target temperature (16.0 °C to 28.0 °C) in 0.5 °C increments.
  * Turn heating/cooling on (`HEAT_COOL`) or off (`OFF`) directly from dashboards or automations.
  * Instant action feedback and follow-up request polling.
* **Real Door Lock Entity (`lock`)**:
  * Standard Home Assistant lock entity reflecting door lock state in real-time.
  * Lock and unlock doors with a single stateful tile.
* **Remote Action Buttons (`button`)**:
  * **Start / Stop Charging** (`button.*_start_charging` / `button.*_stop_charging`): Remotely trigger or stop EV charging on demand (ideal for solar surplus or off-peak electricity automations).
  * **Locate Vehicle & Flash Lights** (`button.*_flash_lights`): Flash vehicle exterior lights to easily locate the car in dark parking areas.
  * **Honk Horn & Flash Lights** (`button.*_honk_horn_and_flash_lights`): Trigger both horn and lights for quick vehicle location.
  * **Start / Stop Climate** (`button.*_start_climate` / `button.*_stop_climate`): Quick-action buttons for pre-conditioning cabin temperature.
* **Complete Electric Vehicle (EV) Metrics**:
  * **Battery Level** (`sensor.*_battery_level` in %).
  * **Remaining Autonomy / Range** (`sensor.*_remaining_range` in km with accurate metric conversion).
  * **Remaining Charge Time** (`sensor.*_remaining_charge_time` in minutes).
  * **Charging Active Binary Sensor** (`binary_sensor.*_charging`).
  * **Charger Cable Plugged-In Binary Sensor** (`binary_sensor.*_plugged_in`).
* **Adaptive, Server-Friendly Polling**:
  * **Idle Polling:** 8 hours default (configurable from 1h to 24h) to avoid abusive polling and prevent account rate limits.
  * **Charging Polling:** Automatically accelerates to 30 minutes (configurable 5m to 120m) while the vehicle is actively charging.
  * **Climate Polling:** Automatically accelerates to 10 minutes (configurable 2m to 60m) while cabin heating/cooling is active.
* **Multi-Vehicle Single Session (Zero Duplicate Logins)**:
  * Having multiple Nissan vehicles (e.g. Ariya + Leaf) on the same account shares the exact same authenticated HTTP session, cookies, and OAuth tokens.
  * Zero redundant authentications to avoid server bans or quota violations.
  * Coordinated token renewal with thread locking across all configured vehicles.
* **Diagnostics & Health Indicators**:
  * **Last Communication** (`sensor.*_last_communication`): Timestamp when the car last uploaded telemetry to Nissan.
  * **Service Due Mileage** (`sensor.*_service_due_mileage`): Next maintenance milestone.
  * **12V Auxiliary Battery Alert** (`binary_sensor.*_12v_battery_alert`): Warns you before a weak 12V battery prevents starting.
  * **Vehicle Warning Light** (`binary_sensor.*_vehicle_warning`): Single aggregate indicator for instrument cluster warnings (ABS, airbags, brakes, power steering), with full warning messages provided in attributes.
  * **Openings**: Engine hood (`binary_sensor.*_hood`) and trunk/hatch (`binary_sensor.*_trunk`).
* **Secure Encrypted Credential Storage**:
  * Automatic unattended token renewal requires login capability because Nissan Canada WSO2 SSO refresh tokens expire after 7 days.
  * Stored credentials in Home Assistant are encrypted using **Fernet (AES-128-CBC + HMAC-SHA256)** with a 256-bit key kept in `.storage/nissan_connect_ca.key` (permission mode `0600`). Passwords are never stored in plaintext and are immediately wiped from RAM after use.

---

## Installation via HACS

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance.
2. In Home Assistant, open **HACS** > **Integrations**.
3. Click the three dots `...` in the top right corner and select **Custom repositories**.
4. In the **Repository** field, paste:
   ```text
   https://github.com/NicDesrochers/nissan_connect_ca
   ```
5. Select **Integration** as the Category and click **Add**.
6. Find **Nissan Connect Canada** in HACS and click **Download**.
7. Restart Home Assistant.

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

---

## Disclaimer

> This integration is an independent open-source project and is **not** affiliated with, maintained, authorized, or endorsed by Nissan Motor Co., Ltd., Nissan Canada, or any of their affiliates or subsidiaries.
>
> Use this integration at your own risk. The author is not responsible for any account suspension, battery drain, vehicle modifications, or misuse of the Nissan APIs.
