import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from app.api.auth import create_token
from app.db.session import async_session
from app.dependencies import authenticate_principal
from app.main import app
from app.models.conversation import Conversation
from app.models.user import User
from app.services.conversation_access import get_or_create_owned_conversation


async def _create_user() -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4()}@example.com",
        hashed_password="unused",
        api_key=f"ma-{uuid.uuid4().hex[:24]}",
        is_active=True,
    )
    async with async_session() as db:
        db.add(user)
        await db.commit()
    return user


@pytest.mark.asyncio
async def test_token_and_api_key_resolve_to_server_side_principal():
    user = await _create_user()
    token = create_token(user.id, timedelta(minutes=5))

    async with async_session() as db:
        token_principal = await authenticate_principal(db, bearer_token=token)
        key_principal = await authenticate_principal(
            db,
            api_key=user.api_key,
            end_user_id="customer-1",
        )

    assert token_principal["id"] == user.id
    assert token_principal["auth_type"] == "jwt"
    assert key_principal["id"] == user.id
    assert key_principal["auth_type"] == "api_key"
    assert key_principal["external_user_id"] == "customer-1"


@pytest.mark.asyncio
async def test_conversation_id_cannot_be_claimed_by_another_user():
    owner = await _create_user()
    other = await _create_user()
    conversation_id = str(uuid.uuid4())

    async with async_session() as db:
        conversation = await get_or_create_owned_conversation(
            db,
            conversation_id=conversation_id,
            user_id=owner.id,
        )
        assert conversation.user_id == owner.id

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc_info:
            await get_or_create_owned_conversation(
                db,
                conversation_id=conversation_id,
                user_id=other.id,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_same_api_key_separates_external_users():
    app_owner = await _create_user()
    conversation_id = str(uuid.uuid4())

    async with async_session() as db:
        await get_or_create_owned_conversation(
            db,
            conversation_id=conversation_id,
            user_id=app_owner.id,
            external_user_id="customer-a",
        )

    async with async_session() as db:
        with pytest.raises(HTTPException) as exc_info:
            await get_or_create_owned_conversation(
                db,
                conversation_id=conversation_id,
                user_id=app_owner.id,
                external_user_id="customer-b",
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rest_chat_rejects_foreign_conversation_before_agent_runs():
    owner = await _create_user()
    other = await _create_user()
    conversation_id = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Conversation(
            id=conversation_id,
            user_id=owner.id,
            title="private",
            workspace="default",
        ))
        await db.commit()

    token = create_token(other.id, timedelta(minutes=5))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "hello", "conversation_id": conversation_id},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_key_conversation_list_is_scoped_by_end_user():
    app_owner = await _create_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing_scope = await client.get(
            "/api/v1/conversations",
            headers={"X-API-Key": app_owner.api_key},
        )
        created_a = await client.post(
            "/api/v1/conversations",
            headers={"X-API-Key": app_owner.api_key, "X-End-User-ID": "customer-a"},
            json={"title": "A"},
        )
        created_b = await client.post(
            "/api/v1/conversations",
            headers={"X-API-Key": app_owner.api_key, "X-End-User-ID": "customer-b"},
            json={"title": "B"},
        )
        list_a = await client.get(
            "/api/v1/conversations",
            headers={"X-API-Key": app_owner.api_key, "X-End-User-ID": "customer-a"},
        )

    assert missing_scope.status_code == 400
    assert created_a.status_code == 200
    assert created_b.status_code == 200
    assert [item["title"] for item in list_a.json()] == ["A"]


def test_websocket_does_not_trust_client_supplied_user_id():
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/ws/{uuid.uuid4()}") as websocket:
            websocket.send_json({
                "type": "chat",
                "message": "hello",
                "user_id": "forged-user",
            })
            event = websocket.receive_json()

    assert event["type"] == "error"
    assert event["data"]["code"] == "UNAUTHORIZED"
