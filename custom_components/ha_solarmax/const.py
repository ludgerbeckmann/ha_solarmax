"""Constants for the Solarmax Inverter integration."""

from enum import StrEnum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.helpers.entity import EntityCategory

DOMAIN = "solarmax"

# Configuration constants
CONF_HOST = "host"
CONF_PORT = "port"
CONF_ADDRESS = "address"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_DEVICE_NAME = "device_name"
CONF_VERIFY_CHECKSUM = "verify_checksum"
CONF_TWILIGHT_ELEVATION_THRESHOLD = "twilight_elevation_threshold"
CONF_NIGHT_KEEP_VALUES = "night_keep_values"

# Default values
DEFAULT_PORT = 12345
DEFAULT_ADDRESS = 1
DEFAULT_UPDATE_INTERVAL = 30
DEFAULT_DEVICE_NAME = "Solarmax Inverter"
DEFAULT_VERIFY_CHECKSUM = True
# Sun elevation (in degrees) below which the inverter is considered to be in
# the dusk/dawn twilight window and expected to be offline due to
# insufficient irradiance, even though the sun is technically above the
# horizon.
DEFAULT_TWILIGHT_ELEVATION_THRESHOLD = 5

DEFAULT_NIGHT_KEEP_VALUES = False

# Coordinator poll cadence (seconds) when the engine reports OFFLINE_EXPECTED.
NIGHT_POLL_SECONDS = 900
DAWN_POLL_SECONDS = 60
# Maximum cadence during a daytime connection failure or startup grace.
FAULT_POLL_SECONDS = 60
# How long an OFFLINE_FAULT must persist before a repair issue is raised.
FAULT_REPAIR_SECONDS = 300
# A repair probe succeeded; only a complete ONLINE poll verifies recovery.
REPAIR_PENDING = "verification_pending"
REPAIR_PENDING_ENDPOINT = "verification_endpoint"

# Static device-identification MaxComm keys (queried once for device info)
DEVICE_KEY_TYPE = "TYP"  # device type / model identifier
DEVICE_KEY_FIRMWARE = "SWV"  # firmware version number
DEVICE_KEY_BUILD = "BDN"  # firmware build/release number
DEVICE_KEY_SERIAL = "DIN"  # inverter serial number

# Special sensor keys that need custom decoding (enum / bitmask)
SENSOR_TYPE_STATUS = "SYS"  # operating-status register
SENSOR_TYPE_ALARM = "SAL"  # alarm bitmask register

# =============================================================================
# Device Type Map (TYP key) - per MaxComm Protocol Section 2.3
# Maps the TYP register value (decimal) to the device model name.
# =============================================================================
DEVICE_TYPE_MAP: dict[int, str] = {
    # TS-SV MT series
    20812: "SolarMax 1440TS-SV MT",
    20809: "SolarMax 1080TS-SV MT",
    20806: "SolarMax 720TS-SV MT",
    20803: "SolarMax 360TS-SV MT",
    # TS-SV ST series
    20712: "SolarMax 1440TS-SV ST",
    20709: "SolarMax 1080TS-SV ST",
    20706: "SolarMax 720TS-SV ST",
    20703: "SolarMax 360TS-SV ST",
    20700: "SolarMax 360TS-SV",
    # TP series
    20653: "SolarMax 4TP",
    20652: "SolarMax 5TP2",
    20651: "SolarMax 6TP2",
    20650: "SolarMax 7TP2",
    # P series
    20640: "SolarMax 5000P",
    20635: "SolarMax 4600P",
    20630: "SolarMax 4000P",
    20620: "SolarMax 3000P",
    20610: "SolarMax 2000P",
    # TS-SV MT (1320 series)
    20512: "SolarMax 1320TS-SV MT",
    20509: "SolarMax 990TS-SV MT",
    20506: "SolarMax 660TS-SV MT",
    20503: "SolarMax 330TS-SV MT",
    # TS-SV ST (1320 series)
    20412: "SolarMax 1320TS-SV ST",
    20409: "SolarMax 990TS-SV ST",
    20406: "SolarMax 660TS-SV ST",
    20403: "SolarMax 330TS-SV ST",
    # TS series
    20318: "SolarMax 300TS MT",
    20316: "SolarMax 300TS ST",
    20314: "SolarMax 100TS",
    20312: "SolarMax 80TS",
    20310: "SolarMax 50TS",
    # HT series
    20266: "SolarMax 32HT2",
    20262: "SolarMax 32HT4",
    20260: "SolarMax 30HT4",
    20258: "SolarMax 25HT4",
    20257: "SolarMax 25HT2",
    20256: "SolarMax 20HT4",
    20255: "SolarMax 20HT2",
    # MT series A
    20254: "SolarMax 18MT3 A",
    20252: "SolarMax 15MT3 A",
    20250: "SolarMax 12MT2 A",
    # MT series SV
    20240: "SolarMax 18MT3 SV",
    # MT series
    20215: "SolarMax 8MT2",
    20213: "SolarMax 15MT2",
    20211: "SolarMax 13MT2",
    20210: "SolarMax 10MT2",
    20208: "SolarMax 15MT3",
    20206: "SolarMax 13MT3",
    20202: "SolarMax 10MT",
    # S series
    20110: "SolarMax 35S",
    20100: "SolarMax 20S",
    20040: "SolarMax 6000S",
    20030: "SolarMax 4200S",
    20020: "SolarMax 3000S",
    20010: "SolarMax 2000S",
    # SXT series
    12060: "SolarMax 110SXT",
    12055: "SolarMax 255SXT",
    12054: "SolarMax 250SXT",
    # SHT-S series
    11130: "SolarMax 60SHT-S",
    11125: "SolarMax 50SHT-S",
    11120: "SolarMax 60SHT-S2",
    11115: "SolarMax 50SHT-S2",
    # SHT series
    11110: "SolarMax 60SHT",
    11105: "SolarMax 50SHT",
    11100: "SolarMax 30SHT",
    11095: "SolarMax 28SHT",
    11090: "SolarMax 25SHT",
    11085: "SolarMax 22SHT",
    11080: "SolarMax 20SHT",
    11075: "SolarMax 17SHT",
    # SMT series
    11070: "SolarMax 15SMT",
    11065: "SolarMax 13SMT",
    11060: "SolarMax 10SMT",
    11055: "SolarMax 8SMT",
    11050: "SolarMax 6SMT",
    # SP series
    11045: "SolarMax 6000SP",
    11040: "SolarMax 5000SP",
    11035: "SolarMax 4600SP",
    11030: "SolarMax 4000SP",
    11025: "SolarMax 3600SP",
    11020: "SolarMax 3000SP",
    11015: "SolarMax 2500SP",
    11010: "SolarMax 2000SP",
    11005: "SolarMax 1500SP",
    11000: "SolarMax 1000SP",
    # MaxCount / MaxMeteo
    10300: "MaxCount",
    10210: "MaxMeteo plus2T",
    10200: "MaxMeteo",
    # C/E series (6000)
    6010: "SolarMax 6000C",
    6000: "SolarMax 6000E",
    # C/E series (4000)
    4200: "SolarMax 4200C",
    4010: "SolarMax 4000C",
    4001: "SolarMax 4000",
    4000: "SolarMax 4000E",
    # C/E series (3000)
    3010: "SolarMax 3000C",
    3001: "SolarMax 3000E",
    3000: "SolarMax 3000",
    # C/E series (2000)
    2010: "SolarMax 2000C",
    2001: "SolarMax 2000E",
    2000: "SolarMax 2000",
    # Smaller models
    330: "SolarMax 330C-SV",
    300: "SolarMax 300C",
    126: "SolarMax 125",
    101: "SolarMax 100",
    100: "SolarMax 100C",
    80: "SolarMax 80C",
    61: "SolarMax 60",
    50: "SolarMax 50C",
    46: "SolarMax 45",
    41: "SolarMax 40",
    35: "SolarMax 35C",
    31: "SolarMax 30",
    30: "SolarMax 30C",
    25: "SolarMax 25C",
    21: "SolarMax 20",
    20: "SolarMax 20C",
}

# Status code mappings (SYS) - maps raw integer code to option key
# Info from https://github.com/t-pa/solarmaxcom/blob/main/src/main/java/solarmaxcom/protocol/StatusLookup.java
SYS_STATUS_MAP: dict[int, str] = {
    20000: "no_communication",
    20001: "in_operation",
    20002: "low_irradiation",
    20003: "starting_up",
    20004: "mpp_operation",
    20005: "fan_running",
    20006: "max_power_operation",
    20007: "temperature_limitation",
    20008: "grid_operation",
    20009: "dc_current_limited",
    20010: "ac_current_limited",
    20011: "test_mode",
    20012: "remote_controlled",
    20013: "start_delay",
    20014: "external_limitation",
    20015: "frequency_limitation",
    20016: "restart_limitation",
    20017: "booting",
    20018: "insufficient_boot_power",
    20019: "insufficient_power",
    20021: "uninitialized",
    20022: "disabled",
    20023: "idle",
    20024: "powerunit_not_ready",
    20050: "program_firmware",
    20101: "device_error_101",
    20102: "device_error_102",
    20103: "device_error_103",
    20104: "device_error_104",
    20105: "insulation_fault_dc",
    20106: "insulation_fault_dc_2",
    20107: "device_error_107",
    20108: "device_error_108",
    20109: "vdc_too_high",
    20110: "device_error_110",
    20111: "device_error_111",
    20112: "device_error_112",
    20113: "device_error_113",
    20114: "leakage_current_high",
    20115: "no_grid",
    20116: "grid_frequency_high",
    20117: "grid_frequency_low",
    20118: "mains_error",
    20119: "vac_10min_too_high",
    20120: "device_error_120",
    20121: "device_error_121",
    20122: "grid_voltage_high",
    20123: "grid_voltage_low",
    20124: "temperature_too_high",
    20125: "grid_current_asymmetric",
    20126: "external_input_error_1",
    20127: "external_input_error_2",
    20128: "device_error_128",
    20129: "incorrect_rotation",
    20130: "wrong_device_type",
    20131: "main_switch_off",
    20132: "diode_overtemperature",
    20133: "device_error_133",
    20134: "fan_defective",
    20135: "device_error_135",
    20136: "device_error_136",
    20137: "device_error_137",
    20138: "device_error_138",
    20139: "device_error_139",
    20140: "device_error_140",
    20141: "device_error_141",
    20142: "device_error_142",
    20143: "device_error_143",
    20144: "device_error_144",
    20145: "dfdt_too_high",
    20146: "device_error_146",
    20147: "device_error_147",
    20148: "device_error_148",
    20150: "ierr_step_too_high",
    20151: "ierr_step_too_high_2",
    20153: "device_error_153",
    20154: "shutdown_1",
    20155: "shutdown_2",
    20156: "device_error_156",
    20157: "insulation_fault_dc_3",
    20158: "device_error_158",
    20159: "device_error_159",
    20160: "device_error_160",
    20161: "device_error_161",
    20163: "device_error_163",
    20164: "ierr_too_high_2",
    20165: "no_mains_2",
    20166: "frequency_too_high_2",
    20167: "frequency_too_low_2",
    20168: "mains_error_2",
    20169: "vac_10min_too_high_2",
    20170: "device_error_170",
    20171: "device_error_171",
    20172: "vac_too_high_2",
    20173: "vac_too_low_2",
    20174: "device_error_174",
    20175: "device_error_175",
    20176: "error_dc_polarity",
    20177: "device_error_177",
    20178: "device_error_178",
    20179: "device_error_179",
    20180: "vdc_too_low",
    20181: "blocked_external",
    20185: "device_error_185",
    20186: "device_error_186",
    20187: "device_error_187",
    20188: "device_error_188",
    20189: "l_n_interchanged",
    20190: "below_average_yield",
    20191: "limitation_error",
    20198: "device_error_198",
    20199: "device_error_199",
    20999: "device_error_999",
}

# Special SYS states (not from inverter data) — mirror EngineState, minus ONLINE.
SYS_STATE_OFFLINE_EXPECTED = "offline_expected"
SYS_STATE_OFFLINE_FAULT = "offline_fault"
SYS_STATE_UNKNOWN = "unknown"

# All possible SYS options for the enum sensor
SYS_OPTIONS: list[str] = list(SYS_STATUS_MAP.values()) + [
    SYS_STATE_OFFLINE_EXPECTED,
    SYS_STATE_OFFLINE_FAULT,
    SYS_STATE_UNKNOWN,
]

# Alarm code mappings (SAL) - bitmask values, can be combined
SAL_ALARM_MAP: dict[int, str] = {
    0: "no_error",
    1: "external_fault_1",
    2: "insulation_fault_dc",
    4: "earth_fault_current",
    8: "fuse_break_center_earth",
    16: "external_alarm_2",
    32: "long_term_temp_limit",
    64: "ac_feed_in_error",
    128: "external_alarm_4",
    256: "fan_defect",
    512: "fuse_break",
    1024: "temp_sensor_failure",
    2048: "alarm_12",
    4096: "alarm_13",
    8192: "alarm_14",
    16384: "alarm_15",
    32768: "alarm_16",
    65536: "alarm_17",
}

SAL_STATE_MULTIPLE = "multiple_alarms"
SAL_STATE_UNKNOWN = "unknown_alarm"

# All possible SAL options for the enum sensor
SAL_OPTIONS: list[str] = list(SAL_ALARM_MAP.values()) + [
    SAL_STATE_MULTIPLE,
    SAL_STATE_UNKNOWN,
]

# Sensor types and their properties
# Info from MaxComm protocol spec,
# https://github.com/t-pa/solarmaxcom/blob/main/src/main/java/solarmaxcom/protocol/Keys.java
# https://github.com/benchmarex/SOLARMAX_to_SQL/blob/master/main_solarmax_sql.py
# Sensor entity descriptions, keyed by MaxComm field (description.key).
# Convention: entities are enabled by default and have no entity category
# unless stated otherwise (entity_registry_enabled_default=False marks an
# opt-in/diagnostic sensor). Display names come from translations via
# translation_key + has_entity_name, so no `name` is set here.
SENSOR_TYPES: tuple[SensorEntityDescription, ...] = (
    # --- Main power/energy measurements (enabled by default) ---
    SensorEntityDescription(
        key="PAC",
        translation_key="pac",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="PDC",
        translation_key="pdc",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
    ),
    # --- Per-string DC power (diagnostic, opt-in) ---
    SensorEntityDescription(
        key="PD01",
        translation_key="pd01",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="PD02",
        translation_key="pd02",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="PD03",
        translation_key="pd03",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:solar-power",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # --- AC/DC voltages (diagnostic, opt-in) ---
    SensorEntityDescription(
        key="UL1",
        translation_key="ul1",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UL2",
        translation_key="ul2",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UL3",
        translation_key="ul3",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UDC",
        translation_key="udc",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UD01",
        translation_key="ud01",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UD02",
        translation_key="ud02",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="UD03",
        translation_key="ud03",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # --- AC/DC currents (diagnostic, opt-in) ---
    SensorEntityDescription(
        key="IL1",
        translation_key="il1",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="IL2",
        translation_key="il2",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="IL3",
        translation_key="il3",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="IDC",
        translation_key="idc",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="ID01",
        translation_key="id01",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="ID02",
        translation_key="id02",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="ID03",
        translation_key="id03",
        native_unit_of_measurement="A",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:current-dc",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # --- Energy counters ---
    SensorEntityDescription(
        key="KDY",
        translation_key="kdy",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="KMT",
        translation_key="kmt",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="KYR",
        translation_key="kyr",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    SensorEntityDescription(
        key="KT0",
        translation_key="kt0",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:solar-power",
    ),
    # Historical energy (opt-in; not provided by all inverters). These are
    # point-in-time totals for a past period, not a running meter, so they have
    # no state_class (state_class=measurement is invalid for energy in HA).
    SensorEntityDescription(
        key="KLD",
        translation_key="kld",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:solar-power",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="KLM",
        translation_key="klm",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:solar-power",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="KLY",
        translation_key="kly",
        native_unit_of_measurement="kWh",
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:solar-power",
        entity_registry_enabled_default=False,
    ),
    # --- Temperatures (diagnostic, opt-in) ---
    SensorEntityDescription(
        key="TKK",
        translation_key="tkk",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="TK2",
        translation_key="tk2",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="TK3",
        translation_key="tk3",
        native_unit_of_measurement="°C",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # --- Counters / misc diagnostics ---
    SensorEntityDescription(
        key="KHR",
        translation_key="khr",
        native_unit_of_measurement="h",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="CAC",
        translation_key="cac",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:restart",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="PRL",
        translation_key="prl",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:gauge",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="PIN",
        translation_key="pin",
        native_unit_of_measurement="W",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:information",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # --- Grid monitoring (diagnostic, opt-in) ---
    SensorEntityDescription(
        key="TNF",
        translation_key="tnf",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="ULH",
        translation_key="ulh",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="ULL",
        translation_key="ull",
        native_unit_of_measurement="V",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="TNH",
        translation_key="tnh",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="TNL",
        translation_key="tnl",
        native_unit_of_measurement="Hz",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sine-wave",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    # --- Status / alarm registers (enum, decoded at the sensor level) ---
    SensorEntityDescription(
        key="SAL",
        translation_key="sal",
        device_class=SensorDeviceClass.ENUM,
        options=SAL_OPTIONS,
        icon="mdi:alert-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="SYS",
        translation_key="sys",
        device_class=SensorDeviceClass.ENUM,
        options=SYS_OPTIONS,
        icon="mdi:information",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class NightPolicy(StrEnum):
    """What a sensor reports while the inverter is offline overnight.

    Only consulted when the user enables CONF_NIGHT_KEEP_VALUES; the default
    is UNAVAILABLE, which reproduces the integration's original behaviour.
    """

    UNAVAILABLE = "unavailable"
    ZERO = "zero"
    HOLD = "hold"
    HOLD_UNTIL_MIDNIGHT = "hold_until_midnight"


# Hand-assigned per key: device_class cannot derive this. UDC/UD01-03 are DC
# string voltages and really are ~0 V in darkness, while UL1-3 are AC grid
# voltages that stay near 230 V — same device_class, opposite correct answers.
# Keys absent from this table default to UNAVAILABLE.
NIGHT_POLICY: dict[str, NightPolicy] = {
    # No production at night: power, current, and DC-side voltage are truly 0.
    **dict.fromkeys(("PAC", "PDC", "PD01", "PD02", "PD03", "PRL"), NightPolicy.ZERO),
    **dict.fromkeys(
        ("IDC", "ID01", "ID02", "ID03", "IL1", "IL2", "IL3"), NightPolicy.ZERO
    ),
    **dict.fromkeys(("UDC", "UD01", "UD02", "UD03"), NightPolicy.ZERO),
    # Cumulative counters must never regress; zeroing one reads as a meter
    # reset to HA's statistics engine.
    **dict.fromkeys(("KMT", "KYR", "KT0", "KHR", "CAC"), NightPolicy.HOLD),
    # Historical totals and static configuration reads never change at night.
    **dict.fromkeys(("KLD", "KLM", "KLY"), NightPolicy.HOLD),
    **dict.fromkeys(("PIN", "ULH", "ULL", "TNH", "TNL"), NightPolicy.HOLD),
    # An alarm that was live at dusk stays visible through the night.
    "SAL": NightPolicy.HOLD,
    # The inverter resets its daily counter at midnight, so we do too.
    "KDY": NightPolicy.HOLD_UNTIL_MIDNIGHT,
}
