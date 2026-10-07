import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid5


PLAN_ID_NAMESPACE = UUID("b75f6539-4d80-4e1e-b674-798f4c87e653")
EntityType = Literal["vm", "link", "port"]


@dataclass(frozen=True, slots=True)
class PlanIdentityFactory:
    slice_id: int
    scope: str

    def entity_id(self, entity_type: EntityType, *natural_key: str) -> UUID:
        identity = json.dumps(
            [self.scope, self.slice_id, entity_type, *natural_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return uuid5(PLAN_ID_NAMESPACE, identity)
