DEFAULT_TITLES = frozenset({"新对话", "新会话", "New Chat"})


def derive_conversation_title(text: str, *, max_len: int = 50) -> str:
    cleaned = text.strip().replace("\n", " ")
    return cleaned[:max_len] if cleaned else "新对话"


def is_default_conversation_title(title: str | None) -> bool:
    if not title:
        return True
    return title.strip() in DEFAULT_TITLES
