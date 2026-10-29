"""Local Sign in with ChatGPT, with server-side OAuth credentials."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import httpx
import jwt

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
CALLBACK = "http://127.0.0.1:8000/api/chatgpt/callback"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
USAGE_URL = "https://chatgpt.com/settings/usage"


class ChatGPTError(RuntimeError):
    pass


class ChatGPTService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = asyncio.Lock()
        self.pending = {}
        self.errors = {}

    def _read(self):
        path = self.directory / "connections.json"
        return json.loads(path.read_text()) if path.exists() else {"devices": {}}

    def _save(self, data):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".connections-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
            os.replace(name, self.directory / "connections.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _selected(data, device_id):
        device = data.get("devices", {}).get(device_id, {})
        return device.get("profiles", {}).get(device.get("active"), {})

    def connected(self, device_id):
        profile = self._selected(self._read(), device_id)
        return bool(profile.get("access_token") and "chatgpt.tokens.use.direct" in profile.get("scopes", []))

    def status(self, device_id):
        data = self._read()
        device = data.get("devices", {}).get(device_id, {})
        profile = self._selected(data, device_id)
        return {
            "connected": self.connected(device_id),
            "accounts": [{"id": key, "label": p.get("label", "ChatGPT")}
                         for key, p in device.get("profiles", {}).items()],
            "active_account": device.get("active"),
            "model": profile.get("model"), "models": profile.get("models", []),
            "connected_at": profile.get("connected_at"),
            "welcome_seen": device.get("welcome_seen", False),
            "usage_url": USAGE_URL, "error": self.errors.get(device_id),
        }

    async def start(self, device_id, new_account=False):
        async with self.lock:
            data = self._read()
            data.setdefault("host_id", "urn:uuid:" + str(uuid.uuid4()))
            profile = {} if new_account else self._selected(data, device_id)
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            self.pending = {k: v for k, v in self.pending.items() if v["expires"] > time.time()}
            self.pending[state] = {"device_id": device_id, "nonce": nonce, "verifier": verifier,
                                   "expires": time.time() + 600, "profile": profile}
            params = {"client_id": profile.get("client_id", "dynamic_agent_client"),
                      "ext_agent_host_id": data["host_id"], "response_type": "code",
                      "redirect_uri": CALLBACK, "scope": SCOPES, "resource": RESOURCE,
                      "state": state, "nonce": nonce, "code_challenge_method": "S256",
                      "code_challenge": challenge}
            if not profile:
                params["agent_name_hint"] = "Auktiomera"
            # Retained ID tokens are deliberately not put in a browser URL.
            self.errors.pop(device_id, None)
            self._save(data)
            return AUTHORIZE + "?" + urlencode(params)

    @staticmethod
    async def _validate_identity(client, token, client_id, nonce):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError("unsupported signing algorithm")
            result = await client.get(ISSUER + "/.well-known/jwks.json")
            result.raise_for_status()
            key = next(k for k in result.json()["keys"] if k.get("kid") == header.get("kid"))
            identity = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                                  issuer=ISSUER, audience=client_id,
                                  options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
            if not secrets.compare_digest(identity["nonce"], nonce):
                raise ValueError("nonce mismatch")
            return identity
        except (ValueError, TypeError, StopIteration, KeyError, jwt.PyJWTError) as exc:
            raise ChatGPTError("ChatGPT-inloggningen kunde inte verifieras. Försök igen.") from exc

    async def finish(self, params):
        # Consume state before any network access: a callback is usable only once.
        async with self.lock:
            attempt = self.pending.pop(params.get("state", ""), None)
        if not attempt or attempt["expires"] <= time.time():
            raise ChatGPTError("Inloggningen har gått ut eller är ogiltig. Starta en ny inloggning.")
        device_id = attempt["device_id"]
        try:
            if params.get("error"):
                raise ChatGPTError("Inloggningen avbröts. Godkänn åtkomst till ChatGPT-planen för att använda AI-värdering.")
            previous = attempt["profile"]
            client_id = params.get("client_id") or previous.get("client_id")
            if not client_id or client_id == "dynamic_agent_client" or not params.get("code"):
                raise ChatGPTError("ChatGPT returnerade ingen färdig registrering. Försök igen.")
            if previous and client_id != previous["client_id"]:
                raise ChatGPTError("Inloggningen tillhör ett annat registrerat konto.")
            async with httpx.AsyncClient(timeout=30) as client:
                result = await client.post(TOKEN, data={"grant_type": "authorization_code", "client_id": client_id,
                                                       "code": params["code"], "code_verifier": attempt["verifier"],
                                                       "redirect_uri": CALLBACK, "resource": RESOURCE})
                if result.status_code != 200:
                    raise ChatGPTError("ChatGPT kunde inte slutföra inloggningen. Starta en ny inloggning.")
                tokens = result.json()
                identity = await self._validate_identity(client, tokens.get("id_token", ""), client_id, attempt["nonce"])
                if previous and identity["sub"] != previous["subject"]:
                    raise ChatGPTError("Det valda kontot stämmer inte med inloggningen. Lägg till kontot separat.")
                scopes = tokens.get("scope", "").split()
                if not {"resource.invoke", "chatgpt.tokens.use.direct"}.issubset(scopes):
                    raise ChatGPTError("Åtkomst till ChatGPT-planen godkändes inte. Försök igen och godkänn AI-användning.")
                if not tokens.get("access_token") or tokens.get("token_type", "").lower() != "bearer":
                    raise ChatGPTError("ChatGPT returnerade inga giltiga åtkomstuppgifter.")
                try:
                    models = await self._models(client, tokens["access_token"])
                except (ChatGPTError, httpx.HTTPError):
                    models = []
            profile_id = hashlib.sha256(f"{ISSUER}|{identity['sub']}|{client_id}".encode()).hexdigest()
            model = previous.get("model")
            slugs = [m["slug"] for m in models]
            if model not in slugs:
                model = "gpt-5.4" if "gpt-5.4" in slugs else (slugs[0] if slugs else None)
            profile = {"client_id": client_id, "subject": identity["sub"], "issuer": ISSUER,
                       "label": f"{identity.get('email') or identity.get('name') or 'ChatGPT'} · {client_id[-6:]}",
                       "scopes": scopes, "model": model, "models": models, "connected_at": time.time(),
                       "access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token"),
                       "id_token": tokens["id_token"], "expires_at": time.time() + tokens.get("expires_in", 3600)}
            async with self.lock:
                data = self._read()
                device = data["devices"].setdefault(device_id, {"profiles": {}})
                device["profiles"][profile_id] = profile
                device["active"] = profile_id
                self._save(data)
                self.errors.pop(device_id, None)
            return device_id
        except ChatGPTError as exc:
            self.errors[device_id] = str(exc)
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            message = "Kunde inte kontakta ChatGPT. Försök logga in igen."
            self.errors[device_id] = message
            raise ChatGPTError(message) from exc

    @staticmethod
    async def _models(client, access_token):
        response = await client.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + access_token})
        if response.status_code != 200:
            raise ChatGPTError("Kunde inte hämta modeller för ditt ChatGPT-konto. Kontrollera kontots åtkomst.")
        return [{"slug": m["slug"], "display_name": m.get("display_name", m["slug"])}
                for m in response.json().get("models", []) if m.get("visibility") == "list" and m.get("slug")]

    async def _credential(self, device_id):
        # One runtime owns this volume; serialize rotating refresh tokens.
        async with self.lock:
            data = self._read()
            profile = self._selected(data, device_id)
            if not profile.get("access_token"):
                raise ChatGPTError("Anslut ditt ChatGPT-konto först.")
            if profile.get("expires_at", 0) <= time.time() + 60:
                if not profile.get("refresh_token"):
                    raise ChatGPTError("ChatGPT-inloggningen har gått ut. Logga in igen.")
                async with httpx.AsyncClient(timeout=30) as client:
                    result = await client.post(TOKEN, data={"grant_type": "refresh_token", "client_id": profile["client_id"],
                                                           "refresh_token": profile["refresh_token"], "resource": RESOURCE})
                if result.status_code != 200:
                    if result.status_code in (400, 401):
                        for key in ("access_token", "refresh_token", "id_token"):
                            profile.pop(key, None)
                        self._save(data)
                    raise ChatGPTError("ChatGPT-inloggningen kunde inte förnyas. Försök igen eller logga in på nytt.")
                tokens = result.json()
                scopes = tokens.get("scope", " ".join(profile["scopes"])).split()
                if not tokens.get("access_token") or "chatgpt.tokens.use.direct" not in scopes:
                    raise ChatGPTError("ChatGPT-planens åtkomst saknas. Logga in igen.")
                profile.update({"access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", profile["refresh_token"]),
                                "expires_at": time.time() + tokens.get("expires_in", 3600), "scopes": scopes})
                if tokens.get("id_token"):
                    profile["id_token"] = tokens["id_token"]
                self._save(data)
            return dict(profile)

    async def refresh_models(self, device_id):
        profile = await self._credential(device_id)
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                models = await self._models(client, profile["access_token"])
            except httpx.HTTPError as exc:
                raise ChatGPTError("Kunde inte hämta ChatGPT-modeller. Försök igen.") from exc
        async with self.lock:
            data = self._read()
            current = self._selected(data, device_id)
            if current.get("client_id") != profile.get("client_id"):
                raise ChatGPTError("Kontot ändrades. Hämta modellerna igen.")
            current["models"] = models
            if current.get("model") not in [m["slug"] for m in models]:
                current["model"] = models[0]["slug"] if models else None
            self._save(data)
        return self.status(device_id)

    async def update(self, device_id, account=None, model=None, welcome_seen=False):
        async with self.lock:
            data = self._read()
            device = data.get("devices", {}).get(device_id, {})
            if account is not None:
                if account not in device.get("profiles", {}):
                    raise ChatGPTError("Kontot finns inte på den här enheten.")
                device["active"] = account
                self.errors.pop(device_id, None)
            if model is not None:
                profile = self._selected(data, device_id)
                if model not in [m["slug"] for m in profile.get("models", [])]:
                    raise ChatGPTError("Modellen är inte tillgänglig för det valda kontot.")
                profile["model"] = model
            if welcome_seen and device:
                device["welcome_seen"] = True
            self._save(data)
        if account is not None and self.connected(device_id):
            return await self.refresh_models(device_id)
        return self.status(device_id)

    async def disconnect(self, device_id):
        async with self.lock:
            data = self._read()
            profile = self._selected(data, device_id)
            confirmed = True
            if profile.get("refresh_token"):
                confirmed = False
                try:
                    async with httpx.AsyncClient(timeout=20) as client:
                        discovery = await client.get(ISSUER + "/.well-known/openid-configuration")
                        discovery.raise_for_status()
                        endpoint = discovery.json().get("revocation_endpoint")
                        if endpoint != ISSUER + "/api/accounts/oauth/revoke":
                            raise ValueError("unexpected revocation endpoint")
                        response = await client.post(endpoint, data={"token": profile["refresh_token"],
                                                                    "token_type_hint": "refresh_token", "client_id": profile["client_id"]})
                        confirmed = response.status_code == 200
                except (httpx.HTTPError, ValueError):
                    pass
            for field in ("access_token", "refresh_token", "id_token"):
                profile.pop(field, None)
            self._save(data)
            self.errors.pop(device_id, None)
            return {"ok": True, "revocation_confirmed": confirmed}

    async def complete(self, device_id, instructions, prompt):
        profile = await self._credential(device_id)
        if not profile.get("model"):
            raise ChatGPTError("Ditt konto saknar tillgängliga modeller. Logga in igen eller kontrollera ChatGPT-inställningarna.")
        payload = {"model": profile["model"], "instructions": instructions,
                   "input": [{"role": "user", "content": prompt}], "store": False, "stream": True}
        parts, completed = [], False
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                async with client.stream("POST", RESOURCE + "/responses", headers={"Authorization": "Bearer " + profile["access_token"]}, json=payload) as response:
                    if response.status_code in (401, 403):
                        raise ChatGPTError("ChatGPT nekade åtkomst. Logga in igen och kontrollera appens behörigheter.")
                    if response.status_code == 429:
                        raise ChatGPTError("ChatGPT-planens användningsgräns är nådd. Hantera användningen i ChatGPT-inställningarna.")
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        event = json.loads(line[5:].strip())
                        kind = event.get("type")
                        if kind == "response.output_text.delta":
                            parts.append(event.get("delta", ""))
                        elif kind in ("response.failed", "response.incomplete", "error"):
                            error = event.get("response", {}).get("error") or event.get("error") or {}
                            if "usage" in str(error.get("code", "")):
                                raise ChatGPTError("ChatGPT-planens användning är begränsad. Hantera användningen i ChatGPT-inställningarna.")
                            raise ChatGPTError("ChatGPT kunde inte slutföra värderingen. Försök igen.")
                        elif kind == "response.completed":
                            completed = True
                            break
            if not completed or not parts:
                raise ChatGPTError("ChatGPT-svaret avbröts. Försök igen.")
            return "".join(parts)
        except (httpx.HTTPError, ValueError) as exc:
            raise ChatGPTError("Kunde inte hämta värderingen från ChatGPT. Försök igen.") from exc
