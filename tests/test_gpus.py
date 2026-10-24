# racklint/tests/test_gpus.py
from racklint.gpus import GPU_RULES, OpenBmcAccelerators, RedfishGpus
from racklint.models import ActualNode, Gpu, GpuSpec, Processor
from tests.factories import GPU_MODEL

PLANNED = GpuSpec(count=2, model=GPU_MODEL)
OTHER_MODEL = "NVIDIA A100-SXM4-80GB"

CPU = Processor(id="CPU_0", type="CPU", model="Intel(R) Xeon(R) Platinum 8480+")
GPU = Processor(id="GPU_0", type="GPU", model=GPU_MODEL)
OTHER_GPU = Processor(id="GPU_1", type="GPU", model=OTHER_MODEL)
ACCELERATOR = Processor(id="accelerator0", type="Accelerator", model=GPU_MODEL)
OTHER_ACCELERATOR = Processor(id="dpu0", type="Accelerator", model="BlueField-3")


def reporting(*processors: Processor) -> ActualNode:
    return ActualNode(processors=processors)


def test_the_redfish_rule_finds_every_gpu_whatever_its_model() -> None:
    actual = reporting(CPU, GPU, OTHER_GPU, ACCELERATOR)

    assert RedfishGpus().find(actual, PLANNED) == (
        Gpu(id="GPU_0", model=GPU_MODEL),
        Gpu(id="GPU_1", model=OTHER_MODEL),
    )


def test_the_openbmc_rule_finds_the_accelerators_of_the_planned_model_only() -> None:
    actual = reporting(CPU, GPU, ACCELERATOR, OTHER_ACCELERATOR)

    assert OpenBmcAccelerators().find(actual, PLANNED) == (Gpu(id="accelerator0", model=GPU_MODEL),)


def test_a_rule_finds_nothing_in_a_node_without_its_processors() -> None:
    actual = reporting(CPU)

    assert RedfishGpus().find(actual, PLANNED) == ()
    assert OpenBmcAccelerators().find(actual, PLANNED) == ()


def test_the_rules_are_tried_in_order_the_standard_first() -> None:
    assert [type(rule) for rule in GPU_RULES] == [RedfishGpus, OpenBmcAccelerators]
