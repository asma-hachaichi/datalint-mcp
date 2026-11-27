import pandas as pd
import pytest

from datalint_mcp import checks, pii


@pytest.mark.parametrize("text", [
    "+33 6 12 34 56 78", "+44 20 7946 0958", "+1 (415) 555-0132", "06 98 76 54 32",
])
def test_phone_numbers(text):
    assert pii.is_phone(text)


@pytest.mark.parametrize("text", [
    "12345678901",       # a bare run of digits is an ID or an amount
    "2024-01-05",        # a date
    "192.168.1.10",      # an IP address
    "4111 1111 1111 1111",  # a card number: too many digits
])
def test_things_that_are_not_phone_numbers(text):
    assert not pii.is_phone(text)


def test_card_numbers_need_a_valid_checksum():
    assert pii.is_card("4111 1111 1111 1111")
    assert pii.is_card("3782-822463-10005")
    assert not pii.is_card("4111 1111 1111 1112")  # one digit off fails the Luhn check


def test_iban_needs_a_valid_checksum():
    assert pii.is_iban("GB82 WEST 1234 5698 7654 32")
    assert not pii.is_iban("GB83 WEST 1234 5698 7654 32")


def test_ip_addresses():
    assert pii.is_ipv4("10.0.0.255")
    assert not pii.is_ipv4("10.0.0.256")


def test_random_ids_are_not_card_numbers():
    # About 1 in 10 random 16-digit numbers passes the Luhn check by chance, so a column
    # of IDs must not be flagged on those alone.
    ids = pd.Series([str(4000_0000_0000_0000 + i * 7919) for i in range(200)])
    assert "credit_card" not in pii.personal_kinds(ids, "reference")


def test_decimal_numbers_are_never_scanned():
    assert pii.personal_kinds(pd.Series([1.5, 2.25, 3.0]), "amount") == []


def test_scan_finds_every_kind_in_the_example_database(shop):
    customers = checks.detect_pii(shop["customers"])
    kinds = {f["column"]: {p["kind"] for p in f["personal_data"]} for f in customers["findings"]}
    assert kinds == {
        "name": {"person_name"},
        "email": {"email"},
        "phone": {"phone"},
        "card": {"credit_card"},
    }
    orders = checks.detect_pii(shop["orders"])
    kinds = {f["column"]: {p["kind"] for p in f["personal_data"]} for f in orders["findings"]}
    assert kinds == {"ip": {"ip_address"}, "notes": {"email"}}


def test_scan_reports_evidence_but_never_values(shop):
    result = checks.detect_pii(shop["customers"])
    email = next(f for f in result["findings"] if f["column"] == "email")["personal_data"][0]
    assert email == {"kind": "email", "basis": "values", "matching_values": 6,
                     "matching_pct": 100.0}
    name = next(f for f in result["findings"] if f["column"] == "name")["personal_data"][0]
    assert name == {"kind": "person_name", "basis": "column_name"}
    assert "@" not in str(result)


def test_profile_flags_personal_data(shop):
    profiles = {p["column"]: p for p in checks.profile_dataset(shop["customers"])["column_profiles"]}
    assert profiles["email"]["personal_data"] == ["email"]
    assert profiles["city"]["personal_data"] == []


def test_describe_hides_values_of_personal_columns(shop):
    hidden = checks.describe_column(shop["customers"], "email")
    assert "top_values" not in hidden
    assert "email" in hidden["values_hidden"]
    assert "@" not in str(hidden)
    shown = checks.describe_column(shop["customers"], "city")
    assert "values_hidden" not in shown
