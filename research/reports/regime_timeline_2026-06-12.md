# Market Thermometer — regime timeline (2007-01-03 to 2026-06-11)

_Generated 2026-06-12. Source: local Parquet store. Detector: regime_detector.py._

## Configuration
- Trend method: **momentum** on SPY, QQQ
- Thresholds: bull >= 10, bear <= -10, shock vol_score >= 70
- Vol weights: {'term_structure': 0.45, 'realized_vol': 0.4, 'credit': 0.15}
- Hysteresis: 5 consecutive days

## Acceptance checks (BRIEF.md section 5)

| Check | Result | Detail |
|---|---|---|
| 2008 defensive (shock+bear) | PASS | 91% shock+bear |
| 2008 has shock | PASS | 42% shock |
| 2020 has shock | PASS | 17% shock |
| 2017 trend-bull | PASS | 67% bull |
| 2017 calm (no shock) | PASS | 0% shock |
| 2022 trend-bear | PASS | 88% bear |
| 2015 not long (bull <= 25%) | PASS | 0% bull |
| 2023 constructive (bear <= 20%) | PASS | 3% bear |

> **Note on 2015/2023.** The brief's eyeball target labels 2015 and 2023 as range/chop. This detector is momentum-driven (it gates daily positioning), so it reads 2015 as bear (the Aug-2015 correction drove 6-12m momentum negative) and 2023 as bull (+24% on the year). That is a price-level-round-trip vs momentum disagreement, not a bug. If round-trip years should register as RANGE, that is a deliberate design choice (a price-level chop overlay) for the owner to request.

## Yearly state distribution (trading days)

| Year | Bull | Bear | Range | Shock | Total | Dominant |
|---|---|---|---|---|---|---|
| 2007 | 134 | 9 | 13 | 95 | 251 | BULL |
| 2008 | 0 | 123 | 23 | 107 | 253 | BEAR |
| 2009 | 171 | 43 | 16 | 22 | 252 | BULL |
| 2010 | 68 | 104 | 64 | 16 | 252 | BEAR |
| 2011 | 55 | 61 | 92 | 44 | 252 | RANGE |
| 2012 | 110 | 56 | 84 | 0 | 250 | BULL |
| 2013 | 114 | 0 | 138 | 0 | 252 | RANGE |
| 2014 | 15 | 127 | 103 | 7 | 252 | BEAR |
| 2015 | 0 | 165 | 58 | 29 | 252 | BEAR |
| 2016 | 122 | 38 | 92 | 0 | 252 | BULL |
| 2017 | 169 | 5 | 77 | 0 | 251 | BULL |
| 2018 | 36 | 64 | 95 | 56 | 251 | RANGE |
| 2019 | 142 | 44 | 48 | 18 | 252 | BULL |
| 2020 | 140 | 16 | 54 | 43 | 253 | BULL |
| 2021 | 68 | 95 | 89 | 0 | 252 | BEAR |
| 2022 | 0 | 220 | 19 | 12 | 251 | BEAR |
| 2023 | 210 | 7 | 33 | 0 | 250 | BULL |
| 2024 | 136 | 49 | 67 | 0 | 252 | BULL |
| 2025 | 89 | 60 | 68 | 33 | 250 | BULL |
| 2026 | 42 | 14 | 55 | 0 | 111 | RANGE |

## Current regime
- **CLEAR_TREND_BULL** as of 2026-06-11 (regime_score 51, vol_score 37)
