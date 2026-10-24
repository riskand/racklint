# racklint/tests/test_netbox.py
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from racklint.errors import NetBoxError
from racklint.models import ExpectedNode, ExpectedSwitch, UncheckableLink
from racklint.netbox import NetBoxSource, auth_header, netbox_client
from tests.factories import (
    RULES,
    expected_node,
    expected_switch,
    far_interface,
    netbox_device,
    netbox_far_interfaces,
    netbox_interfaces,
    netbox_port,
    netbox_switch,
    netbox_switch_ports,
    page,
)

NETBOX = "https://netbox.test"


@pytest.fixture
async def source() -> AsyncIterator[NetBoxSource]:
    async with netbox_client(NETBOX, "nbt_abc.def") as client:
        yield NetBoxSource(client)


def serve(router: respx.MockRouter, endpoint: str, *pages: dict[str, Any]) -> respx.Route:
    """Answer the endpoint's calls with `pages`, one per call."""
    return router.get(f"{NETBOX}/api/{endpoint}/").mock(
        side_effect=[httpx.Response(200, json=p) for p in pages]
    )


async def test_reads_every_device_in_a_rack(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    racks = serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    devices = serve(
        respx_mock,
        "dcim/devices",
        page(netbox_device(), netbox_device(2, "n02", serial="", oob_ip=None, config_context={})),
    )
    interfaces = serve(respx_mock, "dcim/interfaces", page(*netbox_interfaces()))

    nodes = await source.rack("R01")

    assert nodes == [expected_node(), ExpectedNode(name="n02", device_type="gpu-node-h100")]
    assert racks.calls.last.request.url.params["name"] == "R01"
    assert devices.calls.last.request.url.params["rack_id"] == "7"
    query = interfaces.calls.last.request.url.params
    assert query.get_list("device_id") == ["1", "2"]
    assert (query["kind"], query["mgmt_only"]) == ("physical", "false")


async def test_sends_the_token_and_the_site(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    racks = serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc2"}}))
    serve(respx_mock, "dcim/devices", page(netbox_device()))
    serve(respx_mock, "dcim/interfaces", page())

    await source.rack("R01", site="dc2")

    assert racks.calls.last.request.url.params["site"] == "dc2"
    assert racks.calls.last.request.headers["Authorization"] == "Bearer nbt_abc.def"


async def test_follows_pages(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    devices = serve(
        respx_mock, "dcim/devices", page(netbox_device(), count=2), page(netbox_device(2, "n02"), count=2)
    )
    serve(respx_mock, "dcim/interfaces", page())

    nodes = await source.rack("R01")

    assert [node.name for node in nodes] == ["n01", "n02"]
    assert [call.request.url.params["offset"] for call in devices.calls] == ["0", "1"]


async def test_an_unknown_rack_is_an_error(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    serve(respx_mock, "dcim/racks", page())

    with pytest.raises(NetBoxError, match="rack 'R01' not found"):
        await source.rack("R01")


async def test_a_rack_name_in_several_sites_needs_a_site(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(
        respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc2"}}, {"id": 8, "site": {"slug": "dc1"}})
    )

    with pytest.raises(NetBoxError, match=r"several sites \(dc1, dc2\); pass --site"):
        await source.rack("R01")


async def test_an_empty_rack_is_an_error(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    serve(respx_mock, "dcim/devices", page())

    with pytest.raises(NetBoxError, match="rack 'R01' has no devices"):
        await source.rack("R01")


async def test_reads_devices_by_name(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    devices = serve(respx_mock, "dcim/devices", page(netbox_device(), netbox_device(2, "n02")))
    serve(respx_mock, "dcim/interfaces", page())

    nodes = await source.devices(["n01", "n02"])

    assert [node.name for node in nodes] == ["n01", "n02"]
    assert devices.calls.last.request.url.params.get_list("name") == ["n01", "n02"]


async def test_unknown_devices_are_an_error(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device()))

    with pytest.raises(NetBoxError, match="devices not found: n08, n09"):
        await source.devices(["n01", "n09", "n08"])


async def test_an_invalid_config_context_is_an_error(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device(config_context={"racklint": {"gpu": {}}})))
    serve(respx_mock, "dcim/interfaces", page())

    with pytest.raises(NetBoxError, match="n01: invalid 'racklint' config context"):
        await source.devices(["n01"])


async def test_an_invalid_credential_set_name_is_an_invalid_config_context(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(
        respx_mock, "dcim/devices", page(netbox_device(config_context={"racklint": {"credentials": "../x"}}))
    )
    serve(respx_mock, "dcim/interfaces", page())

    with pytest.raises(NetBoxError, match="n01: invalid 'racklint' config context"):
        await source.devices(["n01"])


async def test_a_server_carries_the_credential_set_its_config_context_names(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    context = {"racklint": {"credentials": "bmc-dc1"}}
    serve(respx_mock, "dcim/devices", page(netbox_device(config_context=context)))
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.credential_set == "bmc-dc1"


async def test_a_switch_carries_the_credential_set_its_config_context_names(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    context = {"racklint": {"kind": "switch", "credentials": "leaf-dc1"}}
    serve(respx_mock, "dcim/devices", page(netbox_switch(config_context=context)))
    serve(respx_mock, "dcim/interfaces", page())
    serve(respx_mock, "dcim/cables", page())

    [node] = await source.devices(["leaf1"])

    assert node.credential_set == "leaf-dc1"


async def test_a_server_carries_the_platform_its_config_context_names(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(
        respx_mock, "dcim/devices", page(netbox_device(config_context={"racklint": {"platform": "openbmc"}}))
    )
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.platform == "openbmc"


async def test_a_switch_carries_the_platform_and_gnmi_port_its_config_context_names(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    context = {"racklint": {"kind": "switch", "platform": "srlinux", "gnmi_port": 8080}}
    serve(respx_mock, "dcim/devices", page(netbox_switch(config_context=context)))
    serve(respx_mock, "dcim/interfaces", page())
    serve(respx_mock, "dcim/cables", page())

    [node] = await source.devices(["leaf1"])

    assert isinstance(node, ExpectedSwitch)
    assert (node.platform, node.gnmi_port) == ("srlinux", 8080)


async def test_a_device_whose_config_context_names_no_platform_has_none(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device()))
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.platform is None


async def test_a_device_whose_config_context_names_no_credential_set_has_none(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device()))
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.credential_set is None


async def test_a_failed_request_is_an_error(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    respx_mock.get(f"{NETBOX}/api/dcim/racks/").respond(403)

    with pytest.raises(NetBoxError, match=r"^NetBox request failed: HTTP 403$"):
        await source.rack("R01")


async def test_an_answer_that_is_not_json_is_an_error(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    respx_mock.get(f"{NETBOX}/api/dcim/racks/").respond(200, text="<html>")

    with pytest.raises(NetBoxError, match="NetBox request failed"):
        await source.rack("R01")


async def test_reads_a_switch_with_its_links_to_other_switches(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    devices = serve(
        respx_mock,
        "dcim/devices",
        page(netbox_switch()),
        # The far ends, with their config contexts.
        page(netbox_switch(21, "spine1"), netbox_device(1, "n01")),
    )
    interfaces = serve(respx_mock, "dcim/interfaces", page(*netbox_switch_ports()), netbox_far_interfaces())
    cables = serve(respx_mock, "dcim/cables", page({"id": 101}, {"id": 102}, {"id": 103}))

    nodes = await source.rack("R01")

    assert nodes == [expected_switch()]
    assert sorted(devices.calls.last.request.url.params.get_list("id")) == ["1", "21"]
    assert sorted(interfaces.calls.last.request.url.params.get_list("id")) == ["1001", "2101", "2102"]
    assert cables.calls.last.request.url.params.get_list("status") == ["planned", "connected"]


async def test_a_link_carries_the_label_of_its_far_interface(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    serve(
        respx_mock,
        "dcim/devices",
        page(netbox_switch()),
        page(netbox_switch(21, "spine1"), netbox_device(1, "n01")),
    )
    serve(
        respx_mock,
        "dcim/interfaces",
        page(*netbox_switch_ports()),
        netbox_far_interfaces({2101: "Eth1/1"}),
    )
    serve(respx_mock, "dcim/cables", page({"id": 101}, {"id": 102}, {"id": 103}))

    [switch] = await source.rack("R01")

    assert isinstance(switch, ExpectedSwitch)
    # A far interface with an empty label has none.
    assert [link.peer_interface_label for link in switch.links] == ["Eth1/1", None]


async def test_a_switch_without_cables_asks_for_no_far_interfaces(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_switch()))
    interfaces = serve(respx_mock, "dcim/interfaces", page(netbox_port("ethernet-1/3")))
    serve(respx_mock, "dcim/cables", page())

    await source.devices(["leaf1"])

    assert interfaces.call_count == 1


async def test_a_switch_link_whose_cable_is_not_planned_or_connected_is_ignored(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    serve(
        respx_mock,
        "dcim/devices",
        page(netbox_switch()),
        page(netbox_switch(21, "spine1"), netbox_device(1, "n01")),
    )
    serve(respx_mock, "dcim/interfaces", page(*netbox_switch_ports()), netbox_far_interfaces())
    serve(respx_mock, "dcim/cables", page({"id": 101}, {"id": 103}))  # cable 102 is being decommissioned

    [switch] = await source.rack("R01")

    assert isinstance(switch, ExpectedSwitch)
    assert [link.interface for link in switch.links] == ["ethernet-1/1"]


async def test_a_link_through_a_patch_panel_reaches_the_traced_far_end(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    [first, second, *rest] = netbox_switch_ports()
    through_a_panel = second | {
        "link_peers": [{"device": {"id": 31, "name": "pp-r01"}, "name": "1"}],
        "link_peers_type": "dcim.frontport",
    }
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    serve(
        respx_mock,
        "dcim/devices",
        page(netbox_switch()),
        page(netbox_switch(21, "spine1"), netbox_device(1, "n01")),
    )
    serve(respx_mock, "dcim/interfaces", page(first, through_a_panel, *rest), netbox_far_interfaces())
    serve(respx_mock, "dcim/cables", page({"id": 101}, {"id": 102}, {"id": 103}))

    assert await source.rack("R01") == [expected_switch()]


async def test_a_link_that_cannot_be_checked_is_kept_with_the_reason(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    unknown_far_end = netbox_port("ethernet-1/6", 104, far_interface(2201, 22, "spine2", "ethernet-1/1"))
    untraced = netbox_port(
        "ethernet-1/7",
        105,
        link_peers=[{"device": {"id": 31, "name": "pp-r01"}, "name": "2"}],
        link_peers_type="dcim.frontport",
    )
    serve(respx_mock, "dcim/racks", page({"id": 7, "site": {"slug": "dc1"}}))
    serve(
        respx_mock,
        "dcim/devices",
        page(netbox_switch()),
        page(
            netbox_switch(21, "spine1"),
            netbox_device(1, "n01"),
            netbox_switch(22, "spine2", config_context={}),
        ),
    )
    serve(
        respx_mock,
        "dcim/interfaces",
        page(*netbox_switch_ports(), unknown_far_end, untraced),
        netbox_far_interfaces(),
    )
    serve(respx_mock, "dcim/cables", page(*({"id": cable} for cable in range(101, 106))))

    [switch] = await source.rack("R01")

    assert isinstance(switch, ExpectedSwitch)
    assert switch.links == expected_switch().links
    assert switch.uncheckable_links == (
        UncheckableLink(interface="ethernet-1/6", reason="spine2 has no racklint config context"),
        UncheckableLink(interface="ethernet-1/7", reason="its cable does not reach an interface in NetBox"),
    )


async def test_a_server_reads_its_firmware_rules_from_the_config_context(
    respx_mock: respx.MockRouter, source: NetBoxSource
) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device()))
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.firmware == RULES


async def test_a_server_ignores_the_primary_ip(respx_mock: respx.MockRouter, source: NetBoxSource) -> None:
    serve(respx_mock, "dcim/devices", page(netbox_device(oob_ip=None, primary_ip={"address": "10.0.0.5/24"})))
    serve(respx_mock, "dcim/interfaces", page())

    [node] = await source.devices(["n01"])

    assert node.address is None


@pytest.mark.parametrize(
    ("token", "header"),
    [("nbt_abc.def", "Bearer nbt_abc.def"), ("0123456789abcdef", "Token 0123456789abcdef")],
)
def test_v2_tokens_use_bearer_and_legacy_tokens_use_token(token: str, header: str) -> None:
    assert auth_header(token) == header
