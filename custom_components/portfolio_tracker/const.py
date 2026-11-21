"""Constants for Portfolio Tracker."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "portfolio_tracker"
PLATFORMS: Final = ["sensor", "binary_sensor"]
FRONTEND_VERSION: Final = "1.8.3.3"
FRONTEND_URL: Final = "/portfolio_tracker/frontend"

CONF_PORTFOLIO_NAME: Final = "portfolio_name"
CONF_BASE_CURRENCY: Final = "base_currency"
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_HOLDINGS: Final = "holdings"
CONF_HOLDING_ID: Final = "holding_id"
CONF_SYMBOL: Final = "symbol"
CONF_NAME: Final = "name"
CONF_ASSET_TYPE: Final = "asset_type"
CONF_QUANTITY: Final = "quantity"
CONF_AVERAGE_PRICE: Final = "average_price"
CONF_COINGECKO_ID: Final = "coingecko_id"
CONF_DISPLAY_CURRENCY: Final = "display_currency"
CONF_TRANSACTIONS: Final = "transactions"
CONF_INDEXES: Final = "indexes"
CONF_INDEX_SYMBOL: Final = "index_symbol"
CONF_BENCHMARK_INDEX: Final = "benchmark_index"
CONF_TRANSACTION_ID: Final = "transaction_id"
CONF_TRANSACTION_TYPE: Final = "transaction_type"
CONF_TRANSACTION_DATE: Final = "transaction_date"
CONF_TRANSACTION_PRICE: Final = "transaction_price"
CONF_TRANSACTION_PRICE_MODE: Final = "transaction_price_mode"
CONF_TRANSACTION_MARKET_PRICE: Final = "transaction_market_price"
CONF_TRANSACTION_MARKET_CURRENCY: Final = "transaction_market_currency"
CONF_TRANSACTION_CURRENCY: Final = "transaction_currency"
CONF_TRANSACTION_FEES: Final = "transaction_fees"
CONF_TRANSACTION_TAXES: Final = "transaction_taxes"
CONF_TRANSACTION_EXCHANGE: Final = "transaction_exchange"
CONF_TRANSACTION_NOTE: Final = "transaction_note"
CONF_WATCH_ONLY: Final = "watch_only"
CONF_ALERT_ABOVE: Final = "alert_above"
CONF_ALERT_BELOW: Final = "alert_below"
CONF_NOTIFY_TARGETS: Final = "notify_targets"
CONF_PERSISTENT_NOTIFICATIONS: Final = "persistent_notifications"
CONF_PRICE_SOURCE: Final = "price_source"
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_PRICE_CURRENCY: Final = "price_currency"
CONF_PRICE_MODE: Final = "price_mode"
CONF_ISIN: Final = "isin"
CONF_WKN: Final = "wkn"
CONF_VALOR: Final = "valor"
CONF_ISSUER: Final = "issuer"
CONF_UNDERLYING: Final = "underlying"
CONF_WARRANT_TYPE: Final = "warrant_type"
CONF_STRIKE: Final = "strike"
CONF_RATIO: Final = "ratio"
CONF_EXPIRY: Final = "expiry"
CONF_TRADING_VENUE: Final = "trading_venue"
CONF_SWISSQUOTE_PRIMARY: Final = "swissquote_primary"
CONF_SWISSQUOTE_MAPPINGS: Final = "swissquote_mappings"

DEFAULT_BASE_CURRENCY: Final = "CHF"
DEFAULT_UPDATE_INTERVAL: Final = 15
MIN_UPDATE_INTERVAL: Final = 5
DEFAULT_PERSISTENT_NOTIFICATIONS: Final = True
DEFAULT_SWISSQUOTE_PRIMARY: Final = True
# Asset types that are quoted by Swissquote first when the option is enabled.
SWISSQUOTE_PRIMARY_ASSET_TYPES: Final = {"stock", "etf", "fund", "other"}

ASSET_TYPES: Final = ["stock", "etf", "crypto", "fund", "currency", "warrant", "other"]
STANDARD_ASSET_TYPES: Final = [item for item in ASSET_TYPES if item != "warrant"]
PRICE_SOURCES: Final = ["swissquote", "yahoo", "entity"]
PRICE_MODES: Final = ["state", "bid", "ask", "midpoint"]
WARRANT_TYPES: Final = ["call", "put"]
WARRANT_VENUES: Final = ["six", "swiss_dots", "other"]
CRYPTO_DISPLAY_CURRENCIES: Final = ["CHF", "EUR", "USD", "GBP", "JPY", "CAD", "AUD"]
TRANSACTION_TYPES: Final = ["opening", "buy", "sell"]
TRANSACTION_PRICE_MODES: Final = ["per_unit", "total"]
INDEX_PRESETS: Final = {
    "^SSMI": "SMI - Swiss Market Index",
    "^GDAXI": "DAX - Deutscher Aktienindex",
    "^DJI": "Dow Jones Industrial Average",
    "^GSPC": "S&P 500",
    "^IXIC": "NASDAQ Composite",
    "^STOXX50E": "EURO STOXX 50",
}
DEFAULT_INDEXES: Final = ["^SSMI", "^GDAXI", "^DJI", "^GSPC"]
DEFAULT_BENCHMARK_INDEX: Final = "^SSMI"

# Existing portfolios can use common Yahoo crypto symbols without being edited.
# A manually configured CoinGecko ID always takes precedence.
COINGECKO_IDS_BY_SYMBOL: Final = {
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "BCH": "bitcoin-cash",
    "BNB": "binancecoin",
    "BTC": "bitcoin",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "ETH": "ethereum",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "SHIB": "shiba-inu",
    "SOL": "solana",
    "TRX": "tron",
    "USDC": "usd-coin",
    "USDT": "tether",
    "XLM": "stellar",
    "XRP": "ripple",
}


def coingecko_id_for_holding(holding) -> str | None:
    """Return the explicit or well-known CoinGecko coin ID."""
    explicit = str(holding.get(CONF_COINGECKO_ID, "")).strip().lower()
    if explicit:
        return explicit
    ticker = str(holding.get(CONF_SYMBOL, "")).split("-", 1)[0].upper()
    return COINGECKO_IDS_BY_SYMBOL.get(ticker)


def crypto_currency_for_holding(holding, base_currency: str) -> str:
    """Return a crypto position's configured display currency."""
    configured = str(holding.get(CONF_DISPLAY_CURRENCY, "")).strip().upper()
    if len(configured) == 3 and configured.isascii() and configured.isalpha():
        return configured
    return base_currency.upper()


def crypto_data_key(coin_id: str, currency: str) -> str:
    """Return the coordinator key for one coin/currency combination."""
    return f"{coin_id}:{currency.upper()}"


def benchmark_index_for_entry(entry) -> str:
    """Return the selected benchmark, including an explicit no-benchmark choice."""
    indexes = list(entry_value(entry, CONF_INDEXES, DEFAULT_INDEXES))
    if CONF_BENCHMARK_INDEX in entry.options:
        configured = str(entry.options[CONF_BENCHMARK_INDEX]).upper()
    elif CONF_BENCHMARK_INDEX in entry.data:
        configured = str(entry.data[CONF_BENCHMARK_INDEX]).upper()
    else:
        configured = DEFAULT_BENCHMARK_INDEX
    if not configured:
        return ""
    if configured in indexes:
        return configured
    return indexes[0] if indexes else ""


class SwissquoteMappingError(ValueError):
    """Raised for an invalid Swissquote mapping line."""


def parse_swissquote_mappings(text) -> dict[str, tuple[str | None, int | None]]:
    """Parse ``SYMBOL = ISIN[@EXCHANGE_ID]`` lines.

    ``AAPL = US0378331005`` pins a listing by ISIN, ``NOVN.SW = @4`` keeps the
    ticker lookup but forces a Swissquote exchange ID, and
    ``AAPL = US0378331005@1234`` does both. Empty lines and ``#`` comments are
    ignored.
    """
    mappings: dict[str, tuple[str | None, int | None]] = {}
    for line_number, raw_line in enumerate(str(text or "").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise SwissquoteMappingError(f"line {line_number}: missing '='")
        symbol, target = (part.strip() for part in line.split("=", 1))
        symbol = symbol.upper()
        if not symbol or len(symbol) > 80:
            raise SwissquoteMappingError(f"line {line_number}: missing symbol")
        isin_part, _, exchange_part = target.partition("@")
        isin = isin_part.strip().upper() or None
        if isin is not None and (
            len(isin) != 12 or not isin[:2].isalpha() or not isin.isalnum()
        ):
            raise SwissquoteMappingError(f"line {line_number}: invalid ISIN")
        exchange_id: int | None = None
        if exchange_part.strip():
            try:
                exchange_id = int(exchange_part.strip())
            except ValueError as err:
                raise SwissquoteMappingError(
                    f"line {line_number}: invalid exchange ID"
                ) from err
        if isin is None and exchange_id is None:
            raise SwissquoteMappingError(f"line {line_number}: empty mapping")
        mappings[symbol] = (isin, exchange_id)
    return mappings


def safe_swissquote_mappings(text) -> dict[str, tuple[str | None, int | None]]:
    """Parse mappings, ignoring the whole block if it is invalid."""
    try:
        return parse_swissquote_mappings(text)
    except SwissquoteMappingError:
        return {}


def entry_value(entry, key: str, default=None):
    """Return an option, falling back to config-entry data."""
    return entry.options.get(key, entry.data.get(key, default))
