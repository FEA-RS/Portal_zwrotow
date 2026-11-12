# qualify_return nie dotyka bazy, sieci ani zegara (Wariant B, zob.
# stan_projektu.md) - stąd testy bez mocków, tylko dane wejściowe + assert.

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.graph.rules import (
    Decision,
    FailedRule,
    ReturnReason,
    ShippingStatus,
    qualify_return,
)

# Ta sama klasa i strefa co WARSAW_TZ w rules.py - inaczej testy liczyłyby
# czas inną logiką kalendarzową niż produkcyjny kod.
WARSAW = ZoneInfo("Europe/Warsaw")


def test_all_conditions_met_returns_auto_approve() -> None:
    delivered_at = datetime(2026, 1, 5, 10, 0, tzinfo=WARSAW)
    ticket_created_at = datetime(2026, 1, 8, 10, 0, tzinfo=WARSAW)

    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=delivered_at,
        ticket_created_at=ticket_created_at,
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.AUTO_APPROVE
    assert result.failed_rules == []


def test_calendar_day_14_still_within_window() -> None:
    # Granica: dokładnie 14 dni kalendarzowych w Warszawie (§1).
    delivered_at = datetime(2026, 3, 1, 23, 0, tzinfo=WARSAW)
    ticket_created_at = datetime(2026, 3, 15, 0, 30, tzinfo=WARSAW)

    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=delivered_at,
        ticket_created_at=ticket_created_at,
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.AUTO_APPROVE
    assert FailedRule.WITHIN_14_DAYS not in result.failed_rules


def test_calendar_day_15_fails_window_rule() -> None:
    # Chroni przed liczeniem .days na timedelcie (24h okresy) zamiast dni
    # kalendarzowych - dostawa wieczorem "dnia 0", zgłoszenie po północy
    # "dnia 15" to zegarowo <15*24h, ale kalendarzowo już 15. dzień.
    delivered_at = datetime(2026, 3, 1, 18, 0, tzinfo=WARSAW)
    ticket_created_at = datetime(2026, 3, 16, 0, 30, tzinfo=WARSAW)

    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=delivered_at,
        ticket_created_at=ticket_created_at,
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.WITHIN_14_DAYS]


def test_utc_timestamps_use_warsaw_calendar_day_not_raw_utc_day() -> None:
    # Daty w UTC (jak kolumny w bazie) dobrane tak, by dzień kalendarzowy w
    # Warszawie różnił się od dnia w UTC - sprawdza, że rules.py faktycznie
    # konwertuje do Warszawy, a nie liczy .date() na surowym UTC.
    delivered_at = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    ticket_created_at = datetime(2026, 11, 24, 23, 30, tzinfo=UTC)

    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=delivered_at,
        ticket_created_at=ticket_created_at,
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.WITHIN_14_DAYS]


def test_dst_summer_offset_still_uses_warsaw_calendar_day() -> None:
    # Jak wyżej, ale w lecie (CEST, UTC+2) - sprawdza, że offset do Warszawy
    # liczy się dynamicznie, nie jest zahardkodowany na stałe +1h.
    delivered_at = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
    ticket_created_at = datetime(2026, 7, 15, 22, 30, tzinfo=UTC)

    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=delivered_at,
        ticket_created_at=ticket_created_at,
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.WITHIN_14_DAYS]


def test_delivered_status_without_delivery_date_fails_window_rule() -> None:
    # Chroni przed tym, że DELIVERED + delivered_at=None (kombinacja, której
    # CHECK w bazie nie powinien dopuszczać) maskuje brak daty i daje
    # AUTO_APPROVE tylko dlatego, że status się zgadza.
    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=None,
        ticket_created_at=datetime(2026, 1, 1, tzinfo=WARSAW),
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.WITHIN_14_DAYS]


def test_not_yet_delivered_fails_both_window_and_delivery_rules() -> None:
    # Świadoma decyzja projektowa: dla SENT/UNSENT + delivered_at=None
    # pojawiają się obie reguły naraz, mimo że termin formalnie jeszcze się
    # nie zaczął. Ten test jest specyfikacją tej decyzji, nie przypadkiem.
    result = qualify_return(
        shipping_status=ShippingStatus.SENT,
        delivered_at=None,
        ticket_created_at=datetime(2026, 1, 1, tzinfo=WARSAW),
        return_amount=Decimal(100),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    # Kolejność sprawdzania reguł w qualify_return jest szczegółem
    # implementacji, nie częścią kontraktu - porównujemy zbiór, żeby test
    # nie wywalił się bez powodu, jeśli kiedyś zmienimy porządek warunków.
    assert result.decision == Decision.MANUAL_REVIEW
    assert set(result.failed_rules) == {
        FailedRule.WITHIN_14_DAYS,
        FailedRule.NOT_DELIVERED,
    }


def test_amount_over_limit_fails_only_that_rule() -> None:
    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=datetime(2026, 1, 1, 10, 0, tzinfo=WARSAW),
        ticket_created_at=datetime(2026, 1, 3, 10, 0, tzinfo=WARSAW),
        return_amount=Decimal(601),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.AMOUNT_LIMIT]


def test_amount_exactly_at_limit_still_passes() -> None:
    # 600 zł (sam limit, nie 601) - regulamin §2 mówi "nie przekracza", więc
    # dokładnie 600 ma przejść. Test z 601 nie odróżnia ">" od błędnego ">=",
    # bo oba warianty i tak by go odrzuciły - ten test odróżnia je.
    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=datetime(2026, 1, 1, 10, 0, tzinfo=WARSAW),
        ticket_created_at=datetime(2026, 1, 3, 10, 0, tzinfo=WARSAW),
        return_amount=Decimal(600),
        return_reason=ReturnReason.WRONG_SIZE,
    )

    assert result.decision == Decision.AUTO_APPROVE
    assert FailedRule.AMOUNT_LIMIT not in result.failed_rules


def test_reason_outside_allowed_list_fails_only_that_rule() -> None:
    # None = powód spoza 15 pozycji z §3, w tym "inne" - to, co NodeVal
    # przekaże, gdy LLM nie dopasuje powodu klienta do żadnego ReturnReason.
    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=datetime(2026, 1, 1, 10, 0, tzinfo=WARSAW),
        ticket_created_at=datetime(2026, 1, 3, 10, 0, tzinfo=WARSAW),
        return_amount=Decimal(100),
        return_reason=None,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert result.failed_rules == [FailedRule.ALLOWED_REASON]


def test_naive_delivered_at_raises_value_error() -> None:
    # Naiwność jest tu celowa (to właśnie testujemy), więc wyciszamy DTZ001.
    naive_delivered_at = datetime(2026, 1, 1, 10, 0)  # noqa: DTZ001

    with pytest.raises(ValueError, match="delivered_at musi mieć strefę czasową"):
        qualify_return(
            shipping_status=ShippingStatus.DELIVERED,
            delivered_at=naive_delivered_at,
            ticket_created_at=datetime(2026, 1, 3, 10, 0, tzinfo=WARSAW),
            return_amount=Decimal(100),
            return_reason=ReturnReason.WRONG_SIZE,
        )


def test_naive_ticket_created_at_raises_even_when_delivered_at_is_none() -> None:
    # Chroni przed tym, że naiwny ticket_created_at przy delivered_at=None
    # przejdzie niezauważony - bez tej walidacji funkcja zwróciłaby False
    # po cichu, zamiast zgłosić błędne dane wejściowe.
    naive_ticket_created_at = datetime(2026, 1, 3, 10, 0)  # noqa: DTZ001

    with pytest.raises(ValueError, match="ticket_created_at musi mieć strefę czasową"):
        qualify_return(
            shipping_status=ShippingStatus.SENT,
            delivered_at=None,
            ticket_created_at=naive_ticket_created_at,
            return_amount=Decimal(100),
            return_reason=ReturnReason.WRONG_SIZE,
        )


def test_multiple_failed_rules_are_all_listed() -> None:
    result = qualify_return(
        shipping_status=ShippingStatus.DELIVERED,
        delivered_at=datetime(2026, 1, 1, 10, 0, tzinfo=WARSAW),
        ticket_created_at=datetime(2026, 1, 3, 10, 0, tzinfo=WARSAW),
        return_amount=Decimal(601),
        return_reason=None,
    )

    assert result.decision == Decision.MANUAL_REVIEW
    assert set(result.failed_rules) == {
        FailedRule.AMOUNT_LIMIT,
        FailedRule.ALLOWED_REASON,
    }
