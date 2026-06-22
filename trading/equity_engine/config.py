"""
Central configuration for the Equity Trading Engine.

Reads from:
  - configs/trading_params.yaml  (thresholds, multipliers, risk limits)
  - configs/seed_universe.json   (initial stock list)
  - Environment variables        (broker credentials, paths)
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Path resolution ─────────────────────────────────────────────────────────
ENGINE_DIR = Path(__file__).resolve().parent
CONFIGS_DIR = ENGINE_DIR / "configs"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


# ── Broker connection defaults ──────────────────────────────────────────────
# IB Gateway (on NAS, accessed via Tailscale)
IBG_HOST = os.environ.get("IBG_HOST", "kko-nas.tail9a4917.ts.net")
IBG_PORT_PAPER = int(os.environ.get("IBG_PORT_PAPER", "7497"))     # TWS paper
IBG_PORT_LIVE = int(os.environ.get("IBG_PORT_LIVE", "7496"))       # TWS live
IBG_CLIENT_ID = int(os.environ.get("IBG_CLIENT_ID", "100"))
IB_ACCOUNT_ID = os.environ.get("IB_ACCOUNT_ID", "")                # e.g., DUQ538194

# Longbridge (cloud-native, on Mac)
LB_APP_KEY = os.environ.get("LONGBRIDGE_APP_KEY", "")
LB_APP_SECRET = os.environ.get("LONGBRIDGE_APP_SECRET", "")
LB_ACCESS_TOKEN = os.environ.get("LONGBRIDGE_ACCESS_TOKEN", "")

# Supabase (existing regime table)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://nwatzlrmoefluymhqgwi.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# Paths
STATE_DIR = Path(os.environ.get("EQUITY_STATE_DIR", str(Path.home() / ".hermes" / "equity_engine")))
MODELS_DIR = ENGINE_DIR / "models"
TRADES_LOG = STATE_DIR / "trades.jsonl"
STATE_FILE = STATE_DIR / "state.json"

# Ensure state directory exists
STATE_DIR.mkdir(parents=True, exist_ok=True)


# ── Trading parameters ──────────────────────────────────────────────────────

@dataclass
class Layer1Config:
    """Macro filter (daily) parameters."""
    sma_period: int = 200
    # Minimum market cap in USD (filter tiny stocks)
    min_market_cap: float = 10_000_000_000  # $10B
    # Minimum daily dollar volume in USD
    min_dollar_volume: float = 100_000_000  # $100M
    # How many bars of D1 data to fetch for SMA computation
    d1_lookback: int = 300
    # Run daily filter at this UTC time (09:25 ET = 14:25 UTC)
    run_at_utc: str = "14:25"


@dataclass
class Layer2Config:
    """Tactical trigger (15-minute) parameters."""
    # XGBoost model path
    model_path: str = str(MODELS_DIR / "xgb_entry_classifier.json")
    # Entry threshold — probability must exceed this to fire
    entry_prob_threshold: float = 0.65
    # Number of future bars to forecast (for label creation during training)
    forecast_bars: int = 4  # 4 x 15m = 1 hour lookahead
    # Feature lookback periods
    rsi_period: int = 14
    atr_period: int = 14
    volume_z_period: int = 20
    # M15 data buffer size
    m15_buffer_bars: int = 500
    # Minimum bars before inference starts
    m15_warmup_bars: int = 50


@dataclass
class Layer3Config:
    """Micro guard (1-minute) parameters."""
    # Trailing stop base multiplier (× ATR15 from Layer 2)
    base_trail_mult: float = 1.5
    # Tighten multiplier when micro-volatility spikes
    tighten_mult: float = 0.75
    # Loosen multiplier when volatility collapses
    loosen_mult: float = 2.0
    # Volume acceleration Z-score threshold for tightening
    tighten_vol_z: float = 2.5
    # Volume deceleration Z-score threshold for loosening
    loosen_vol_z: float = -1.5
    # Micro volatility lookback (bars)
    micro_lookback: int = 5
    # Time decay: exit if flat for this many hours
    max_flat_hours: float = 5.0
    # Time decay: minimum price movement % to avoid decay
    min_move_pct: float = 0.002  # 0.2%
    # M1 data buffer size
    m1_buffer_bars: int = 500


@dataclass
class RiskConfig:
    """Risk control parameters."""
    # Max risk per trade as fraction of portfolio equity
    max_risk_per_trade: float = 0.01       # 1%
    # Max concurrent positions
    max_positions: int = 5
    # Daily loss limit as fraction of starting equity
    daily_loss_limit: float = 0.03         # 3%
    # PDT rules
    pdt_equity_threshold: float = 25_000.0
    pdt_max_day_trades: int = 3
    pdt_rolling_window_days: int = 5
    # Overnight gap protection: don't enter if gap > N × ATR(15)
    max_gap_atr_mult: float = 2.0
    # Don't enter in first N minutes of regular session
    open_cooldown_minutes: int = 5
    # Slippage estimate (fraction of price)
    slippage: float = 0.0005               # 5 bps


@dataclass
class EngineConfig:
    """Master configuration aggregating all sub-configs."""
    layer1: Layer1Config = field(default_factory=Layer1Config)
    layer2: Layer2Config = field(default_factory=Layer2Config)
    layer3: Layer3Config = field(default_factory=Layer3Config)
    risk: RiskConfig = field(default_factory=RiskConfig)
    # Seed universe (list of ticker strings like "AAPL.US")
    seed_universe: list[str] = field(default_factory=list)
    # Whether to use the screener output to dynamically expand universe
    use_screener: bool = False
    # Path to screener watchlist JSON
    screener_path: str = str(Path.home() / ".hermes" / "trading" / "watchlist" / "watchlist.json")
    # IB Gateway: paper or live
    ib_paper: bool = True
    # Minimum sleep between engine loop iterations (seconds)
    loop_interval: float = 1.0

    @classmethod
    def from_defaults(cls) -> "EngineConfig":
        """Load config from files + env, falling back to dataclass defaults."""
        cfg = cls()
        # Load seed universe
        seed_file = CONFIGS_DIR / "seed_universe.json"
        if seed_file.exists():
            seed = _load_json(seed_file)
            cfg.seed_universe = seed.get("tickers", [])
        # Load YAML overrides
        yaml_file = CONFIGS_DIR / "trading_params.yaml"
        if yaml_file.exists():
            _overlay_from_yaml(cfg, yaml_file)
        return cfg

    @property
    def ib_port(self) -> int:
        return IBG_PORT_PAPER if self.ib_paper else IBG_PORT_LIVE

    @property
    def ib_host(self) -> str:
        return IBG_HOST


def _overlay_from_yaml(cfg: EngineConfig, yaml_path: Path):
    """Parse YAML and override dataclass fields where provided."""
    try:
        import yaml
    except ImportError:
        return  # yaml not installed — use defaults

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    for section_name, section_data in data.items():
        if not isinstance(section_data, dict):
            continue
        section_obj = getattr(cfg, section_name, None)
        if section_obj is None:
            continue
        for key, val in section_data.items():
            if hasattr(section_obj, key):
                setattr(section_obj, key, val)