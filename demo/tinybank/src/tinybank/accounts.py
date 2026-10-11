"""TinyBank account rules.

Every rule in docs/account-rules.md has exactly one named value in this file,
so the handbook and the code can be compared line by line.
"""

from dataclasses import dataclass

MINIMUM_DEPOSIT = 100  # EUR a customer needs to open an account
DAILY_WITHDRAWAL_LIMIT = 1000  # EUR a customer may take out per day
TRANSFER_FEE = 2  # EUR the sender pays on every transfer


@dataclass
class Account:
    owner_name: str
    balance: int
    currency: str = "EUR"
    withdrawn_today: int = 0
    closed: bool = False


def open_account(owner_name, first_deposit, currency="EUR"):
    """Open an account. The first deposit must be at least MINIMUM_DEPOSIT."""
    if first_deposit < MINIMUM_DEPOSIT:
        raise ValueError(f"the first deposit must be at least {MINIMUM_DEPOSIT} {currency}")
    return Account(owner_name=owner_name, balance=first_deposit, currency=currency)


def deposit(account, amount):
    """Put money into an account."""
    if amount <= 0:
        raise ValueError("the amount must be positive")
    account.balance += amount
    return account.balance


def withdraw(account, amount):
    """Take money out of an account, within DAILY_WITHDRAWAL_LIMIT and the balance."""
    if account.withdrawn_today + amount > DAILY_WITHDRAWAL_LIMIT:
        raise ValueError(f"no more than {DAILY_WITHDRAWAL_LIMIT} EUR may be withdrawn per day")
    if amount > account.balance:
        raise ValueError("not enough money on the account")
    account.balance -= amount
    account.withdrawn_today += amount
    return account.balance


def transfer(sender, receiver, amount, instant=False):
    """Move money from one account to another. The sender also pays TRANSFER_FEE.

    A standard transfer arrives the next business day; an instant transfer
    arrives right away.
    """
    total = amount + TRANSFER_FEE
    if total > sender.balance:
        raise ValueError("not enough money to cover the amount and the fee")
    sender.balance -= total
    receiver.balance += amount
    return "instant" if instant else "next business day"


def close_account(account):
    """Close an account. Only an account with a zero balance can be closed."""
    if account.balance != 0:
        raise ValueError("the balance must be zero before closing")
    account.closed = True
    return account
