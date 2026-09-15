#!/usr/bin/env python3
"""Empirical connection probe for SolarMax inverters (MaxComm over TCP).

Answers the questions the connection-engine redesign depends on:

  A. Does the inverter keep an idle TCP connection alive between polls?
     (tested at 30s / 60s / 120s / 300s idle gaps)
  B. Response times for rapid back-to-back polls on ONE connection.
  C. Baseline: connect-poll-close cycles (today's strategy).
  D. Does the inverter accept a SECOND concurrent connection?
  E. --watch: long-running dusk/dawn capture — logs SYS status transitions
     and the exact way the inverter leaves the network (refused / timeout /
     reset), with timestamps.

Usage:
    python3 tools/probe_connection.py --host 192.168.x.x            # tests A-D
    python3 tools/probe_connection.py --host 192.168.x.x --watch    # dusk capture
    python3 tools/probe_connection.py --host 192.168.x.x --quick    # A(30/60s)+B+C

Results land in probe_results.json next to the log output. No dependencies.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import datetime

# --- minimal MaxComm codec (self-contained on purpose) -----------------------


def _checksum(payload: str) -> str:
    return format(sum(ord(c) for c in payload), "04X")


def build_request(address: int, fields: list[str]) -> bytes:
    data = ";".join(fields)
    req = "{FB;" + format(address, "02X") + ";!!|64:" + data + "|$$$$}"
    req = req.replace("!!", format(len(req), "02X"))
    req = req.replace("$$$$", _checksum(req[1 : req.index("$$$$")]))
    return req.encode()


def parse_values(response: str) -> dict[str, int]:
    """Best-effort KV extraction; tolerant of partial frames."""
    values: dict[str, int] = {}
    if ":" not in response:
        return values
    body = response.split(":", 1)[1]
    body = body.rsplit("|", 1)[0]
    for item in body.split(";"):
        if "=" in item:
            key, _, raw = item.partition("=")
            try:
                values[key] = int(raw.split(",")[0], 16)
            except ValueError:
                pass
    return values


# --- probe primitives ---------------------------------------------------------


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def connect(host: str, port: int, timeout: float = 3.0) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def poll_on(
    sock: socket.socket, address: int, fields: list[str], timeout: float = 3.0
) -> tuple[dict[str, int], float]:
    """One request/response on an existing socket. Returns (values, seconds)."""
    sock.settimeout(timeout)
    start = time.monotonic()
    sock.sendall(build_request(address, fields))
    buf = b""
    while not buf.endswith(b"}"):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed (recv returned 0 bytes)")
        buf += chunk
    elapsed = time.monotonic() - start
    return parse_values(buf.decode(errors="ignore")), elapsed


def classify_error(exc: Exception) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return "REFUSED"
    if isinstance(exc, ConnectionResetError):
        return "RESET"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, ConnectionError):
        return f"CONN({exc})"
    if isinstance(exc, OSError):
        return f"OS({exc.errno}:{exc.strerror})"
    return f"{type(exc).__name__}({exc})"


# --- tests --------------------------------------------------------------------

POLL_FIELDS = ["PAC", "SYS", "KDY"]


def test_idle_persistence(host: str, port: int, address: int, gaps: list[int]) -> dict:
    """A: hold one connection, poll, idle for GAP, poll again. Survived?"""
    results = {}
    for gap in gaps:
        log(f"A: idle-gap {gap}s — connecting fresh")
        try:
            sock = connect(host, port)
            poll_on(sock, address, POLL_FIELDS)
            log(f"A: first poll OK, sleeping {gap}s with connection open...")
            time.sleep(gap)
            values, rt = poll_on(sock, address, POLL_FIELDS)
            results[str(gap)] = {"survived": True, "response_s": round(rt, 3)}
            log(
                f"A: idle {gap}s -> SURVIVED (response {rt * 1000:.0f} ms, "
                f"PAC={values.get('PAC')})"
            )
            sock.close()
        except Exception as exc:
            kind = classify_error(exc)
            results[str(gap)] = {"survived": False, "error": kind}
            log(f"A: idle {gap}s -> DIED: {kind}")
        finally:
            try:
                sock.close()  # never leak a dead socket into the next test
            except Exception:
                pass
        time.sleep(5)
    return results


def test_rapid_polls(host: str, port: int, address: int, count: int = 10) -> dict:
    """B: N back-to-back polls on one connection."""
    log(f"B: {count} rapid polls on one connection")
    times: list[float] = []
    errors: list[str] = []
    try:
        sock = connect(host, port)
        for _i in range(count):
            _, rt = poll_on(sock, address, POLL_FIELDS)
            times.append(rt)
        sock.close()
    except Exception as exc:
        errors.append(f"poll {len(times) + 1}: {classify_error(exc)}")
    if times:
        log(
            f"B: {len(times)}/{count} OK — min {min(times) * 1000:.0f} / "
            f"avg {sum(times) / len(times) * 1000:.0f} / max {max(times) * 1000:.0f} ms"
        )
    for e in errors:
        log(f"B: ERROR {e}")
    return {
        "ok": len(times),
        "of": count,
        "errors": errors,
        "ms": [round(t * 1000) for t in times],
    }


def test_per_poll_baseline(host: str, port: int, address: int, count: int = 5) -> dict:
    """C: connect-poll-close cycles (current integration strategy)."""
    log(f"C: {count} connect-poll-close cycles")
    times: list[float] = []
    errors: list[str] = []
    for _ in range(count):
        try:
            start = time.monotonic()
            sock = connect(host, port)
            poll_on(sock, address, POLL_FIELDS)
            sock.close()
            times.append(time.monotonic() - start)
        except Exception as exc:
            errors.append(classify_error(exc))
        time.sleep(1)
    if times:
        log(
            f"C: {len(times)}/{count} OK — full-cycle avg "
            f"{sum(times) / len(times) * 1000:.0f} ms"
        )
    return {
        "ok": len(times),
        "of": count,
        "errors": errors,
        "ms": [round(t * 1000) for t in times],
    }


def test_concurrent_connections(host: str, port: int, address: int) -> dict:
    """D: open a second connection while the first is alive; poll on both."""
    log("D: concurrent-connection test")
    result: dict = {}
    try:
        s1 = connect(host, port)
        poll_on(s1, address, ["PAC"])
        result["first"] = "OK"
        try:
            s2 = connect(host, port)
            _, rt = poll_on(s2, address, ["PAC"])
            result["second"] = "OK"
            log(f"D: second connection ACCEPTED and answered ({rt * 1000:.0f} ms)")
            # does the first still work afterwards?
            _, rt1 = poll_on(s1, address, ["PAC"])
            result["first_after_second"] = "OK"
            log(f"D: first connection still alive afterwards ({rt1 * 1000:.0f} ms)")
            s2.close()
        except Exception as exc:
            result["second"] = classify_error(exc)
            log(f"D: second connection -> {result['second']}")
            try:
                poll_on(s1, address, ["PAC"])
                result["first_after_second"] = "OK"
            except Exception as exc2:
                result["first_after_second"] = classify_error(exc2)
        s1.close()
    except Exception as exc:
        result["first"] = classify_error(exc)
        log(f"D: first connection failed: {result['first']}")
    return result


def watch(host: str, port: int, address: int, interval: int) -> None:
    """E: long-running capture — SYS transitions and the exact drop signature."""
    log(
        f"WATCH: polling every {interval}s. Ctrl+C to stop. "
        "Leave running through dusk (or dawn)."
    )
    last_sys: int | None = None
    last_state = "START"
    while True:
        try:
            sock = connect(host, port)
            values, rt = poll_on(sock, address, POLL_FIELDS)
            sock.close()
            sys_val = values.get("SYS")
            state = "ONLINE"
            if sys_val != last_sys:
                log(
                    f"WATCH: SYS {last_sys} -> {sys_val}  "
                    f"(PAC={values.get('PAC')}, KDY={values.get('KDY')}, "
                    f"{rt * 1000:.0f} ms)"
                )
                last_sys = sys_val
        except Exception as exc:
            state = classify_error(exc)
        if state != last_state:
            log(f"WATCH: connection state {last_state} -> {state}")
            last_state = state
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=12345)
    ap.add_argument("--address", type=int, default=1)
    ap.add_argument(
        "--watch",
        action="store_true",
        help="long-running dusk/dawn capture instead of tests A-D",
    )
    ap.add_argument(
        "--interval",
        type=int,
        default=30,
        help="watch-mode poll interval (default 30s)",
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help="idle gaps 30/60s only (~3 min instead of ~10)",
    )
    args = ap.parse_args()

    if args.watch:
        try:
            watch(args.host, args.port, args.address, args.interval)
        except KeyboardInterrupt:
            log("WATCH: stopped")
        return 0

    gaps = [30, 60] if args.quick else [30, 60, 120, 300]
    findings = {
        "timestamp": datetime.now().isoformat(),
        "host": args.host,
        "A_idle_persistence": test_idle_persistence(
            args.host, args.port, args.address, gaps
        ),
        "B_rapid_polls": test_rapid_polls(args.host, args.port, args.address),
        "C_per_poll_baseline": test_per_poll_baseline(
            args.host, args.port, args.address
        ),
        "D_concurrent": test_concurrent_connections(args.host, args.port, args.address),
    }
    out = "probe_results.json"
    with open(out, "w") as fh:
        json.dump(findings, fh, indent=2)
    log(f"Findings written to {out} — paste the file (or its contents) back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
