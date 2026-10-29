"""OAuth, account isolation and streamed inference without real credentials."""
import asyncio
import base64
import hashlib
import json
import stat
import time
from urllib.parse import parse_qs, urlencode, urlparse
from unittest.mock import AsyncMock, Mock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import chatgpt_auth as auth
import server
import valuation
from test_repairs import run_case, watch

ORIGINAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def service(tmp_path):
    return auth.ChatGPTService(tmp_path / "credentials")


@pytest.fixture
def signing_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public["kid"] = "test-key"
    return private, public


def fake_network(monkeypatch, handler):
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda **kw: ORIGINAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


async def login(service, monkeypatch, signing_key, device="device-a", subject="subject-a", client_id="oaiapp_test-a", nonce_override=None, new=False):
    params = parse_qs(urlparse(await service.start(device, new_account=new)).query)
    state = params["state"][0]
    nonce = service.pending[state]["nonce"]
    verifier = service.pending[state]["verifier"]
    private, public = signing_key
    token = jwt.encode({"iss": auth.ISSUER, "aud": client_id, "sub": subject, "exp": int(time.time()) + 3600,
                        "nonce": nonce_override or nonce, "email": device + "@example.com"}, private, algorithm="RS256", headers={"kid": "test-key"})
    def respond(request):
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json={"keys": [public]})
        if request.url.path.endswith("/token"):
            body = parse_qs(request.content.decode())
            assert body["client_id"] == [client_id]
            assert body["redirect_uri"] == [auth.CALLBACK]
            assert body["code_verifier"] == [verifier]
            return httpx.Response(200, json={"access_token": "fake-access-" + device, "refresh_token": "fake-refresh-" + device,
                                            "token_type": "Bearer", "id_token": token, "expires_in": 3600, "scope": auth.SCOPES})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"models": [{"slug": "gpt-5.4", "display_name": "GPT-5.4", "visibility": "list"},
                                                        {"slug": "second", "display_name": "Other", "visibility": "list"},
                                                        {"slug": "hidden", "visibility": "hidden"}]})
        raise AssertionError(str(request.url))
    fake_network(monkeypatch, respond)
    await service.finish({"state": state, "code": "fake-code", "client_id": client_id})
    return state


def test_authorization_uses_pkce_and_stable_host_id(service):
    async def case():
        first = parse_qs(urlparse(await service.start("device-a")).query)
        attempt = service.pending[first["state"][0]]
        expected = base64.urlsafe_b64encode(hashlib.sha256(attempt["verifier"].encode()).digest()).decode().rstrip("=")
        assert first["code_challenge"] == [expected]
        assert first["redirect_uri"] == [auth.CALLBACK]
        assert first["client_id"] == ["dynamic_agent_client"]
        restarted = auth.ChatGPTService(service.directory)
        second = parse_qs(urlparse(await restarted.start("device-a")).query)
        assert first["ext_agent_host_id"] == second["ext_agent_host_id"]
        assert first["state"] != second["state"]
    asyncio.run(case())


def test_invalid_or_expired_state_never_exchanges_a_code(service, monkeypatch):
    async def case():
        fake_network(monkeypatch, lambda request: pytest.fail("Unexpected network access"))
        with pytest.raises(auth.ChatGPTError):
            await service.finish({"state": "unrecognized", "code": "code"})
        params = parse_qs(urlparse(await service.start("device-a")).query)
        state = params["state"][0]
        service.pending[state]["expires"] = time.time() - 1
        with pytest.raises(auth.ChatGPTError):
            await service.finish({"state": state, "code": "code"})
    asyncio.run(case())


def test_login_verifies_identity_and_keeps_credentials_out_of_status(service, monkeypatch, signing_key):
    async def case():
        state = await login(service, monkeypatch, signing_key)
        status = service.status("device-a")
        assert status["connected"] and status["model"] == "gpt-5.4"
        assert len(status["models"]) == 2
        assert "fake-access" not in json.dumps(status)
        assert "fake-refresh" not in json.dumps(status)
        assert not service.status("device-b")["connected"]
        path = service.directory / "connections.json"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(service.directory.stat().st_mode) == 0o700
        with pytest.raises(auth.ChatGPTError):
            await service.finish({"state": state, "code": "code", "client_id": "oaiapp_test-a"})
        returning = parse_qs(urlparse(await service.start("device-a")).query)
        assert returning["client_id"] == ["oaiapp_test-a"]
        assert "agent_name_hint" not in returning
    asyncio.run(case())


def test_invalid_nonce_does_not_save_login(service, monkeypatch, signing_key):
    async def case():
        with pytest.raises(auth.ChatGPTError, match="verifieras"):
            await login(service, monkeypatch, signing_key, nonce_override="wrong-nonce")
        assert not service.connected("device-a")
    asyncio.run(case())


def test_accounts_and_devices_keep_separate_credentials(service, monkeypatch, signing_key):
    async def case():
        await login(service, monkeypatch, signing_key)
        first = service.status("device-a")["active_account"]
        await login(service, monkeypatch, signing_key, subject="subject-b", client_id="oaiapp_test-b", new=True)
        assert len(service.status("device-a")["accounts"]) == 2
        await service.update("device-a", account=first)
        assert (await service._credential("device-a"))["client_id"] == "oaiapp_test-a"
        with pytest.raises(auth.ChatGPTError):
            await service.update("device-b", account=first)
        with pytest.raises(auth.ChatGPTError):
            await service.update("device-a", model="hidden")
    asyncio.run(case())


def test_parallel_requests_refresh_rotating_token_once(service, monkeypatch, signing_key):
    async def case():
        await login(service, monkeypatch, signing_key)
        data = service._read()
        service._selected(data, "device-a")["expires_at"] = time.time() - 1
        service._save(data)
        calls = []
        def respond(request):
            calls.append(request)
            body = parse_qs(request.content.decode())
            assert body["client_id"] == ["oaiapp_test-a"]
            assert "scope" not in body
            return httpx.Response(200, json={"access_token": "renewed", "refresh_token": "rotated", "expires_in": 3600})
        fake_network(monkeypatch, respond)
        results = await asyncio.gather(*(service._credential("device-a") for _ in range(5)))
        assert len(calls) == 1
        assert all(r["access_token"] == "renewed" and r["refresh_token"] == "rotated" for r in results)
    asyncio.run(case())


@pytest.mark.parametrize("terminal", ["response.completed", "response.failed", "response.incomplete", None])
def test_inference_requires_completed_event(service, monkeypatch, signing_key, terminal):
    async def case():
        await login(service, monkeypatch, signing_key)
        def respond(request):
            payload = json.loads(request.content)
            assert request.url == auth.RESOURCE + "/responses"
            assert payload["store"] is False and payload["stream"] is True
            assert isinstance(payload["input"], list)
            assert payload["instructions"] == "instructions"
            events = [{"type": "response.output_text.delta", "delta": '{"value":1}'}]
            if terminal:
                events.append({"type": terminal, "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
            return httpx.Response(200, text="\n\n".join("data: " + json.dumps(e) for e in events), headers={"content-type": "text/event-stream"})
        fake_network(monkeypatch, respond)
        if terminal == "response.completed":
            assert await service.complete("device-a", "instructions", "prompt") == '{"value":1}'
        else:
            with pytest.raises(auth.ChatGPTError):
                await service.complete("device-a", "instructions", "prompt")
    asyncio.run(case())


def test_disconnect_clears_tokens_but_retains_registration(service, monkeypatch, signing_key):
    async def case():
        await login(service, monkeypatch, signing_key)
        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"})
            assert parse_qs(request.content.decode())["client_id"] == ["oaiapp_test-a"]
            return httpx.Response(200)
        fake_network(monkeypatch, respond)
        assert (await service.disconnect("device-a"))["revocation_confirmed"]
        assert not service.connected("device-a")
        assert service.status("device-a")["accounts"]
        profile = service._selected(service._read(), "device-a")
        assert not any(profile.get(k) for k in ("access_token", "refresh_token", "id_token"))
        params = parse_qs(urlparse(await service.start("device-a")).query)
        assert params["client_id"] == ["oaiapp_test-a"]
    asyncio.run(case())


def test_valuation_uses_connected_chatgpt_without_emergent(run_case, monkeypatch, tmp_path):
    async def case(db, api):
        api.base_url = "http://localhost:8000"
        monkeypatch.setattr(server, "CHATGPT_LOCAL_ENABLED", True)
        service = auth.ChatGPTService(tmp_path / "oauth")
        service._save({"devices": {"owner": {"active": "account", "profiles": {"account": {"access_token": "fake", "scopes": ["chatgpt.tokens.use.direct"]}}}}})
        monkeypatch.setattr(server, "chatgpt", service)
        complete = AsyncMock(return_value='{"used_price_estimate":2000,"confidence":50}')
        monkeypatch.setattr(service, "complete", complete)
        require = Mock(side_effect=AssertionError("Emergent must not be used"))
        monkeypatch.setattr(valuation, "require_configured", require)
        monkeypatch.setattr(server, "_ingest_auctionet_sold", AsyncMock())
        monkeypatch.setattr(server, "_find_comps", AsyncMock(return_value=[]))
        doc = watch()
        await db.auctions.insert_one(doc)
        response = await api.post(f"/api/auctions/{doc['id']}/analyze", headers={"X-Device-Id": "owner", "Origin": "http://localhost:3000"})
        assert response.status_code == 200, response.text
        assert response.json()["valuation"]["used_price_estimate"] == 2000
        complete.assert_awaited_once()
        require.assert_not_called()
        forbidden = await api.post("/api/chatgpt/connect", json={}, headers={"X-Device-Id": "owner", "Origin": "https://untrusted.example"})
        assert forbidden.status_code == 403
    run_case(case)


def test_local_valuation_requires_chatgpt_login_before_external_work(run_case, monkeypatch, tmp_path):
    async def case(db, api):
        monkeypatch.setattr(server, "CHATGPT_LOCAL_ENABLED", True)
        monkeypatch.setattr(server, "chatgpt", auth.ChatGPTService(tmp_path / "oauth"))
        ingest = AsyncMock()
        monkeypatch.setattr(server, "_ingest_auctionet_sold", ingest)
        doc = watch()
        await db.auctions.insert_one(doc)
        response = await api.post(f"/api/auctions/{doc['id']}/analyze", headers={"X-Device-Id": "owner"})
        assert response.status_code == 503
        assert "ChatGPT-knappen" in response.json()["detail"]
        ingest.assert_not_awaited()
    run_case(case)
