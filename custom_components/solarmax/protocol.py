"""Pure MaxComm protocol codec: request framing, checksums, field scaling.

Implements the MaxComm data protocol for communication with SolarMax products.
Protocol reference: "Beschreibung des MaxComm Datenprotokolls" (August 2022)

Protocol structure:
    {<Src-Adr>;<Dest-Adr>;<Length>|<Port>:<Data>|<CRC>}

    STX  = '{'   Start of Text (ASCII 123)
    ETX  = '}'   End of Text (ASCII 125)
    FRS  = '|'   Frame Separator (ASCII 124)
    US   = ':'   Union Separator (ASCII 58)
    FS   = ';'   Field Separator (ASCII 59)

All numeric values are transmitted as ASCII hex characters.
Communication is Master-Slave: devices only respond to requests.
Typical response time: 300ms, maximum timeout: 3000ms.

This module is pure — no sockets, no I/O, just MaxComm string framing/parsing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# MaxComm Protocol Constants
# =============================================================================

# Protocol framing characters
PROTO_STX = "{"  # Start of Text (ASCII 123)
PROTO_ETX = "}"  # End of Text (ASCII 125)
PROTO_FRS = "|"  # Frame Separator (ASCII 124)
PROTO_US = ":"  # Union Separator (ASCII 58)
PROTO_FS = ";"  # Field Separator (ASCII 59)

# Protocol port numbers
PROTO_PORT_MESSAGE = 0x3E8  # 1000 - Interface messages (IPR, IPN)

# Protocol error indicators in responses
PROTO_ERROR_INVALID_PROTOCOL = "IPR"  # Checksum/length error
PROTO_ERROR_INVALID_PORT = "IPN"  # Invalid port number

# =============================================================================
# Network Variable Types (per MaxComm protocol spec)
# Defines the resolution/scaling for each data type.
# =============================================================================
# Spannung_2:         0.1 V/digit    (UDC, UL1, UL2, UL3, UD01, UD02, UD03)
# Strom_positiv_2:    0.01 A/digit   (IDC, ID01, ID02, ID03, IL1, IL2, IL3)
# Leistung:           0.5 W/digit    (PAC, PDC, PD01, PD02, PD03, PIN)
# Energie_1:          0.1 kWh/digit  (KDY, KLD)
# Energie_2:          1 kWh/digit    (KMT, KYR, KT0, KLM, KLY)
# Frequenz:           0.01 Hz/digit  (TNF)
# Temperatur_positiv: 1 °C/digit     (TKK)
# ohne_Einheit_1:     1/digit 32-bit (KHR)
# ohne_Einheit_2:     1/digit 16-bit (SWV, TYP, PRL)
# Register:           1/digit 16-bit (SYS, SAL)

# =============================================================================
# Field Mapping for Inverter Data Queries
# =============================================================================
# Keys documented in the MaxComm protocol specification (Section 2.4):
#   PAC, KHR, KDY, KMT, KYR, KT0, UDC, UL1, UL2, UL3,
#   IDC, IL1, IL2, IL3, TKK, PIN, TNP, ADR, PRL, SWV, TYP, SYS, SAL
#
# Additional keys supported by some models (undocumented/model-specific):
#   PDC, PD01, PD02, UD01, UD02, ID01, ID02, CAC
#
# Per the protocol: "Nicht definierte Keys werden in der Antwort ignoriert"
# (Undefined keys are simply ignored in the response)

FIELD_MAP_INVERTER: dict[str, str] = {
    # Official MaxComm protocol keys (Section 2.4)
    "PAC": "AC_Power",  # Leistung (0.5 W/digit)
    "UDC": "DC_Voltage",  # Spannung_2 (0.1 V/digit)
    "IDC": "DC_Current",  # Strom_positiv_2 (0.01 A/digit)
    "UL1": "AC_Voltage_Phase_1",  # Spannung_2 (0.1 V/digit)
    "UL2": "AC_Voltage_Phase_2",  # Spannung_2 (0.1 V/digit)
    "UL3": "AC_Voltage_Phase_3",  # Spannung_2 (0.1 V/digit)
    "IL1": "AC_Current_Phase_1",  # Strom_positiv_2 (0.01 A/digit)
    "IL2": "AC_Current_Phase_2",  # Strom_positiv_2 (0.01 A/digit)
    "IL3": "AC_Current_Phase_3",  # Strom_positiv_2 (0.01 A/digit)
    "KDY": "Energy_Day",  # Energie_1 (0.1 kWh/digit)
    "KMT": "Energy_Month",  # Energie_2 (1 kWh/digit)
    "KYR": "Energy_Year",  # Energie_2 (1 kWh/digit)
    "KT0": "Energy_Total",  # Energie_2 (1 kWh/digit)
    "KHR": "Operating_Hours",  # ohne_Einheit_1 (1 h/digit)
    "TKK": "Temperature_Power_Unit",  # Temperatur_positiv (1 °C/digit)
    "TK2": "Temperature_Power_Unit_2",  # Temperatur_positiv (1 °C/digit)
    "TK3": "Temperature_Power_Unit_3",  # Temperatur_positiv (1 °C/digit)
    "SYS": "Status_Code",  # Register
    "SAL": "Alarm_Code",  # Register (bitmask)
    "PIN": "Installed_Power",  # Leistung (0.5 W/digit) — rated inverter power
    "PRL": "Relative_Power",  # ohne_Einheit_2 (1 %/digit) — % of rated power
    # Grid monitoring configuration (read from inverter settings)
    "ULH": "Grid_Voltage_Upper_Limit",  # Spannung_2 (0.1 V/digit)
    "ULL": "Grid_Voltage_Lower_Limit",  # Spannung_2 (0.1 V/digit)
    "TNH": "Grid_Frequency_Upper_Limit",  # Frequenz_2 (0.01 Hz/digit)
    "TNL": "Grid_Frequency_Lower_Limit",  # Frequenz_2 (0.01 Hz/digit)
    # Model-specific keys (not in official MaxComm spec, but supported by some models)
    "PDC": "DC_Power",  # Leistung (0.5 W/digit)
    "PD01": "DC_Power_String_1",  # Leistung (0.5 W/digit)
    "PD02": "DC_Power_String_2",  # Leistung (0.5 W/digit)
    "PD03": "DC_Power_String_3",  # Leistung (0.5 W/digit)
    "UD01": "DC_Voltage_String_1",  # Spannung_2 (0.1 V/digit)
    "UD02": "DC_Voltage_String_2",  # Spannung_2 (0.1 V/digit)
    "UD03": "DC_Voltage_String_3",  # Spannung_2 (0.1 V/digit)
    "ID01": "DC_Current_String_1",  # Strom_positiv_2 (0.01 A/digit)
    "ID02": "DC_Current_String_2",  # Strom_positiv_2 (0.01 A/digit)
    "ID03": "DC_Current_String_3",  # Strom_positiv_2 (0.01 A/digit)
    "KLD": "Energy_Yesterday",  # Energie_1 (0.1 kWh/digit)
    "KLM": "Energy_Last_Month",  # Energie_2 (1 kWh/digit)
    "KLY": "Energy_Last_Year",  # Energie_2 (1 kWh/digit)
    "TNF": "Grid_Frequency",  # Frequenz (0.01 Hz/digit)
    "CAC": "Startups",  # ohne_Einheit_1
}

# Field-set groupings used by the connection engine to decide what to poll.
STATIC_FIELDS: tuple[str, ...] = ("PIN", "ULH", "ULL", "TNH", "TNL")
DEVICE_FIELDS: tuple[str, ...] = ("TYP", "SWV", "DIN", "BDN")
HOT_FIELDS: tuple[str, ...] = tuple(
    key for key in FIELD_MAP_INVERTER if key not in STATIC_FIELDS
)

# MaxComm request template
# Format: {<Src-Adr>;<Dest-Adr>;<Length>|<Port>:<Keys>|<CRC>}
# - FB (251) = Host address per MaxComm spec Section 1.3
# - ## = Dest-Adr placeholder (device address, 2 hex chars)
# - !! = Length placeholder (total packet length, 2 hex chars)
# - 64 = Port 100 (0x64) for data queries per Section 1.4
# - && = Data keys placeholder (semicolon-separated)
# - $$$$ = CRC placeholder (4 hex chars)
REQUEST_TEMPLATE = "{FB;##;!!|64:&&|$$$$}"

# Value scaling per MaxComm Section 2.2 (Netzwerkvariable): physical = raw / divisor.
# Fields not listed are transmitted 1:1 (Energie_2, Temperatur, unitless values,
# and the SYS/SAL registers, which are decoded at the sensor level).
_SCALE_GROUPS: dict[int, tuple[str, ...]] = {
    2: ("PAC", "PDC", "PD01", "PD02", "PD03", "PIN"),  # Leistung: 0.5 W/digit
    10: (
        "UL1",
        "UL2",
        "UL3",
        "UDC",
        "UD01",
        "UD02",
        "UD03",
        "ULH",
        "ULL",  # 0.1 V
        "KDY",
        "KLD",  # Energie_1: 0.1 kWh/digit
    ),
    100: (
        "IDC",
        "ID01",
        "ID02",
        "ID03",
        "IL1",
        "IL2",
        "IL3",  # Strom: 0.01 A/digit
        "TNH",
        "TNL",
        "TNF",  # Frequenz: 0.01 Hz/digit
    ),
}
_FIELD_DIVISOR: dict[str, int] = {
    field: divisor for divisor, fields in _SCALE_GROUPS.items() for field in fields
}

# Frame layout: {<payload>|<CRC>}  — CRC is 4 hex chars immediately before the ETX.
_CRC_LEN = 4
_HEX_CHARS = frozenset("0123456789ABCDEFabcdef")


def _frame_payload(frame: str) -> str:
    """Return the CRC-covered payload: everything between STX and the CRC+ETX."""
    return frame[1 : -(_CRC_LEN + 1)]


def _frame_crc(frame: str) -> str:
    """Return the 4-char CRC stated at the end of a frame (just before the ETX)."""
    return frame[-(_CRC_LEN + 1) : -1]


def _is_hex(value: str, *, length: int | None = None) -> bool:
    """Return whether value contains the expected number of hex digits."""
    return (
        bool(value)
        and (length is None or len(value) == length)
        and all(char in _HEX_CHARS for char in value)
    )


class ProtocolError(Exception):
    """Raised when MaxComm protocol communication fails."""


class RetryableProtocolError(ProtocolError):
    """Transient protocol failure (corrupted/truncated response).

    Unlike deterministic errors (IPR/IPN), these may succeed on retry.
    """


def build_request(address: int, fields: Sequence[str]) -> str:
    """Build a MaxComm protocol request message.

    Request format per MaxComm spec Section 2.1:
        {<Src>;<Dest>;<Len>|<Port>:<Key1>;<Key2>;...|<CRC>}

    The CRC is the sum of ASCII values of all characters from Src-Adr
    up to and including the FRS before the CRC field (Section 1.1).

    Raises ProtocolError if the request exceeds 255 bytes
    (the maximum representable in the 2-hex-char length field).
    """
    fields_str = PROTO_FS.join(fields)
    req = REQUEST_TEMPLATE.replace("##", format(address, "02X"))
    req = req.replace("&&", fields_str)

    if len(req) > 255:
        raise ProtocolError(
            f"Request too large ({len(req)} bytes, max 255). "
            f"Reduce the number of queried fields."
        )

    # Length = total packet length (including STX/ETX, per protocol spec)
    req = req.replace("!!", format(len(req), "02X"))
    # CRC covers the payload: from Src-Adr to (and including) the FRS before
    # the CRC placeholder, i.e. everything except STX and the "$$$$}" tail.
    req = req.replace("$$$$", calculate_checksum(_frame_payload(req)))
    return req


def calculate_checksum(data: str) -> str:
    """Calculate MaxComm CRC: sum of ASCII values, formatted as 4-char hex.

    Per protocol spec Section 1.1: "Summe der ASCII-Werte aller Zeichen
    von Adress bis und mit dem FRS vor Crc"
    """
    checksum_value = sum(ord(c) for c in data)
    return format(checksum_value, "04X")


def split_frames(data: str) -> list[str]:
    """Split a multi-frame response into individual frames.

    The inverter splits responses exceeding 255 bytes into multiple
    frames, each with its own {Src;Dest;Len|...|CRC} structure.
    Non-final frames use ')' as ETX, the final frame uses '}'.
    """
    frames = []
    current = ""
    for char in data:
        current += char
        if char == PROTO_ETX or char == ")":
            if current.startswith(PROTO_STX):
                frames.append(current)
            current = ""
    return frames


def verify_frame_checksum(frame: str) -> bool:
    """Verify the CRC checksum of a MaxComm response frame.

    Returns True if checksum is valid, False otherwise.
    Per protocol spec: if CRC or Length don't match, the data should be
    considered corrupted (Section 3.1).

    Accepts both '}' (final frame) and ')' (continuation frame) as ETX.
    """
    try:
        if not frame or frame[0] != PROTO_STX:
            return False
        if frame[-1] != PROTO_ETX and frame[-1] != ")":
            return False

        # Extract the stated CRC and recompute it over the same payload
        # range used by build_request().
        stated_crc = _frame_crc(frame)
        expected_crc = calculate_checksum(_frame_payload(frame))
        if stated_crc != expected_crc:
            _LOGGER.warning(
                "MaxComm CRC mismatch: stated=%s, calculated=%s",
                stated_crc,
                expected_crc,
            )
            return False
        return True
    except (IndexError, ValueError) as e:
        _LOGGER.debug("CRC verification failed: %s", e)
        return False


def scale_value(field: str, raw: int) -> float | int:
    """Convert raw hex digit value to physical units per MaxComm network variables.

    Scaling factors per MaxComm protocol Section 2.2 (Netzwerkvariable):
    - Leistung (Power):         0.5 W/digit   → value / 2
    - Spannung_2 (Voltage):     0.1 V/digit   → value / 10
    - Strom_positiv_2 (Current): 0.01 A/digit → value / 100
    - Energie_1 (Energy Day):   0.1 kWh/digit → value / 10
    - Energie_2 (Energy M/Y/T): 1 kWh/digit   → no conversion
    - Temperatur_positiv:       1 °C/digit    → no conversion
    - ohne_Einheit (unitless):  1/digit       → no conversion
    - Register (SYS/SAL):       raw value     → decoded at sensor level
    """
    divisor = _FIELD_DIVISOR.get(field)
    if divisor is None:
        # Energie_2, Temperatur, unitless, and SYS/SAL registers: raw 1:1
        # (SYS/SAL are decoded into enums/bitmasks at the sensor level).
        return raw
    return raw / divisor


def _extract_data_from_frames(frames: list[str]) -> str:
    """Merge frame payloads, preserving fields split across frame boundaries."""
    return "".join(
        _extract_frame_data(frame, first=index == 0)
        for index, frame in enumerate(frames)
    )


def _extract_frame_data(frame: str, *, first: bool) -> str:
    """Return the data section from one response frame."""
    inner = _frame_payload(frame).removesuffix(PROTO_FRS)
    _, separator, payload = inner.partition(PROTO_FRS)
    if not separator:
        return ""

    # Only the first frame prefixes its data with the response port.
    if first and PROTO_US in payload:
        return payload.partition(PROTO_US)[2]
    return payload


def _has_valid_frame_structure(frame: str, *, first: bool, final: bool) -> bool:
    """Return whether a frame has a valid MaxComm envelope and header."""
    if not frame.startswith(PROTO_STX):
        return False
    expected_etx = PROTO_ETX if final else ")"
    if not frame.endswith(expected_etx):
        return False

    payload = _frame_payload(frame)
    header, separator, data = payload.partition(PROTO_FRS)
    if not separator or not data.endswith(PROTO_FRS):
        return False

    header_fields = header.split(PROTO_FS)
    if (
        len(header_fields) != 3
        or not all(_is_hex(field, length=2) for field in header_fields)
        or not _is_hex(_frame_crc(frame), length=_CRC_LEN)
    ):
        return False

    if not first:
        return True
    port, port_separator, _ = data.removesuffix(PROTO_FRS).partition(PROTO_US)
    return bool(port_separator and _is_hex(port))


def _validated_frames(data: str, verify_checksum: bool) -> list[str]:
    """Return complete frames or raise a retryable parse error."""
    frames = split_frames(data)
    if not frames:
        raise RetryableProtocolError("No valid MaxComm frames found in response")
    if not all(
        _has_valid_frame_structure(
            frame, first=index == 0, final=index == len(frames) - 1
        )
        for index, frame in enumerate(frames)
    ):
        raise RetryableProtocolError("Invalid MaxComm frame structure")
    if verify_checksum and not all(verify_frame_checksum(frame) for frame in frames):
        raise RetryableProtocolError(
            "MaxComm response checksum verification failed: data may be corrupted"
        )
    return frames


def _raise_for_interface_error(frame: str) -> None:
    """Raise for deterministic errors returned on the interface-message port."""
    port_markers = (
        f"{PROTO_FRS}{PROTO_PORT_MESSAGE:X}{PROTO_US}",
        f"{PROTO_FRS}{PROTO_PORT_MESSAGE:04X}{PROTO_US}",
    )
    if not any(marker in frame for marker in port_markers):
        return

    error_data = frame.split(PROTO_US)[1].split(PROTO_FRS)[0]
    if PROTO_ERROR_INVALID_PROTOCOL in error_data:
        raise ProtocolError(
            "Inverter reported invalid protocol (IPR): "
            "checksum or length error in our request"
        )
    if PROTO_ERROR_INVALID_PORT in error_data:
        raise ProtocolError("Inverter reported invalid port number (IPN)")


def _parse_field(item: str) -> tuple[str, dict[str, float | int]] | None:
    """Parse one response field, skipping unavailable or malformed values."""
    if "=" not in item:
        if item.strip():
            _LOGGER.debug("MaxComm: key '%s' returned as not applicable", item)
        return None

    field, value_str = item.split("=", 1)
    raw_value = value_str.split(",", 1)[0] if field == "SYS" else value_str
    try:
        value = int(raw_value, 16)
    except ValueError:
        _LOGGER.debug(
            "MaxComm: field '%s' has a malformed value %r; skipping",
            field,
            value_str,
        )
        return None

    return field, {"value": scale_value(field, value), "raw_value": value}


def parse_response(
    data: str, verify_checksum: bool = True
) -> dict[str, dict[str, float | int]]:
    """Parse a MaxComm protocol response into a dictionary.

    Response format per MaxComm spec Section 2.1:
        {<Src>;<Dest>;<Len>|<Port>:<Key1>=<Val1>;<Key2>=<Val2>|<CRC>}

    Handles multi-frame responses where the inverter splits large responses
    into multiple frames (each max 255 bytes).

    Error cases handled per Section 1.6:
    - Key without '=' → "not applicable" (key known but currently unavailable)
    - Empty data section → "not supported" (unknown key)
    - Port 3E8 responses → interface error messages (IPR, IPN)

    Raises RetryableProtocolError on missing/corrupted frames (may succeed on
    retry), ProtocolError on deterministic inverter-reported errors (IPR/IPN).
    """
    try:
        frames = _validated_frames(data, verify_checksum)
        _raise_for_interface_error(frames[0])
        result_dict: dict[str, dict[str, float | int]] = {}
        for item in _extract_data_from_frames(frames).split(PROTO_FS):
            if parsed := _parse_field(item):
                field, reading = parsed
                result_dict[field] = reading

        _LOGGER.debug("Parsed %d values from inverter response", len(result_dict))
        return result_dict

    except ProtocolError:
        raise
    except Exception as e:
        raise RetryableProtocolError(
            f"Unexpected error parsing MaxComm response: {e}"
        ) from e
