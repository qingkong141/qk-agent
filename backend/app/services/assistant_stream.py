"""Model chunks and cancellable NDJSON responses for studio assistants."""
import asyncio
import json
from contextlib import aclosing, suppress

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic_core import from_json


def text_content(content):
    if isinstance(content, str):
        return content
    return ''.join(block.get('text', '') for block in content if isinstance(block, dict))


async def model_response(model, messages, emit=None, *, structured=False, limit=25000):
    if emit is None:
        return await model.ainvoke(messages)
    result = None
    previous = ''
    async with aclosing(model.astream(messages)) as chunks:
        async for chunk in chunks:
            result = chunk if result is None else result + chunk
            text = text_content(result.content)
            if len(text) > limit:
                raise HTTPException(502, '模型输出过长，请缩小问题范围后重试')
            if structured:
                # Only expose the human-readable explanation, never partial SQL/operation JSON.
                candidate = text.lstrip()
                if candidate.startswith('```'):
                    candidate = candidate.partition('\n')[2]
                try:
                    value = from_json(candidate, allow_partial='trailing-strings')
                except ValueError:
                    continue
                text = value.get('explanation', '') if isinstance(value, dict) else ''
            if isinstance(text, str) and text and text != previous:
                previous = text
                await emit({'type': 'text', 'text': text})
    if result is None:
        raise HTTPException(502, '模型未返回内容，请重试')
    if result.invalid_tool_calls:
        raise HTTPException(502, '模型返回的工具参数不完整，请重试')
    if not text_content(result.content).strip() and not result.tool_calls:
        raise HTTPException(502, '模型未返回完整回答，请重试')
    return result


def answer_stream(operation, db):
    async def events():
        queue = asyncio.Queue(maxsize=4)

        async def emit(event):
            await queue.put(json.dumps(event, ensure_ascii=False, default=str) + '\n')

        async def execute():
            try:
                result = await operation(emit)
                await emit({'type': 'done', 'data': result})
            except Exception as exc:
                message = str(exc.detail) if isinstance(exc, HTTPException) else '本次处理失败，请稍后重试'
                await emit({'type': 'error', 'message': message})

        task = asyncio.create_task(execute())
        try:
            yield json.dumps({'type': 'progress', 'message': '正在读取数据范围…'}, ensure_ascii=False) + '\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield '\n'
                    continue
                yield event
                if json.loads(event)['type'] in ('done', 'error'):
                    break
        finally:
            # Starlette cancellation must not interrupt SQLAlchemy connection cleanup.
            with anyio.CancelScope(shield=True):
                if not task.cancelling(): task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await db.rollback()

    return StreamingResponse(events(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
