"""K0: fixed time windows, recorded routes and physical network state."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from scanner.io.model import Experiment

D = Decimal


def dec(value: Any) -> Decimal:
    return value if isinstance(value, D) else D(str(value))


@dataclass
class Context:
    experiment: Experiment
    capture_start: Decimal
    capture_end: Decimal
    start: Decimal
    end: Decimal
    timeout: Decimal
    nodes: dict[str, dict]
    links: dict[str, dict]
    flows: dict[str, dict]
    events: dict[str, dict]
    by_link: dict[str, list[dict]]
    by_flow: dict[str, list[dict]]
    grid: list[tuple[Decimal, Decimal]]
    selected_links: tuple[str, ...]
    exact_time: bool

    @property
    def duration(self) -> Decimal:
        return self.end - self.start

    def node_up(self, node_id: str, time: Decimal, *, before: bool = False) -> bool | None:
        node = self.nodes[node_id]
        up = node.get("initial_up")
        events = (event for event in self.events.values()
                  if event.get("result") == "executed" and event.get("object_type") == "nodes"
                  and node_id in event.get("object_ids", []))
        for event in sorted(events, key=lambda item: dec(item["actual_time_s"])):
            instant = dec(event["actual_time_s"])
            if instant < time or (instant == time and not before):
                changes = event.get("changes", {})
                up = changes.get("node_up", None)
        return up if isinstance(up, bool) else None

    def channel_at(self, link_id: str, time: Decimal, *, before: bool = False) -> dict | None:
        for row in self.by_link.get(link_id, []):
            left, right = row["time_start_s"], row["time_end_s"]
            if (left < time <= right) if before else (left <= time < right):
                return row
        return None

    def link_up(self, link_id: str, time: Decimal, *, before: bool = False) -> bool | None:
        row = self.channel_at(link_id, time, before=before)
        if row is None or row["observation_status"] != "complete":
            return None
        link = self.links[link_id]
        states = (row["link_up"], self.node_up(link["node_from"], time, before=before),
                  self.node_up(link["node_to"], time, before=before))
        if False in states:
            return False
        if None in states:
            return None
        return True

    def _state_boundaries(self, start: Decimal, end: Decimal) -> list[Decimal]:
        boundaries = {start, end}
        for left, right in self.grid:
            if start < left < end:
                boundaries.add(left)
            if start < right < end:
                boundaries.add(right)
        for event in self.events.values():
            if event.get("result") == "executed" and event.get("object_type") == "nodes":
                instant = dec(event["actual_time_s"])
                if start < instant < end:
                    boundaries.add(instant)
        return sorted(boundaries)

    def physical_at(self, path: list[str], time: Decimal, *, before: bool = False) -> bool | None:
        if not path:
            return False
        states = [self.link_up(link_id, time, before=before) for link_id in path]
        if False in states:
            return False
        return None if None in states else True

    def physical(self, path: list[str], start: Decimal, end: Decimal) -> bool | None:
        if not path:
            return False
        if start < self.capture_start or end > self.capture_end or start >= end:
            return None
        unknown = False
        for instant in self._state_boundaries(start, end)[:-1]:
            state = self.physical_at(path, instant)
            if state is False:
                return False
            if state is None:
                unknown = True
        return None if unknown else True

    def connected_at(self, flow_id: str, time: Decimal) -> bool | None:
        """True if definitely connected, False if impossible, None if unknown."""
        flow = self.flows[flow_id]
        definite: dict[str, list[str]] = defaultdict(list)
        possible: dict[str, list[str]] = defaultdict(list)
        for link_id, link in self.links.items():
            state = self.link_up(link_id, time)
            if state is True:
                definite[link["node_from"]].append(link["node_to"])
            if state is not False:
                possible[link["node_from"]].append(link["node_to"])

        def reachable(graph: dict[str, list[str]]) -> bool:
            seen = {flow["source_node"]}
            stack = list(seen)
            while stack:
                for target in graph[stack.pop()]:
                    if target not in seen:
                        seen.add(target)
                        stack.append(target)
            return flow["destination_node"] in seen

        if reachable(definite):
            return True
        return None if reachable(possible) else False

    def connected(self, flow_id: str, start: Decimal, end: Decimal) -> bool | None:
        if start < self.capture_start or end > self.capture_end or start >= end:
            return None
        unknown = False
        for instant in self._state_boundaries(start, end)[:-1]:
            state = self.connected_at(flow_id, instant)
            if state is False:
                return False
            if state is None:
                unknown = True
        return None if unknown else True

    def route_at(self, flow_id: str, time: Decimal, *, before: bool = False) -> dict | None:
        for row in self.by_flow.get(flow_id, []):
            left, right = row["time_start_s"], row["time_end_s"]
            if (left < time <= right) if before else (left <= time < right):
                return row
        return None

    def same_installed_path(self, flow_id: str, path: list[str], start: Decimal, end: Decimal) -> bool | None:
        unknown = False
        covered = start
        for row in self.by_flow.get(flow_id, []):
            if row["time_end_s"] <= start or row["time_start_s"] >= end:
                continue
            if row["time_start_s"] > covered:
                unknown = True
            covered = max(covered, row["time_end_s"])
            if row["route_state"] == "unknown":
                unknown = True
            elif row["route_state"] != "installed" or row["path_links"] != path:
                return False
        if covered < end:
            unknown = True
        return None if unknown else True


def prepare(experiment: Experiment) -> Context:
    m = experiment.passport
    meas = m["measurement"]
    by_link: dict[str, list[dict]] = defaultdict(list)
    by_flow: dict[str, list[dict]] = defaultdict(list)
    for row in experiment.tables["channels.csv"]:
        by_link[row["link_id"]].append(row)
    for row in experiment.tables["routes.csv"]:
        by_flow[row["flow_id"]].append(row)
    for rows in (*by_link.values(), *by_flow.values()):
        rows.sort(key=lambda row: row["time_start_s"])
    first_link = next(iter(by_link.values()), [])
    grid = [(row["time_start_s"], row["time_end_s"]) for row in first_link]
    return Context(
        experiment=experiment,
        capture_start=dec(meas["capture_start_s"]), capture_end=dec(meas["capture_end_s"]),
        start=dec(meas["evaluation_start_s"]), end=dec(meas["evaluation_end_s"]),
        timeout=dec(meas["loss_timeout_s"]),
        nodes={row["node_id"]: row for row in m["network"]["nodes"]},
        links={row["link_id"]: row for row in m["network"]["links"]},
        flows={row["flow_id"]: row for row in m["traffic"]["flows"]},
        events={row["event_id"]: row for row in m["scenario"]["events"]},
        by_link=dict(by_link), by_flow=dict(by_flow), grid=grid,
        selected_links=tuple(meas["balance"]["selected_link_ids"]),
        exact_time=(meas.get("time_base") == "shared_model_clock"
                    and dec(meas["clock_error_bound_s"]) == 0
                    and meas.get("route_observation_mode") == "exact_state_changes"
                    and dec(meas["route_time_error_bound_s"]) == 0),
    )
