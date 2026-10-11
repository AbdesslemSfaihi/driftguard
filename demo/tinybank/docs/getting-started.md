# Getting started

A short tour of the library for developers. The rules themselves are
explained in plain language in [Account rules](account-rules.md).

## Install

```bash
pip install -e .
```

## Open an account

`open_account(owner_name, first_deposit, currency="EUR")` creates an account.
The first deposit must meet the minimum in the account rules; a smaller one
raises `ValueError`.

```python
from tinybank.accounts import open_account

alice = open_account("Alice", first_deposit=500)
```

## Put money in

`deposit(account, amount)` adds money and returns the new balance.

```python
from tinybank.accounts import deposit

deposit(alice, 250)  # balance is now 750
```

## Take money out

`withdraw(account, amount)` removes money and returns the new balance. It
refuses amounts over the daily limit or over the balance.

```python
from tinybank.accounts import withdraw

withdraw(alice, 200)  # balance is now 550
```

## Send money

`transfer(sender, receiver, amount, instant=False)` moves money between two
accounts and charges the sender the transfer fee. Transfers are standard by
default and arrive the next business day; pass `instant=True` to send the
money right away.

```python
from tinybank.accounts import open_account, transfer

bob = open_account("Bob", first_deposit=300)
transfer(alice, bob, 50)                # arrives the next business day
transfer(alice, bob, 50, instant=True)  # arrives right away
```

## Close an account

`close_account(account)` closes an account whose balance is zero.

```python
from tinybank.accounts import close_account

close_account(bob)  # only works once Bob's balance is 0
```
