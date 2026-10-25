"""K1: seven raw indicators from appendix B, equations (3.14)–(3.31)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from .prepare import Context, dec
from .types import Value

D = Decimal
KEYS = ("P_zad", "P_pr", "P_pot", "P_bal", "P_ust", "P_ad", "P_res")
SYMBOLS = ("P_зад(A)", "P_пр(A)", "P_пот(A)", "P_бал(A)", "P_уст(A)", "P_ад(A)", "P_рес(A)")
UNITS = ("s", "bit/s", "1", "1", "1", "s", "byte")
DIRECTIONS = ("min", "max", "min", "max", "max", "min", "min")


def _packet_indicators(ctx: Context) -> tuple[dict[str, Value], dict[str, Any]]:
    packets = ctx.experiment.tables["packets.csv"]
    cohort = [p for p in packets if ctx.start <= p["tx_time_s"] < ctx.end]
    common = {"sent_packets": len(cohort), "time_window_s": [ctx.start, ctx.end],
              "loss_timeout_s": ctx.timeout}
    if not ctx.exact_time:
        reason = "Временная погрешность или способ регистрации требует согласованной интервальной обработки (Q-04)."
        return ({"P_zad": Value.unknown("s", reason), "P_pr": Value.unknown("bit/s", reason),
                 "P_pot": Value.unknown("1", reason)}, common)
    export_status = ctx.experiment.passport["export_status"]
    if export_status.get("lost_log_records") or not export_status.get("data_logging_complete") or export_status.get("logging_gaps"):
        reason = "Полнота списка отправок не подтверждена: журнал содержит потери или пробелы."
        return ({"P_zad": Value.unknown("s", reason), "P_pr": Value.unknown("bit/s", reason),
                 "P_pot": Value.unknown("1", reason)}, common)

    timely = []
    lost = []
    uncertain = []
    for packet in cohort:
        rx = packet["rx_time_s"]
        if packet["observation_status"] != "complete":
            uncertain.append(packet)
        elif rx is not None and rx - packet["tx_time_s"] <= ctx.timeout:
            timely.append(packet)
        elif rx is not None or packet["drop_reason"] or packet["observed_until_s"] > packet["tx_time_s"] + ctx.timeout:
            lost.append(packet)
        else:
            uncertain.append(packet)
    common.update({
        "timely_received": len(timely), "confirmed_lost": len(lost),
        "unknown_packets": len(uncertain),
        "drop_reasons": dict(Counter(p["drop_reason"] for p in cohort if p["drop_reason"])),
    })
    if not cohort:
        delay = Value.not_applicable("s", "В основном окне нет отправок.")
        loss = Value.not_applicable("1", "В основном окне нет отправок.")
    else:
        if uncertain:
            delay = (Value.interval(0, ctx.timeout, "s", reason="Не все своевременные доставки установлены.")
                     if timely else Value.unknown("s", "Неизвестно, существуют ли своевременные доставки."))
        elif timely:
            delay = Value.exact(sum((p["rx_time_s"] - p["tx_time_s"] for p in timely), D(0)) / len(timely), "s")
        else:
            delay = Value.not_applicable("s", "Ни один пакет не доставлен в срок.")
        loss = Value.interval(D(len(lost)) / len(cohort), D(len(lost) + len(uncertain)) / len(cohort), "1")

    received = [p for p in packets if p["observation_status"] == "complete" and p["rx_time_s"] is not None
                and ctx.start <= p["rx_time_s"] < ctx.end]
    uncertain_reception = [p for p in packets if p["observation_status"] != "complete" and p["tx_time_s"] < ctx.end]
    bytes_lower = sum(p["payload_bytes"] for p in received)
    bytes_upper = bytes_lower + sum(p["payload_bytes"] for p in uncertain_reception)
    goodput = Value.interval(D(8 * bytes_lower) / ctx.duration, D(8 * bytes_upper) / ctx.duration, "bit/s")
    common.update({"received_in_window": len(received), "payload_received_in_window_bytes": bytes_lower,
                   "received_after_window": sum(p["rx_time_s"] is not None and p["rx_time_s"] >= ctx.end for p in cohort)})
    return {"P_zad": delay, "P_pr": goodput, "P_pot": loss}, common


def _balance(ctx: Context) -> tuple[Value, list[dict[str, Any]]]:
    if not ctx.exact_time:
        return Value.unknown("1", "Временная сетка имеет несогласованную погрешность (Q-04)."), []
    low_sum = D(0)
    high_sum = D(0)
    trace: list[dict[str, Any]] = []
    undefined = False
    for start, end in ctx.grid:
        if not ctx.start <= start < end <= ctx.end:
            continue
        loads = []
        uncertain = False
        for link_id in ctx.selected_links:
            row = ctx.channel_at(link_id, start)
            if row is None:
                uncertain = True
                continue
            up = ctx.link_up(link_id, start)
            if up is None or row["observation_status"] != "complete" or row["tx_data_bytes"] is None:
                uncertain = True
            elif up:
                loads.append(D(8 * row["tx_data_bytes"]) / (row["capacity_bps"] * (end - start)))
        if uncertain:
            lo, hi = D(0), D(1)
            trace.append({"start_s": start, "end_s": end, "status": "unknown", "lower": lo, "upper": hi})
        elif not loads or sum((rho * rho for rho in loads), D(0)) == 0:
            undefined = True
            trace.append({"start_s": start, "end_s": end, "status": "not_applicable",
                          "reason": "Нет активных каналов или весь пользовательский трафик равен нулю."})
            continue
        else:
            total = sum(loads, D(0))
            jain = total * total / (D(len(loads)) * sum((rho * rho for rho in loads), D(0)))
            lo = hi = jain
            trace.append({"start_s": start, "end_s": end, "status": "exact", "value": jain,
                          "active_links": len(loads)})
        low_sum += (end - start) * lo
        high_sum += (end - start) * hi
    if undefined:
        return Value.not_applicable("1", "Индекс Джейна не определён в одном или нескольких интервалах."), trace
    return Value.interval(low_sum / ctx.duration, high_sum / ctx.duration, "1"), trace


def _stability(ctx: Context) -> tuple[Value, list[dict[str, Any]]]:
    checks = ctx.experiment.passport["measurement"]["stability"]["checks"]
    if not checks:
        return Value.not_applicable("1", "Не назначены проверки сохранения пути."), []
    if ctx.experiment.passport["algorithm"]["routing_mode"] != "single_path_per_flow":
        return Value.not_applicable("1", "Для многопутевого режима не назначено правило проверяемого пути."), []
    if not ctx.exact_time:
        return Value.unknown("1", "Моменты маршрутов или каналов имеют несогласованную погрешность (Q-04)."), []
    trace = []
    successful = unknown = 0
    for check in checks:
        start = dec(check["time_start_s"])
        end = start + dec(check["horizon_s"])
        route = ctx.route_at(check["flow_id"], start)
        if route is None or route["route_state"] == "unknown":
            passed = None
        elif route["route_state"] != "installed":
            passed = False
        else:
            passed = ctx.physical(route["path_links"], start, end)
        successful += passed is True
        unknown += passed is None
        trace.append({"check_id": check.get("check_id"), "flow_id": check["flow_id"],
                      "path_links": route["path_links"] if route else None,
                      "start_s": start, "end_s": end, "passed": passed})
    return Value.interval(D(successful) / len(checks), D(successful + unknown) / len(checks), "1"), trace


def _adaptivity(ctx: Context) -> tuple[Value, list[dict[str, Any]]]:
    setup = ctx.experiment.passport["measurement"]["adaptivity"]
    checks = setup["checks"]
    if not checks:
        return Value.not_applicable("s", "Не назначены задачи перестроения."), []
    if ctx.experiment.passport["algorithm"]["routing_mode"] != "single_path_per_flow":
        return Value.not_applicable("s", "Для многопутевого режима не определён один проверяемый путь."), []
    if not ctx.exact_time:
        return Value.unknown("s", "Моменты событий или маршрутов имеют несогласованную погрешность (Q-04)."), []
    horizon = dec(setup["horizon_s"])
    confirmation = dec(setup["confirmation_window_s"])
    trace: list[dict[str, Any]] = []
    ineligible = False
    uncertain_scope = False
    lower_sum = D(0)
    all_complete = True
    lower_open = False
    for check in checks:
        event = ctx.events[check["event_id"]]
        flow_id = check["flow_id"]
        if event.get("result") != "executed" or event.get("actual_time_s") is None:
            ineligible = True
            trace.append({"check_id": check.get("check_id"), "status": "not_applicable",
                          "reason": "Воздействие не выполнено фактически."})
            continue
        instant = dec(event["actual_time_s"])
        old = ctx.route_at(flow_id, instant, before=True)
        before = ctx.physical_at(old["path_links"], instant, before=True) if old and old["route_state"] == "installed" else False
        after = ctx.physical_at(old["path_links"], instant) if old and old["route_state"] == "installed" else False
        if old is None or old["route_state"] != "installed" or before is False or after is True:
            ineligible = True
            trace.append({"check_id": check.get("check_id"), "status": "not_applicable",
                          "reason": "До воздействия не было работоспособного пути или оно не нарушило его."})
            continue
        if before is None or after is None:
            uncertain_scope = True
            trace.append({"check_id": check.get("check_id"), "status": "unknown",
                          "reason": "Нарушение прежнего пути не подтверждено полной историей."})
            continue
        scope_end = min(instant + horizon + confirmation, ctx.capture_end)
        bypass = ctx.connected(flow_id, instant, scope_end)
        if bypass is False:
            ineligible = True
            trace.append({"check_id": check.get("check_id"), "status": "not_applicable",
                          "reason": "На горизонте отсутствовала физическая возможность обхода."})
            continue
        if bypass is None:
            uncertain_scope = True
            trace.append({"check_id": check.get("check_id"), "status": "unknown",
                          "reason": "Физическая возможность обхода не подтверждена."})
            continue
        candidates = {instant}
        for route in ctx.by_flow.get(flow_id, []):
            for candidate in (route["time_start_s"], route["time_end_s"]):
                if instant <= candidate <= instant + horizon:
                    candidates.add(candidate)
        for start, end in ctx.grid:
            for candidate in (start, end):
                if instant <= candidate <= instant + horizon:
                    candidates.add(candidate)
        for ev in ctx.events.values():
            if ev.get("result") == "executed" and ev.get("actual_time_s") is not None:
                candidate = dec(ev["actual_time_s"])
                if instant <= candidate <= instant + horizon:
                    candidates.add(candidate)
        completed = None
        earlier_unknown = False
        for candidate in sorted(candidates):
            if candidate + confirmation > ctx.capture_end:
                continue
            route = ctx.route_at(flow_id, candidate)
            if route is None or route["route_state"] == "unknown":
                earlier_unknown = True
                continue
            if route["route_state"] != "installed":
                continue
            stable = ctx.same_installed_path(flow_id, route["path_links"], candidate, candidate + confirmation)
            physical = ctx.physical(route["path_links"], candidate, candidate + confirmation)
            if stable is None or physical is None:
                earlier_unknown = True
            if stable is True and physical is True:
                completed = candidate - instant
                break
        if completed is not None and not earlier_unknown:
            lower_sum += completed
            trace.append({"check_id": check.get("check_id"), "status": "exact", "time_s": completed,
                          "confirmation_window_s": confirmation})
        elif completed is not None:
            all_complete = False
            trace.append({"check_id": check.get("check_id"), "status": "interval", "lower_s": D(0),
                          "upper_s": completed, "reason": "Ранее была неизвестная история."})
        else:
            all_complete = False
            full = ctx.capture_end >= instant + horizon + confirmation and not earlier_unknown
            lower = horizon if full else (D(0) if earlier_unknown else max(D(0), min(horizon, ctx.capture_end - instant - confirmation)))
            lower_sum += lower
            lower_open |= full
            trace.append({"check_id": check.get("check_id"),
                          "status": "not_completed_within_horizon" if full else "incomplete_history",
                          "lower_s": lower, "lower_open": full,
                          "reason": "Момент завершения не наблюдался; горизонт не подставлен вместо времени."})
    if ineligible:
        return Value.not_applicable("s", "Не все назначенные задачи соответствуют области применимости."), trace
    if uncertain_scope:
        return Value.unknown("s", "Область применимости задачи перестроения не подтверждена."), trace
    if all_complete:
        return Value.exact(lower_sum / len(checks), "s"), trace
    return Value.interval(lower_sum / len(checks), None, "s", lower_open=lower_open,
                          reason="Незавершённое перестроение сохранено как нижняя граница."), trace


def _service(ctx: Context) -> tuple[Value, dict[str, Any]]:
    low = high = D(0)
    incomplete = 0
    atom = dec(ctx.experiment.passport["measurement"]["channel_max_atomic_packet_bytes"])
    for rows in ctx.by_link.values():
        for row in rows:
            if not ctx.start <= row["time_start_s"] < row["time_end_s"] <= ctx.end:
                continue
            amount = row["tx_routing_bytes"]
            if row["observation_status"] == "complete" and amount is not None:
                low += amount
                high += amount
            else:
                incomplete += 1
                if row["link_up"] is not False:
                    high += row["capacity_bps"] * (row["time_end_s"] - row["time_start_s"]) / 8 + atom
    return Value.interval(low, high, "byte", reason="Служебные счётчики неполны." if incomplete else ""), {
        "complete_bytes_lower": low, "possible_bytes_upper": high,
        "incomplete_intervals": incomplete, "counter_scope": "egress", "window_s": [ctx.start, ctx.end],
    }


def calculate_raw(ctx: Context) -> tuple[dict[str, Value], dict[str, Any]]:
    packet_values, packet_trace = _packet_indicators(ctx)
    balance, balance_trace = _balance(ctx)
    stability, stability_trace = _stability(ctx)
    adaptivity, adaptivity_trace = _adaptivity(ctx)
    service, service_trace = _service(ctx)
    values = {**packet_values, "P_bal": balance, "P_ust": stability, "P_ad": adaptivity, "P_res": service}
    traces = {
        "P_zad": packet_trace, "P_pr": packet_trace, "P_pot": packet_trace,
        "P_bal": {"intervals": balance_trace}, "P_ust": {"checks": stability_trace},
        "P_ad": {"checks": adaptivity_trace}, "P_res": service_trace,
    }
    return values, traces
