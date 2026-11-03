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
    ActionOp.DELETE_LINK: handlers.delete_link,
    ActionOp.ATTACH_PORT: handlers.attach_port,
    ActionOp.DETACH_PORT: handlers.detach_port,
    ActionOp.CREATE_VM: handlers.create_vm,
    ActionOp.DELETE_VM: handlers.delete_vm,
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

    # Detectar si es una ejecucion de rollback / compensacion de Temporal
    is_compensating = (
        ":compensate:" in request.idempotency_key
        or "undo:" in str(request.action.id)
        or operation in {ActionOp.DELETE_VM, ActionOp.DETACH_PORT, ActionOp.DELETE_LINK}
        or request.action.op in {ActionOp.DELETE_VM, ActionOp.DETACH_PORT, ActionOp.DELETE_LINK}
    )

    if is_compensating:
        if operation in {ActionOp.CREATE_VM, ActionOp.DELETE_VM} or request.action.op in {ActionOp.CREATE_VM, ActionOp.DELETE_VM}:
            handler = handlers.delete_vm
            effective_op = ActionOp.DELETE_VM
        elif operation in {ActionOp.ATTACH_PORT, ActionOp.DETACH_PORT} or request.action.op in {ActionOp.ATTACH_PORT, ActionOp.DETACH_PORT}:
            handler = handlers.detach_port
            effective_op = ActionOp.DETACH_PORT
        elif operation in {ActionOp.CREATE_LINK, ActionOp.DELETE_LINK} or request.action.op in {ActionOp.CREATE_LINK, ActionOp.DELETE_LINK}:
            handler = handlers.delete_link
            effective_op = ActionOp.DELETE_LINK
        elif operation == ActionOp.DESTROY_SLICE_HOST:
            handler = handlers.destroy_slice_host
            effective_op = ActionOp.DESTROY_SLICE_HOST
        else:
            handler = _HANDLERS.get(operation, _HANDLERS.get(request.action.op))
            effective_op = operation
    else:
        if request.action.op != operation:
            raise ApplicationError("ACTION_MISMATCH", type="ACTION_MISMATCH", non_retryable=True)
        handler = _HANDLERS[operation]
        effective_op = operation

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


@activity.defn(name="delete_vm")
async def delete_vm(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.DELETE_VM)


@activity.defn(name="detach_port")
async def detach_port(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.DETACH_PORT)


@activity.defn(name="delete_link")
async def delete_link(request: AdapterActionInput) -> AdapterActionOutput:
    return await _dispatch_async(request, ActionOp.DELETE_LINK)


ADAPTER_ACTIVITIES = [
    create_link,
    delete_link,
    attach_port,
    detach_port,
    create_vm,
    delete_vm,
    destroy_slice_host,
]
