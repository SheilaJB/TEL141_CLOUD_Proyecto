"""Temporal activities for Linux adapter actions."""

from collections.abc import Callable
from typing import Any

from contracts.adapter import AdapterActionInput, AdapterActionOutput
from contracts.plan import ActionOp, Executor
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import handlers
from .errors import NonRetryableError, RetryableError

Handler = Callable[[AdapterActionInput], dict[str, Any]]

_HANDLERS: dict[ActionOp, Handler] = {
    ActionOp.CREATE_LINK: handlers.create_link,
    ActionOp.ATTACH_PORT: handlers.attach_port,
    ActionOp.CREATE_VM: handlers.create_vm,
    ActionOp.DESTROY_SLICE_HOST: handlers.destroy_slice_host,
}
_ALLOWED_FACTS = {
    "pid",
    "vnc_port",
    "state",
    "overlay_ref",
    "seed_sha256",
    "virtual_size_mb",
    "tap_names",
    "veth_names",
    "destroyed_vms",
    "destroyed_bridge",
}


def _dispatch(request: AdapterActionInput, operation: ActionOp) -> AdapterActionOutput:
    """Dispatch one complete action without enrichment or exception masking."""
    if request.action.executor != Executor.ADAPTER:
        raise ApplicationError("EXECUTOR_MISMATCH", type="EXECUTOR_MISMATCH", non_retryable=True)
    if request.action.op != operation:
        raise ApplicationError("ACTION_MISMATCH", type="ACTION_MISMATCH", non_retryable=True)
    handler = _HANDLERS[operation]
    try:
        facts = handler(request)
    except NonRetryableError as exc:
        raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
    except RetryableError as exc:
        raise ApplicationError(str(exc), type=exc.code, non_retryable=False) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise ApplicationError(str(exc), type="INVALID_ACTION", non_retryable=True) from exc
    except RuntimeError as exc:
        raise ApplicationError(str(exc), type="COMMAND_FAILED", non_retryable=True) from exc
    already_existed = bool(facts.pop("_already_existed", False))
    already_absent = bool(facts.pop("_already_absent", False))
    unexpected = set(facts) - _ALLOWED_FACTS
    if unexpected:
        raise ApplicationError(
            f"Unexpected facts: {sorted(unexpected)}",
            type="INVALID_FACTS",
            non_retryable=True,
        )
    return AdapterActionOutput(
        action_id=request.action.id,
        operation=operation,
        idempotency_key=request.idempotency_key,
        already_existed=already_existed,
        already_absent=already_absent,
        facts=facts,
    )


@activity.defn(name="create_link")
async def create_link(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.CREATE_LINK)


@activity.defn(name="attach_port")
async def attach_port(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.ATTACH_PORT)


@activity.defn(name="create_vm")
async def create_vm(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.CREATE_VM)


@activity.defn(name="destroy_slice_host")
async def destroy_slice_host(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.DESTROY_SLICE_HOST)


async def _dispatch_async(
    request: AdapterActionInput, operation: ActionOp
) -> AdapterActionOutput:
    import asyncio

    return await asyncio.to_thread(_dispatch, request, operation)


ADAPTER_ACTIVITIES = [
    create_link,
    attach_port,
    create_vm,
    destroy_slice_host,
]
