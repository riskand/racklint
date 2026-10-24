# racklint/tests/test_api.py
import subprocess
import sys
from collections.abc import Sequence
from importlib.resources import files
from typing import Literal

from pydantic import SecretStr

import racklint


def test_every_public_name_is_importable() -> None:
    assert [name for name in racklint.__all__ if not hasattr(racklint, name)] == []


def test_every_error_is_a_racklint_error() -> None:
    for error in (
        racklint.BaselineError,
        racklint.NetBoxError,
        racklint.InventoryError,
        racklint.RedfishError,
        racklint.GnmiError,
        racklint.AuthenticationError,
        racklint.CredentialError,
        racklint.VaultError,
        racklint.UnknownPlatform,
    ):
        assert issubclass(error, racklint.RacklintError), error


def test_a_refused_login_is_an_inventory_error() -> None:
    assert issubclass(racklint.AuthenticationError, racklint.InventoryError)
    assert issubclass(racklint.VaultError, racklint.CredentialError)


async def test_a_library_user_checks_nodes_with_a_credential_lookup() -> None:
    class Reader:
        def __init__(self) -> None:
            self.usernames: list[str] = []

        async def read(self, address: str, credentials: racklint.Credentials) -> racklint.ActualNode:
            self.usernames.append(credentials.username)
            return racklint.ActualNode(serials=("SN-01",))

    login = racklint.Credentials(username="admin", password=SecretStr("secret"), origin="set default-server")
    lookup = racklint.CredentialLookup(racklint.StaticStore({"default-server": login}))
    node = racklint.ExpectedNode(name="n01", device_type="gpu-node", serial="SN-01", bmc_address="192.0.2.11")
    reader = Reader()

    report = await racklint.check_nodes([node], reader, [racklint.SerialCheck()], lookup, concurrency=1)

    assert report.passed
    assert reader.usernames == ["admin"]


async def test_a_library_user_writes_a_credential_source_for_identified_devices() -> None:
    class SameLoginForEveryone:
        async def credentials_for(self, device: racklint.Identified) -> racklint.Credentials:
            login = SecretStr("secret")
            return racklint.Credentials(username=device.name, password=login, origin="my source")

    source: racklint.CredentialSource = SameLoginForEveryone()
    node = racklint.ExpectedNode(name="n01", device_type="gpu-node")

    assert (await source.credentials_for(node)).username == "n01"


async def test_a_library_user_checks_a_plan_with_a_platform_of_their_own() -> None:
    """A platform needs no racklint base class: an object with a name, a kind and two methods."""

    class Reader:
        async def read(self, address: str, credentials: racklint.Credentials) -> racklint.ActualNode:
            return racklint.ActualNode(serials=("SN-01",))

    class VendorServers:
        name = "vendor"
        kind: Literal[racklint.Kind.SERVER] = racklint.Kind.SERVER

        def reader(self, access: racklint.BmcAccess) -> racklint.ActualReader[racklint.ActualNode]:
            return Reader()

        def checks(self) -> Sequence[racklint.Check[racklint.ExpectedNode, racklint.ActualNode]]:
            return [racklint.SerialCheck()]

    login = racklint.Credentials(username="admin", password=SecretStr("secret"), origin="set default-server")
    lookup = racklint.CredentialLookup(racklint.StaticStore({"default-server": login}))
    node = racklint.ExpectedNode(
        name="n01", device_type="gpu-node", serial="SN-01", platform="vendor", bmc_address="192.0.2.11"
    )
    platforms = racklint.Platforms([VendorServers()])

    report = await racklint.check_plan([node], platforms, lookup, racklint.BmcAccess(), None, concurrency=1)

    assert report.passed


def test_a_library_user_loads_the_installed_platforms() -> None:
    platforms = racklint.load_platforms()

    assert platforms.server("openbmc").name == "openbmc"
    assert platforms.switch("srlinux").name == "srlinux"


def test_a_library_user_adds_a_gpu_rule_to_the_built_in_ones() -> None:
    class OemGpus:
        def find(self, actual: racklint.ActualNode, planned: racklint.GpuSpec) -> tuple[racklint.Gpu, ...]:
            return tuple(
                racklint.Gpu(id=processor.id, model=processor.model)
                for processor in actual.processors
                if processor.type == "OEM" and processor.model == planned.model
            )

    gpu = racklint.GpuSpec(count=1, model="X1")
    node = racklint.ExpectedNode(
        name="n01",
        device_type="gpu-node",
        spec=racklint.HardwareSpec(gpu=gpu, memory=racklint.MemorySpec(dimm_count=0, total_gib=0)),
    )
    actual = racklint.ActualNode(processors=(racklint.Processor(id="oem0", type="OEM", model="X1"),))

    assert racklint.GpuCheck().run(node, actual) != []
    assert racklint.GpuCheck([*racklint.GPU_RULES, OemGpus()]).run(node, actual) == []


def test_the_package_is_marked_as_typed() -> None:
    assert files("racklint").joinpath("py.typed").is_file()


def test_importing_the_library_does_not_import_the_cli_stack_or_grpc() -> None:
    modules = "('rich', 'typer', 'grpc', 'pygnmi')"
    code = f"import sys, racklint; print(sorted(m for m in {modules} if m in sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "[]"
