"""seven.io SMS gateway client. Uses requests in a worker thread."""
import asyncio
import logging
from typing import Optional

import requests

import config

log = logging.getLogger(__name__)

SMS_ENDPOINT = "https://gateway.seven.io/api/sms"
BALANCE_ENDPOINT = "https://gateway.seven.io/api/balance"


class SmsResult:
    def __init__(
        self,
        ok: bool,
        message: str,
        raw: Optional[dict] = None,
        error: Optional[str] = None,
    ):
        self.ok = ok
        self.message = message
        self.raw = raw or {}
        self.error = error

    def __repr__(self) -> str:
        return f"SmsResult(ok={self.ok}, message={self.message!r})"


def _send_sync(api_key: str, to: str, text: str, sender: str) -> SmsResult:
    headers = {
        "Authorization": f"basic {api_key}",
        "Accept": "application/json",
    }
    data = {"to": to, "from": sender, "text": text}
    try:
        r = requests.post(
            SMS_ENDPOINT,
            headers=headers,
            data=data,
            timeout=config.SMS_API_TIMEOUT,
        )
    except requests.Timeout:
        return SmsResult(False, "SMS service timed out. Try again.", error="timeout")
    except requests.RequestException as e:
        return SmsResult(False, "SMS service unavailable.", error=str(e))

    if r.status_code != 200:
        return SmsResult(
            False,
            f"Gateway returned HTTP {r.status_code}.",
            error=r.text[:500],
        )

    try:
        body = r.json()
    except ValueError:
        return SmsResult(False, "Gateway returned non-JSON response.", error=r.text[:500])

    messages = body.get("messages") or []
    if not messages:
        return SmsResult(False, "Gateway response had no messages.", raw=body, error=str(body))

    first = messages[0]
    err_text = first.get("error_text")
    if err_text:
        return SmsResult(False, f"Gateway error: {err_text}", raw=body, error=err_text)

    success = first.get("success")
    # seven.io returns either boolean True or string "true" depending on plan
    if success is True or str(success).lower() == "true":
        return SmsResult(True, "SMS dispatched.", raw=body)

    return SmsResult(
        False,
        f"Unexpected gateway response: {body}",
        raw=body,
        error=str(body),
    )


async def send_sms(api_key: str, to: str, text: str, sender: Optional[str] = None) -> SmsResult:
    sender = (sender or config.SMS_SENDER_NAME).strip() or "Anonymous"
    return await asyncio.to_thread(_send_sync, api_key, to, text, sender)


def _balance_sync(api_key: str) -> Optional[float]:
    try:
        r = requests.get(
            BALANCE_ENDPOINT,
            headers={"X-Api-Key": api_key},
            timeout=config.SMS_API_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        return float(r.text.strip())
    except (requests.RequestException, ValueError):
        return None


async def get_balance(api_key: str) -> Optional[float]:
    return await asyncio.to_thread(_balance_sync, api_key)
