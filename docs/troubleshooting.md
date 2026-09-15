# Troubleshooting

Check the Status Code entity first. It stays available when the inverter does not respond.

| State | Action |
| --- | --- |
| `online` | The last poll succeeded. Investigate a specific sensor instead of the connection. |
| `unknown` | Allow the 150-second startup reconnect window. Check the host and port if it persists. |
| `offline_expected` | Confirm that it is dark or the inverter is shutting down. This is normal overnight. |
| `offline_fault` | Check the single-client constraint, network path, and inverter state. |

## Daytime connection failures

SolarMax inverters accept one TCP client. Close the vendor application, test scripts, and other Home Assistant instances before retrying. A crashed client can leave the inverter unavailable for about two minutes.

Confirm these settings from **Settings → Devices & services → Solarmax Inverter → Configure**:

- Host matches the inverter's current address.
- Port matches the MaxComm service, usually `12345`.
- Inverter address matches the device, usually `1`.

Saving options reloads the integration without a connection probe. Watch the Status Code entity and logs after the reload. A fault lasting five minutes creates an item under **Settings → Repairs**.

Avoid opening a manual TCP session while Home Assistant polls the inverter. That session can take the only client slot and change the failure you are trying to inspect.

## Expected offline at the wrong time

The integration marks a disconnect as expected after the inverter reports low irradiation (`SYS=20002`), after `PDC` falls below 25 W, or while the sun sits below the configured twilight threshold.

Adjust the twilight elevation threshold if your installation starts late or shuts down early because of terrain, roof orientation, or shading. An armed disconnect above the threshold exposes `expected_outside_twilight`. It becomes `offline_fault` after one hour and ten failed probes if the sun never explains the outage.

## Checksum errors

Keep **Verify response checksum** enabled for normal operation. Repeated checksum failures with otherwise plausible values may indicate firmware with a non-standard checksum implementation. Disable the checkbox from **Configure** only after confirming that pattern.

The integration honors the checkbox during initial setup and runtime polling. Ignoring checksums cannot fix timeouts, malformed frames, a wrong inverter address, or another client holding the connection.

## Unavailable sensors

An inverter may omit MaxComm fields it does not support. Those entities remain unavailable while the rest continue to update.

During `offline_fault`, data sensors become unavailable from the first failed poll. During `offline_expected`, **Keep sensor values overnight** can zero production readings, hold cumulative values, and reset daily energy at local midnight. Grid voltage, grid frequency, and temperatures remain unavailable because the integration cannot infer honest values.

After a restart at night, held sensors remain unavailable until one successful daytime poll provides a value.

## Logs and diagnostics

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.solarmax: debug
```

Restart Home Assistant or reload logging, reproduce the issue, then download diagnostics from the Solarmax integration page.

Diagnostics redact the configured host and inverter serial number. Review the file and log excerpt before publishing them because surrounding Home Assistant data may contain network names or other private details.

Include this information in a bug report:

- Home Assistant and integration versions
- Inverter model and firmware, when known
- Installation method and connection state
- Time of day, sun conditions, and twilight setting
- Checksum and overnight-value settings
- Other software that can poll the inverter
- Diagnostics and the relevant debug log window

Use the [bug report form](https://github.com/oschick/solarmax-ha-integration/issues/new/choose) after removing private data.
