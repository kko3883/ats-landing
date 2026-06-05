#!/usr/bin/env python3
"""
ATS FX — interactive Telegram bot (Phase 1: read-only visibility).

Long-polls Telegram for commands, reads the daemon's state.json snapshot, and
replies. Pure stdlib (urllib + json). Only responds to TELEGRAM_CHAT_ID; messages
from anyone else are ignored.

Commands: /status /positions /balance /signal /help
Phase 2 (switches: /pause /resume /enable /disable) and Phase 3 (/set tuning)
will write a control.json the daemon reads — not wired yet.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, STATE_PATH (default /state/state.json)
"""
import json
import os
import time
import urllib.parse
import urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
STATE_PATH = os.environ.get("STATE_PATH", "/state/state.json")
CONTROL_PATH = os.environ.get("CONTROL_PATH", "/state/control.json")
TRADES_PATH = os.environ.get("TRADES_PATH", "/state/trades.jsonl")
PAIRS = {  # normalized user input -> instrument id the daemon uses
    "EURUSD": "EUR/USD.IDEALPRO",
    "AUDJPY": "AUD/JPY.IDEALPRO",
    "NZDJPY": "NZD/JPY.IDEALPRO",
}
API = f"https://api.telegram.org/bot{TOKEN}"


def _api(method: str, params: dict, timeout: int = 40):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def send(text: str):
    try:
        _api("sendMessage", {"chat_id": CHAT_ID, "text": text}, timeout=15)
    except Exception as exc:
        print(f"send failed: {exc}", flush=True)


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def load_control():
    try:
        with open(CONTROL_PATH) as f:
            c = json.load(f)
    except Exception:
        c = {}
    c.setdefault("trading_enabled", True)
    c.setdefault("pairs", {})
    return c


def save_control(c):
    tmp = CONTROL_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, CONTROL_PATH)


def set_trading(enabled: bool) -> str:
    c = load_control()
    c["trading_enabled"] = enabled
    save_control(c)
    return ("▶️ Trading RESUMED — new entries allowed." if enabled
            else "⏸️ Trading PAUSED — no new entries (open positions still managed).")


def set_pair(arg: str, enabled: bool) -> str:
    iid = PAIRS.get(arg.strip().upper().replace("/", ""))
    if not iid:
        return f"Unknown pair '{arg}'. Use one of: EURUSD, AUDJPY, NZDJPY."
    c = load_control()
    c["pairs"][iid] = enabled
    save_control(c)
    return f"{iid}: {'✅ enabled' if enabled else '🚫 disabled'}."


def _fmt_signals(s) -> list[str]:
    out = ["Signals:"]
    sigs = s.get("signals", {})
    if not sigs:
        out.append("  (warming up — no bars yet)")
    for pair, g in sigs.items():
        out.append(
            f"  {pair}: L{g.get('level')} {g.get('signal')} "
            f"RSI{g.get('rsi')} @ {g.get('price')}"
        )
    return out


def _fmt_positions(s) -> list[str]:
    pos = s.get("positions", [])
    stops = s.get("stops", {})
    if not pos:
        return ["Positions: none"]
    out = ["Positions:"]
    for p in pos:
        stop = stops.get(p["pair"], "—")
        out.append(f"  📌 {p['pair']} {p['side']} {p['size']} @ {p['entry']} | stop {stop}")
    return out


def _fmt_balance(s) -> list[str]:
    acct = s.get("account", [])
    if not acct:
        return ["Balance: (not reported yet)"]
    return ["Balance: " + " | ".join(acct)]


def fmt_status(s) -> str:
    if not s:
        return "⚠️ No state yet — the daemon may still be starting. Try again shortly."
    on = s.get("trading_enabled")
    lines = [
        f"📊 ATS FX — {s.get('ts', '?')}",
        f"Trading: {'🟢 ON' if on else '🔴 PAUSED'}",
    ]
    disabled = [p for p, en in (s.get("pairs") or {}).items() if not en]
    if disabled:
        lines.append("Disabled: " + ", ".join(disabled))
    lines += _fmt_balance(s)
    lines.append("")
    lines += _fmt_signals(s)
    lines.append("")
    lines += _fmt_positions(s)
    return "\n".join(lines)


def fmt_history(n: int = 12) -> str:
    try:
        with open(TRADES_PATH) as f:
            lines = f.readlines()[-n:]
    except Exception:
        return "🧾 No trades yet."
    if not lines:
        return "🧾 No trades yet."
    out = ["🧾 Recent trades:"]
    for ln in lines:
        try:
            t = json.loads(ln)
        except Exception:
            continue
        ts = t.get("ts", "")[:16].replace("T", " ")
        if t.get("type") == "fill":
            out.append(f"{ts}  {t.get('side')} {t.get('qty')} {t.get('pair')} @ {t.get('px')}")
        elif t.get("type") == "close":
            out.append(
                f"{ts}  CLOSE {t.get('pair')} @ {t.get('exit')} | "
                f"PnL {t.get('pnl')} ({t.get('return')})"
            )
    return "\n".join(out)


def handle(text: str) -> str:
    cmd = text.strip().split()[0].lower().lstrip("/").split("@")[0]
    s = load_state()
    if cmd in ("status", "start"):
        return fmt_status(s)
    if cmd == "positions":
        return "\n".join(_fmt_positions(s)) if s else "⚠️ No state yet."
    if cmd == "balance":
        return "\n".join(_fmt_balance(s)) if s else "⚠️ No state yet."
    if cmd == "signal":
        return "\n".join(_fmt_signals(s)) if s else "⚠️ No state yet."
    if cmd == "pause":
        return set_trading(False)
    if cmd == "resume":
        return set_trading(True)
    if cmd in ("enable", "disable"):
        parts = text.strip().split()
        if len(parts) < 2:
            return f"Usage: /{cmd} EURUSD"
        return set_pair(parts[1], cmd == "enable")
    if cmd in ("history", "trades"):
        return fmt_history()
    if cmd == "help":
        return (
            "Commands:\n"
            "/status — overview (trading state, balance, signals, positions)\n"
            "/positions — open positions + stops\n"
            "/balance — account balance\n"
            "/signal — current signal per pair\n"
            "/history — recent fills + closes (with P&L)\n"
            "/pause — halt new entries (keeps managing open positions)\n"
            "/resume — allow new entries again\n"
            "/disable EURUSD — stop entering one pair\n"
            "/enable EURUSD — re-enable a pair\n"
            "/help — this list\n\n"
            "(tuning /set coming in phase 3)"
        )
    return "Unknown command. Send /help for the list."


def main():
    send("🤖 ATS FX bot online. Send /status for an overview.")
    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = _api("getUpdates", params, timeout=40)
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                if str(msg.get("chat", {}).get("id")) != CHAT_ID:
                    continue  # ignore everyone except the authorized chat
                text = msg.get("text", "")
                if text:
                    send(handle(text))
        except Exception as exc:
            print(f"poll error: {exc}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
