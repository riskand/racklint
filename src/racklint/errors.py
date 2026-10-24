# racklint/src/racklint/errors.py
"""The errors racklint raises. A caller can catch RacklintError to handle all of them."""


class RacklintError(Exception):
    """Base class of every error racklint raises on purpose."""


class BaselineError(RacklintError):
    """The firmware baseline is missing or invalid."""


class NetBoxError(RacklintError):
    """NetBox could not be read, or does not hold what was asked for."""


class InventoryError(RacklintError):
    """A node's actual state could not be read from its BMC."""


class AuthenticationError(InventoryError):
    """The device refused the credentials. Never retried: another try could lock the account."""
