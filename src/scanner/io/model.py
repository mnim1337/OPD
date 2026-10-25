"""Typed representation of the four-file exchange, independent of the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


HEADERS: dict[str, tuple[str, ...]] = {
    "packets.csv": (
        "experiment_id", "flow_id", "packet_id", "tx_time_s", "rx_time_s",
        "payload_bytes", "rx_copy_count", "observed_until_s", "observation_status",
        "drop_reason",
    ),
    "routes.csv": (
        "experiment_id", "flow_id", "route_set_id", "path_id", "time_start_s",
        "time_end_s", "path_links", "route_state", "selection_share",
        "cause_event_id",
    ),
    "channels.csv": (
        "experiment_id", "link_id", "time_start_s", "time_end_s", "link_up",
        "capacity_bps", "propagation_delay_s", "routing_cost", "tx_data_bytes",
        "tx_routing_bytes", "tx_other_bytes", "observation_status",
        "queue_mean_bytes", "queue_max_bytes", "drop_data_packets",
    ),
}
FILES = ("experiment.json", *HEADERS)
NUMERIC: dict[str, dict[str, str]] = {
    "packets.csv": {
        "tx_time_s": "decimal", "rx_time_s": "decimal", "payload_bytes": "integer",
        "rx_copy_count": "integer", "observed_until_s": "decimal",
    },
    "routes.csv": {
        "time_start_s": "decimal", "time_end_s": "decimal",
        "selection_share": "decimal", "path_links": "path",
    },
    "channels.csv": {
        "time_start_s": "decimal", "time_end_s": "decimal", "link_up": "boolean",
        "capacity_bps": "decimal", "propagation_delay_s": "decimal",
        "routing_cost": "decimal", "tx_data_bytes": "integer",
        "tx_routing_bytes": "integer", "tx_other_bytes": "integer",
        "queue_mean_bytes": "decimal", "queue_max_bytes": "decimal",
        "drop_data_packets": "integer",
    },
}
REQUIRED_NUMERIC = {
    "packets.csv": {"tx_time_s", "payload_bytes", "rx_copy_count", "observed_until_s"},
    "routes.csv": {"time_start_s", "time_end_s", "path_links"},
    "channels.csv": {
        "time_start_s", "time_end_s", "link_up", "capacity_bps",
        "propagation_delay_s",
    },
}


def file_contract() -> dict[str, Any]:
    """Fixed transport rules from appendix A, version 1.0.0."""
    return {
        "encoding": "UTF-8", "bom": False, "csv_delimiter": ",",
        "decimal_separator": ".", "csv_quotechar": '"', "csv_line_ending": "CRLF",
        "json_line_ending": "LF", "csv_null": "empty_field",
        "csv_boolean_values": ["0", "1"], "csv_header_order_is_fixed": True,
        "json_key_order_is_significant": False, "csv_no_comment_rows": True,
        "csv_no_summary_rows": True,
        "path_links_encoding": "JSON array of link_id strings inside a CSV cell",
        "identifier_pattern": "^[A-Za-z][A-Za-z0-9_]*$",
        "observation_status_values": ["complete", "partial", "unknown"],
        "route_state_values": ["installed", "no_route", "loop", "unknown"],
        "table_headers": {name: list(header) for name, header in HEADERS.items()},
    }


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    location: str
    field: str
    message: str
    remedy: str

    def __str__(self) -> str:
        return f"{self.severity} {self.code} {self.location} {self.field}: {self.message}. {self.remedy}"


@dataclass
class Experiment:
    passport: dict[str, Any]
    tables: dict[str, list[dict[str, Any]]]
    original_files: dict[str, bytes] = field(default_factory=dict)
    source: str = "manual"

    @property
    def experiment_id(self) -> str:
        return str(self.passport.get("experiment_id", ""))

    def table_strings(self) -> dict[str, list[dict[str, str]]]:
        """Return complete editable rows, preserving a real zero versus an empty cell."""
        result: dict[str, list[dict[str, str]]] = {}
        for name, rows in self.tables.items():
            result[name] = []
            for row in rows:
                cells = {}
                for key in HEADERS[name]:
                    value = row.get(key)
                    if value is None:
                        cells[key] = ""
                    elif isinstance(value, bool):
                        cells[key] = "1" if value else "0"
                    elif isinstance(value, list):
                        import json
                        cells[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    elif isinstance(value, Decimal):
                        cells[key] = format(value, "f")
                    else:
                        cells[key] = str(value)
                result[name].append(cells)
        return result
