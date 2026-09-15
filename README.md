# Solarmax Inverter for Home Assistant

[![Validate](https://github.com/oschick/solarmax-ha-integration/actions/workflows/validate.yml/badge.svg)](https://github.com/oschick/solarmax-ha-integration/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/oschick/solarmax-ha-integration.svg)](https://github.com/oschick/solarmax-ha-integration/releases/)
[![GitHub license](https://img.shields.io/github/license/oschick/solarmax-ha-integration.svg)](LICENSE)

Solarmax Inverter connects Home Assistant directly to a SolarMax inverter over
the MaxComm TCP protocol on your local network. It reads production, energy
totals, operating status, alarms, and diagnostic measurements without a cloud
account.

## At a glance

- Local, read-only communication with no cloud account
- Optional night values for dashboards and energy statistics
- One persistent inverter connection with automatic recovery
- Faster checks after daytime failures and quiet polling overnight
- Clear states for normal shutdowns and unexpected connection faults
- Native reconfiguration and repair flows in Home Assistant
- English, German, and French translations

## Before you install

You need:

- Home Assistant 2024.12.0 or newer
- A SolarMax inverter that exposes the MaxComm protocol over TCP
- Network access from Home Assistant to the inverter, normally on port `12345`

### Supported inverters

| Compatibility | Models or protocol |
| --- | --- |
| Confirmed by users | SolarMax 7TP2, 4200S, and 3000S |
| Likely to work | Other pre-2015 SolarMax inverters with MaxComm TCP support |
| Not supported | Models that provide only Modbus, serial, or cloud access |

Support is determined by the protocol, not only by the model name. Some
inverters omit individual MaxComm fields. The integration still works, but the
entities for those fields remain unavailable.

> [!IMPORTANT]
> A SolarMax inverter accepts only one TCP client at a time. Stop MaxTalk,
> vendor software, test scripts, or another Home Assistant instance before
> setting up the integration.

Give the inverter a fixed DHCP lease if possible, so its address does not
change after setup.

## Install

### HACS

1. Open HACS and select **Integrations**.
2. Open the menu and select **Custom repositories**.
3. Add `https://github.com/oschick/solarmax-ha-integration` as an
   **Integration** repository.
4. Search for **Solarmax Inverter** and install it.
5. Restart Home Assistant.

HACS handles updates after the custom repository has been added.

### Manual installation

1. Download `solarmax.zip` from the latest
   [GitHub release](https://github.com/oschick/solarmax-ha-integration/releases/).
2. Create `custom_components/solarmax` in the Home Assistant configuration
   directory and extract the archive there.
3. Confirm that `custom_components/solarmax/manifest.json` exists.
4. Restart Home Assistant.

## Set up and configure

After installation:

1. Open **Settings → Devices & services**.
2. Select **Add integration**.
3. Search for **Solarmax Inverter**.
4. Enter the inverter details and polling settings.

Home Assistant briefly connects to the inverter before saving the entry. After
a successful check, the integration creates one device and its sensor entities.

### Connection and polling settings

| Setting | Default | Description |
| --- | ---: | --- |
| Host | `192.168.1.100` | Inverter IP address or host name |
| Port | `12345` | MaxComm TCP port |
| Inverter address | `1` | MaxComm address from 1 to 249 |
| Update interval | `30 s` | Normal online interval from 5 to 3600 seconds |
| Device name | `Solarmax Inverter` | Device name shown in Home Assistant |
| Verify response checksum | On | Reject responses with an invalid MaxComm checksum |
| Keep sensor values overnight | Off | Apply the synthetic night policies described below |
| Twilight elevation threshold | `5°` | Sun elevation below which an offline inverter is expected |

### Change settings later

The integration menu offers two actions:

| Action | Use it for |
| --- | --- |
| **Reconfigure** | Host, port, inverter address, or device name |
| **Configure** | Update interval, checksum verification, night values, or twilight threshold |

Home Assistant tests a changed host, port, or inverter address before saving
it, so the inverter must be reachable. It then reloads the integration and
restores the previous connection if the new one cannot start. Entity IDs and
unique IDs stay unchanged. Changing only the device name does not contact the
inverter.

Options do not require a connection test. Home Assistant reloads the
integration after saving them and restores the previous options if that reload
fails.

### Checksum verification

Leave **Verify response checksum** enabled unless your inverter is known to
return valid MaxComm data with a non-standard checksum. If its logs repeatedly
show checksum errors, turn the option off. The integration will then ignore the
CRC value but will still reject malformed responses.

### Upgrading and downgrading

`v1.4.0` migrates existing entries to configuration schema version 2 while
preserving their connection settings and preferences. After migration,
`v1.3.3` and older releases cannot read the entry. To downgrade from `v1.4.0`,
restore a Home Assistant backup made before you installed the update.

## Connection, outages, and recovery

The integration owns one persistent TCP connection and reuses it across polls.
It requests device information separately from live measurements, so a missing
optional value does not discard an otherwise valid update.

### Architecture in brief

- The protocol layer builds and validates MaxComm frames, checks checksums when
  enabled, and converts raw register values to Home Assistant units.
- `SolarmaxLink` owns the inverter's single TCP connection and serializes
  requests so two exchanges cannot overlap.
- `ConnectionEngine` caches values, applies the 15-second poll budget and
  retry policy, and turns connection or protocol failures into an
  `EngineSnapshot`.
- `SolarmaxCoordinator` schedules the next poll from that snapshot and
  supplies the resulting state to sensors, diagnostics, and repairs.

See [the architecture guide](docs/architecture.md) for the full component and
state model.

### What the Status Code means

The **Status Code** entity shows the inverter's translated `SYS` state while it
is online. If the inverter cannot be reached, it reports one of these
integration states:

| Home Assistant state | Raw state | Meaning |
| --- | --- | --- |
| **Unknown** | `unknown` | No successful connection yet; daytime startup is still within its reconnect window |
| **Offline (expected)** | `offline_expected` | Darkness or inverter shutdown evidence explains the disconnect |
| **Offline (fault)** | `offline_fault` | The inverter failed during daytime without shutdown evidence |

The normal online condition is represented by the live inverter status rather
than a synthetic `online` value.

### How Home Assistant classifies an outage

The integration uses the last valid inverter data together with Home
Assistant's sun position:

1. A successful poll arms an expected shutdown when `SYS` reports `20002` or
   DC power (`PDC`) falls below 25 W.
2. The next disconnect is **Offline (expected)** when that evidence exists or
   the sun is below the configured twilight threshold.
3. An unexplained daytime disconnect is **Offline (fault)**.
4. A shutdown that starts unusually early is allowed one hour and at least ten
   failed probes before it escalates to a fault.

At daytime startup, the integration keeps the state **Unknown** (`unknown`)
during a 150-second reconnect window. This avoids raising a fault while the
inverter or network is still becoming available.

### Polling and recovery

| Condition | Poll interval |
| --- | --- |
| Online | Configured update interval |
| Startup reconnect | Configured interval or 60 seconds, whichever is shorter |
| Daytime fault | Configured interval or 60 seconds, whichever is shorter |
| Expected offline during full night | 15 minutes |
| Expected offline from civil dawn or during daytime | 60 seconds |

Expected-offline polling accelerates when the rising sun reaches -6°, before
the configurable twilight threshold used to classify faults. This helps detect
the inverter's return promptly without increasing traffic throughout the
night. Each poll has a 15-second budget. A lost response or corrupt frame gets
one retry within that budget. Reloading or unloading the integration closes
the socket so the inverter does not retain the client slot.

If Home Assistant's `sun.sun` entity is unavailable, the integration logs one
warning and uses the local clock: 20:00-06:00 for expected-offline
classification, with faster recovery polling starting at 05:00. A diagnostic
download reports `sun.sun`, `clock_fallback`, or `unknown` as the active sun
source.

### Connection repairs

After five minutes of a daytime fault, Home Assistant creates a repair issue.
Open it to change the host or port and test the connection. A successful test
saves the new connection, but the issue remains visible until the inverter
completes a full online update. You can also use Home Assistant's **Ignore**
action.

### Automations and diagnostics

When relevant, the Status Code entity exposes:

| Attribute | Purpose |
| --- | --- |
| `last_successful_update` | Time of the last valid inverter response |
| `fault_since` | Start of the current unexplained fault |
| `reconnecting` | Whether startup recovery is still in progress |
| `expected_outside_twilight` | Whether shutdown evidence occurred above the twilight threshold |
| `code` and `raw_value` | Raw status or offline details for diagnostics |

Automations created with an older version must replace **Offline (Night)**
(`offline_night`) with **Offline (expected)** (`offline_expected`), and
**Connection failed** (`connection_failed`) with **Offline (fault)**
(`offline_fault`). Automations match the raw values shown in parentheses.

## Sensor values at night

Many SolarMax inverters turn off their network interface when production ends.
That is normal, but it leaves Home Assistant without a live value until the
next successful poll.

To enable night values:

1. Open **Settings → Devices & services**.
2. Find **Solarmax Inverter** and select **Configure**.
3. Enable **Keep sensor values overnight** and save.

With this option disabled, measurement entities become unavailable while the
inverter is offline. When enabled, the integration applies a policy suited to
each register:

| Policy | Sensors | Value while expected offline |
| --- | --- | --- |
| Zero | `PAC`, `PDC`, `PD01`–`PD03`, `PRL`, `IDC`, `ID01`–`ID03`, `IL1`–`IL3`, `UDC`, `UD01`–`UD03` | `0` |
| Hold | `KMT`, `KYR`, `KT0`, `KHR`, `CAC`, `KLD`, `KLM`, `KLY`, `PIN`, `ULH`, `ULL`, `TNH`, `TNL`, `SAL` | Last successful value |
| Hold until midnight | `KDY` | Last value, then `0` after local midnight |
| Unavailable | `UL1`–`UL3`, `TNF`, `TKK`, `TK2`, `TK3`, and unlisted sensors | Unavailable |

Synthetic states include a `night_value_source` attribute such as `zero`,
`hold`, or `unavailable`, so automations can distinguish them from live data.

An early disconnect above the twilight threshold is treated cautiously. Zero
policy sensors stay unavailable until darkness because the integration cannot
yet assume production has stopped. Hold policy sensors may retain their last
value.

After a Home Assistant restart at night, a sensor remains unavailable until
the integration has received that register at least once. This prevents an
unsupported register from appearing as `0`. Once observed, zero-policy sensors
can report `0`, and hold-policy sensors can retain their last value.

## Available sensors

Entity names are translated in Home Assistant. The MaxComm register is shown
in parentheses to make protocol logs and diagnostics easier to interpret.

### Enabled by default

| Entity | Register | Unit or value |
| --- | --- | --- |
| AC power | `PAC` | W |
| DC power | `PDC` | W |
| Energy today | `KDY` | kWh |
| Energy this month | `KMT` | kWh |
| Energy this year | `KYR` | kWh |
| Total energy | `KT0` | kWh |
| Alarm | `SAL` | Translated alarm state and active alarm details |
| Status Code | `SYS` | Translated inverter or connection state |

### Optional production and history entities

| Entity | Register | Unit |
| --- | --- | --- |
| Energy yesterday | `KLD` | kWh |
| Energy last month | `KLM` | kWh |
| Energy last year | `KLY` | kWh |
| Relative power | `PRL` | % |

### Optional diagnostic entities

| Group | Registers | Unit |
| --- | --- | --- |
| DC string power | `PD01`–`PD03` | W |
| AC phase voltage | `UL1`–`UL3` | V |
| DC and string voltage | `UDC`, `UD01`–`UD03` | V |
| AC phase current | `IL1`–`IL3` | A |
| DC and string current | `IDC`, `ID01`–`ID03` | A |
| Temperature | `TKK`, `TK2`, `TK3` | °C |
| Operating hours | `KHR` | h |
| Start count | `CAC` | count |
| Installed power | `PIN` | W |
| Grid frequency | `TNF` | Hz |
| Grid voltage limits | `ULH`, `ULL` | V |
| Grid frequency limits | `TNH`, `TNL` | Hz |

Optional entities can be enabled from the SolarMax device page. An unavailable
optional entity usually means the inverter does not return that register; it
does not necessarily indicate a connection failure.

The Alarm entity exposes its numeric `code` and, for a bitmask containing
multiple alarms, an `active_alarms` attribute. The Status Code entity also
retains its raw code for diagnostics while displaying a translated state.

## Troubleshooting and support

| Symptom | First check |
| --- | --- |
| Setup cannot connect | Confirm the IP and port, then stop every other MaxComm client |
| Repeated checksum errors | Verify the inverter response, then try the checksum checkbox for known non-standard firmware |
| **Offline (fault)** (`offline_fault`) during daylight | Check power, Ethernet, IP address, and whether another client took the connection |
| **Offline (expected)** (`offline_expected`) at the wrong time | Check Home Assistant's location, time zone, sun entity, and twilight threshold |
| Only some entities are unavailable | The inverter may not implement those MaxComm registers |
| Held values are missing after a night restart | Wait for the first successful daytime poll |

The [troubleshooting guide](docs/troubleshooting.md) explains these checks,
logging, connection states, and diagnostic downloads in more detail.

If the problem remains, use the matching
[GitHub issue form](https://github.com/oschick/solarmax-ha-integration/issues/new/choose).
Include the Home Assistant and integration versions, inverter model, connection
state, relevant settings, diagnostics, and logs. Remove credentials or network
details that you do not want to publish.

## Contributing and development

[CONTRIBUTING.md](CONTRIBUTING.md) covers local setup, checks, translations,
pull requests, and releases. [docs/architecture.md](docs/architecture.md)
describes the protocol, connection engine, coordinator, entities, and test
emulator.

Run the same validation used by CI with:

```bash
script/check
```

## Disclaimer

This is an independent, community-maintained project. It is not affiliated
with, endorsed by, sponsored by, or supported by SolarMax or any related
company, distributor, installer, or rights holder. Product and company names
are trademarks of their respective owners.

## License

This project is licensed under the [MIT License](LICENSE).
