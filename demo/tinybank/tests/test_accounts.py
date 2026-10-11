import pytest
from tinybank.accounts import (
    DAILY_WITHDRAWAL_LIMIT,
    MINIMUM_DEPOSIT,
    TRANSFER_FEE,
    close_account,
    deposit,
    open_account,
    transfer,
    withdraw,
)


def test_opening_needs_the_minimum_deposit():
    with pytest.raises(ValueError):
        open_account("Alice", MINIMUM_DEPOSIT - 1)
    assert open_account("Alice", MINIMUM_DEPOSIT).balance == MINIMUM_DEPOSIT


def test_deposit_and_withdraw_move_the_balance():
    account = open_account("Alice", 500)
    assert deposit(account, 100) == 600
    assert withdraw(account, 50) == 550


def test_withdrawals_stop_at_the_daily_limit():
    account = open_account("Alice", DAILY_WITHDRAWAL_LIMIT * 2)
    withdraw(account, DAILY_WITHDRAWAL_LIMIT)
    with pytest.raises(ValueError):
        withdraw(account, 1)


def test_transfer_charges_the_sender_the_fee():
    alice = open_account("Alice", 500)
    bob = open_account("Bob", 500)
    assert transfer(alice, bob, 100) == "next business day"
    assert alice.balance == 500 - 100 - TRANSFER_FEE
    assert bob.balance == 600


def test_only_an_empty_account_can_be_closed():
    account = open_account("Alice", 500)
    with pytest.raises(ValueError):
        close_account(account)
    withdraw(account, 500)
    assert close_account(account).closed is True
