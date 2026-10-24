# racklint/src/racklint/credentials.py
"""Where each device's username and password come from.

A CredentialStore holds credentials: factory secrets by serial number, and named sets. CredentialLookup
applies racklint's lookup order to a store, and is the CredentialSource the engine uses:

1. the factory secret for the device's serial, if the store has one;
2. the set the device's config context names;
3. the default set for the device's kind: `default-server` or `default-switch`.

The first one found is the only one used: a device is never logged in to twice.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, Self

import yaml
from pydantic import ConfigDict, Field, SecretStr, ValidationError, model_validator

from racklint.errors import RacklintError
from racklint.models import CredentialSetName, FrozenModel, Kind

DEFAULT_SET = {Kind.SERVER: "default-server", Kind.SWITCH: "default-switch"}


class CredentialError(RacklintError):
    """A device's credentials could not be found or read."""


class Credentials(FrozenModel):
    """A username and password, and where they came from, for messages."""

    # A password is kept as it was written: FrozenModel's stripping would change one with spaces at an end.
    model_config = ConfigDict(str_strip_whitespace=False)

    username: str
    password: SecretStr
    origin: str
    factory: bool = False


class Identified(Protocol):
    """What a lookup needs to know about a planned device."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> Kind: ...

    @property
    def serial(self) -> str | None: ...

    @property
    def credential_set(self) -> str | None: ...


class CredentialSource(Protocol):
    async def credentials_for(self, device: Identified) -> Credentials:
        """The credentials to read `device` with, or raise CredentialError."""
        ...


class CredentialStore(Protocol):
    @property
    def where(self) -> str:
        """Where the store keeps its credentials, for messages: "Vault", "the settings"."""
        ...

    async def factory(self, serial: str) -> Credentials | None:
        """The factory credentials for a serial number, or None if the store has none."""
        ...

    async def named(self, name: str) -> Credentials | None:
        """The credentials of a named set, or None if the store has no such set."""
        ...


class CredentialLookup:
    """The CredentialSource that applies the lookup order to a store, asking it about each key once."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store
        self._factory: dict[str, asyncio.Task[Credentials | None]] = {}
        self._named: dict[str, asyncio.Task[Credentials | None]] = {}

    async def credentials_for(self, device: Identified) -> Credentials:
        try:
            return await self._find(device)
        except CredentialError as error:
            raise CredentialError(f"no credentials for {device.name}: {error}") from error

    async def _find(self, device: Identified) -> Credentials:
        if device.serial:
            factory = await _once(self._factory, device.serial, self._store.factory)
            if factory is not None:
                return factory
        name = device.credential_set or DEFAULT_SET[device.kind]
        named = await _once(self._named, name, self._store.named)
        if named is None:
            raise CredentialError(f"set {name} not found in {self._store.where}")
        return named


async def _once(
    cache: dict[str, asyncio.Task[Credentials | None]],
    key: str,
    lookup: Callable[[str], Awaitable[Credentials | None]],
) -> Credentials | None:
    """Look `key` up once, however many devices ask for it at the same time.

    A cancelled awaiter cancels the shared lookup, which only happens when the run is shutting down.
    """
    if key not in cache:
        cache[key] = asyncio.ensure_future(lookup(key))
    return await cache[key]


class StaticStore:
    """Credentials held in memory: named sets, and factory secrets by serial."""

    def __init__(
        self,
        sets: Mapping[str, Credentials],
        factory: Mapping[str, Credentials] | None = None,
        where: str = "the settings",
    ) -> None:
        self._sets = dict(sets)
        self._factory = dict(factory or {})
        self.where = where

    @classmethod
    def from_file(cls, path: Path) -> Self:
        """A credentials file: sets and factory secrets, each password in a file or an environment variable.

        The passwords are read now, at the start of the run.
        """
        try:
            loaded = _CredentialFile.model_validate(yaml.safe_load(path.read_text()))
        except OSError as error:
            raise CredentialError(f"cannot read credentials file {path}: {error.strerror}") from error
        except yaml.YAMLError as error:
            raise CredentialError(f"cannot load credentials file {path}: {_yaml_problem(error)}") from None
        except ValidationError as error:
            raise CredentialError(f"cannot load credentials file {path}: {error}") from error
        return cls(
            sets={name: entry.credentials(f"set {name}") for name, entry in loaded.sets.items()},
            factory={
                serial: entry.credentials(f"factory secret for serial {serial}", factory=True)
                for serial, entry in loaded.factory.items()
            },
            where=f"credentials file {path}",
        )

    async def factory(self, serial: str) -> Credentials | None:
        return self._factory.get(serial)

    async def named(self, name: str) -> Credentials | None:
        return self._sets.get(name)


def read_password(path: Path) -> SecretStr:
    """A password file as secret managers write it: the password, perhaps followed by a newline."""
    try:
        password = path.read_text().rstrip("\r\n")
    except OSError as error:
        raise CredentialError(f"cannot read password file {path}: {error.strerror}") from error
    if not password:
        raise CredentialError(f"password file {path} is empty")
    return SecretStr(password)


def _yaml_problem(error: yaml.YAMLError) -> str:
    """What is wrong with the YAML, without PyYAML's quote of the line: that line could hold a password."""
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark is not None:
        return f"{error.problem} (line {error.problem_mark.line + 1})"
    return "not valid YAML"


class _FileModel(FrozenModel):
    """A part of a credentials file. Errors never quote the input, which may hold a password."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class _Entry(_FileModel):
    """One entry of a credentials file. The password is never in the file itself."""

    username: str
    password_file: Path | None = None
    password_env: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_password_in_the_file(cls, data: Any) -> Any:
        if isinstance(data, dict) and "password" in data:
            raise ValueError(
                "put the password in a file (password_file) or a variable (password_env), not here"
            )
        return data

    @model_validator(mode="after")
    def _one_password_source(self) -> Self:
        if (self.password_file is None) == (self.password_env is None):
            raise ValueError("give exactly one of password_file and password_env")
        return self

    def credentials(self, origin: str, factory: bool = False) -> Credentials:
        return Credentials(username=self.username, password=self._password(), origin=origin, factory=factory)

    def _password(self) -> SecretStr:
        if self.password_file is not None:
            return read_password(self.password_file)
        assert self.password_env is not None  # the validator guarantees one of the two
        value = os.environ.get(self.password_env, "")
        if not value:
            raise CredentialError(f"environment variable {self.password_env} is not set")
        return SecretStr(value)


class _CredentialFile(_FileModel):
    sets: dict[CredentialSetName, _Entry] = Field(default_factory=dict)
    factory: dict[str, _Entry] = Field(default_factory=dict)
