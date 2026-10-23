"""Shared semantic checks for imported files and manually entered data."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from .model import Diagnostic, Experiment, HEADERS

IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
STATES = {"complete", "partial", "unknown"}
ROUTE_STATES = {"installed", "no_route", "loop", "unknown"}
D = Decimal


class Checks:
    def __init__(self) -> None:
        self.items: list[Diagnostic] = []

    def check(self, condition: bool, code: str, place: str, field: str, message: str, remedy: str = "Исправьте значение") -> bool:
        if not condition:
            self.items.append(Diagnostic("error", code, place, field, message, remedy))
        return bool(condition)

    def warn(self, code: str, place: str, field: str, message: str) -> None:
        self.items.append(Diagnostic("warning", code, place, field, message, "Уточните полноту наблюдения"))

    def required(self, obj: Any, keys: tuple[str, ...], place: str) -> dict:
        if not self.check(isinstance(obj, dict), "TYPE_OBJECT", place, "", "ожидается объект"):
            return {}
        for key in keys:
            self.check(key in obj, "FIELD_REQUIRED", place, key, "обязательное поле отсутствует", "Добавьте поле")
        return obj

    def number(self, value: Any, place: str, field: str, minimum: Decimal | None = None, strict: bool = False) -> Decimal | None:
        try:
            if isinstance(value, bool) or value is None:
                raise InvalidOperation
            result = D(str(value))
            if not result.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            self.check(False, "TYPE_NUMBER", place, field, "ожидается конечное число")
            return None
        if minimum is not None:
            self.check(result > minimum if strict else result >= minimum, "RANGE_NUMBER", place, field, "число вне допустимого диапазона")
        return result

    def integer(self, value: Any, place: str, field: str, minimum: int = 0) -> int | None:
        if not isinstance(value, int) or isinstance(value, bool):
            self.check(False, "TYPE_INT", place, field, "ожидается целое число")
            return None
        self.check(value >= minimum, "RANGE_INT", place, field, "целое число вне допустимого диапазона")
        return value

    def ident(self, value: Any, place: str, field: str) -> bool:
        return self.check(isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None,
                          "TYPE_ID", place, field, "идентификатор не соответствует схеме")


def _coverage(c: Checks, rows: list[tuple[Decimal, Decimal, str]], start: Decimal, end: Decimal, place: str) -> None:
    expected = start
    for a, b, row_place in sorted(rows):
        c.check(a == expected, "TIME_COVERAGE", row_place, "time_start_s", "пропуск или перекрытие интервалов")
        expected = b
    c.check(expected == end, "TIME_COVERAGE", place, "time_end_s", "история не покрывает весь интервал регистрации")


def validate_experiment(experiment: Experiment, *, originals: dict[str, bytes] | None = None, complete: bool = True) -> list[Diagnostic]:
    c = Checks()
    m = c.required(experiment.passport, (
        "format_name", "schema_version", "data_origin", "description_ru", "experiment_id",
        "scenario_id", "repeat_id", "algorithm", "environment", "network", "traffic",
        "scenario", "measurement", "file_contract", "export_status", "extensions",
    ), "experiment.json")
    c.check(m.get("format_name") == "PAK_ROUTING_EXPORT_4F" and m.get("schema_version") == "1.0.0",
            "FORMAT_VERSION", "experiment.json", "schema_version", "неподдерживаемое имя или версия формата", "Используйте 1.0.0")
    c.check(m.get("data_origin") in {"synthetic_reference", "pak_export", "external_simulator_export", "manual_prepared"},
            "TYPE_ORIGIN", "experiment.json", "data_origin", "неизвестное происхождение")
    c.ident(m.get("experiment_id"), "experiment.json", "experiment_id")
    c.ident(m.get("scenario_id"), "experiment.json", "scenario_id")
    c.integer(m.get("repeat_id"), "experiment.json", "repeat_id", 1)

    algorithm = c.required(m.get("algorithm"), ("algorithm_id", "implementation_version", "routing_mode", "settings", "settings_completeness"), "algorithm")
    c.ident(algorithm.get("algorithm_id"), "algorithm", "algorithm_id")
    c.check(algorithm.get("routing_mode") in {"single_path_per_flow", "multipath_per_flow"},
            "TYPE_MODE", "algorithm", "routing_mode", "неизвестный режим")
    c.check(isinstance(algorithm.get("settings"), dict), "TYPE_OBJECT", "algorithm", "settings", "ожидается объект")
    environment = c.required(m.get("environment"), (
        "execution_source", "pak_version", "time_mode", "hardware_description",
        "queue_discipline", "buffer_limit_bytes", "link_errors", "randomness",
    ), "environment")
    random = c.required(environment.get("randomness"), ("used", "seeds"), "environment.randomness")
    c.check(isinstance(random.get("used"), bool), "TYPE_BOOL", "environment.randomness", "used", "ожидается JSON boolean")
    c.check(isinstance(random.get("seeds"), dict), "TYPE_OBJECT", "environment.randomness", "seeds", "ожидается объект")
    if m.get("data_origin") == "pak_export":
        c.check(bool(environment.get("pak_version")), "FIELD_REQUIRED", "environment", "pak_version", "для ПАК нужна фактическая версия")

    meas = c.required(m.get("measurement"), (
        "capture_start_s", "capture_end_s", "evaluation_start_s", "evaluation_end_s",
        "loss_timeout_s", "time_base", "timestamp_resolution_s", "clock_error_bound_s",
        "interval_convention", "packet_tx_point", "packet_rx_point", "route_observation_mode",
        "route_time_error_bound_s", "channel_nominal_interval_s", "channel_grid_rule",
        "channel_counter_mode", "channel_counter_point", "channel_byte_scope",
        "channel_categories_disjoint", "channel_max_atomic_packet_bytes", "balance", "stability", "adaptivity",
    ), "measurement")
    cs = c.number(meas.get("capture_start_s"), "measurement", "capture_start_s", D(0))
    ce = c.number(meas.get("capture_end_s"), "measurement", "capture_end_s", D(0))
    es = c.number(meas.get("evaluation_start_s"), "measurement", "evaluation_start_s", D(0))
    ee = c.number(meas.get("evaluation_end_s"), "measurement", "evaluation_end_s", D(0))
    timeout = c.number(meas.get("loss_timeout_s"), "measurement", "loss_timeout_s", D(0), True)
    if None not in (cs, ce, es, ee):
        c.check(cs <= es < ee <= ce, "TIME_WINDOW", "measurement", "evaluation_start_s", "окна измерения не согласованы")
    c.number(meas.get("timestamp_resolution_s"), "measurement", "timestamp_resolution_s", D(0), True)
    c.number(meas.get("clock_error_bound_s"), "measurement", "clock_error_bound_s", D(0))
    c.number(meas.get("route_time_error_bound_s"), "measurement", "route_time_error_bound_s", D(0))
    c.integer(meas.get("channel_max_atomic_packet_bytes"), "measurement", "channel_max_atomic_packet_bytes", 1)
    c.check(meas.get("interval_convention") == "[start,end)", "FORMAT_INTERVAL", "measurement", "interval_convention", "ожидается [start,end)")
    c.check(meas.get("channel_counter_mode") == "interval_delta", "FORMAT_COUNTER", "measurement", "channel_counter_mode", "ожидаются приращения")
    c.check(meas.get("channel_categories_disjoint") is True, "FORMAT_COUNTER", "measurement", "channel_categories_disjoint", "категории должны быть непересекающимися")

    fc = c.required(m.get("file_contract"), ("table_headers",), "file_contract")
    for key, value in {"encoding": "UTF-8", "bom": False, "csv_delimiter": ",", "decimal_separator": ".", "csv_quotechar": '"', "csv_line_ending": "CRLF", "csv_null": "empty_field"}.items():
        c.check(fc.get(key) == value, "FORMAT_CONTRACT", "file_contract", key, f"ожидается {value!r}")
    c.check(fc.get("table_headers") == {name: list(header) for name, header in HEADERS.items()},
            "FORMAT_HEADER", "file_contract", "table_headers", "заголовки не соответствуют схеме")

    network = c.required(m.get("network"), ("network_id", "directed", "nodes", "links"), "network")
    c.check(network.get("directed") is True, "TYPE_BOOL", "network", "directed", "сеть должна быть направленной")
    nodes: dict[str, dict] = {}
    for index, node in enumerate(network.get("nodes", []) if isinstance(network.get("nodes"), list) else [], 1):
        place = f"network.nodes[{index}]"
        obj = c.required(node, ("node_id", "node_type", "plane_id", "initial_up"), place)
        ident = obj.get("node_id")
        if c.ident(ident, place, "node_id"):
            c.check(ident not in nodes, "REFERENCE_DUP", place, "node_id", "повтор узла")
            nodes[ident] = obj
        c.check(isinstance(obj.get("initial_up"), bool), "TYPE_BOOL", place, "initial_up", "ожидается JSON boolean")
    links: dict[str, dict] = {}
    for index, link in enumerate(network.get("links", []) if isinstance(network.get("links"), list) else [], 1):
        place = f"network.links[{index}]"
        obj = c.required(link, (
            "link_id", "node_from", "node_to", "interface_from", "interface_to", "link_type",
            "initial_up", "initial_capacity_bps", "initial_propagation_delay_s",
            "initial_routing_cost", "resource_allocation",
        ), place)
        ident = obj.get("link_id")
        if c.ident(ident, place, "link_id"):
            c.check(ident not in links, "REFERENCE_DUP", place, "link_id", "повтор канала")
            links[ident] = obj
        c.check(isinstance(obj.get("node_from"), str) and isinstance(obj.get("node_to"), str)
                and obj.get("node_from") in nodes and obj.get("node_to") in nodes and obj.get("node_from") != obj.get("node_to"),
                "REFERENCE_NODE", place, "node_from", "неверные концы канала")
        c.check(isinstance(obj.get("initial_up"), bool), "TYPE_BOOL", place, "initial_up", "ожидается JSON boolean")
        c.number(obj.get("initial_capacity_bps"), place, "initial_capacity_bps", D(0))
        c.number(obj.get("initial_propagation_delay_s"), place, "initial_propagation_delay_s", D(0))
        c.number(obj.get("initial_routing_cost"), place, "initial_routing_cost")
    traffic = c.required(m.get("traffic"), ("flows",), "traffic")
    flows: dict[str, dict] = {}
    for index, flow in enumerate(traffic.get("flows", []) if isinstance(traffic.get("flows"), list) else [], 1):
        place = f"traffic.flows[{index}]"
        obj = c.required(flow, ("flow_id", "source_node", "destination_node", "transport", "packetization", "generation"), place)
        ident = obj.get("flow_id")
        if c.ident(ident, place, "flow_id"):
            c.check(ident not in flows, "REFERENCE_DUP", place, "flow_id", "повтор потока")
            flows[ident] = obj
        c.check(isinstance(obj.get("source_node"), str) and isinstance(obj.get("destination_node"), str)
                and obj.get("source_node") in nodes and obj.get("destination_node") in nodes,
                "REFERENCE_NODE", place, "source_node", "узел потока отсутствует")
    c.check(bool(nodes) and bool(links) and bool(flows), "FIELD_REQUIRED", "network", "", "нужны узлы, каналы и потоки")

    scenario = c.required(m.get("scenario"), ("events",), "scenario")
    events: dict[str, dict] = {}
    for index, event in enumerate(scenario.get("events", []) if isinstance(scenario.get("events"), list) else [], 1):
        place = f"scenario.events[{index}]"
        obj = c.required(event, ("event_id", "event_type", "object_type", "object_ids", "scheduled_time_s", "actual_time_s", "result", "changes"), place)
        ident = obj.get("event_id")
        if c.ident(ident, place, "event_id"):
            c.check(ident not in events, "REFERENCE_DUP", place, "event_id", "повтор события")
            events[ident] = obj
        c.number(obj.get("scheduled_time_s"), place, "scheduled_time_s", cs)
        actual = obj.get("actual_time_s")
        if obj.get("result") == "executed":
            actual_num = c.number(actual, place, "actual_time_s", cs)
            if actual_num is not None and ce is not None:
                c.check(actual_num < ce, "TIME_EVENT", place, "actual_time_s", "событие вне регистрации")
        kind = obj.get("object_type")
        refs = {"directed_links": links, "nodes": nodes, "flows": flows}.get(kind)
        c.check(refs is not None, "TYPE_EVENT", place, "object_type", "неизвестный тип объекта")
        if refs is not None:
            c.check(isinstance(obj.get("object_ids"), list) and all(isinstance(x, str) and x in refs for x in obj["object_ids"]),
                    "REFERENCE_EVENT", place, "object_ids", "неизвестный объект события")
        c.check(isinstance(obj.get("changes"), dict), "TYPE_OBJECT", place, "changes", "ожидается объект изменений")

    status = c.required(m.get("export_status"), ("complete", "data_logging_complete", "lost_log_records", "logging_gaps", "tables"), "export_status")
    c.check(isinstance(status.get("data_logging_complete"), bool), "TYPE_BOOL", "export_status", "data_logging_complete", "ожидается JSON boolean")
    c.integer(status.get("lost_log_records"), "export_status", "lost_log_records")
    if not status.get("data_logging_complete") or status.get("lost_log_records") or status.get("logging_gaps"):
        c.warn("INCOMPLETE_LOG", "export_status", "", "регистрация отмечена как неполная")
    if complete:
        c.check(status.get("complete") is True, "INCOMPLETE_EXPORT", "export_status", "complete", "выгрузка не завершена")
    for name in HEADERS:
        rows = experiment.tables.get(name)
        if not c.check(isinstance(rows, list), "TYPE_TABLE", name, "", "таблица отсутствует"):
            continue
        if complete and originals is not None:
            stored_tables = status.get("tables") if isinstance(status.get("tables"), dict) else {}
            stats = c.required(stored_tables.get(name), ("row_count", "size_bytes", "sha256"), f"export_status.tables.{name}")
            raw = originals.get(name, b"")
            c.check(stats.get("row_count") == len(rows), "FORMAT_COUNT", name, "row_count", "число строк не совпадает с паспортом")
            c.check(stats.get("size_bytes") == len(raw), "FORMAT_SIZE", name, "size_bytes", "размер не совпадает с паспортом")
            c.check(stats.get("sha256") == hashlib.sha256(raw).hexdigest(), "FORMAT_HASH", name, "sha256", "контрольная сумма не совпадает")
        for index, row in enumerate(rows, 2):
            c.check(row.get("experiment_id") == m.get("experiment_id"), "REFERENCE_EXPERIMENT", f"{name}:{index}", "experiment_id", "чужой опыт")

    # Later checks dereference validated passport objects and time values.
    if any(item.severity == "error" for item in c.items):
        return c.items
    if None in (cs, ce, es, ee):
        return c.items
    packets = experiment.tables.get("packets.csv", [])
    seen_packets = set()
    for index, row in enumerate(packets, 2):
        place = f"packets.csv:{index}"
        ident = row.get("packet_id")
        c.ident(ident, place, "packet_id")
        c.check(ident not in seen_packets, "REFERENCE_DUP", place, "packet_id", "повтор пакета")
        seen_packets.add(ident)
        c.check(row.get("flow_id") in flows, "REFERENCE_FLOW", place, "flow_id", "поток отсутствует")
        tx = c.number(row.get("tx_time_s"), place, "tx_time_s", cs)
        obs = c.number(row.get("observed_until_s"), place, "observed_until_s")
        c.integer(row.get("payload_bytes"), place, "payload_bytes", 1)
        copies = c.integer(row.get("rx_copy_count"), place, "rx_copy_count")
        c.check(row.get("observation_status") in STATES, "TYPE_STATUS", place, "observation_status", "неизвестный статус")
        if tx is not None and obs is not None:
            c.check(tx < ce and tx <= obs <= ce, "TIME_PACKET", place, "observed_until_s", "неверное окно наблюдения")
        if row.get("rx_time_s") is not None:
            rx = c.number(row["rx_time_s"], place, "rx_time_s")
            if None not in (tx, rx, obs):
                c.check(tx <= rx <= obs and rx < ce, "TIME_PACKET", place, "rx_time_s", "приём вне окна")
            c.check(copies is not None and copies >= 1, "RANGE_COPIES", place, "rx_copy_count", "при приёме нужна хотя бы одна копия")
            c.check(row.get("drop_reason") == "", "TYPE_DROP", place, "drop_reason", "полученный пакет не может иметь причину окончательной потери")
        else:
            c.check(copies == 0, "RANGE_COPIES", place, "rx_copy_count", "без приёма число копий должно быть 0")
            if None not in (tx, obs, timeout) and obs < tx + timeout:
                c.warn("INCOMPLETE_PACKET", place, "observed_until_s", "ожидание доставки не завершено")
        if row.get("observation_status") != "complete":
            c.warn("INCOMPLETE_PACKET", place, "observation_status", "наблюдение пакета неполное")

    by_link: dict[str, list[tuple[Decimal, Decimal, str]]] = defaultdict(list)
    grid = None
    for index, row in enumerate(experiment.tables.get("channels.csv", []), 2):
        place = f"channels.csv:{index}"
        lid = row.get("link_id")
        c.check(lid in links, "REFERENCE_LINK", place, "link_id", "канал отсутствует")
        a = c.number(row.get("time_start_s"), place, "time_start_s", cs)
        b = c.number(row.get("time_end_s"), place, "time_end_s")
        if None not in (a, b):
            c.check(a < b <= ce, "TIME_CHANNEL", place, "time_end_s", "неверный интервал")
            by_link[lid].append((a, b, place))
        up = row.get("link_up")
        c.check(isinstance(up, bool), "TYPE_BOOL", place, "link_up", "ожидается 0 или 1")
        cap = c.number(row.get("capacity_bps"), place, "capacity_bps", D(0))
        c.number(row.get("propagation_delay_s"), place, "propagation_delay_s", D(0))
        if row.get("routing_cost") is not None:
            c.number(row["routing_cost"], place, "routing_cost")
        c.check(row.get("observation_status") in STATES, "TYPE_STATUS", place, "observation_status", "неизвестный статус")
        amounts = []
        for key in ("tx_data_bytes", "tx_routing_bytes", "tx_other_bytes"):
            value = row.get(key)
            if value is None:
                c.check(row.get("observation_status") != "complete", "FIELD_REQUIRED", place, key, "при complete нужен счётчик")
            else:
                c.integer(value, place, key)
                amounts.append(value)
        for key in ("queue_mean_bytes", "queue_max_bytes"):
            if row.get(key) is not None:
                c.number(row[key], place, key, D(0))
        if row.get("drop_data_packets") is not None:
            c.integer(row["drop_data_packets"], place, "drop_data_packets")
        if row.get("queue_mean_bytes") is not None and row.get("queue_max_bytes") is not None:
            c.check(row["queue_mean_bytes"] <= row["queue_max_bytes"], "RANGE_QUEUE", place, "queue_mean_bytes", "средняя очередь больше максимальной")
        if up is False:
            c.check(cap == 0 and sum(amounts) == 0, "STATE_CHANNEL", place, "link_up", "отключённый канал имеет ёмкость или передачу")
        elif up is True:
            c.check(cap is not None and cap > 0, "STATE_CHANNEL", place, "capacity_bps", "доступному каналу нужна положительная ёмкость")
        atom = meas.get("channel_max_atomic_packet_bytes")
        if None not in (a, b, cap) and isinstance(atom, int):
            c.check(D(8 * sum(amounts)) <= cap * (b - a) + D(8 * atom), "RANGE_CAPACITY", place, "tx_data_bytes", "байты превышают ресурс интервала")
        if lid in links and a is not None:
            expected_up = links[lid].get("initial_up")
            expected_cap = c.number(links[lid].get("initial_capacity_bps"), place, "initial_capacity_bps")
            relevant = [ev for ev in events.values() if ev.get("result") == "executed" and ev.get("object_type") == "directed_links" and lid in ev.get("object_ids", []) and ev.get("actual_time_s") is not None]
            for ev in sorted(relevant, key=lambda x: D(str(x["actual_time_s"]))):
                t = D(str(ev["actual_time_s"]))
                if b is not None:
                    c.check(not (a < t < b), "TIME_EVENT", place, "time_start_s", "интервал не разделён в момент изменения")
                if t <= a:
                    expected_up = ev.get("changes", {}).get("link_up", expected_up)
                    expected_cap = D(str(ev.get("changes", {}).get("capacity_bps", expected_cap)))
            c.check(up == expected_up and cap == expected_cap, "STATE_CHANNEL", place, "link_up", "состояние не соответствует паспорту и событиям")
    for lid in links:
        rows = by_link.get(lid, [])
        _coverage(c, rows, cs, ce, f"channels.csv:{lid}")
        pairs = [(a, b) for a, b, _ in sorted(rows)]
        if grid is None:
            grid = pairs
        else:
            c.check(pairs == grid, "TIME_GRID", f"channels.csv:{lid}", "", "границы каналов не совпадают")
    if grid is not None:
        edges = {a for a, _ in grid} | {b for _, b in grid}
        c.check(es in edges and ee in edges, "TIME_GRID", "measurement", "evaluation_start_s", "границы окна оценки отсутствуют в сетке")
    balance = c.required(meas.get("balance"), ("selected_link_ids",), "measurement.balance")
    selected = balance.get("selected_link_ids")
    c.check(isinstance(selected, list) and bool(selected) and all(isinstance(x, str) for x in selected)
            and len(set(selected)) == len(selected) and set(selected) <= set(links),
            "REFERENCE_BALANCE", "measurement.balance", "selected_link_ids", "неверный набор каналов")

    by_flow: dict[str, list[tuple[Decimal, Decimal, str]]] = defaultdict(list)
    groups: dict[str, dict[tuple[Decimal, Decimal, str], list[dict]]] = defaultdict(lambda: defaultdict(list))
    path_ids: dict[tuple[str, str], tuple[str, ...]] = {}
    for index, row in enumerate(experiment.tables.get("routes.csv", []), 2):
        place = f"routes.csv:{index}"
        fid = row.get("flow_id")
        c.check(fid in flows, "REFERENCE_FLOW", place, "flow_id", "поток отсутствует")
        c.ident(row.get("route_set_id"), place, "route_set_id")
        a = c.number(row.get("time_start_s"), place, "time_start_s", cs)
        b = c.number(row.get("time_end_s"), place, "time_end_s")
        if None not in (a, b):
            c.check(a < b <= ce, "TIME_ROUTE", place, "time_end_s", "неверный интервал")
            by_flow[fid].append((a, b, place))
            groups[fid][(a, b, str(row.get("route_set_id")))].append(row)
        state = row.get("route_state")
        c.check(state in ROUTE_STATES, "TYPE_ROUTE", place, "route_state", "неизвестное состояние")
        path = row.get("path_links")
        c.check(isinstance(path, list) and all(lid in links for lid in path), "REFERENCE_PATH", place, "path_links", "неизвестный канал пути")
        share = row.get("selection_share")
        if share is not None:
            c.number(share, place, "selection_share", D(0))
            c.check(share <= 1, "RANGE_SHARE", place, "selection_share", "доля больше 1")
        if state in {"no_route", "unknown"}:
            c.check(path == [] and row.get("path_id") == "", "STATE_ROUTE", place, "path_id", "путь должен быть пуст")
            c.check(share == (D(0) if state == "no_route" else None), "STATE_ROUTE", place, "selection_share", "неверная доля")
        elif isinstance(path, list) and path and all(lid in links for lid in path):
            c.ident(row.get("path_id"), place, "path_id")
            vertices = [links[path[0]]["node_from"]]
            for lid in path:
                c.check(links[lid]["node_from"] == vertices[-1], "REFERENCE_PATH", place, "path_links", "цепочка каналов разорвана")
                vertices.append(links[lid]["node_to"])
            if fid in flows:
                c.check(vertices[0] == flows[fid].get("source_node"), "REFERENCE_PATH", place, "path_links", "путь начинается не в источнике")
                if state == "installed":
                    c.check(vertices[-1] == flows[fid].get("destination_node") and len(set(vertices)) == len(vertices),
                            "STATE_ROUTE", place, "path_links", "неверный конец или петля")
                elif state == "loop":
                    c.check(len(set(vertices)) < len(vertices), "STATE_ROUTE", place, "path_links", "нет петли")
            key = (fid, row.get("path_id"))
            value = tuple(path)
            c.check(key not in path_ids or path_ids[key] == value, "REFERENCE_PATH", place, "path_id", "ID обозначает разные пути")
            path_ids[key] = value
        else:
            c.check(False, "STATE_ROUTE", place, "path_links", "путь отсутствует")
        event_id = row.get("cause_event_id")
        if event_id:
            c.check(event_id in events, "REFERENCE_EVENT", place, "cause_event_id", "событие отсутствует")
            if event_id in events and a is not None:
                event = events[event_id]
                actual = event.get("actual_time_s")
                c.check(event.get("result") == "executed" and actual is not None and D(str(actual)) <= a,
                        "TIME_EVENT", place, "cause_event_id", "событие ещё не выполнено")
    for fid in flows:
        if algorithm.get("routing_mode") == "single_path_per_flow":
            _coverage(c, by_flow.get(fid, []), cs, ce, f"routes.csv:{fid}")
            for group in groups[fid].values():
                for row in group:
                    if row.get("route_state") in {"installed", "loop"}:
                        c.check(row.get("selection_share") == 1, "RANGE_SHARE", "routes.csv", "selection_share", "для одного пути доля должна быть 1")
        else:
            representatives = [(a, b, f"routes.csv:{fid}") for a, b, _ in groups[fid]]
            _coverage(c, representatives, cs, ce, f"routes.csv:{fid}")
            for group in groups[fid].values():
                if any(row.get("route_state") in {"no_route", "unknown"} for row in group):
                    c.check(len(group) == 1, "STATE_ROUTE", "routes.csv", "route_set_id", "отсутствие пути нельзя смешивать с путями")
                else:
                    shares = [row.get("selection_share") for row in group]
                    if all(share is not None for share in shares):
                        c.check(abs(sum(shares, D(0)) - 1) <= D("0.000000001"), "RANGE_SHARE", "routes.csv", "selection_share", "сумма долей не равна 1")
                    else:
                        c.warn("INCOMPLETE_SHARE", "routes.csv", "selection_share", "доли путей не полностью известны")

    stability = c.required(meas.get("stability"), ("checks",), "measurement.stability")
    for index, check in enumerate(stability.get("checks", []) if isinstance(stability.get("checks"), list) else [], 1):
        place = f"measurement.stability.checks[{index}]"
        if not isinstance(check, dict):
            c.check(False, "TYPE_OBJECT", place, "", "ожидается объект проверки")
            continue
        c.check(check.get("flow_id") in flows, "REFERENCE_FLOW", place, "flow_id", "поток отсутствует")
        start = c.number(check.get("time_start_s"), place, "time_start_s")
        horizon = c.number(check.get("horizon_s"), place, "horizon_s", D(0), True)
        if None not in (start, horizon):
            c.check(cs <= start < start + horizon <= ce, "TIME_CHECK", place, "horizon_s", "проверка вне регистрации")
    adaptivity = c.required(meas.get("adaptivity"), ("horizon_s", "confirmation_window_s", "checks"), "measurement.adaptivity")
    c.number(adaptivity.get("horizon_s"), "measurement.adaptivity", "horizon_s", D(0), True)
    c.number(adaptivity.get("confirmation_window_s"), "measurement.adaptivity", "confirmation_window_s", D(0), True)
    for index, check in enumerate(adaptivity.get("checks", []) if isinstance(adaptivity.get("checks"), list) else [], 1):
        place = f"measurement.adaptivity.checks[{index}]"
        if not isinstance(check, dict):
            c.check(False, "TYPE_OBJECT", place, "", "ожидается объект проверки")
            continue
        c.check(check.get("flow_id") in flows and check.get("event_id") in events,
                "REFERENCE_CHECK", place, "", "неверная ссылка проверки адаптивности")
    return c.items
