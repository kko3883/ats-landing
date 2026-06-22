import sys
import os
import time
import threading

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
except Exception as e:
    print("MISSING_DEP ibapi not importable:", e)
    sys.exit(2)

HOST = os.environ.get("IB_DIAG_HOST", "127.0.0.1")
PORT = int(os.environ.get("IB_DIAG_PORT", "4004"))
CLIENT_ID = int(os.environ.get("IB_DIAG_CLIENT_ID", "977"))

TESTS = [
    ("4 D",   "15 mins"),
    ("10 D",  "15 mins"),
    ("30 D",  "15 mins"),
    ("90 D",  "15 mins"),
    ("90 D",  "1 hour"),
    ("1 W",   "15 mins"),
]

PER_REQ_TIMEOUT = 35


class Diag(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected_ok = False
        self.bar_counts = {}
        self.first = {}
        self.last = {}
        self.errors = {}
        self.done = {}

    def nextValidId(self, orderId):
        self.connected_ok = True

    def error(self, *args):
        if len(args) >= 5:
            req_id = args[0]; code = args[2]; msg = args[3]
        elif len(args) >= 3:
            req_id = args[0]; code = args[1]; msg = args[2]
        else:
            req_id = args[0] if args else -1; code = None; msg = str(args)
        if code in (2104, 2106, 2158, 2107, 2119):
            return
        self.errors.setdefault(req_id, []).append("%s:%s" % (code, msg))
        print("    [error] reqId=%s code=%s msg=%s" % (req_id, code, msg))

    def historicalData(self, reqId, bar):
        self.bar_counts[reqId] = self.bar_counts.get(reqId, 0) + 1
        if reqId not in self.first:
            self.first[reqId] = bar.date
        self.last[reqId] = bar.date

    def historicalDataEnd(self, reqId, start, end):
        self.done[reqId] = True


def main():
    app = Diag()
    print("CONNECTING host=%s port=%s client_id=%s" % (HOST, PORT, CLIENT_ID))
    try:
        app.connect(HOST, PORT, CLIENT_ID)
    except Exception as e:
        print("CONNECT_FAIL host=%s port=%s err=%s" % (HOST, PORT, e))
        return 3

    t = threading.Thread(target=app.run, daemon=True)
    t.start()

    deadline = time.time() + 15
    while not app.connected_ok and time.time() < deadline:
        time.sleep(0.2)
    if not app.connected_ok:
        print("CONNECT_FAIL no nextValidId; gateway not accepting API on %s:%s" % (HOST, PORT))
        app.disconnect()
        return 3
    sv = None
    try:
        sv = app.serverVersion()
    except Exception:
        pass
    print("CONNECTED host=%s port=%s server_version=%s" % (HOST, PORT, sv))

    contract = Contract()
    contract.symbol = "EUR"
    contract.secType = "CASH"
    contract.currency = "USD"
    contract.exchange = "IDEALPRO"

    rid = 100
    results = []
    for duration, bar_size in TESTS:
        rid += 1
        label = "%s / %s" % (duration, bar_size)
        print("\n== TEST %s (reqId=%s) ==" % (label, rid))
        app.reqHistoricalData(rid, contract, "", duration, bar_size,
                              "MIDPOINT", 0, 2, False, [])
        t0 = time.time()
        while time.time() - t0 < PER_REQ_TIMEOUT:
            if rid in app.done:
                break
            if app.errors.get(rid):
                break
            time.sleep(0.3)
        n = app.bar_counts.get(rid, 0)
        if rid in app.done and n > 0:
            print("    RESULT OK bars=%d first=%s last=%s" % (n, app.first.get(rid), app.last.get(rid)))
            results.append((label, "OK", n))
        elif app.errors.get(rid):
            print("    RESULT ERROR %s" % "; ".join(app.errors[rid]))
            results.append((label, "ERROR", app.errors[rid]))
        elif n > 0:
            print("    RESULT PARTIAL bars=%d (no end signal)" % n)
            results.append((label, "PARTIAL", n))
        else:
            print("    RESULT TIMEOUT no bars, no error, no end within %ds" % PER_REQ_TIMEOUT)
            results.append((label, "TIMEOUT", 0))
        time.sleep(1)

    app.disconnect()
    time.sleep(0.5)

    print("\n================ SUMMARY ================")
    for label, status, detail in results:
        print("  %-16s %-8s %s" % (label, status, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
