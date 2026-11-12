# Czysta logika reguł kwalifikacji zwrotu - bez LangGraph, bazy, sieci.
# Woła to NodeVal jako zwykłą funkcję Pythona (Wariant B, zob. stan_projektu.md).
# Node: Zbieranie Danych (LLM) ustala return_reason wcześniej, w stanie grafu -
# ta funkcja dostaje już same ustalone fakty i porównuje je z regulaminem.

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel

# §2 regulaminu. WARSAW_TZ: termin liczy się w dniach kalendarzowych w Polsce
# (§1: "14 pełnych dni kalendarzowych"), a kolumny w bazie są w UTC.
MAX_AUTO_APPROVE_AMOUNT = Decimal(600)
AUTO_APPROVE_WINDOW_DAYS = 14
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


class ReturnReason(StrEnum):
    # 15 dozwolonych powodów, §3 regulaminu (data/regulamin.md).
    PRODUCT_MISMATCH = "product_mismatch"  # niezgodny z zamówieniem
    MECHANICAL_DAMAGE_IN_TRANSIT = "mechanical_damage_in_transit"  # w transporcie
    FACTORY_DEFECT = "factory_defect"  # wadliwy fabrycznie
    INCOMPLETE_SET = "incomplete_set"  # niekompletny zestaw
    WRONG_SIZE = "wrong_size"  # nieprawidłowy rozmiar
    WRONG_COLOR = "wrong_color"  # niezgodność koloru
    NOT_AS_DESCRIBED = "not_as_described"  # niezgodny z opisem
    WITHDRAWAL_NO_REASON = "withdrawal_no_reason"  # bez przyczyny
    ORDERED_BY_MISTAKE = "ordered_by_mistake"  # zamówione omyłkowo
    SPEC_MISMATCH = "spec_mismatch"  # parametry techniczne
    DELAYED_DELIVERY = "delayed_delivery"  # opóźniona dostawa
    DAMAGED_BULK_PACKAGING = "damaged_bulk_packaging"  # opakowanie zbiorcze
    MISSING_DOCUMENTATION = "missing_documentation"  # brak dokumentacji
    WRONG_QUANTITY = "wrong_quantity"  # niewłaściwa ilość
    DOUBLE_CHARGE = "double_charge"  # podwójne obciążenie


class ShippingStatus(StrEnum):
    # Te same trzy wartości co kolumna orders.shipped_status.
    UNSENT = "UNSENT"
    SENT = "SENT"
    DELIVERED = "DELIVERED"


class FailedRule(StrEnum):
    # Która z czterech reguł automatycznej kwalifikacji (§2) zawiodła.
    WITHIN_14_DAYS = "within_14_days"
    AMOUNT_LIMIT = "amount_limit"
    ALLOWED_REASON = "allowed_reason"
    NOT_DELIVERED = "not_delivered"


class Decision(StrEnum):
    AUTO_APPROVE = "auto_approve"
    MANUAL_REVIEW = "manual_review"


class QualificationResult(BaseModel):
    decision: Decision
    # Lista, nie pojedyncza wartość - przy MANUAL_REVIEW może zawiodło kilka
    # reguł naraz; uzasadnienie w mailu/panelu admina ma pokazać wszystkie.
    failed_rules: list[FailedRule]


def _is_within_return_window(
    delivered_at: datetime | None, ticket_created_at: datetime
) -> bool:
    # ticket_created_at sprawdzamy przed obsługą delivered_at=None, inaczej
    # naiwny ticket_created_at przeszedłby niezauważony w tej gałęzi.
    if ticket_created_at.tzinfo is None:
        raise ValueError("ticket_created_at musi mieć strefę czasową")

    # Brak daty dostawy = nie da się potwierdzić terminu, traktujemy jako
    # niespełniony warunek, nie jako "pomijam sprawdzenie".
    if delivered_at is None:
        return False

    # .astimezone() na naiwnym datetime nie rzuca błędu - po cichu zakłada
    # strefę systemową, co dałoby błędny wynik bez żadnego ostrzeżenia.
    if delivered_at.tzinfo is None:
        raise ValueError("delivered_at musi mieć strefę czasową")

    # §1 regulaminu liczy dni kalendarzowe w Polsce, nie 24h okresy w UTC.
    delivered_date = delivered_at.astimezone(WARSAW_TZ).date()
    created_date = ticket_created_at.astimezone(WARSAW_TZ).date()
    return (created_date - delivered_date).days <= AUTO_APPROVE_WINDOW_DAYS


def qualify_return(
    *,
    # shipping_status i delivered_at razem (mirror dwóch kolumn z bazy),
    # zamiast wywodzić "doręczone" tylko z obecności daty.
    shipping_status: ShippingStatus,
    delivered_at: datetime | None,
    # Moment utworzenia ticketu, nie "teraz" - termin liczy się od zgłoszenia,
    # a funkcja może wykonać się z opóźnieniem (retry, kolejka).
    ticket_created_at: datetime,
    return_amount: Decimal,
    # None = powód spoza 15 pozycji z §3, w tym "inne" (§2) -> manualna
    # weryfikacja. Odrzucone: sztuczna wartość ReturnReason.OTHER - enum ma
    # reprezentować dokładnie 15 pozycji z regulaminu.
    return_reason: ReturnReason | None,
) -> QualificationResult:
    failed: list[FailedRule] = []

    if not _is_within_return_window(delivered_at, ticket_created_at):
        failed.append(FailedRule.WITHIN_14_DAYS)
    if return_amount > MAX_AUTO_APPROVE_AMOUNT:
        failed.append(FailedRule.AMOUNT_LIMIT)
    if shipping_status != ShippingStatus.DELIVERED:
        failed.append(FailedRule.NOT_DELIVERED)
    if return_reason is None:
        failed.append(FailedRule.ALLOWED_REASON)

    decision = Decision.MANUAL_REVIEW if failed else Decision.AUTO_APPROVE
    return QualificationResult(decision=decision, failed_rules=failed)
