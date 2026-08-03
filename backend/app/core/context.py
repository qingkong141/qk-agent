from contextvars import ContextVar

request_context: ContextVar[dict] = ContextVar("request_context", default={})


def set_request_context(user_id: str, workspace: str = "default") -> None:
    request_context.set({"user_id": user_id, "workspace": workspace})


def get_request_context() -> dict:
    return request_context.get()
