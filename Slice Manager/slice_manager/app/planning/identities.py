import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid5


PLAN_ID_NAMESPACE = UUID("b75f6539-4d80-4e1e-b674-798f4c87e653")
EntityType = Literal["vm", "link", "port"]


@dataclass(frozen=True, slots=True)
class PlanIdentityFactory:
    slice_id: int
    scope: str
    # P37: IDs ya persistidos, indexados por (entity_type, natural_key).
    existing_ids: Mapping[tuple[EntityType, tuple[str, ...]], UUID] | None = field(
        default=None, compare=False
    )

    def entity_id(self, entity_type: EntityType, *natural_key: str) -> UUID:
        if self.existing_ids is not None:
            existing = self.existing_ids.get((entity_type, natural_key))
            if existing is not None:
                return existing
        identity = json.dumps(
            [self.scope, self.slice_id, entity_type, *natural_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return uuid5(PLAN_ID_NAMESPACE, identity)
