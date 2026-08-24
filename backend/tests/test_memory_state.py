import uuid
from asyncio import sleep

import pytest

from app.db.session import async_session
from app.memory.manager import memory_manager
from app.models.conversation import Conversation


async def _create_conversation() -> str:
    conversation_id = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Conversation(
            id=conversation_id,
            user_id="test-user",
            title="新对话",
            workspace="test",
        ))
        await db.commit()
    return conversation_id


@pytest.mark.asyncio
async def test_memory_returns_last_interrupted_turn_only_when_latest_assistant_is_interrupted():
    conversation_id = await _create_conversation()

    await memory_manager.append(
        conversation_id,
        "医疗急救",
        "我可以介绍基础医疗急救知识，先从现场安全和呼救说起。",
        status="interrupted",
        metadata={"resumable": True},
    )

    interrupted = await memory_manager.get_last_interrupted_turn(conversation_id)
    assert interrupted is not None
    assert interrupted["user_message"] == "医疗急救"
    assert "现场安全" in interrupted["partial_output"]

    await sleep(0.01)
    await memory_manager.append(
        conversation_id,
        "护士",
        "护士通常负责护理评估、执行医嘱和患者沟通。",
        status="completed",
    )

    assert await memory_manager.get_last_interrupted_turn(conversation_id) is None


@pytest.mark.asyncio
async def test_memory_returns_last_needs_user_input_turn_only_when_latest_assistant_waits():
    conversation_id = await _create_conversation()

    await memory_manager.append(
        conversation_id,
        "你会什么",
        "我可以提供多种帮助。请问您现在需要了解哪个方面的信息呢？",
        status="needs_user_input",
    )

    pending = await memory_manager.get_last_needs_user_input_turn(conversation_id)
    assert pending is not None
    assert pending["user_message"] == "你会什么"
    assert "哪个方面" in pending["assistant_prompt"]

    await sleep(0.01)
    await memory_manager.append(
        conversation_id,
        "哈根达斯",
        "哈根达斯是一个冰淇淋品牌。",
        status="completed",
    )

    assert await memory_manager.get_last_needs_user_input_turn(conversation_id) is None
