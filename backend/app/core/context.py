from contextvars import ContextVar

request_context: ContextVar[dict] = ContextVar("request_context", default={})


def set_request_context(
    user_id: str,
    workspace: str = "default",
    *,
    external_user_id: str = "",
    conversation_id: str = "",
    run_id: str = "",
    approval_id: str = "",
) -> None:
    request_context.set({
        "user_id": user_id,
        "external_user_id": external_user_id,
        "workspace": workspace,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "approval_id": approval_id,
    })


def get_request_context() -> dict:
    return request_context.get()
