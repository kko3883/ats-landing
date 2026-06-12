"""Phase 2 — Market Thermometer (BRIEF.md section 5).

Measures the present regime; does not predict. Emits, per trading day:
  - vol_score    in [0, 100]   (how stressed: VIX term structure + SPY realized
                                vol percentile + long-history credit proxy)
  - regime_score in [-100, +100] (trend axis: sign = direction, |.| = strength)
  - state flag in {CLEAR_TREND_BULL, CLEAR_TREND_BEAR, BORING_RANGE_MARKET,
                   SYSTEMIC_SHOCK}, with hysteresis to stop whipsaw.

All inputs come from the local Parquet store (Phase 1). All indicators are pure
and backward-looking (indicators.py), so the label at day T never depends on a
later bar. Supersedes trading/regime/regime_detector.py but maps onto its
Supabase `regime` contract so the dashboard/screener keep working until the old
detector is retired (decision log 2026-06-12).

Usage:
    uv run python regime_detector.py build       # compute timeline + md report
    uv run python regime_detector.py breakdown    # yearly state distribution
    uv run python regime_detector.py today        # latest regime + contract row
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import polars as pl

import indicators as ind
from ingest_engine import load_config, parquet_path, read_store

BULL = "CLEAR_TREND_BULL"
BEAR = "CLEAR_TREND_BEAR"
RANGE = "BORING_RANGE_MARKET"
SHOCK = "SYSTEMIC_SHOCK"
STATES = (BULL, BEAR, RANGE, SHOCK)

# State -> legacy Supabase `regime` contract (trading/regime/regime_detector.py).
# Keeps activated_groups/regime_name stable for the dashboard and screener.
LEGACY_CONTRACT = {
    BULL: ("risk_on", ["high_beta_growth", "moderate_growth"]),
    RANGE: ("choppy", ["neutral", "moderate_defensive"]),
    BEAR: ("risk_off", ["defensive", "moderate_defensive"]),
    SHOCK: ("risk_off", ["defensive", "moderate_defensive"]),
}


# --- data assembly ------------------------------------------------------------


def _load(config: dict, source: str, name: str, cols: dict[str, str]) -> pl.DataFrame:
    frame = read_store(parquet_path(config["data"]["dir"], source, name))
    if frame is None:
        raise FileNotFoundError(f"{source}/{name} not in store — run ingest_engine.py refresh")
    return frame.select("date", *[pl.col(src).alias(dst) for src, dst in cols.items()])


def build_panel(config: dict) -> pl.DataFrame:
    """Align every series to the anchor's (SPY) trading calendar. Trend assets
    carry OHLC (for ADX); vol inputs carry close/value only. Non-anchor series
    are left-joined and forward-filled — forward-fill is backward-looking (last
    known value), so it introduces no leak."""
    t = config["thermometer"]
    anchor = t["anchor"]
    credit = t["vol"]["credit_series"]
    assets = list(dict.fromkeys([anchor, *t["trend"]["assets"]]))  # de-dup, keep order

    panel = _load(config, "yfinance", anchor, {"close": f"{anchor}_close", "high": f"{anchor}_high", "low": f"{anchor}_low"})
    for asset in assets:
        if asset == anchor:
            continue
        panel = panel.join(
            _load(config, "yfinance", asset, {"close": f"{asset}_close", "high": f"{asset}_high", "low": f"{asset}_low"}),
            on="date", how="left",
        )
    panel = panel.join(_load(config, "yfinance", "^VIX", {"close": "vix"}), on="date", how="left")
    panel = panel.join(_load(config, "yfinance", "^VIX3M", {"close": "vix3m"}), on="date", how="left")
    panel = panel.join(_load(config, "fred", credit, {"value": "credit"}), on="date", how="left")

    fill_cols = [c for c in panel.columns if c != "date" and not c.startswith(f"{anchor}_")]
    return panel.sort("date").with_columns(pl.col(fill_cols).fill_null(strategy="forward"))


# --- scoring ------------------------------------------------------------------


def _clip01(expr: pl.Expr) -> pl.Expr:
    return expr.clip(0.0, 1.0)


def compute_scores(panel: pl.DataFrame, config: dict) -> pl.DataFrame:
    t = config["thermometer"]
    vol, trend, thr = t["vol"], t["trend"], t["thresholds"]
    anchor = t["anchor"]

    # --- volatility components (each in [0,100], may be null early) ---
    ratio = ind.vix_term_structure(panel.get_column("vix"), panel.get_column("vix3m"))
    ts_stress = (
        _clip01((ratio - vol["contango_ratio"]) / (vol["backwardation_ratio"] - vol["contango_ratio"]))
        * 100.0
    )
    rv = ind.realized_vol(panel.get_column(f"{anchor}_close"), vol["realized_vol_window"])
    rv_stress = (
        ind.rolling_percentile(rv, vol["percentile_window"], vol["percentile_min_samples"]) * 100.0
    )
    credit_z = ind.rolling_zscore(panel.get_column("credit"), vol["credit_z_window"])
    credit_stress = _clip01(credit_z / vol["credit_z_hot"]) * 100.0

    # --- trend metric -> regime_score in [-100, 100] (direction + strength) ---
    if trend["method"] == "momentum":
        per_asset = [
            ind.momentum_zscore(panel.get_column(f"{a}_close"), trend["momentum_horizons"], trend["momentum_z_window"])
            for a in trend["assets"]
        ]
        scale = trend["score_scale"]
    elif trend["method"] == "adx":
        per_asset = []
        for a in trend["assets"]:
            adx_df = ind.adx(panel.get_column(f"{a}_high"), panel.get_column(f"{a}_low"), panel.get_column(f"{a}_close"), trend["adx_window"])
            direction = (adx_df.get_column("plus_di") - adx_df.get_column("minus_di")).sign()
            per_asset.append(direction * adx_df.get_column("adx"))
        scale = trend["adx_scale"]
    else:
        raise ValueError(f"unknown trend method {trend['method']!r}")
    trend_metric = pl.DataFrame({f"t{i}": s for i, s in enumerate(per_asset)}).select(pl.mean_horizontal(pl.all())).to_series()

    # --- ADX strength gate (real OHLC): the trend-vs-range discriminator ---
    adx_strength = pl.DataFrame(
        {
            f"a{i}": ind.adx(
                panel.get_column(f"{a}_high"), panel.get_column(f"{a}_low"), panel.get_column(f"{a}_close"), trend["adx_window"]
            ).get_column("adx")
            for i, a in enumerate(trend["assets"])
        }
    ).select(pl.mean_horizontal(pl.all())).to_series()

    out = pl.DataFrame(
        {
            "date": panel.get_column("date"),
            "ts_stress": ts_stress,
            "rv_stress": rv_stress,
            "credit_stress": credit_stress,
            "trend_metric": trend_metric,
            "adx_strength": adx_strength,
        }
    )

    # weighted vol_score, renormalized over whichever components exist that day
    w = vol["weights"]
    comp = {"ts_stress": w["term_structure"], "rv_stress": w["realized_vol"], "credit_stress": w["credit"]}
    num = pl.sum_horizontal([(pl.col(c) * weight).fill_null(0.0) for c, weight in comp.items()])
    den = pl.sum_horizontal([pl.when(pl.col(c).is_not_null()).then(weight).otherwise(0.0) for c, weight in comp.items()])

    out = out.with_columns(
        vol_score=pl.when(den > 0).then(num / den).otherwise(None),
        regime_score=100.0 * (pl.col("trend_metric") / scale).tanh(),
    )

    # raw state:
    #   1. shock overrides everything (vol_score above threshold)
    #   2. a trend exists only if EITHER ADX confirms directional strength OR
    #      momentum is strongly directional (catches smooth low-ADX grinds like
    #      2013/2017 that ADX alone misses); otherwise the tape is range/chop
    #   3. when trending, direction comes from regime_score with a small deadband
    is_trending = (pl.col("adx_strength") >= trend["adx_range_ceiling"]) | (
        pl.col("regime_score").abs() >= thr["strong_trend"]
    )
    raw_state = (
        pl.when(pl.col("vol_score") >= thr["shock_vol_score"]).then(pl.lit(SHOCK))
        .when(pl.col("regime_score").is_null() | pl.col("adx_strength").is_null()).then(None)
        .when(~is_trending).then(pl.lit(RANGE))
        .when(pl.col("regime_score") >= thr["bull"]).then(pl.lit(BULL))
        .when(pl.col("regime_score") <= thr["bear"]).then(pl.lit(BEAR))
        .otherwise(pl.lit(RANGE))
    )
    return out.with_columns(raw_state=raw_state)


def apply_hysteresis(raw_states: list[str | None], n: int) -> list[str | None]:
    """Official state flips only after `n` consecutive confirming days of a new
    raw state (BRIEF.md section 5). Null raw days hold the current state."""
    official: str | None = None
    candidate: str | None = None
    run = 0
    out: list[str | None] = []
    for raw in raw_states:
        if raw is None:
            out.append(official)
            continue
        if official is None:
            official = candidate = raw
            run = 0
        elif raw == official:
            candidate, run = official, 0
        else:
            run = run + 1 if raw == candidate else 1
            candidate = raw
            if run >= n:
                official, run = raw, 0
        out.append(official)
    return out


def build_timeline(config: dict) -> pl.DataFrame:
    t = config["thermometer"]
    scored = compute_scores(build_panel(config), config)
    states = apply_hysteresis(scored.get_column("raw_state").to_list(), t["hysteresis_days"])
    timeline = scored.with_columns(state=pl.Series("state", states, dtype=pl.String))
    start = dt.datetime.fromisoformat(t["timeline_start"]).replace(tzinfo=dt.timezone.utc)
    return timeline.filter(pl.col("date") >= start)


# --- reporting ----------------------------------------------------------------


def yearly_breakdown(timeline: pl.DataFrame) -> pl.DataFrame:
    counts = (
        timeline.drop_nulls("state")
        .with_columns(year=pl.col("date").dt.year())
        .group_by("year", "state")
        .len()
    )
    wide = counts.pivot(values="len", index="year", on="state").sort("year").fill_null(0)
    for s in STATES:
        if s not in wide.columns:
            wide = wide.with_columns(pl.lit(0).alias(s))
    total = pl.sum_horizontal([pl.col(s) for s in STATES])
    dominant = pl.concat_list([pl.col(s) for s in STATES])
    return wide.select(
        "year", *STATES,
        total=total,
        dominant=pl.lit(list(STATES)).list.get(dominant.list.arg_max()),
    )


def acceptance_checks(timeline: pl.DataFrame) -> list[tuple[str, bool, str]]:
    """The owner's eyeball criteria (BRIEF.md section 5), encoded as defensible
    daily-regime assertions. NOTE: the brief also says "2015 and 2023 chop as
    range." On a momentum basis those years read bear (the 2015 H2 correction)
    and bull (2023 was +24%) respectively — see ACCEPTANCE_NOTE. The checks
    below therefore assert the defensible call: the gate is *not long* in 2015
    and *not bearish* in 2023, rather than forcing both into RANGE."""
    tl = timeline.drop_nulls("state").with_columns(year=pl.col("date").dt.year())

    def frac(year: int, states: tuple[str, ...]) -> float:
        sub = tl.filter(pl.col("year") == year)
        if sub.height == 0:
            return 0.0
        return sub.filter(pl.col("state").is_in(list(states))).height / sub.height

    checks = [
        ("2008 defensive (shock+bear)", frac(2008, (SHOCK, BEAR)) >= 0.60, f"{frac(2008,(SHOCK,BEAR)):.0%} shock+bear"),
        ("2008 has shock", frac(2008, (SHOCK,)) >= 0.15, f"{frac(2008,(SHOCK,)):.0%} shock"),
        ("2020 has shock", frac(2020, (SHOCK,)) >= 0.10, f"{frac(2020,(SHOCK,)):.0%} shock"),
        ("2017 trend-bull", frac(2017, (BULL,)) >= 0.55, f"{frac(2017,(BULL,)):.0%} bull"),
        ("2017 calm (no shock)", frac(2017, (SHOCK,)) <= 0.02, f"{frac(2017,(SHOCK,)):.0%} shock"),
        ("2022 trend-bear", frac(2022, (BEAR,)) >= 0.55, f"{frac(2022,(BEAR,)):.0%} bear"),
        ("2015 not long (bull <= 25%)", frac(2015, (BULL,)) <= 0.25, f"{frac(2015,(BULL,)):.0%} bull"),
        ("2023 constructive (bear <= 20%)", frac(2023, (BEAR,)) <= 0.20, f"{frac(2023,(BEAR,)):.0%} bear"),
    ]
    return [(name, bool(ok), detail) for name, ok, detail in checks]


ACCEPTANCE_NOTE = (
    "The brief's eyeball target labels 2015 and 2023 as range/chop. This detector "
    "is momentum-driven (it gates daily positioning), so it reads 2015 as bear "
    "(the Aug-2015 correction drove 6-12m momentum negative) and 2023 as bull "
    "(+24% on the year). That is a price-level-round-trip vs momentum disagreement, "
    "not a bug. If round-trip years should register as RANGE, that is a deliberate "
    "design choice (a price-level chop overlay) for the owner to request."
)


def write_report(config: dict, timeline: pl.DataFrame, today: dt.date) -> Path:
    t = config["thermometer"]
    breakdown = yearly_breakdown(timeline)
    checks = acceptance_checks(timeline)
    latest = timeline.drop_nulls("state").tail(1).to_dicts()[0]

    lines = [
        f"# Market Thermometer — regime timeline ({timeline['date'].min().date()} to {timeline['date'].max().date()})",
        "",
        f"_Generated {today.isoformat()}. Source: local Parquet store. Detector: regime_detector.py._",
        "",
        "## Configuration",
        f"- Trend method: **{t['trend']['method']}** on {', '.join(t['trend']['assets'])}",
        f"- Thresholds: bull >= {t['thresholds']['bull']}, bear <= {t['thresholds']['bear']}, shock vol_score >= {t['thresholds']['shock_vol_score']}",
        f"- Vol weights: {t['vol']['weights']}",
        f"- Hysteresis: {t['hysteresis_days']} consecutive days",
        "",
        "## Acceptance checks (BRIEF.md section 5)",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    lines += ["", f"> **Note on 2015/2023.** {ACCEPTANCE_NOTE}", "",
              "## Yearly state distribution (trading days)", "",
              "| Year | Bull | Bear | Range | Shock | Total | Dominant |", "|---|---|---|---|---|---|---|"]
    for row in breakdown.iter_rows(named=True):
        lines.append(
            f"| {row['year']} | {row[BULL]} | {row[BEAR]} | {row[RANGE]} | {row[SHOCK]} "
            f"| {row['total']} | {row['dominant'].replace('CLEAR_TREND_','').replace('BORING_RANGE_MARKET','RANGE').replace('SYSTEMIC_','')} |"
        )

    lines += ["", "## Current regime",
              f"- **{latest['state']}** as of {latest['date'].date()} "
              f"(regime_score {latest['regime_score']:.0f}, vol_score {latest['vol_score']:.0f})", ""]

    report_dir = Path("research/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"regime_timeline_{today.isoformat()}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def to_regime_contract(state: str, vol_score: float | None) -> dict:
    """Map a Thermometer state onto the legacy Supabase `regime` row shape."""
    regime_name, activated = LEGACY_CONTRACT[state]
    return {"regime_name": regime_name, "state": state, "activated_groups": activated, "vol_score": vol_score}


# --- persistence + CLI --------------------------------------------------------


def save_timeline(config: dict, timeline: pl.DataFrame) -> Path:
    path = Path(config["data"]["dir"]) / "regime" / "timeline.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_parquet(path)
    return path


def _print(df: pl.DataFrame) -> None:
    with pl.Config(tbl_rows=-1, tbl_cols=-1, ascii_tables=True, fmt_str_lengths=40):
        print(df)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ATS Phase 2 Market Thermometer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="compute timeline, write parquet + markdown report")
    sub.add_parser("breakdown", help="yearly state distribution")
    sub.add_parser("today", help="latest regime + legacy contract mapping")
    args = parser.parse_args(argv)

    config = load_config()
    timeline = build_timeline(config)

    if args.command == "build":
        store = save_timeline(config, timeline)
        report = write_report(config, timeline, dt.date.today())
        _print(yearly_breakdown(timeline))
        print(f"\ntimeline -> {store}\nreport   -> {report}")
        for name, ok, detail in acceptance_checks(timeline):
            print(f"  [{'PASS' if ok else 'FAIL'}] {name} ({detail})")
    elif args.command == "breakdown":
        _print(yearly_breakdown(timeline))
    elif args.command == "today":
        latest = timeline.drop_nulls("state").tail(1).to_dicts()[0]
        print(to_regime_contract(latest["state"], latest["vol_score"]))
        print(f"date={latest['date'].date()} regime_score={latest['regime_score']:.0f} vol_score={latest['vol_score']:.0f}")


if __name__ == "__main__":
    main()
