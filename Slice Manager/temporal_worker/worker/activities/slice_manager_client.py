from typing import Any

import httpx
from temporalio.exceptions import ApplicationError

from contracts.errors import ErrorCode, ErrorResponse
from contracts.placement import (
    PlacementReserveRequest,
    PlacementReserveResponse,
    PlacementRollbackRequest,
    PlacementRollbackResponse,
)
from contracts.registry import (
    ApprovalState,
    DeploymentContext,
    FinishDeployment,
    RoundFacts,
    RoundFactsResult,
    StartDeployment,
)


class SliceManagerClient:
    def __init__(self, base_url: str, service_token: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_plan(self, deployment_id: int) -> DeploymentContext:
        response = await self._request(
            "GET", f"/internal/deployments/{deployment_id}/plan"
        )
        return DeploymentContext.model_validate(response)

    async def get_approval_state(self, deployment_id: int) -> ApprovalState:
        response = await self._request(
            "GET", f"/internal/deployments/{deployment_id}/approval"
        )
        return ApprovalState.model_validate(response)

    async def reserve_placement(
        self, request: PlacementReserveRequest
    ) -> PlacementReserveResponse:
        response = await self._request(
            "POST",
            "/internal/placements/reserve",
            json=request.model_dump(mode="json"),
        )
        return PlacementReserveResponse.model_validate(response)

    async def rollback_placement(
        self, request: PlacementRollbackRequest
    ) -> PlacementRollbackResponse:
        response = await self._request(
            "POST",
            "/internal/placements/rollback",
            json=request.model_dump(mode="json"),
        )
        return PlacementRollbackResponse.model_validate(response)

    async def start_deployment(
        self, deployment_id: int, request: StartDeployment
    ) -> None:
        await self._request(
            "POST",
            f"/internal/deployments/{deployment_id}/start",
            json=request.model_dump(mode="json"),
        )

    async def record_round_facts(
        self, deployment_id: int, facts: RoundFacts
    ) -> RoundFactsResult:
        response = await self._request(
            "POST",
            f"/internal/deployments/{deployment_id}/rounds/{facts.round_num}/facts",
            json=facts.model_dump(mode="json"),
        )
        return RoundFactsResult.model_validate(response)

    async def finish_deployment(
        self, deployment_id: int, finish: FinishDeployment
    ) -> None:
        await self._request(
            "POST",
            f"/internal/deployments/{deployment_id}/finish",
            json=finish.model_dump(mode="json"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        if not self._service_token:
            raise ApplicationError(
                "ORCHESTRATOR_INTERNAL_SERVICE_TOKEN is not configured",
                type=ErrorCode.CONFLICT.value,
                non_retryable=True,
            )
        try:
            response = await self._client.request(
                method,
                path,
                json=json,
                headers={"X-Internal-Token": self._service_token},
            )
        except httpx.TransportError as exc:
            raise ApplicationError(
                "Slice Manager is unavailable",
                type=ErrorCode.BACKEND_UNAVAILABLE.value,
                non_retryable=False,
            ) from exc
        if response.is_success:
            if response.status_code == 204 or not response.content:
                return None
            return response.json()

        detail = self._error_detail(response)
        retryable = response.status_code >= 500 or response.status_code == 429
        code = detail.code.value if detail else self._code_for_status(response.status_code)
        message = detail.message if detail else f"Slice Manager returned HTTP {response.status_code}"
        raise ApplicationError(
            message,
            type=code,
            non_retryable=not retryable,
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> ErrorResponse | None:
        try:
            return ErrorResponse.model_validate(response.json())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _code_for_status(status_code: int) -> str:
        if status_code == 404:
            return ErrorCode.RESOURCE_NOT_FOUND.value
        if status_code == 409:
            return ErrorCode.CONFLICT.value
        if status_code == 422:
            return ErrorCode.INVALID_SPEC.value
        if status_code >= 500 or status_code == 429:
            return ErrorCode.BACKEND_UNAVAILABLE.value
        return ErrorCode.CONFLICT.value
