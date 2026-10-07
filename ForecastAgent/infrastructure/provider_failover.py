"""Bounded credential failover around immutable forecasting releases.

Only explicit, pre-inference credit exhaustion permits one backup attempt.
Logical task reservations remain owned by the release; receipts count HTTP calls.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import site
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

ENDPOINTS = {
    "openrouter": ("openrouter.ai", {"/api/v1/chat/completions", "/api/alpha/decisions"}),
    "tavily": ("api.tavily.com", {"/search", "/extract"}),
}


def utcnow():
    return datetime.now(timezone.utc)


def exhaustion(provider, status, body, headers=None):
    """Return an allowlisted reason, never classify generic rate/service errors."""
    if provider == "tavily":
        return {432: "monthly_plan_limit", 433: "pay_as_you_go_limit"}.get(status)
    try:
        error = json.loads(body).get("error", {})
        metadata = error.get("metadata") or {}
        message = str(error.get("message", "")).lower()
    except (ValueError, AttributeError, TypeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("provider_name") or metadata.get("provider_code"):
        return None
    source = metadata.get("limit_source")
    if source == "openrouter_in_flight_budget" or metadata.get("reason") == "weight_exceeds_budget":
        return None
    if status == 402:
        # In-flight holds need backoff, not a credential change.
        if any(k.lower() == "retry-after" for k in (headers or {})):
            return None
        if source == "openrouter_key_limit":
            return "key_credit_limit"
        if source == "openrouter_credits" or any(term in message for term in (
                "insufficient credits", "not enough credits", "credit limit exceeded")):
            return "insufficient_credits"
    if status == 429 and ("free-models-per-day" in message or
                         ("daily" in message and any(term in message for term in
                          ("quota", "limit", "exceeded")))):
        return "free_daily_limit"
    return None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temp, path)


class ProviderTransport:
    _forecast_provider_transport = True

    def __init__(self, original, credentials, root, clock=utcnow):
        self.original, self.credentials, self.root, self.clock = original, credentials, Path(root), clock
        self.lock = threading.RLock()

    def redact(self, value):
        for pair in self.credentials.values():
            for key in pair:
                if key:
                    value = value.replace(key, "[REDACTED]")
        return value

    def identity(self, provider):
        return [hashlib.sha256(key.encode()).hexdigest() for key in self.credentials[provider]]

    def load(self, provider):
        try:
            state = json.loads((self.root / provider / "route.json").read_text(encoding="utf-8"))
            if state.get("credential_digests") == self.identity(provider) and isinstance(state.get("exhausted"), dict):
                return state
        except (OSError, ValueError, AttributeError):
            pass
        return {"schema_version": 1, "provider": provider,
                "credential_digests": self.identity(provider), "exhausted": {}}

    def blocked(self, state, role):
        entry = state["exhausted"].get(role, {})
        try:
            return datetime.fromisoformat(entry["retry_at_utc"]) > self.clock()
        except (KeyError, TypeError, ValueError):
            return False

    def mark_exhausted(self, provider, role, reason, status):
        now = self.clock()
        if reason == "free_daily_limit":
            retry = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif reason == "monthly_plan_limit":
            retry = (now.replace(day=28) + timedelta(days=4)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            # Credits and adjustable spend caps can recover after a top-up.
            retry = now + timedelta(hours=1)
        state = self.load(provider)
        state["exhausted"][role] = {"reason": reason, "http_status": status,
            "observed_at_utc": now.isoformat(), "retry_at_utc": retry.isoformat()}
        _write(self.root / provider / "route.json", state)

    def request(self, req, provider, role, group, args, kwargs):
        key = self.credentials[provider][role == "backup"]
        headers = {k: v for k, v in req.header_items() if k.lower() != "authorization"}
        headers["Authorization"] = "Bearer " + key
        copied = urllib.request.Request(req.full_url, data=req.data, headers=headers,
                                         method=req.get_method())
        receipt = {"schema_version": 1, "provider": provider, "endpoint": urlsplit(req.full_url).path,
                   "logical_transport_id": group, "credential_role": role,
                   "started_at_utc": self.clock().isoformat(),
                   "payload_sha256": hashlib.sha256(req.data or b"").hexdigest(),
                   "state": "reserved", "http_status": None, "usage": "unknown"}
        path = self.root / provider / "attempts" / (uuid.uuid4().hex + ".json")
        _write(path, receipt)
        start = time.monotonic()
        try:
            response = self.original(copied, *args, **kwargs)
            receipt.update(state="response", http_status=getattr(response, "status", 200))
            return response
        except urllib.error.HTTPError as error:
            body = error.read(16384).decode("utf-8", errors="replace")
            reason = exhaustion(provider, error.code, body, error.headers)
            receipt.update(state="http_error", http_status=error.code, exhaustion_reason=reason)
            if reason:
                self.mark_exhausted(provider, role, reason, error.code)
            safe = urllib.error.HTTPError(req.full_url, error.code, self.redact(str(error.reason)),
                {k: self.redact(str(v)) for k, v in (error.headers or {}).items()},
                io.BytesIO(self.redact(body).encode()))
            safe.forecast_exhaustion = reason
            raise safe from None
        except Exception as error:
            receipt.update(state="transport_error", error_type=type(error).__name__)
            # Outcome is unknown: never retry at this layer or log request headers.
            raise urllib.error.URLError(provider + " transport failed: " + type(error).__name__) from None
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
            _write(path, receipt)

    def __call__(self, req, *args, **kwargs):
        if not isinstance(req, urllib.request.Request):
            return self.original(req, *args, **kwargs)
        url = urlsplit(req.full_url)
        auth = next((v for k, v in req.header_items() if k.lower() == "authorization"), None)
        provider = next((p for p, (host, paths) in ENDPOINTS.items() if
            p in self.credentials and self.credentials[p][0] and
            url.scheme == "https" and url.netloc == host and url.path in paths and
            not url.query and req.get_method() == "POST" and
            auth == "Bearer " + self.credentials[p][0]), None)
        if provider is None:
            return self.original(req, *args, **kwargs)
        # Serialize route transitions within each interpreter. Production has one worker.
        with self.lock:
            state = self.load(provider)
            primary, backup = self.credentials[provider]
            roles = ["primary"] + (["backup"] if backup and backup != primary else [])
            group = uuid.uuid4().hex
            last = None
            for role in roles:
                if self.blocked(state, role):
                    continue
                try:
                    return self.request(req, provider, role, group, args, kwargs)
                except urllib.error.HTTPError as error:
                    if not getattr(error, "forecast_exhaustion", None):
                        raise
                    last = error
            if last is not None:
                raise last
            # All configured credentials are in a persisted exhaustion cooldown.
            entry = state["exhausted"].get(roles[-1], {})
            status = entry.get("http_status", 402)
            marker = "free-models-per-day " if entry.get("reason") == "free_daily_limit" else ""
            message = marker + "Configured credentials exhausted; retry at " + str(entry.get("retry_at_utc"))
            body = {"error": {"code": status, "message": message}} if provider == "openrouter" else {
                "detail": {"error": message}}
            raise urllib.error.HTTPError(req.full_url, status, "Credential quota cooldown", {},
                                         io.BytesIO(json.dumps(body).encode()))


def install():
    if getattr(urllib.request.urlopen, "_forecast_provider_transport", False):
        return
    # Compose with the existing Exa transport even under an immutable release checkout.
    runpy.run_path(str(Path(__file__).with_name("exa_failover.py")))["install"]()
    credentials = {"openrouter": (os.environ.get("OPENROUTER_API_KEY", ""),
                                    os.environ.get("OPENROUTER_API_KEY2", "")),
                   "tavily": (os.environ.get("TAVILY_API_KEY", ""), os.environ.get("TAVILY_API_KEY2", ""))}
    transport = ProviderTransport(urllib.request.urlopen, credentials,
                                 os.environ.get("FORECAST_PROVIDER_TRANSPORT_ROOT", ".local/provider-transport"))
    # A legacy Exa .pth may execute after this bootstrap; the inner Exa is already installed.
    transport._forecast_exa_transport = getattr(transport.original, "_forecast_exa_transport", False)
    urllib.request.urlopen = transport


def setup_bootstrap():
    target = Path(site.getsitepackages()[0]) / "forecast_provider_transport.pth"
    content = ("import os, runpy; runpy.run_path(os.environ['FORECAST_PROVIDER_FAILOVER_MODULE'])['install']() "
               "if os.environ.get('FORECAST_PROVIDER_FAILOVER_MODULE') else None\n")
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--root")
    parser.add_argument("--exa-root")
    parser.add_argument("--module")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    setup_bootstrap()
    if options.setup:
        return
    if not options.module:
        parser.error("--module is required")
    os.environ["FORECAST_PROVIDER_FAILOVER_MODULE"] = str(Path(__file__).resolve())
    if options.root:
        os.environ["FORECAST_PROVIDER_TRANSPORT_ROOT"] = str(Path(options.root).resolve())
    if options.exa_root:
        os.environ["FORECAST_EXA_TRANSPORT_ROOT"] = str(Path(options.exa_root).resolve())
    install()
    # Configuration presence only; never print credential contents or digests.
    print(json.dumps({"provider_key_failover": "installed", "logical_budget_reset": False,
        "backup_configured": {p: bool(pair[1] and pair[1] != pair[0])
                              for p, pair in urllib.request.urlopen.credentials.items()},
        "exa_transport_installed": bool(getattr(urllib.request.urlopen, "_forecast_exa_transport", False))}))
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    sys.argv = [options.module] + arguments
    sys.path.insert(0, os.getcwd())
    runpy.run_module(options.module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
