"""Exa credit failover transport for immutable releases and inherited workers."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import site
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid
from datetime import datetime, timezone

EXHAUSTED = {"NO_MORE_CREDITS", "API_KEY_BUDGET_EXCEEDED", "TEAM_BUDGET_EXCEEDED"}


def exhausted(status, body):
    if status != 402:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("tag") in EXHAUSTED


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temp, path)


class ExaTransport:
    _forecast_exa_transport = True
    def __init__(self, original, primary, backup, root):
        self.original, self.primary, self.backup = original, primary, backup
        self.root = Path(root)
        self.scope = datetime.now(timezone.utc).strftime("%Y-%m")
        # A digest detects credential rotation; credentials never enter receipts.
        self.identity = hashlib.sha256(primary.encode()).hexdigest()
        self.state = self.root / "route.json"

    def backup_active(self):
        try:
            state = json.loads(self.state.read_text(encoding="utf-8"))
            return state.get("month") == self.scope and state.get("primary_digest") == self.identity
        except (OSError, ValueError):
            return False

    def redact(self, value):
        for key in (self.primary, self.backup):
            if key:
                value = value.replace(key, "[REDACTED]")
        return value

    def request(self, req, role, group, args, kwargs):
        key = self.backup if role == "backup" else self.primary
        headers = {k: v for k, v in req.header_items() if k.lower() != "x-api-key"}
        headers["x-api-key"] = key
        copied = urllib.request.Request(req.full_url, data=req.data, headers=headers,
                                        method=req.get_method())
        receipt = {"schema_version": 1, "logical_transport_id": group,
                   "credential_role": role, "started_at_utc": datetime.now(timezone.utc).isoformat(),
                   "payload_sha256": hashlib.sha256(req.data or b"").hexdigest(),
                   "state": "reserved", "http_status": None, "usage": "unknown"}
        path = self.root / "attempts" / (uuid.uuid4().hex + ".json")
        _write(path, receipt)
        start = time.monotonic()
        try:
            response = self.original(copied, *args, **kwargs)
            receipt.update(state="response", http_status=getattr(response, "status", 200))
            return response
        except urllib.error.HTTPError as error:
            # Preserve a bounded sanitized body for the original provider's error handling.
            body = error.read(8000).decode("utf-8", errors="replace")
            receipt.update(state="http_error", http_status=error.code,
                           credits_exhausted=exhausted(error.code, body))
            safe_headers = {k: self.redact(str(v)) for k, v in (error.headers or {}).items()}
            safe = urllib.error.HTTPError(req.full_url, error.code, self.redact(str(error.reason)),
                                          safe_headers, io.BytesIO(self.redact(body).encode()))
            safe.exa_exhausted = exhausted(error.code, body)
            raise safe from None
        except Exception as error:
            receipt.update(state="transport_error", error_type=type(error).__name__)
            # Do not expose transport exception messages containing request headers.
            raise urllib.error.URLError("Exa transport failed: " + type(error).__name__) from None
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
            _write(path, receipt)

    def __call__(self, req, *args, **kwargs):
        if not isinstance(req, urllib.request.Request):
            return self.original(req, *args, **kwargs)
        url = urlsplit(req.full_url)
        key = next((v for k, v in req.header_items() if k.lower() == "x-api-key"), None)
        if (url.scheme, url.netloc, url.path, req.get_method(), key) != (
                "https", "api.exa.ai", "/search", "POST", self.primary):
            return self.original(req, *args, **kwargs)
        group = uuid.uuid4().hex
        role = "backup" if self.backup and self.backup != self.primary and self.backup_active() else "primary"
        try:
            return self.request(req, role, group, args, kwargs)
        except urllib.error.HTTPError as error:
            if role != "primary" or not getattr(error, "exa_exhausted", False):
                raise
            if not self.backup or self.backup == self.primary:
                raise
            _write(self.state, {"month": self.scope, "primary_digest": self.identity,
                               "reason": "primary_credits_exhausted", "selected_role": "backup"})
            # Exactly one backup attempt in the existing logical search reservation.
            return self.request(req, "backup", group, args, kwargs)


def install():
    primary = os.environ.get("EXA_API_KEY", "")
    if not primary or getattr(urllib.request.urlopen, "_forecast_exa_transport", False):
        return
    urllib.request.urlopen = ExaTransport(
        urllib.request.urlopen, primary, os.environ.get("EXA_API_KEY2", ""),
        os.environ.get("FORECAST_EXA_TRANSPORT_ROOT", ".local/exa-transport"))


def setup_bootstrap():
    """An opt-in .pth survives child processes that replace PYTHONPATH."""
    target = Path(site.getsitepackages()[0]) / "forecast_exa_transport.pth"
    content = ("import os, runpy; runpy.run_path(os.environ['FORECAST_EXA_FAILOVER_MODULE'])['install']() "
               "if os.environ.get('FORECAST_EXA_FAILOVER_MODULE') else None\n")
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        target.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--root")
    parser.add_argument("--module")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    setup_bootstrap()
    if options.setup:
        return
    if not options.module:
        parser.error("--module is required")
    os.environ["FORECAST_EXA_FAILOVER_MODULE"] = str(Path(__file__).resolve())
    if options.root:
        os.environ["FORECAST_EXA_TRANSPORT_ROOT"] = str(Path(options.root).resolve())
    install()
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    sys.argv = [options.module] + arguments
    sys.path.insert(0, os.getcwd())
    runpy.run_module(options.module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
