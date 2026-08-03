import httpx
from loguru import logger

from app.config import settings


def format_llm_error(exc: Exception) -> str:
    msg = str(exc)
    if "Connection refused" in msg or "ConnectError" in msg or "Failed to establish" in msg:
        if settings.LLM_PROVIDER == "ollama" or settings.EMBEDDING_PROVIDER == "ollama":
            return (
                f"无法连接 Ollama 服务 ({settings.OLLAMA_BASE_URL})。"
                "请确认 Ollama 已启动且模型已下载。"
            )
    if "model" in msg.lower() and "not found" in msg.lower():
        return f"Ollama 模型未找到，请先执行: ollama pull {settings.LLM_MODEL}"
    if "Arrearage" in msg:
        return "DashScope 账户欠费或不可用，请充值或改用 LLM_PROVIDER=ollama"
    return msg[:500]


def _has_model(model_names: list[str], target: str) -> bool:
    base = target.split(":")[0]
    return any(
        n == target or n.startswith(f"{base}:") or n.split(":")[0] == base
        for n in model_names
    )


async def check_ollama_health() -> dict:
    """检查 Ollama 服务与所需模型是否可用"""
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    result: dict = {
        "url": base,
        "reachable": False,
        "models": [],
        "llm_model_ok": False,
        "embedding_model_ok": False,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            result["reachable"] = True
            model_names = [m.get("name", "") for m in resp.json().get("models", [])]
            result["models"] = model_names
            result["llm_model_ok"] = _has_model(model_names, settings.LLM_MODEL)
            result["embedding_model_ok"] = _has_model(model_names, settings.EMBEDDING_MODEL)
    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"Ollama health check failed: {e}")
    return result
