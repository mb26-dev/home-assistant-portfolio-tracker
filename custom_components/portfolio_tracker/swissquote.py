"""Swissquote client for warrants, stocks, ETFs and funds.

Warrants are resolved by Swiss valor number (SIX / Swiss DOTS). Stocks, ETFs
and funds are resolved by ticker (Yahoo-style symbols are translated) or by
ISIN, so Swissquote can act as primary quote source with Yahoo as fallback.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as url_quote

from aiohttp import ClientError, ClientSession

from .models import Quote, StockHistoryPoint

# Current public search used by swissquote.ch itself (POST JSON, no session).
SEARCH_V2_URL = (
    "https://www.swissquote.ch/securities-search-plugin/api/search/allGrouped"
)
# Legacy quick search; since late 2026 it only answers with a session cookie.
SEARCH_URL = (
    "https://www.swissquote.ch/sqi_web_search/search/quicksearch/init/0/20"
)
FULL_QUOTE_URL = (
    "https://www.swissquote.ch/securities-retail-fullquote-plugin/api/"
    "fullQuote/{isin}/{exchange_id}_{currency}"
)
HISTORY_URL = (
    "https://www.swissquote.ch/securities-retail-chart-plugin/api/"
    "chart/trading-view/histo"
)
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Home-Assistant-Portfolio-Tracker/1.8.3.3",
}
VENUE_EXCHANGE_IDS = {"six": 4, "swiss_dots": 672}
# A failed security lookup is not repeated before this many seconds.
SECURITY_MISS_TTL = 6 * 3600
PENCE_CURRENCIES = {"GBX"}

# Yahoo suffix -> currencies a matching Swissquote listing may be quoted in.
YAHOO_SUFFIX_CURRENCIES: dict[str, tuple[str, ...]] = {
    "": ("USD",),
    "SW": ("CHF",),
    "VX": ("CHF",),
    "DE": ("EUR",),
    "F": ("EUR",),
    "MU": ("EUR",),
    "SG": ("EUR",),
    "HM": ("EUR",),
    "HA": ("EUR",),
    "BE": ("EUR",),
    "DU": ("EUR",),
    "PA": ("EUR",),
    "AS": ("EUR",),
    "BR": ("EUR",),
    "MI": ("EUR",),
    "MC": ("EUR",),
    "LS": ("EUR",),
    "VI": ("EUR",),
    "HE": ("EUR",),
    "IR": ("EUR",),
    "L": ("GBX", "GBP"),
    "IL": ("USD", "GBX", "GBP", "EUR"),
    "TO": ("CAD",),
    "V": ("CAD",),
    "AX": ("AUD",),
    "T": ("JPY",),
    "HK": ("HKD",),
    "ST": ("SEK",),
    "OL": ("NOK",),
    "CO": ("DKK",),
}

_LOGGER = logging.getLogger(__name__)


class SwissquoteError(Exception):
    """Base Swissquote client error."""


class SwissquoteWarrantNotFound(SwissquoteError):
    """Raised when Swissquote does not know the requested warrant."""


class SwissquoteSecurityNotFound(SwissquoteError):
    """Raised when no unambiguous Swissquote listing matches a security."""


class SwissquoteClient:
    """Resolve a Swiss valor number and normalize its public full quote."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._instruments: dict[tuple[str, str], dict] = {}
        self._securities: dict[tuple[str, str, int | None], dict] = {}
        self._security_misses: dict[tuple[str, str, int | None], float] = {}

    async def async_search(self, search_text: str) -> list[dict]:
        """Return normalized Swissquote search items.

        Each item has ``symbol``, ``isin``, ``exchangeId``, ``currency``,
        ``type``, ``name`` and ``market``. The current search endpoint is
        tried first, the legacy quick search only as fallback.
        """
        term = search_text.strip()
        try:
            payload = await self._async_post_json(
                SEARCH_V2_URL, params={}, body={"count": 100, "searchTerm": term}
            )
            return normalize_search_v2(payload)
        except SwissquoteError as v2_error:
            try:
                payload = await self._async_get_json(
                    SEARCH_URL, params={"searchText": term}
                )
            except SwissquoteError:
                raise v2_error from None
            items = payload.get("data")
            return (
                [item for item in items if isinstance(item, dict)]
                if isinstance(items, list)
                else []
            )

    async def async_get_security_quote(
        self,
        symbol: str,
        *,
        isin: str | None = None,
        exchange_id: int | None = None,
    ) -> Quote:
        """Fetch a stock, ETF or fund quote by ticker or ISIN."""
        item = await self.async_resolve_security(
            symbol, isin=isin, exchange_id=exchange_id
        )
        isin_value, exchange, currency = _stock_key(item)
        payload = await self._async_get_json(
            FULL_QUOTE_URL.format(
                isin=url_quote(isin_value, safe=""),
                exchange_id=exchange,
                currency=url_quote(currency, safe=""),
            ),
            params={"serviceName": "QUOTE"},
        )
        return parse_swissquote_quote(
            payload,
            symbol=symbol.strip().upper(),
            price_mode="state",
            default_type="EQUITY",
        )

    async def async_get_security_history(
        self,
        symbol: str,
        *,
        isin: str | None = None,
        exchange_id: int | None = None,
    ) -> tuple[StockHistoryPoint, ...]:
        """Fetch 30 days of daily history for a stock, ETF or fund."""
        item = await self.async_resolve_security(
            symbol, isin=isin, exchange_id=exchange_id
        )
        isin_value, exchange, currency = _stock_key(item)
        end = date.today()
        payload = await self._async_post_json(
            HISTORY_URL,
            params={
                "from": (end - timedelta(days=30)).isoformat(),
                "to": end.isoformat(),
                "countBack": "-1",
            },
            body={
                "isin": isin_value,
                "exchangeId": exchange,
                "currency": currency,
            },
        )
        history = parse_swissquote_history(payload)
        if _is_pence(currency):
            return tuple(_history_in_pounds(point) for point in history)
        return history

    async def async_resolve_security(
        self,
        symbol: str,
        *,
        isin: str | None = None,
        exchange_id: int | None = None,
    ) -> dict:
        """Resolve and cache the Swissquote listing for one security."""
        normalized_symbol = symbol.strip().upper()
        normalized_isin = (isin or "").strip().upper()
        cache_key = (normalized_symbol, normalized_isin, exchange_id)
        item = self._securities.get(cache_key)
        if item is not None:
            return item
        missed_at = self._security_misses.get(cache_key)
        if missed_at is not None and time.monotonic() - missed_at < SECURITY_MISS_TTL:
            raise SwissquoteSecurityNotFound(normalized_symbol)

        tickers, currencies = security_search_hints(normalized_symbol)
        search_terms = [normalized_isin] if normalized_isin else list(tickers)
        for search_term in search_terms:
            if not search_term:
                continue
            search_payload = {"data": await self.async_search(search_term)}
            item = find_security(
                search_payload,
                tickers=tickers,
                isin=normalized_isin or None,
                exchange_id=exchange_id,
                currencies=currencies,
            )
            if item is not None:
                _LOGGER.debug(
                    "Swissquote resolved %s to %s (exchange %s, %s)",
                    normalized_symbol,
                    item.get("isin"),
                    item.get("exchangeId"),
                    item.get("currency"),
                )
                self._securities[cache_key] = item
                self._security_misses.pop(cache_key, None)
                return item
        self._security_misses[cache_key] = time.monotonic()
        raise SwissquoteSecurityNotFound(normalized_symbol)

    async def async_get_quote(
        self,
        valor: str,
        *,
        price_mode: str = "state",
        trading_venue: str | None = None,
        symbol: str | None = None,
    ) -> Quote:
        """Fetch one warrant by exact Swiss valor number."""
        normalized_valor, item = await self._async_resolve_instrument(
            valor, trading_venue
        )

        isin = str(item.get("isin") or "").strip().upper()
        currency = str(item.get("currency") or "CHF").strip().upper()
        exchange_id = _integer(item.get("exchangeId"))
        if not isin or exchange_id is None:
            raise SwissquoteError(
                f"Swissquote returned incomplete identifiers for {normalized_valor}"
            )

        payload = await self._async_get_json(
            FULL_QUOTE_URL.format(
                isin=url_quote(isin, safe=""),
                exchange_id=exchange_id,
                currency=url_quote(currency, safe=""),
            ),
            params={"serviceName": "QUOTE"},
        )
        return parse_swissquote_quote(
            payload,
            symbol=(symbol or normalized_valor).strip().upper(),
            price_mode=price_mode,
        )

    async def async_get_history(
        self,
        valor: str,
        *,
        trading_venue: str | None = None,
    ) -> tuple[StockHistoryPoint, ...]:
        """Fetch the most recent 30 calendar days of daily warrant history."""
        _, item = await self._async_resolve_instrument(valor, trading_venue)
        isin = str(item.get("isin") or "").strip().upper()
        currency = str(item.get("currency") or "CHF").strip().upper()
        exchange_id = _integer(item.get("exchangeId"))
        if not isin or exchange_id is None:
            raise SwissquoteError("Swissquote returned incomplete history identifiers")

        end = date.today()
        payload = await self._async_post_json(
            HISTORY_URL,
            params={
                "from": (end - timedelta(days=30)).isoformat(),
                "to": end.isoformat(),
                "countBack": "-1",
            },
            body={
                "isin": isin,
                "exchangeId": exchange_id,
                "currency": currency,
            },
        )
        return parse_swissquote_history(payload)

    async def _async_resolve_instrument(
        self, valor: str, trading_venue: str | None
    ) -> tuple[str, dict]:
        """Resolve and cache the Swissquote stock key for one valor number."""
        normalized_valor = _normalize_valor(valor)
        cache_key = (normalized_valor, trading_venue or "")
        item = self._instruments.get(cache_key)
        if item is not None:
            return normalized_valor, item

        expected_exchange_id = VENUE_EXCHANGE_IDS.get(trading_venue or "")
        search_terms = (
            [_swiss_isin_for_valor(normalized_valor)]
            if trading_venue == "six"
            else [normalized_valor]
        )
        if trading_venue not in VENUE_EXCHANGE_IDS:
            search_terms.append(_swiss_isin_for_valor(normalized_valor))
        for search_term in search_terms:
            search_payload = {"data": await self.async_search(search_term)}
            item = _find_warrant(
                search_payload,
                normalized_valor,
                expected_exchange_id,
            )
            if item is not None:
                self._instruments[cache_key] = item
                return normalized_valor, item
        raise SwissquoteWarrantNotFound(normalized_valor)

    async def _async_get_json(self, url: str, *, params: dict[str, str]) -> dict:
        return await self._async_request_json("GET", url, params=params)

    async def _async_post_json(
        self, url: str, *, params: dict[str, str], body: dict
    ) -> dict:
        return await self._async_request_json("POST", url, params=params, body=body)

    async def _async_request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str],
        body: dict | None = None,
    ) -> dict:
        try:
            async with self._session.request(
                method,
                url,
                params=params,
                json=body,
                headers=HEADERS,
                timeout=20,
            ) as response:
                response.raise_for_status()
                text = await response.text()
                try:
                    payload = json.loads(text)
                except ValueError as err:
                    raise SwissquoteError(
                        "Swissquote answered with "
                        f"{response.headers.get('Content-Type', 'unknown')} "
                        "instead of JSON"
                    ) from err
        except (ClientError, TimeoutError, ValueError) as err:
            raise SwissquoteError(f"Could not load Swissquote data: {err}") from err
        if not isinstance(payload, dict):
            raise SwissquoteError("Unexpected Swissquote response")
        return payload


def parse_swissquote_quote(
    payload: dict,
    *,
    symbol: str,
    price_mode: str = "state",
    default_type: str = "WARRANT",
) -> Quote:
    """Normalize one Swissquote full-quote response."""
    data = payload.get("fullQuoteData")
    if not isinstance(data, dict):
        raise SwissquoteError("Swissquote returned no full quote")
    raw_quote = data.get("quote")
    stock_key = data.get("stockKey")
    if not isinstance(raw_quote, dict) or not isinstance(stock_key, dict):
        raise SwissquoteError("Swissquote returned an incomplete full quote")

    bid = _price_amount(raw_quote.get("bidPrice"), positive=True)
    ask = _price_amount(raw_quote.get("askPrice"), positive=True)
    last = _price_amount(raw_quote.get("lastPrice"))
    midpoint = (
        (bid + ask) / Decimal(2)
        if bid is not None and ask is not None
        else _price_amount(raw_quote.get("midPrice"), positive=True)
    )
    selected = {
        "bid": bid,
        "ask": ask,
        "midpoint": midpoint,
    }.get(price_mode)
    if price_mode == "state":
        selected = last
    if selected is None:
        selected = next(
            (value for value in (last, midpoint, bid, ask) if value is not None),
            None,
        )
    if selected is None:
        raise SwissquoteError(f"Swissquote returned no usable price for {symbol}")

    selected_price = {
        "bid": raw_quote.get("bidPrice"),
        "ask": raw_quote.get("askPrice"),
        "state": raw_quote.get("lastPrice"),
    }.get(price_mode)
    timestamp = _price_timestamp(selected_price)
    if timestamp is None:
        timestamp = _price_timestamp(raw_quote.get("lastPrice"))

    currency = str(stock_key.get("currency") or "CHF").upper()
    previous_close = _price_amount(raw_quote.get("closePrice"))
    if _is_pence(currency):
        # London listings are quoted in pence; normalize to pounds.
        selected, previous_close, bid, ask = (
            _pence_to_pounds(value)
            for value in (selected, previous_close, bid, ask)
        )
        currency = "GBP"

    return Quote(
        symbol=symbol,
        name=str(data.get("productName") or symbol).strip(),
        price=selected,
        previous_close=previous_close,
        currency=currency,
        exchange=str(data.get("stockExchangeName") or "Swissquote"),
        instrument_type=str(data.get("productType") or default_type),
        timestamp=timestamp,
        bid=bid,
        ask=ask,
    )


def parse_swissquote_history(payload: dict) -> tuple[StockHistoryPoint, ...]:
    """Normalize Swissquote daily chart points for the existing detail card."""
    items = payload.get("productInfo")
    if not isinstance(items, list):
        raise SwissquoteError("Swissquote returned no warrant history")
    points: list[StockHistoryPoint] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        timestamp = _timestamp(item.get("time"))
        close = _decimal_value(item.get("close"))
        if timestamp is None or close is None:
            continue
        points.append(
            StockHistoryPoint(
                timestamp=timestamp,
                open=_decimal_value(item.get("open")),
                high=_decimal_value(item.get("high")),
                low=_decimal_value(item.get("low")),
                close=close,
                volume=_integer(item.get("volume")),
            )
        )
    return tuple(sorted(points, key=lambda point: point.timestamp))


def _find_warrant(
    payload: dict,
    valor: str,
    expected_exchange_id: int | None,
) -> dict | None:
    items = payload.get("data")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip()
        isin = str(item.get("isin", "")).strip().upper()
        swiss_isin_matches = (
            isin.startswith("CH")
            and isin[2:11].lstrip("0") == valor.lstrip("0")
        )
        if symbol != valor and not swiss_isin_matches:
            continue
        exchange_id = _integer(item.get("exchangeId"))
        if expected_exchange_id is not None and exchange_id != expected_exchange_id:
            continue
        return item
    return None


def security_search_hints(symbol: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Translate a Yahoo-style symbol into Swissquote tickers and currencies.

    ``NOVN.SW`` -> (("NOVN",), ("CHF",)); ``BRK-B`` -> (("BRK-B", "BRK.B",
    "BRK/B", "BRK B", "BRKB"), ("USD",)). An unknown suffix yields no currency
    hint, which means only an explicit ISIN mapping can resolve the symbol.
    """
    normalized = symbol.strip().upper()
    base, suffix = normalized, ""
    if "." in normalized:
        head, tail = normalized.rsplit(".", 1)
        if tail in YAHOO_SUFFIX_CURRENCIES and tail:
            base, suffix = head, tail
    currencies = YAHOO_SUFFIX_CURRENCIES.get(suffix, ())
    if suffix == "" and "." in normalized:
        currencies = ()
    variants = [base]
    for separator in ("-", "."):
        if separator in base:
            for replacement in (".", "/", " ", "", "-"):
                variant = base.replace(separator, replacement)
                if variant not in variants:
                    variants.append(variant)
    return tuple(variants), tuple(currencies)


def find_security(
    payload: dict,
    *,
    tickers: tuple[str, ...],
    isin: str | None,
    exchange_id: int | None,
    currencies: tuple[str, ...],
) -> dict | None:
    """Pick the Swissquote listing for a security from search results.

    With an ISIN the listing must carry that ISIN; the preferred currency only
    ranks listings. Without an ISIN the ticker must match exactly and the
    listing currency must fit the Yahoo exchange suffix, because tickers are
    not unique across exchanges.
    """
    items = payload.get("data")
    if not isinstance(items, list):
        return None
    wanted_tickers = {ticker.upper() for ticker in tickers}
    candidates: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_isin = str(item.get("isin", "")).strip().upper()
        item_symbol = str(item.get("symbol", "")).strip().upper()
        item_currency = str(item.get("currency", "")).strip().upper()
        item_exchange = _integer(item.get("exchangeId"))
        if not item_isin or item_exchange is None:
            continue
        if exchange_id is not None and item_exchange != exchange_id:
            continue
        if isin:
            if item_isin != isin:
                continue
        else:
            if item_symbol not in wanted_tickers:
                continue
            if not currencies or item_currency not in currencies:
                continue
        candidates.append(item)
    if not candidates:
        return None
    if not isin and len(
        {str(item.get("isin", "")).strip().upper() for item in candidates}
    ) > 1:
        # Same ticker and currency for different products (e.g. WINC is both
        # an iShares UCITS ETF in USD and a US ETF): require an ISIN mapping.
        return None

    def rank(item: dict) -> tuple[int, int]:
        currency = str(item.get("currency", "")).strip().upper()
        symbol_value = str(item.get("symbol", "")).strip().upper()
        currency_rank = (
            currencies.index(currency) if currency in currencies else len(currencies)
        )
        symbol_rank = 0 if symbol_value in wanted_tickers else 1
        return (currency_rank, symbol_rank)

    return min(candidates, key=rank)


def normalize_search_v2(payload: dict) -> list[dict]:
    """Flatten the current Swissquote search answer into simple listings."""
    securities = payload.get("securities")
    if not isinstance(securities, list):
        raise SwissquoteError("Swissquote search returned no securities")
    items: list[dict] = []

    def walk(entries) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = entry.get("stockKey")
            if isinstance(key, dict) and key.get("isin"):
                items.append(
                    {
                        "symbol": str(entry.get("symbol") or ""),
                        "isin": str(key.get("isin") or ""),
                        "exchangeId": key.get("exchangeId"),
                        "currency": str(key.get("currency") or ""),
                        "type": str(entry.get("type") or ""),
                        "name": str(entry.get("name") or ""),
                        "market": str(entry.get("market") or ""),
                    }
                )
            children = entry.get("children")
            if isinstance(children, list):
                walk(children)

    walk(securities)
    return items


def _stock_key(item: dict) -> tuple[str, int, str]:
    isin = str(item.get("isin") or "").strip().upper()
    currency = str(item.get("currency") or "").strip().upper()
    exchange_id = _integer(item.get("exchangeId"))
    if not isin or exchange_id is None or not currency:
        raise SwissquoteError("Swissquote returned incomplete identifiers")
    return isin, exchange_id, currency


def _is_pence(currency: str) -> bool:
    return currency.upper() in PENCE_CURRENCIES


def _pence_to_pounds(value: Decimal | None) -> Decimal | None:
    return None if value is None else value / Decimal(100)


def _history_in_pounds(point: StockHistoryPoint) -> StockHistoryPoint:
    return StockHistoryPoint(
        timestamp=point.timestamp,
        open=_pence_to_pounds(point.open),
        high=_pence_to_pounds(point.high),
        low=_pence_to_pounds(point.low),
        close=_pence_to_pounds(point.close),
        volume=point.volume,
    )


def _normalize_valor(value: str) -> str:
    normalized = value.replace("'", "").replace("\u2019", "").replace(" ", "")
    if not normalized.isdigit() or len(normalized) > 9:
        raise SwissquoteWarrantNotFound(value)
    return normalized


def _swiss_isin_for_valor(valor: str) -> str:
    """Return the Swiss ISIN whose national identifier is the valor number."""
    padded_valor = valor.zfill(9)
    numeric_base = f"1217{padded_valor}"
    digits = [int(character) for character in f"{numeric_base}0"]
    checksum_total = 0
    for index, digit in enumerate(reversed(digits)):
        doubled = digit * 2 if index % 2 else digit
        checksum_total += doubled // 10 + doubled % 10
    check_digit = (10 - checksum_total % 10) % 10
    return f"CH{padded_valor}{check_digit}"


def _price_amount(value, *, positive: bool = False) -> Decimal | None:
    if not isinstance(value, dict) or str(value.get("status", "")) == "UNAVAILABLE":
        return None
    raw_amount = value.get("amount")
    if raw_amount is None:
        return None
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        return None
    return amount


def _price_timestamp(value) -> datetime | None:
    if not isinstance(value, dict) or not value.get("timestamp"):
        return None
    return _timestamp(value["timestamp"])


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _decimal_value(value) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
