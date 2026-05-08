"""Offline ``phone`` Identifier collector backed by ``libphonenumber``.

Unlike the GitHub / WHOIS / web-profile collectors this one is **offline** —
``phonenumbers`` ships its own metadata database, so we never go to the
network. The Trace it emits is deterministic for any given input string,
which makes it a clean fit for Reckora's evidence chain (the SHA-256 hash
of the canonicalised normalisation result is still recorded).

The collector exposes the high-signal fields a correlation engine actually
wants:

- ``e164`` — canonical international form (``+62 812 ...`` -> ``+62812...``)
- ``country_code`` — numeric calling code (e.g. ``62``)
- ``country_iso`` — two-letter ISO 3166-1 alpha-2 region (e.g. ``ID``)
- ``country_name`` — geocoded country name (English; best-effort)
- ``region`` — sub-national region (e.g. city / province), best-effort
- ``carrier_name`` — operator at the time of porting / first issuance
- ``line_type`` — ``mobile`` / ``fixed_line`` / ``voip`` / ``toll_free`` /
  ``unknown`` (mapped from ``phonenumbers.PhoneNumberType``)
- ``is_valid`` — strict validity check
- ``is_possible`` — looser sanity check (length / leading digits)

Inputs are accepted in either national (``08123456789`` for ID) or
international (``+628123456789``) form. National-only inputs require a
``default_region`` (defaults to ``"US"``); when parsing fails we return an
empty list so the orchestrator records the failure as "no traces from this
collector" rather than aborting the investigation.
"""

from __future__ import annotations

from typing import ClassVar

import phonenumbers
from phonenumbers import (
    NumberParseException,
    PhoneNumberFormat,
    PhoneNumberType,
    region_code_for_number,
)
from phonenumbers import (
    carrier as pn_carrier,
)
from phonenumbers import (
    geocoder as pn_geocoder,
)

from ..evidence.chain import make_evidence
from ..models.entity import Identifier, Trace
from ..models.enums import IdentifierType, TraceSource
from .base import Collector

PHONE_SOURCE_URL_PREFIX = "phone://libphonenumber/"

_LINE_TYPE_BY_ENUM: dict[int, str] = {
    PhoneNumberType.MOBILE: "mobile",
    PhoneNumberType.FIXED_LINE: "fixed_line",
    PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
    PhoneNumberType.TOLL_FREE: "toll_free",
    PhoneNumberType.PREMIUM_RATE: "premium_rate",
    PhoneNumberType.SHARED_COST: "shared_cost",
    PhoneNumberType.VOIP: "voip",
    PhoneNumberType.PERSONAL_NUMBER: "personal_number",
    PhoneNumberType.PAGER: "pager",
    PhoneNumberType.UAN: "uan",
    PhoneNumberType.VOICEMAIL: "voicemail",
    PhoneNumberType.UNKNOWN: "unknown",
}


def _line_type_label(number: phonenumbers.PhoneNumber) -> str:
    """Map ``phonenumbers.PhoneNumberType`` enum to a stable string."""
    return _LINE_TYPE_BY_ENUM.get(phonenumbers.number_type(number), "unknown")


class PhoneCollector(Collector):
    """Offline normaliser for ``phone`` identifiers.

    Parameters
    ----------
    default_region:
        Two-letter ISO 3166-1 alpha-2 region used when the input string has
        no leading ``+`` (so it cannot be parsed unambiguously). Defaults to
        ``"US"`` to match the ``libphonenumber`` upstream convention.
    """

    name: ClassVar[str] = "phone_libphonenumber"
    supported: ClassVar[frozenset[str]] = frozenset({IdentifierType.PHONE.value})

    def __init__(self, *, default_region: str = "US") -> None:
        # No HTTP client needed — pass None to the base class.
        super().__init__(client=None)
        self._default_region = default_region

    async def collect(self, identifier: Identifier) -> list[Trace]:
        if not self.supports(identifier):
            return []
        try:
            number = phonenumbers.parse(identifier.value, self._default_region)
        except NumberParseException:
            return []

        is_valid = phonenumbers.is_valid_number(number)
        is_possible = phonenumbers.is_possible_number(number)
        e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)
        country_iso = region_code_for_number(number)
        country_name = pn_geocoder.country_name_for_number(number, "en") or None
        region_label = pn_geocoder.description_for_number(number, "en") or None
        carrier_name = pn_carrier.name_for_number(number, "en") or None
        line_type = _line_type_label(number)

        # Deduplicated region/country: ``description_for_number`` falls back
        # to country name when no finer region info exists, which would just
        # be redundant noise in the dossier.
        if region_label and country_name and region_label == country_name:
            region_label = None

        fields: dict[str, object | None] = {
            "e164": e164 if is_possible else None,
            "country_code": number.country_code,
            "country_iso": country_iso if country_iso else None,
            "country_name": country_name,
            "region": region_label,
            "carrier_name": carrier_name,
            "line_type": line_type,
            "is_valid": is_valid,
            "is_possible": is_possible,
        }

        # Synthesize a stable, non-network "source URL" for the evidence
        # chain. The hash is still computed over the canonicalised payload,
        # so any drift in ``libphonenumber`` metadata is detectable.
        normalised_key = e164 if is_possible else identifier.value
        source_url = f"{PHONE_SOURCE_URL_PREFIX}{normalised_key}"
        evidence = make_evidence(source_url, fields, keep_raw=False)
        return [
            Trace(
                identifier=identifier,
                source=TraceSource.PHONE_LIBPHONENUMBER,
                fields=fields,
                evidence=evidence,
            ),
        ]
