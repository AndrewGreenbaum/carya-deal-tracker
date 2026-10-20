"""Private API authentication and short-lived browser sessions.

Session signatures use HMAC-SHA256 with a per-process salt. Restarting the
single-worker API signs everyone out; credentials never enter browser builds.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from threading import RLock
from typing import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response


class Authentication:
    def __init__(self, key_provider, *, audience: str, lifetime: int = 3600):
        self.key_provider = key_provider
        self.audience = audience
        self.lifetime = lifetime
        self.salt = secrets.token_bytes(32)
        self.attempts: dict[str, deque] = {}
        self.revoked: dict[str, int] = {}
        self.session_listeners: dict[str, set[Callable[[], None]]] = {}
        self.lock = RLock()

    def configured_keys(self) -> list[str]:
        keys = [key.strip() for key in self.key_provider() if key and key.strip()]
        if not keys or any(len(key) < 32 or len(set(key)) < 8 or
                           key.lower() in {"dev-key", "change-me", "your-api-key"}
                           for key in keys):
            raise RuntimeError("Configure a random API access key of at least 32 characters before starting the API.")
        return keys

    def valid_key(self, candidate: str) -> bool:
        if not candidate or len(candidate) > 4096:
            return False
        return any(hmac.compare_digest(candidate.encode(), key.encode())
                   for key in self.configured_keys())

    def _signature(self, payload: str, key: str) -> str:
        return hmac.new(key.encode() + self.salt,
                        (self.audience + ":" + payload).encode(), hashlib.sha256).hexdigest()

    def issue(self) -> dict:
        expires = int(time.time()) + self.lifetime
        payload = f"v1.{expires}.{secrets.token_hex(24)}"
        token = payload + "." + self._signature(payload, self.configured_keys()[0])
        return {"access_token": token, "token_type": "Bearer", "expires_at": expires}

    def session_expiry(self, token: str) -> int | None:
        if len(token) > 512:
            return None
        try:
            version, raw_expiry, nonce, signature = token.split(".")
            expiry = int(raw_expiry)
        except (ValueError, TypeError):
            return None
        now = int(time.time())
        if version != "v1" or expiry <= now or expiry > now + self.lifetime or len(nonce) != 48:
            return None
        with self.lock:
            self.revoked = {key: end for key, end in self.revoked.items() if end > now}
            if hashlib.sha256(token.encode()).hexdigest() in self.revoked:
                return None
        payload = f"{version}.{raw_expiry}.{nonce}"
        if any(hmac.compare_digest(signature.encode(), self._signature(payload, key).encode())
               for key in self.configured_keys()):
            return expiry
        return None

    def allow_login(self, peer: str) -> bool:
        now = time.monotonic()
        with self.lock:
            for name in list(self.attempts):
                queue = self.attempts[name]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if not queue:
                    del self.attempts[name]
            # The global ceiling also bounds memory and distributed guesses.
            global_queue = self.attempts.setdefault("*", deque())
            peer_queue = self.attempts.setdefault(peer, deque())
            if len(global_queue) >= 100 or len(peer_queue) >= 5:
                return False
            global_queue.append(now)
            peer_queue.append(now)
            return True

    def revoke(self, token: str) -> None:
        listeners = []
        with self.lock:
            expiry = self.session_expiry(token)
            if expiry is not None:
                # A bounded revocation table; rekeying safely expires all sessions.
                if len(self.revoked) >= 10000:
                    self.salt = secrets.token_bytes(32)
                    self.revoked.clear()
                    listeners = [listener for group in self.session_listeners.values()
                                 for listener in group]
                    self.session_listeners.clear()
                else:
                    token_id = hashlib.sha256(token.encode()).hexdigest()
                    self.revoked[token_id] = expiry
                    listeners = list(self.session_listeners.pop(token_id, ()))
        for listener in listeners:
            listener()

    def watch_session(self, token: str, listener: Callable[[], None]) -> bool:
        """Register atomically with validation so logout cannot miss a new socket."""
        with self.lock:
            if self.session_expiry(token) is None:
                return False
            token_id = hashlib.sha256(token.encode()).hexdigest()
            self.session_listeners.setdefault(token_id, set()).add(listener)
            return True

    def unwatch_session(self, token: str, listener: Callable[[], None]) -> None:
        with self.lock:
            token_id = hashlib.sha256(token.encode()).hexdigest()
            listeners = self.session_listeners.get(token_id)
            if listeners is not None:
                listeners.discard(listener)
                if not listeners:
                    del self.session_listeners[token_id]


class AuthenticationMiddleware:
    def __init__(self, app, *, authentication: Authentication, prefix: str = "", health_path: str = "/health"):
        self.app = app
        self.authentication = authentication
        self.prefix = prefix.rstrip("/")
        self.health_path = health_path

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if self.prefix and not (path == self.prefix or path.startswith(self.prefix + "/")):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and (scope["method"] == "OPTIONS" or path == self.health_path):
            return await self.app(scope, receive, send)

        async def respond(status, detail):
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            response = JSONResponse({"detail": detail}, status_code=status,
                                    headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)

        try:
            self.authentication.configured_keys()
        except RuntimeError:
            return await respond(503, "API access is not configured.")

        auth_path = self.prefix + "/auth"
        if path == auth_path + "/login" and scope["type"] == "http":
            if scope["method"] != "POST":
                return await respond(405, "Use POST to sign in.")
            peer = str(scope.get("client", ("unknown",))[0])
            if not self.authentication.allow_login(peer):
                return await respond(429, "Too many sign-in attempts. Try again in a minute.")
            body = bytearray()
            try:
                while True:
                    message = await asyncio.wait_for(receive(), timeout=10)
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 4096:
                        return await respond(413, "Sign-in request is too large.")
                    if not message.get("more_body"):
                        break
                payload = json.loads(body)
                key = payload.get("api_key") if isinstance(payload, dict) else None
            except (ValueError, UnicodeDecodeError, asyncio.TimeoutError):
                return await respond(400, "Invalid sign-in request.")
            if not isinstance(key, str) or not self.authentication.valid_key(key):
                return await respond(401, "Invalid access key.")
            return await JSONResponse(self.authentication.issue(), headers={"Cache-Control": "no-store"})(scope, receive, send)

        headers = Headers(scope=scope)
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            token = ""
        if scope["type"] == "websocket":
            protocols = scope.get("subprotocols", [])
            if len(protocols) == 2 and protocols[0] == "carya-session":
                token = protocols[1]
        expiry = self.authentication.session_expiry(token) if token else None
        api_key = headers.get("x-api-key", "")
        if expiry is None and not self.authentication.valid_key(api_key):
            return await respond(401, "Sign in to access this API.")
        scope.setdefault("state", {})["authenticated"] = True

        if scope["type"] == "http" and path == auth_path + "/session":
            return await JSONResponse({"authenticated": True, "expires_at": expiry},
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)
        if scope["type"] == "http" and path == auth_path + "/logout":
            if scope["method"] != "POST":
                return await respond(405, "Use POST to sign out.")
            self.authentication.revoke(token)
            return await Response(status_code=204, headers={"Cache-Control": "no-store"})(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(message)

        if scope["type"] == "websocket" and expiry is not None:
            revoked = asyncio.Event()
            loop = asyncio.get_running_loop()

            def on_revoked():
                try:
                    loop.call_soon_threadsafe(revoked.set)
                except RuntimeError:
                    pass  # The socket's event loop has already shut down.

            if not self.authentication.watch_session(token, on_revoked):
                return await respond(401, "Sign in to access this API.")
            connection = asyncio.create_task(self.app(scope, receive, private_send))
            revocation = asyncio.create_task(revoked.wait())
            try:
                done, _ = await asyncio.wait(
                    {connection, revocation},
                    timeout=max(0, expiry - time.time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if connection in done:
                    return await connection
                connection.cancel()
                await asyncio.gather(connection, return_exceptions=True)
                await send({"type": "websocket.close", "code": 1008})
            finally:
                self.authentication.unwatch_session(token, on_revoked)
                connection.cancel()
                revocation.cancel()
                await asyncio.gather(connection, revocation, return_exceptions=True)
            return
        return await self.app(scope, receive, private_send)
