# racklint/src/racklint/baseline.py
"""A firmware baseline file: approved versions per device type, overriding the rules planned in NetBox."""

from pathlib import Path
from typing import Self

import yaml
from pydantic import ValidationError

from racklint.errors import BaselineError
from racklint.models import ExpectedNode, ExpectedSwitch, FirmwareRule, FrozenModel


class FirmwareBaseline(FrozenModel):
    device_types: dict[str, tuple[FirmwareRule, ...]]

    @classmethod
    def load(cls, path: Path) -> Self:
        try:
            return cls.model_validate(yaml.safe_load(path.read_text()))
        except (OSError, yaml.YAMLError, ValidationError) as error:
            raise BaselineError(f"cannot load firmware baseline {path}: {error}") from error

    # The union lets a caller with a plan of both kinds apply the baseline to every device at once.
    def applied_to[PlannedT: (ExpectedNode, ExpectedSwitch, ExpectedNode | ExpectedSwitch)](
        self, node: PlannedT
    ) -> PlannedT:
        """The node with its planned rules replaced by this baseline's rules for its device type."""
        return node.model_copy(update={"firmware": self.device_types.get(node.device_type, ())})
