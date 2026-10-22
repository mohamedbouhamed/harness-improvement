# Cost reconciliation (ledger vs provider billing)

Two separate quantities are kept and never merged:
- **reported**: cost computed from provider-reported usage (`prompt_tokens`, and output as
  max(completion, total − prompt)) at the confirmed Standard prices ($0.30/M input,
  $2.50/M output including thinking), converted at 1 USD = 1 EUR (overstates EUR);
- **conservative bound**: every successful request charged as if it had used its full
  4,096-token output allowance. It is a safety reservation, not spending.

## Ledger (runs/sec/cost_ledger.jsonl, 496 requests, all successful)

| Run | Requests | Reported | Cumulative reported | Bound |
|---|---|---|---|---|
| token-accounting check (…03d1e6) | 7 | 0.0065 | 0.0065 | 0.077 |
| native thinking diagnostic | 1 | 0.0012 | 0.0077 | 0.011 |
| calibration T=0 (…898a0a) | 150 | 0.1052 | 0.1129 | 1.627 |
| calibration T=0.7 rep 1 (…0943c9) | 70 | 0.0520 | 0.1649 | 0.762 |
| calibration T=0.7 rep 2 (…096a04) | 47 | 0.0332 | 0.1981 | 0.511 |
| guidance check (…d31dba) | 61 | 0.0468 | 0.2449 | 0.665 |
| adaptation pilot (…93365a) | 59 | 0.0466 | 0.2915 | 0.642 |
| iteration 2 (…f01c21) | 101 | 0.0808 | 0.3723 | 1.102 |
| **Total** | 496 | **0.3723** | | **5.397** |

## Provider billing (figures supplied by the user; not read directly)

| When | Ledger reported (USD) | User-reported remaining of €5 | Implied billed |
|---|---|---|---|
| after calibration | 0.198 | €4.81 | ≈ €0.19 |
| after iteration 2 | 0.372 | €4.70 | ≈ €0.30 |

Both points agree with the reported column within currency conversion (≈ 0.86–0.92 EUR/USD)
and billing delay. There is no sign of billed but unreported usage (e.g. thinking tokens).
This is two aggregate data points, not a per-request reconciliation.

Remaining allowance from here: **€4.70 actual spend** (user), of which the next batch may use
**at most €1**. Requests keep a conservative pre-request reservation.
