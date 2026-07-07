"""Operations layer — the backbone that makes unattended running safe:

  * notify    — alerting (Telegram / log)
  * reconcile — broker book vs the journal (fail-safe pre-trade check)
  * oms       — order lifecycle state machine (SUBMITTED -> FILLED/...)
  * tca       — transaction cost analysis (intended vs actual fill, slippage)
  * health    — heartbeats + missed-run detection
  * drift     — backtest-expected vs live-actual decision agreement
  * report    — the end-of-day snapshot that folds all of the above together
"""
