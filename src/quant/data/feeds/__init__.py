from quant.data.feeds.base import DataFeed


def get_feed(timeframe: str = "1d") -> DataFeed:
    """Config-driven feed factory: the registry decides which vendor serves a
    timeframe by default (yfinance for keyless daily research, Alpaca for
    intraday). Callers that want a specific feed still construct it directly."""
    from quant.data.timeframes import get_timeframe

    if get_timeframe(timeframe).default_feed == "alpaca":
        from quant.data.feeds.alpaca_feed import AlpacaFeed
        return AlpacaFeed()
    from quant.data.feeds.yfinance_feed import YFinanceFeed
    return YFinanceFeed()


__all__ = ["DataFeed", "get_feed"]
