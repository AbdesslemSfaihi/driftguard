# Account rules

These are the rules every TinyBank account follows. Each rule has one named
value in the code (`src/tinybank/accounts.py`), shown in parentheses, so the
handbook and the software can be compared line by line.

## Opening an account

A customer opens an account by paying a first deposit of at least **100 EUR**
(`MINIMUM_DEPOSIT`). A smaller first deposit is refused and no account is
created.

## Withdrawing money

A customer may take out at most **1000 EUR per day** (`DAILY_WITHDRAWAL_LIMIT`).
A withdrawal that would go over the daily limit is refused, and so is a
withdrawal larger than the balance.

## Transfers between accounts

Every transfer costs a fee of **2 EUR** (`TRANSFER_FEE`), paid by the sender on
top of the amount. A standard transfer arrives the next business day; a
customer may ask for an instant transfer, which arrives right away.

## Closing an account

An account can be closed only when its balance is exactly zero. Withdraw or
transfer the remaining money first.
