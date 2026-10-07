from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Response, status

from contracts.slices import (
    CreateSliceRequest,
    CreateVersionRequest,
    PlanPreview,
    SliceCreated,
    ValidationPreview,
    VersionCreated,
)
from slice_manager.app.api.dependencies import (
    get_current_user_id,
    get_slice_lifecycle_service,
)
from slice_manager.app.domain.slice_lifecycle import SliceLifecycleService

router = APIRouter()


@router.post(
    "/slices",
    status_code=status.HTTP_201_CREATED,
    response_model=SliceCreated,
)
async def create_slice(
    body: CreateSliceRequest,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
) -> SliceCreated:
    return await service.create_slice(user_id=user_id, request=body)


@router.post(
    "/slices/{slice_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionCreated,
)
async def create_version(
    slice_id: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
    body: CreateVersionRequest | None = Body(default=None),
) -> VersionCreated:
    return await service.create_version(
        slice_id=slice_id,
        user_id=user_id,
        request=body or CreateVersionRequest(),
    )


@router.put(
    "/slices/{slice_id}/versions/{version_number}",
    response_model=VersionCreated,
)
async def replace_version_spec(
    slice_id: int,
    version_number: int,
    spec: dict[str, Any],
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
) -> VersionCreated:
    return await service.replace_version_spec(
        slice_id=slice_id,
        version_number=version_number,
        user_id=user_id,
        spec=spec,
    )


@router.delete(
    "/slices/{slice_id}/versions/{version_number}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_version(
    slice_id: int,
    version_number: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
) -> Response:
    await service.delete_version(
        slice_id=slice_id,
        version_number=version_number,
        user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/slices/{slice_id}/versions/{version_number}/validate",
    response_model=ValidationPreview,
)
async def validate_version(
    slice_id: int,
    version_number: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
) -> ValidationPreview:
    return await service.validate_version(
        slice_id=slice_id,
        version_number=version_number,
        user_id=user_id,
    )


@router.post(
    "/slices/{slice_id}/versions/{version_number}/plan",
    response_model=PlanPreview,
)
async def preview_version_plan(
    slice_id: int,
    version_number: int,
    user_id: Annotated[int, Depends(get_current_user_id)],
    service: Annotated[
        SliceLifecycleService, Depends(get_slice_lifecycle_service)
    ],
) -> PlanPreview:
    return await service.plan_version(
        slice_id=slice_id,
        version_number=version_number,
        user_id=user_id,
    )
