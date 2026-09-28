"""Revocable publication links. Publisher credentials stay encrypted on the server."""
import hashlib
import json
import secrets

from app.models.publication import PublicationAccess


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def seal(value):
    from app.api.agent_models import cipher
    return cipher().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    from app.api.agent_models import cipher
    return json.loads(cipher().decrypt(value.encode()))


async def revoke(db, artifact_id):
    item = await db.get(PublicationAccess, artifact_id)
    if item:
        item.enabled = False
        item.token_hash = None
        item.credentials = ''


async def configure(db, artifact_id, require_login, headers, user):
    if require_login:
        await revoke(db, artifact_id)
        return
    item = await db.get(PublicationAccess, artifact_id)
    if not item:
        item = PublicationAccess(artifact_id=artifact_id)
        db.add(item)
    previous = unseal(item.credentials) if item.enabled and item.credentials else {}
    token = previous.get('token') or secrets.token_urlsafe(32)
    allowed = ('authorization', 'x-platform-token', 'x-platform-refresh-token', 'x-platform-session-id', 'x-end-user-id')
    credentials = {key: headers[key] for key in allowed if headers.get(key)}
    item.enabled = True
    item.token_hash = digest(token)
    item.credentials = seal({'token': token, 'headers': credentials})
    item.auth_type = user.get('auth_type', 'platform')
