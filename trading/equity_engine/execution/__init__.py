"""
Execution layer — broker integration and risk controls.

Components:
  - ib_bridge.py:      ib_insync connection to IB Gateway on NAS
  - risk_controller.py: Hard safety rails (max risk, PDT, loss limits)
  - state_tracker.py:   Active position map, re-entry prevention, state persistence
"""