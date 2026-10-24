# racklint/tests/test_redfish_reader.py
import base64
from typing import Any

import httpx
import pytest
import respx

from racklint.models import ActualNode, NetworkPort, Processor
from racklint.redfish import BmcAccess, RedfishAuthError, RedfishError, RedfishReader
from tests.factories import (
    BMC_ADDRESS,
    GPU_MODEL,
    PASSWORD,
    actual_node,
    bmc_tree,
    credentials,
    remove_resource,
    serve_bmc,
)

ACCESS = BmcAccess()
BMC = f"https://{BMC_ADDRESS}"
SESSIONS = "/redfish/v1/SessionService/Sessions"
SESSION = f"{SESSIONS}/7"
TOKEN = "session-token-value"
BASIC = "Basic " + base64.b64encode(b"admin:" + PASSWORD.encode()).decode()
ADAPTERS = "/redfish/v1/Chassis/1/NetworkAdapters"


def with_sessions(tree: dict[str, Any]) -> dict[str, Any]:
    """The BMC's resources, and a service root that links a session collection."""
    tree["/redfish/v1"]["Links"] = {"Sessions": {"@odata.id": SESSIONS}}
    return tree


def add_member(tree: dict[str, Any], collection: str, uri: str, fields: dict[str, Any]) -> None:
    """Add a resource, and list it in `collection`."""
    tree[uri] = {"@odata.id": uri, **fields}
    tree[collection]["Members"].append({"@odata.id": uri})


def add_ports(tree: dict[str, Any], adapter: str, ports: dict[str, dict[str, Any]]) -> None:
    """Give the adapter a Ports collection, from the ports' fields by Id. Its functions are unchanged."""
    collection = f"{adapter}/Ports"
    tree[collection] = {"@odata.id": collection, "Members": []}
    tree[adapter]["Ports"] = {"@odata.id": collection}
    for port_id, fields in ports.items():
        add_member(tree, collection, f"{collection}/{port_id}", {"Id": port_id, **fields})


def with_adapter_of_ports(tree: dict[str, Any], ports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The BMC's resources, and a second network adapter, NIC_1, that has Ports and no functions."""
    adapter = f"{ADAPTERS}/NIC_1"
    add_member(tree, ADAPTERS, adapter, {"Id": "NIC_1"})
    add_ports(tree, adapter, ports)
    return tree


def serve_sessions(router: respx.MockRouter, logout: int = 204) -> tuple[respx.Route, respx.Route]:
    """The login and logout routes of a BMC with sessions. Register them before serve_bmc."""
    login = router.post(f"{BMC}{SESSIONS}").respond(
        201, headers={"X-Auth-Token": TOKEN, "Location": SESSION}, json={"@odata.id": SESSION}
    )
    return login, router.delete(f"{BMC}{SESSION}").respond(logout)


async def test_reads_the_node_from_the_service_root(respx_mock: respx.MockRouter) -> None:
    serve_bmc(respx_mock, bmc_tree())

    assert await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials()) == actual_node()


async def test_reads_the_node_through_the_transport_of_the_access_settings() -> None:
    tree = bmc_tree()

    def respond(request: httpx.Request) -> httpx.Response:
        resource = tree.get(request.url.path)
        return httpx.Response(404) if resource is None else httpx.Response(200, json=resource)

    access = BmcAccess(transport=httpx.MockTransport(respond))

    assert await RedfishReader(access).read(BMC_ADDRESS, credentials()) == actual_node()


async def test_a_removed_processor_is_not_reported(respx_mock: respx.MockRouter) -> None:
    tree = bmc_tree()
    remove_resource(tree, "/redfish/v1/Systems/1/Processors/GPU_1")
    serve_bmc(respx_mock, tree)

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert [(processor.id, processor.type) for processor in node.processors] == [
        ("CPU_0", "CPU"),
        ("GPU_0", "GPU"),
    ]


async def test_every_processor_is_reported_with_its_type_and_model(respx_mock: respx.MockRouter) -> None:
    """Shaped like DMTF's public-rackmount1 (CPUs and an FPGA) and bmcweb's Accelerator processors.

    See docs/spikes/gpu-discovery.md, findings 1 and 2.
    """
    tree = bmc_tree()
    collection = "/redfish/v1/Systems/1/Processors"
    for uri in [uri for uri in tree if uri.startswith(f"{collection}/")]:
        remove_resource(tree, uri)
    listed = {
        "CPU1": {"ProcessorType": "CPU", "Model": "Intel(R) Xeon(R) Platinum 8480+"},
        "CPU2": {"ProcessorType": "CPU", "Model": "Intel(R) Xeon(R) Platinum 8480+"},
        "GPU_0": {"ProcessorType": "GPU", "Model": GPU_MODEL},
        "accelerator0": {"ProcessorType": "Accelerator", "Model": GPU_MODEL},
        "FPGA1": {"ProcessorType": "FPGA", "Model": "Altera Stratix 10"},
        "unknown0": {},
    }
    for processor_id, fields in listed.items():
        uri = f"{collection}/{processor_id}"
        tree[uri] = {"@odata.id": uri, "Id": processor_id, **fields}
        tree[collection]["Members"].append({"@odata.id": uri})
    serve_bmc(respx_mock, tree)

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert node.processors == (
        Processor(id="CPU1", type="CPU", model="Intel(R) Xeon(R) Platinum 8480+"),
        Processor(id="CPU2", type="CPU", model="Intel(R) Xeon(R) Platinum 8480+"),
        Processor(id="GPU_0", type="GPU", model=GPU_MODEL),
        Processor(id="accelerator0", type="Accelerator", model=GPU_MODEL),
        Processor(id="FPGA1", type="FPGA", model="Altera Stratix 10"),
        Processor(id="unknown0", type="", model=""),
    )


async def test_an_adapter_with_ports_and_no_functions_reports_its_ports_without_macs(
    respx_mock: respx.MockRouter,
) -> None:
    """Shaped like bmcweb's Port: Id, Name, Status and a link to its metrics, never a MAC address."""
    tree = bmc_tree()
    remove_resource(tree, f"{ADAPTERS}/NIC_0")
    metrics = {"@odata.id": "/redfish/v1/Chassis/1/NetworkAdapters/NIC_1/Ports/port0/Metrics"}
    bmcweb_port: dict[str, Any] = {
        "Name": "Port",
        "Status": {"State": "Enabled", "Health": "OK"},
        "Metrics": metrics,
    }
    serve_bmc(respx_mock, with_adapter_of_ports(tree, {"port0": bmcweb_port, "port1": bmcweb_port}))

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert node.ports == (NetworkPort(id="port0"), NetworkPort(id="port1"))


async def test_a_port_reports_the_first_of_its_associated_macs(respx_mock: respx.MockRouter) -> None:
    ports: dict[str, dict[str, Any]] = {
        "port0": {"Ethernet": {"AssociatedMACAddresses": ["02-00-00-00-02-01", "02:00:00:00:02:09"]}},
        "port1": {"Ethernet": {"AssociatedMACAddresses": []}},
    }
    serve_bmc(respx_mock, with_adapter_of_ports(bmc_tree(), ports))

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert node.ports[2:] == (NetworkPort(id="port0", mac="02:00:00:00:02:01"), NetworkPort(id="port1"))


async def test_functions_and_ports_on_different_adapters_are_all_reported(
    respx_mock: respx.MockRouter,
) -> None:
    serve_bmc(respx_mock, with_adapter_of_ports(bmc_tree(), {"port0": {}}))

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert node.ports == (*actual_node().ports, NetworkPort(id="port0"))


async def test_the_ports_of_an_adapter_that_has_functions_are_not_read(respx_mock: respx.MockRouter) -> None:
    tree = bmc_tree()
    add_ports(tree, f"{ADAPTERS}/NIC_0", {"port0": {}, "port1": {}})
    serve_bmc(respx_mock, tree)

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert node.ports == actual_node().ports


async def test_the_service_root_is_read_without_credentials_and_the_rest_with_basic_authentication(
    respx_mock: respx.MockRouter,
) -> None:
    route = serve_bmc(respx_mock, bmc_tree())

    await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials(username="admin"))

    first, *others = [call.request for call in route.calls]
    assert str(first.url) == f"https://{BMC_ADDRESS}/redfish/v1"
    assert "Authorization" not in first.headers
    assert {request.headers["Authorization"] for request in others} == {BASIC}


async def test_a_bmc_with_sessions_is_logged_in_once_and_every_request_carries_the_token(
    respx_mock: respx.MockRouter,
) -> None:
    login, logout = serve_sessions(respx_mock)
    bmc = serve_bmc(respx_mock, with_sessions(bmc_tree()))

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials(username="admin"))

    assert node == actual_node()
    assert login.call_count == 1
    assert login.calls.last.request.read() == b'{"UserName":"admin","Password":"' + PASSWORD.encode() + b'"}'
    root, *others = [call.request for call in bmc.calls]
    assert "X-Auth-Token" not in root.headers
    assert {request.headers["X-Auth-Token"] for request in others} == {TOKEN}
    assert all("Authorization" not in request.headers for request in [root, *others])
    assert logout.call_count == 1


async def test_the_session_is_closed_also_when_the_read_fails(respx_mock: respx.MockRouter) -> None:
    tree = with_sessions(bmc_tree())
    del tree["/redfish/v1/Systems/1"]
    _, logout = serve_sessions(respx_mock)
    serve_bmc(respx_mock, tree)

    with pytest.raises(RedfishError, match="returned HTTP 404"):
        await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert logout.call_count == 1


async def test_a_failed_logout_does_not_fail_the_read(respx_mock: respx.MockRouter) -> None:
    serve_sessions(respx_mock, logout=500)
    serve_bmc(respx_mock, with_sessions(bmc_tree()))

    assert await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials()) == actual_node()


async def test_a_refused_login_is_an_authentication_error_after_one_post(
    respx_mock: respx.MockRouter,
) -> None:
    login = respx_mock.post(f"{BMC}{SESSIONS}").respond(401)
    logout = respx_mock.delete(f"{BMC}{SESSION}").respond(204)
    serve_bmc(respx_mock, with_sessions(bmc_tree()))

    with pytest.raises(RedfishAuthError):
        await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert login.call_count == 1
    assert logout.call_count == 0


async def test_a_bmc_that_wants_credentials_for_its_service_root_is_read_with_basic_authentication(
    respx_mock: respx.MockRouter,
) -> None:
    tree = bmc_tree()

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != BASIC:
            return httpx.Response(401)
        return httpx.Response(200, json=tree[request.url.path])

    route = respx_mock.route(host=BMC_ADDRESS).mock(side_effect=respond)

    node = await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials(username="admin"))

    assert node == actual_node()
    assert [call.response.status_code for call in route.calls][:2] == [401, 200]


async def test_a_session_is_used_instead_of_basic_authentication_that_the_service_root_needed(
    respx_mock: respx.MockRouter,
) -> None:
    tree = with_sessions(bmc_tree())
    serve_sessions(respx_mock)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != BASIC and request.headers.get("X-Auth-Token") != TOKEN:
            return httpx.Response(401)
        return httpx.Response(200, json=tree[request.url.path])

    route = respx_mock.route(host=BMC_ADDRESS).mock(side_effect=respond)

    await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials(username="admin"))

    assert "Authorization" not in route.calls.last.request.headers
    assert route.calls.last.request.headers["X-Auth-Token"] == TOKEN


async def test_a_bmc_that_refuses_the_credentials_even_with_basic_authentication_is_an_authentication_error(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.route(host=BMC_ADDRESS).respond(401)

    with pytest.raises(RedfishAuthError):
        await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())

    assert route.call_count == 2


async def test_missing_links_read_as_empty(respx_mock: respx.MockRouter) -> None:
    serve_bmc(respx_mock, {"/redfish/v1": {"@odata.id": "/redfish/v1"}})

    assert await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials()) == ActualNode()


async def test_an_unexpected_payload_is_an_error(respx_mock: respx.MockRouter) -> None:
    tree = bmc_tree()
    del tree["/redfish/v1/Systems/1/Processors/GPU_0"]["Id"]
    serve_bmc(respx_mock, tree)

    with pytest.raises(RedfishError, match="unexpected Redfish payload"):
        await RedfishReader(ACCESS).read(BMC_ADDRESS, credentials())


async def test_ipv6_addresses_are_bracketed(respx_mock: respx.MockRouter) -> None:
    route = serve_bmc(respx_mock, bmc_tree(), address="2001:db8::11")

    await RedfishReader(BmcAccess(scheme="http")).read("2001:db8::11", credentials())

    assert str(route.calls[0].request.url) == "http://[2001:db8::11]/redfish/v1"
