"""Local access must not become a production, remote or general API bypass."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from starlette.requests import Request
from app.local_studio import LOCAL_STUDIO_TOKEN, local_studio_principal
from app.config import settings


def request(path="/api/v1/datasets", host="127.0.0.1", origin=None):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [(b"origin", origin.encode())] if origin else [], "client": (host, 12345), "server": ("127.0.0.1", 8017), "scheme": "http", "query_string": b""})


settings.DEBUG = True
settings.LOCAL_STUDIO_NO_LOGIN = True
assert local_studio_principal(request(), LOCAL_STUDIO_TOKEN)["auth_type"] == "local_studio"
assert local_studio_principal(request("/api/v1/studio/artifacts"), LOCAL_STUDIO_TOKEN)
assert local_studio_principal(request(), "real-token") is None
for req in [None, request(host="192.168.10.25"), request("/api/v1/auth/users"), request("/api/v1/studio-other"), request(origin="https://untrusted.example")]:
    try:
        local_studio_principal(req, LOCAL_STUDIO_TOKEN)
        raise AssertionError("unexpected local access")
    except HTTPException as error:
        assert error.status_code == 401
for debug, enabled in [(False, True), (True, False), (False, False)]:
    settings.DEBUG, settings.LOCAL_STUDIO_NO_LOGIN = debug, enabled
    try:
        local_studio_principal(request(), LOCAL_STUDIO_TOKEN)
        raise AssertionError("flag gate failed")
    except HTTPException as error:
        assert error.status_code == 401
print("PASS: explicit flags, loopback and studio path restrictions; normal credentials unaffected")
