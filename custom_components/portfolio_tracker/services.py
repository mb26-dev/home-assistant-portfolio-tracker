"""Diagnostic actions for the Swissquote quote source."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .swissquote import (
    SwissquoteClient,
    SwissquoteError,
    SwissquoteSecurityNotFound,
    find_security,
    security_search_hints,
)

SERVICE_SWISSQUOTE_LOOKUP = "swissquote_lookup"
_RAW_FIELDS = ("symbol", "isin", "exchangeId", "currency", "type", "name", "market")

LOOKUP_SCHEMA = vol.Schema(
    {
        vol.Required("symbol"): vol.All(str, vol.Length(min=1, max=80)),
        vol.Optional("isin", default=""): str,
        vol.Optional("exchange_id"): vol.Coerce(int),
    }
)


def _compact(item: dict[str, Any]) -> dict[str, Any]:
    compact = {key: item.get(key) for key in _RAW_FIELDS if key in item}
    for key, value in item.items():
        if key not in compact and isinstance(value, (str, int, float)):
            compact[key] = value
    return compact


async def _async_lookup(call: ServiceCall) -> ServiceResponse:
    symbol = str(call.data["symbol"]).strip().upper()
    isin = str(call.data.get("isin") or "").strip().upper() or None
    exchange_id = call.data.get("exchange_id")
    client = SwissquoteClient(async_get_clientsession(call.hass))
    tickers, currencies = security_search_hints(symbol)
    search_terms = [isin] if isin else list(tickers)

    results: dict[str, list[dict[str, Any]]] = {}
    try:
        for term in search_terms:
            results[term] = [_compact(item) for item in await client.async_search(term)]
    except SwissquoteError as err:
        raise HomeAssistantError(f"Swissquote search failed: {err}") from err

    selected = None
    for term in search_terms:
        selected = find_security(
            {"data": results.get(term, [])},
            tickers=tickers,
            isin=isin,
            exchange_id=exchange_id,
            currencies=currencies,
        )
        if selected is not None:
            break

    quote: dict[str, Any] | None = None
    quote_error: str | None = None
    if selected is not None:
        try:
            result = await client.async_get_security_quote(
                symbol, isin=isin, exchange_id=exchange_id
            )
        except SwissquoteSecurityNotFound:
            quote_error = "not found"
        except SwissquoteError as err:
            quote_error = str(err)
        else:
            quote = {
                "name": result.name,
                "price": float(result.price),
                "previous_close": (
                    float(result.previous_close)
                    if result.previous_close is not None
                    else None
                ),
                "currency": result.currency,
                "exchange": result.exchange,
                "instrument_type": result.instrument_type,
                "timestamp": (
                    result.timestamp.isoformat() if result.timestamp else None
                ),
            }

    return {
        "symbol": symbol,
        "search_terms": search_terms,
        "expected_currencies": list(currencies),
        "selected": selected and _compact(selected),
        "quote": quote,
        "quote_error": quote_error,
        "search_results": results,
    }


def async_register_services(hass: HomeAssistant) -> None:
    """Register integration-wide diagnostic actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_SWISSQUOTE_LOOKUP):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_SWISSQUOTE_LOOKUP,
        _async_lookup,
        schema=LOOKUP_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
