import sys
import json
import os

USAGE = "usage: baseline_vs_v1_diff.py <baseline_trades.jsonl> <v1_trades.jsonl>"

KEY_FIELDS = ["instrument_id", "side", "filled_qty", "avg_px", "ts_last"]


def load_trades(path):
    if not os.path.exists(path):
        print("FILE_MISSING %s" % path)
        return None
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as e:
                print("PARSE_WARN bad line in %s err=%s" % (path, e))
    return rows


def norm(v):
    if v is None:
        return "null"
    if isinstance(v, float):
        return "%.6f" % v
    return str(v)


def key_of(t):
    parts = []
    for fld in KEY_FIELDS:
        parts.append(norm(t.get(fld)))
    return tuple(parts)


def summarize(rows):
    return sorted(key_of(t) for t in rows)


def main():
    if len(sys.argv) != 3:
        print(USAGE)
        return 2
    a = load_trades(sys.argv[1])
    b = load_trades(sys.argv[2])
    if a is None or b is None:
        return 3
    print("baseline_fills=%d v1_fills=%d" % (len(a), len(b)))
    print("keyed_on=%s" % ",".join(KEY_FIELDS))
    ka = summarize(a)
    kb = summarize(b)
    set_a = {}
    for k in ka:
        set_a[k] = set_a.get(k, 0) + 1
    set_b = {}
    for k in kb:
        set_b[k] = set_b.get(k, 0) + 1
    only_baseline = []
    only_v1 = []
    allk = set(list(set_a.keys()) + list(set_b.keys()))
    for k in sorted(allk):
        ca = set_a.get(k, 0)
        cb = set_b.get(k, 0)
        if ca > cb:
            for _ in range(ca - cb):
                only_baseline.append(k)
        elif cb > ca:
            for _ in range(cb - ca):
                only_v1.append(k)
    if not only_baseline and not only_v1 and len(a) == len(b):
        print("DIFF_RESULT PASS baseline fills match v1 exactly (%d fills)" % len(a))
        return 0
    print("DIFF_RESULT FAIL fill lists differ")
    print("only_in_baseline=%d only_in_v1=%d" % (len(only_baseline), len(only_v1)))
    for k in only_baseline[:20]:
        print("  BASELINE_ONLY", k)
    for k in only_v1[:20]:
        print("  V1_ONLY", k)
    if len(only_baseline) > 20 or len(only_v1) > 20:
        print("  (truncated to first 20 each)")
    return 4


if __name__ == "__main__":
    sys.exit(main())
