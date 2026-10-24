# racklint/src/racklint/gpus.py
"""How racklint finds a server's GPUs in what its BMC reports: one rule per way a BMC exposes them."""

from typing import Protocol

from racklint.models import ActualNode, Gpu, GpuSpec, Processor


class GpuRule(Protocol):
    """One way a BMC exposes its GPUs."""

    def find(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        """The GPUs this rule finds; none when the BMC does not expose them this way."""
        ...


class RedfishGpus:
    """The DMTF standard: every Processor of type GPU, whatever its model."""

    def find(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        return tuple(_gpu(processor) for processor in actual.processors if processor.type == "GPU")


class OpenBmcAccelerators:
    """Upstream OpenBMC (bmcweb) reports every GPU as a Processor of type Accelerator.

    The Redfish schema also uses Accelerator for devices that are not GPUs, such as DPUs, so only the
    Accelerators of the planned GPU model count.
    """

    def find(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        return tuple(
            _gpu(processor)
            for processor in actual.processors
            if processor.type == "Accelerator" and processor.model == planned.model
        )


# Tried in this order; the first rule that finds a GPU decides.
GPU_RULES: tuple[GpuRule, ...] = (RedfishGpus(), OpenBmcAccelerators())


def _gpu(processor: Processor) -> Gpu:
    return Gpu(id=processor.id, model=processor.model)
