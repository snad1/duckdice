"""Fake DuckDice casino for tests (E0-S2). Dice only (+ faucet flag).

Speaks the REST shape the clients parse (duckdice.py `api_place_bet` /
`api_get_balances` / rate-limit probe, tg/engine.py same names). Key in the
query string:

    POST https://duckdice.io/api/dice/play?api_key=K
        {symbol UPPER, chance str, isHigh bool, amount str, faucet bool}
        200 {"bet": {hash, symbol, result bool, isHigh, number, threshold, chance,
             payout, betAmount, winAmount, profit (strings)},
             "user": {hash, username, balance}}
        errors arrive as HTTP 200 {"error": "..."}
    GET  /bot/stats/{SYMBOL}?api_key=K -> {"balances": {"main", "faucet"}}
Read-only GETs (balance, the CLI's 429 probe) never consume the script.

Script outcomes: win, loss, insufficient_balance, http_429, timeout,
api_error_balance. Set `retry_after` to send a Retry-After header with 429s.
An empty script answers insufficient_balance and sets `exhausted`.
"""

import json
from collections import deque
from urllib.parse import parse_qs, urlsplit

import httpx
import requests

API_BASE = "https://duckdice.io/api"
OUTCOMES = {"win", "loss", "insufficient_balance", "http_429", "timeout",
            "api_error_balance"}


class UnexpectedRequest(BaseException):
    """A request the fake has no route for. BaseException on purpose: the bet
    loops catch Exception and retry forever, which would hang the test."""


class FakeTimeout(Exception):
    """Raised by FakeCasino.handle; adapters map it to the client's timeout."""


class FakeCasino:
    def __init__(self, balance: float = 10.0, faucet_balance: float = 0.0,
                 symbol: str = "USDT", api_key: str = "test-key", script=()):
        self.balance = float(balance)
        self.faucet_balance = float(faucet_balance)
        self.symbol = symbol
        self.api_key = api_key
        self.script = deque()
        self.calls = []        # (method, url, body) for every request
        self.bets = []         # {"outcome", "amount", "payout", "profit", "balance", "number", "faucet"}
        self.exhausted = False
        self.retry_after = None  # Retry-After header value on 429 replies
        self.queue(*script)

    def queue(self, *outcomes):
        for o in outcomes:
            if o not in OUTCOMES:
                raise ValueError(f"unknown outcome {o!r}")
            self.script.append(o)

    @property
    def bet_posts(self) -> int:
        return sum(1 for m, _, _ in self.calls if m == "POST")

    @property
    def profit(self) -> float:
        return sum(b["profit"] for b in self.bets)

    def handle(self, method: str, url: str, body: dict):
        """Return (status, json_body) or raise FakeTimeout."""
        self.calls.append((method, url, body))
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        if parse_qs(parts.query).get("api_key") != [self.api_key]:
            raise UnexpectedRequest(f"missing api_key in {url}")
        if method == "GET" and base == f"{API_BASE}/bot/stats/{self.symbol}":
            return 200, {"balances": {"main": f"{self.balance:.8f}",
                                      "faucet": f"{self.faucet_balance:.8f}"}}
        if method != "POST" or base != f"{API_BASE}/dice/play":
            raise UnexpectedRequest(f"unexpected request {method} {url}")

        amount = float(body["amount"])
        wallet = self.faucet_balance if body["faucet"] else self.balance
        outcome = self.script.popleft() if self.script else None
        if outcome is None:
            self.exhausted = True
            outcome = "insufficient_balance"
        if outcome == "timeout":
            raise FakeTimeout()
        if outcome == "http_429":
            return 429, {"error": "Too Many Attempts."}
        if outcome == "api_error_balance":
            # A rejection that mentions "balance" but is not insufficient funds.
            return 200, {"error": "Balance currency not supported"}
        if outcome == "insufficient_balance" or amount > wallet:
            return 200, {"error": "Insufficient balance"}

        win = outcome == "win"
        chance = float(body["chance"])
        payout_mult = round(99.0 / chance, 4)
        high = bool(body["isHigh"])
        threshold = int(round(9999 - chance * 100)) if high else int(round(chance * 100))
        number = (threshold + 1 if high else threshold - 1) if win else \
                 (threshold - 1 if high else threshold + 1)
        win_amount = round(amount * payout_mult, 8) if win else 0.0
        profit = round(win_amount - amount, 8)
        if body["faucet"]:
            self.faucet_balance = round(self.faucet_balance + profit, 8)
        else:
            self.balance = round(self.balance + profit, 8)
        self.bets.append({"outcome": outcome, "amount": amount, "payout": win_amount,
                          "profit": profit, "balance": self.balance, "number": number,
                          "faucet": body["faucet"]})
        return 200, {
            "bet": {"hash": f"h{len(self.bets):08d}", "symbol": body["symbol"],
                    "result": win, "isHigh": high, "number": number, "threshold": threshold,
                    "chance": body["chance"], "payout": f"{payout_mult:.4f}",
                    "betAmount": body["amount"], "winAmount": f"{win_amount:.8f}",
                    "profit": f"{profit:.8f}", "nonce": len(self.bets)},
            "user": {"hash": "u1", "username": "test",
                     "balance": f"{self.faucet_balance if body['faucet'] else self.balance:.8f}"},
        }

    def _headers(self, status: int) -> dict:
        if status == 429 and self.retry_after is not None:
            return {"Retry-After": str(self.retry_after)}
        return {}

    # ── adapters ─────────────────────────────────────────
    def requests_api(self) -> "RequestsAPI":
        return RequestsAPI(self)

    def async_client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else {}
            try:
                status, payload = self.handle(request.method, str(request.url), body)
            except FakeTimeout:
                raise httpx.ReadTimeout("fake timeout", request=request)
            return httpx.Response(status, json=payload, headers=self._headers(status))
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeResponse:
    def __init__(self, status: int, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class RequestsAPI:
    """Stands in for the module-level `requests.get` / `requests.post` the CLI calls."""

    def __init__(self, casino: FakeCasino):
        self.casino = casino

    def _call(self, method, url, body):
        try:
            status, payload = self.casino.handle(method, url, body or {})
            return FakeResponse(status, payload, self.casino._headers(status))
        except FakeTimeout:
            raise requests.exceptions.Timeout("fake timeout")

    def get(self, url, headers=None, timeout=None, **_):
        return self._call("GET", url, None)

    def post(self, url, headers=None, json=None, timeout=None, **_):
        return self._call("POST", url, json)
