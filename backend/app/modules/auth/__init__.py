"""Authentication (SPEC §6.2, §8.1).

The flow is invitation → OTP → registration, then password login with rotating
refresh tokens. Two invariants hold across every file here:

* The account's email comes from the invitation row, never from a submitted form.
* The OTP goes to the invited address, never to one the caller supplies.

Together those are what make an invitation non-transferable.
"""
