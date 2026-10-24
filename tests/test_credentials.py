# racklint/tests/test_credentials.py
import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import SecretStr

from racklint.credentials import CredentialError, CredentialLookup, Credentials, StaticStore, read_password
from tests.factories import PASSWORD, credentials, expected_node, expected_switch

SERVER_SET = credentials(origin="set default-server")
SWITCH_SET = credentials(username="switch-admin", origin="set default-switch")
NAMED_SET = credentials(username="named-admin", origin="set bmc-r02")
FACTORY = credentials(username="root", origin="factory secret for serial SN-01", factory=True)


class CountingStore:
    """A CredentialStore that remembers every question it was asked, and answers after a short wait."""

    where = "the counting store"

    def __init__(
        self, sets: Mapping[str, Credentials] | None = None, factory: Mapping[str, Credentials] | None = None
    ) -> None:
        self._sets = sets or {}
        self._factory = factory or {}
        self.asked: list[tuple[str, str]] = []

    async def factory(self, serial: str) -> Credentials | None:
        self.asked.append(("factory", serial))
        await asyncio.sleep(0.01)
        return self._factory.get(serial)

    async def named(self, name: str) -> Credentials | None:
        self.asked.append(("set", name))
        await asyncio.sleep(0.01)
        return self._sets.get(name)


class BrokenStore:
    where = "the broken store"

    async def factory(self, serial: str) -> Credentials | None:
        raise CredentialError("the store is down")

    async def named(self, name: str) -> Credentials | None:
        raise CredentialError("the store is down")


def a_store() -> CountingStore:
    return CountingStore(
        sets={"default-server": SERVER_SET, "default-switch": SWITCH_SET, "bmc-r02": NAMED_SET},
        factory={"SN-01": FACTORY},
    )


async def test_a_factory_secret_wins_over_the_named_set() -> None:
    lookup = CredentialLookup(a_store())

    found = await lookup.credentials_for(expected_node(serial="SN-01", credential_set="bmc-r02"))

    assert found == FACTORY


async def test_the_named_set_wins_over_the_default() -> None:
    lookup = CredentialLookup(a_store())

    found = await lookup.credentials_for(expected_node(serial="SN-02", credential_set="bmc-r02"))

    assert found == NAMED_SET


async def test_a_device_without_a_named_set_gets_the_default_of_its_kind() -> None:
    lookup = CredentialLookup(a_store())

    server = await lookup.credentials_for(expected_node(serial="SN-02"))
    switch = await lookup.credentials_for(expected_switch(serial="SN-02"))

    assert (server, switch) == (SERVER_SET, SWITCH_SET)


async def test_a_device_without_a_serial_skips_the_factory_lookup() -> None:
    store = a_store()

    await CredentialLookup(store).credentials_for(expected_node(serial=None))

    assert store.asked == [("set", "default-server")]


async def test_a_missing_set_is_an_error_naming_the_device_and_the_store() -> None:
    lookup = CredentialLookup(StaticStore({}))

    with pytest.raises(CredentialError) as raised:
        await lookup.credentials_for(expected_node(name="n04", credential_set="bmc-r02"))

    assert str(raised.value) == "no credentials for n04: set bmc-r02 not found in the settings"


async def test_a_store_error_is_wrapped_with_the_device_name() -> None:
    lookup = CredentialLookup(BrokenStore())

    with pytest.raises(CredentialError) as raised:
        await lookup.credentials_for(expected_node(name="n04"))

    assert str(raised.value) == "no credentials for n04: the store is down"


async def test_a_missing_factory_secret_is_not_an_error_while_the_set_exists() -> None:
    store = CountingStore(sets={"default-server": SERVER_SET})

    found = await CredentialLookup(store).credentials_for(expected_node(serial="SN-02"))

    assert found == SERVER_SET
    assert store.asked == [("factory", "SN-02"), ("set", "default-server")]


async def test_each_key_is_asked_once_for_devices_looked_up_at_the_same_time() -> None:
    store = a_store()
    lookup = CredentialLookup(store)
    devices = [expected_node(name=f"n{index:02d}", serial="SN-02") for index in range(10)]

    found = await asyncio.gather(*(lookup.credentials_for(device) for device in devices))

    assert found == [SERVER_SET] * 10
    assert sorted(store.asked) == [("factory", "SN-02"), ("set", "default-server")]


async def test_a_failed_question_is_not_asked_again() -> None:
    store = CountingStore()
    lookup = CredentialLookup(store)

    for name in ("n01", "n02"):
        with pytest.raises(CredentialError):
            await lookup.credentials_for(expected_node(name=name, serial=None))

    assert store.asked == [("set", "default-server")]


async def test_the_static_store_answers_from_its_sets_and_its_factory_secrets() -> None:
    store = StaticStore({"bmc-r02": NAMED_SET}, {"SN-01": FACTORY})

    assert await store.named("bmc-r02") == NAMED_SET
    assert await store.named("other") is None
    assert await store.factory("SN-01") == FACTORY
    assert await store.factory("SN-02") is None


# Credentials files


def write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def write_password(directory: Path, name: str, content: str = f"{PASSWORD}\n") -> Path:
    return write(directory / name, content)


async def test_a_credentials_file_holds_sets_and_factory_secrets(tmp_path: Path) -> None:
    server_password = write_password(tmp_path, "server")
    factory_password = write_password(tmp_path, "factory", "factory-secret\n")
    file = write(
        tmp_path / "credentials.yaml",
        f"""
sets:
  default-server: {{username: admin, password_file: {server_password}}}
factory:
  SN-0004: {{username: root, password_file: {factory_password}}}
""",
    )

    store = StaticStore.from_file(file)

    server = await store.named("default-server")
    factory = await store.factory("SN-0004")
    assert server is not None
    assert (server.username, server.origin, server.factory) == ("admin", "set default-server", False)
    assert server.password.get_secret_value() == PASSWORD
    assert factory is not None
    assert (factory.username, factory.origin, factory.factory) == (
        "root",
        "factory secret for serial SN-0004",
        True,
    )
    assert factory.password.get_secret_value() == "factory-secret"
    assert store.where == f"credentials file {file}"


async def test_a_password_can_come_from_an_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BMC_DC1_PASSWORD", PASSWORD)
    file = write(
        tmp_path / "credentials.yaml", "sets:\n  bmc-dc1: {username: admin, password_env: BMC_DC1_PASSWORD}\n"
    )

    named = await StaticStore.from_file(file).named("bmc-dc1")

    assert named is not None
    assert named.password.get_secret_value() == PASSWORD


async def test_a_file_without_factory_secrets_has_none(tmp_path: Path) -> None:
    file = write(tmp_path / "credentials.yaml", "sets: {}\n")

    assert await StaticStore.from_file(file).factory("SN-0004") is None


def test_a_password_written_into_the_file_is_rejected_without_being_shown(tmp_path: Path) -> None:
    file = write(
        tmp_path / "credentials.yaml", "sets:\n  default-server: {username: admin, password: hunter2}\n"
    )

    with pytest.raises(CredentialError, match=r"put the password in a file \(password_file\)") as raised:
        StaticStore.from_file(file)

    assert "hunter2" not in str(raised.value)


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param("{username: admin}", id="neither"),
        pytest.param("{username: admin, password_file: /x, password_env: X}", id="both"),
    ],
)
def test_an_entry_needs_exactly_one_password_source(tmp_path: Path, entry: str) -> None:
    file = write(tmp_path / "credentials.yaml", f"sets:\n  default-server: {entry}\n")

    with pytest.raises(CredentialError, match="exactly one of password_file and password_env"):
        StaticStore.from_file(file)


def test_an_unknown_key_is_rejected_without_its_value_being_shown(tmp_path: Path) -> None:
    file = write(
        tmp_path / "credentials.yaml",
        "sets:\n  default-server: {username: admin, password_file: /x, passwrd: hunter2}\n",
    )

    with pytest.raises(CredentialError, match="passwrd") as raised:
        StaticStore.from_file(file)

    assert "hunter2" not in str(raised.value)


def test_an_unset_environment_variable_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMC_DC1_PASSWORD", raising=False)
    file = write(
        tmp_path / "credentials.yaml", "sets:\n  bmc-dc1: {username: admin, password_env: BMC_DC1_PASSWORD}\n"
    )

    with pytest.raises(CredentialError, match="environment variable BMC_DC1_PASSWORD is not set"):
        StaticStore.from_file(file)


def test_an_empty_password_file_is_an_error(tmp_path: Path) -> None:
    empty = write_password(tmp_path, "empty", "\n")
    file = write(
        tmp_path / "credentials.yaml",
        f"sets:\n  default-server: {{username: admin, password_file: {empty}}}\n",
    )

    with pytest.raises(CredentialError, match="is empty"):
        StaticStore.from_file(file)


def test_a_missing_credentials_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CredentialError, match="cannot read credentials file"):
        StaticStore.from_file(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("- a list", id="not-a-mapping"),
        pytest.param("sets:\n  default-server: {username: admin", id="malformed"),
        pytest.param("sets: \x01", id="control-character"),
    ],
)
def test_a_file_that_is_not_a_credentials_file_is_an_error(tmp_path: Path, content: str) -> None:
    file = write(tmp_path / "credentials.yaml", content)

    with pytest.raises(CredentialError, match="cannot load credentials file"):
        StaticStore.from_file(file)


def test_a_malformed_file_is_reported_by_line_and_never_quoted(tmp_path: Path) -> None:
    file = write(
        tmp_path / "credentials.yaml", "sets:\n  default-server: {username: admin, password: hunter2\n"
    )

    with pytest.raises(CredentialError, match=r"\(line \d+\)") as raised:
        StaticStore.from_file(file)

    assert "hunter2" not in str(raised.value)


def test_a_set_name_that_is_not_a_path_segment_is_rejected(tmp_path: Path) -> None:
    file = write(tmp_path / "credentials.yaml", "sets:\n  a/b: {username: admin, password_env: X}\n")

    with pytest.raises(CredentialError, match="cannot load credentials file"):
        StaticStore.from_file(file)


# Password files


def test_a_password_file_loses_only_its_line_ending(tmp_path: Path) -> None:
    file = write_password(tmp_path, "password", " spaced \r\n")

    assert read_password(file).get_secret_value() == " spaced "


def test_credentials_keep_the_spaces_of_a_password() -> None:
    login = Credentials(username=" admin ", password=SecretStr(" spaced "), origin="set x")

    assert login.password.get_secret_value() == " spaced "


def test_the_text_of_credentials_hides_the_password() -> None:
    assert PASSWORD not in repr(credentials())
