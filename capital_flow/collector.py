from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import signal
import time
from typing import Any, Awaitable, Callable

from .engine import normalize_agg_trade
from .storage import CapitalFlowStore


SPOT_WS = "wss://stream.binance.com:9443/ws/{symbol}@aggTrade"
FUTURES_WS = "wss://fstream.binance.com/ws/{symbol}@aggTrade"
FORCE_ORDER_WS = "wss://fstream.binance.com/ws/{symbol}@forceOrder"
SPOT_DEPTH_WS = "wss://stream.binance.com:9443/ws/{symbol}@depth@100ms"


class SingleOwnerLock:
    """OS-backed single-owner guard; the kernel releases it on crash."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\nstarted_at_ms={int(time.time() * 1000)}\n")
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


class DepthReconciler:
    """Binance diff-depth sequence guard; a gap invalidates the book."""

    def __init__(self) -> None:
        self.last_update_id: int | None = None
        self.book: dict[str, Any] | None = None
        self.needs_resync = True

    def seed(self, snapshot: dict[str, Any]) -> None:
        self.book = {"bids": snapshot.get("bids", []), "asks": snapshot.get("asks", [])}
        self.last_update_id = int(snapshot.get("lastUpdateId", 0))
        self.needs_resync = False

    def apply(self, event: dict[str, Any]) -> bool:
        first = int(event.get("U", 0))
        last = int(event.get("u", 0))
        if self.book is None or self.last_update_id is None or first > self.last_update_id + 1:
            self.needs_resync = True
            return False
        if last <= self.last_update_id:
            return True
        if not (first <= self.last_update_id + 1 <= last):
            self.needs_resync = True
            return False

        def update(key: str) -> None:
            levels = {str(p): str(q) for p, q in self.book.get(key, [])}
            for price, quantity in event.get(key, []):
                if float(quantity) == 0:
                    levels.pop(str(price), None)
                else:
                    levels[str(price)] = str(quantity)
            items = [[p, q] for p, q in levels.items()]
            items.sort(key=lambda x: float(x[0]), reverse=(key == "bids"))
            self.book[key] = items

        update("bids")
        update("asks")
        self.last_update_id = last
        return True


class BinancePublicCollector:
    """Opt-in public-data collector. It never places orders and has no keys."""

    def __init__(self, store: CapitalFlowStore, symbol: str = "BTCUSDT") -> None:
        self.store = store
        self.symbol = symbol.lower()
        self.running = True
        self.depth = DepthReconciler()
        self.last_orderbook_persist_ms = 0
        self.sockets: set[Any] = set()
        self.reconnect_count = 0
        self.error_count = 0
        self.health: dict[str, Any] = {
            "collector_owner": os.getpid(),
            "symbol": self.symbol.upper(),
            "spot_ws_alive": False,
            "futures_ws_alive": False,
            "depth_alive": False,
            "liquidation_ws_alive": False,
            "last_spot_trade": None,
            "last_futures_trade": None,
            "last_oi_update": None,
            "last_funding_update": None,
            "last_top_trader_update": None,
            "last_liquidation": None,
            "reconnect_count": 0,
            "error_count": 0,
        }

    def _write_heartbeat(self, status: str = "RUNNING") -> None:
        path = os.environ.get("NCE_CAPITAL_FLOW_HEARTBEAT", "/var/lib/nce-trading/capital_flow_heartbeat.json")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {**self.health, "status": status, "timestamp_ms": int(time.time() * 1000)}
        temp = f"{path}.tmp.{os.getpid()}"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    async def _heartbeat_loop(self) -> None:
        while self.running:
            try:
                self.health["reconnect_count"] = self.reconnect_count
                self.health["error_count"] = self.error_count
                self._write_heartbeat()
            except Exception:
                self.error_count += 1
            await asyncio.sleep(5)

    async def _rest_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | list[Any]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required only when the opt-in collector is launched") from exc
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "NCE-Capital-Flow/2.0"}) as client:
            response = await client.get(url, params=params)
            if response.status_code in (418, 429):
                retry_after = float(response.headers.get("Retry-After", "2"))
                await asyncio.sleep(min(max(retry_after, 1.0), 60.0))
            response.raise_for_status()
            return response.json()

    async def _stream(self, url: str, handler: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets is required only when the opt-in collector is launched") from exc
        delay = 1.0
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=60, max_queue=10000) as socket:
                    self.sockets.add(socket)
                    delay = 1.0
                    if "fstream" in url and "aggTrade" in url:
                        self.health["futures_ws_alive"] = True
                    elif "stream.binance" in url and "aggTrade" in url:
                        self.health["spot_ws_alive"] = True
                    elif "forceOrder" in url:
                        self.health["liquidation_ws_alive"] = True
                    async for raw in socket:
                        await handler(raw if isinstance(raw, dict) else __import__("json").loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error_count += 1
                self.reconnect_count += 1
                if "fstream" in url and "aggTrade" in url:
                    self.health["futures_ws_alive"] = False
                elif "stream.binance" in url and "aggTrade" in url:
                    self.health["spot_ws_alive"] = False
                elif "forceOrder" in url:
                    self.health["liquidation_ws_alive"] = False
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            finally:
                self.sockets = {socket for socket in self.sockets if not getattr(socket, "closed", True)}

    async def spot_aggtrade(self) -> None:
        async def handle(payload: dict[str, Any]) -> None:
            trade = normalize_agg_trade(payload, "spot", self.symbol.upper())
            self.store.insert_trade(trade, int(time.time() * 1000))
            self.health["last_spot_trade"] = trade.timestamp
        await self._stream(SPOT_WS.format(symbol=self.symbol), handle)

    async def futures_aggtrade(self) -> None:
        async def handle(payload: dict[str, Any]) -> None:
            trade = normalize_agg_trade(payload, "futures", self.symbol.upper())
            self.store.insert_trade(trade, int(time.time() * 1000))
            self.health["last_futures_trade"] = trade.timestamp
        await self._stream(FUTURES_WS.format(symbol=self.symbol), handle)

    async def liquidations(self) -> None:
        async def handle(payload: dict[str, Any]) -> None:
            data = payload.get("o", payload.get("data", {}).get("o", {}))
            side = data.get("S")
            price = float(data.get("ap") or data.get("p") or 0)
            qty = float(data.get("q") or 0)
            notional = price * qty
            self.store.insert_json("liquidations_raw", payload, int(payload.get("E", time.time() * 1000)), symbol=self.symbol.upper(), side=side, notional_usd=notional)
            self.health["last_liquidation"] = int(payload.get("E", time.time() * 1000))
        await self._stream(FORCE_ORDER_WS.format(symbol=self.symbol), handle)

    async def depth_stream(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets is required only when the opt-in collector is launched") from exc
        url = SPOT_DEPTH_WS.format(symbol=self.symbol)
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=60, max_queue=10000) as socket:
                    # Buffer updates while obtaining the REST snapshot. The
                    # first accepted event must satisfy U <= last+1 <= u.
                    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
                    reading = True

                    async def reader() -> None:
                        try:
                            while reading:
                                raw = await socket.recv()
                                await queue.put(raw if isinstance(raw, dict) else json.loads(raw))
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            # Closing the socket during SIGTERM is expected;
                            # do not turn normal shutdown into an unhandled
                            # Task exception. Active-stream failures are
                            # handled by the outer reconnect loop.
                            if self.running:
                                raise

                    reader_task = asyncio.create_task(reader())
                    try:
                        snapshot = await self._rest_json("https://api.binance.com/api/v3/depth", {"symbol": self.symbol.upper(), "limit": 1000})
                        self.depth.seed(snapshot)
                        self.health["depth_alive"] = True
                        initial_ms = int(time.time() * 1000)
                        self.last_orderbook_persist_ms = initial_ms
                        compact_snapshot = {"bids": snapshot.get("bids", [])[:100], "asks": snapshot.get("asks", [])[:100]}
                        self.store.insert_json("orderbook_raw", compact_snapshot, initial_ms, symbol=self.symbol.upper(), last_update_id=snapshot.get("lastUpdateId"))
                        first_live = False
                        while True:
                            payload = await queue.get()
                            if payload.get("u") is None:
                                continue
                            if not first_live and int(payload.get("u", 0)) <= int(snapshot.get("lastUpdateId", 0)):
                                continue
                            first_live = True
                            if self.depth.apply(payload) and self.depth.book:
                                now_ms = int(time.time() * 1000)
                                # The executed-flow engine only needs a recent
                                # displayed-liquidity sample. Persisting every
                                # 100ms full-depth diff creates unbounded storage
                                # pressure and is not needed for the API.
                                if now_ms - self.last_orderbook_persist_ms >= 1000:
                                    compact_book = {"bids": self.depth.book.get("bids", [])[:100], "asks": self.depth.book.get("asks", [])[:100]}
                                    self.store.insert_json("orderbook_raw", compact_book, now_ms, symbol=self.symbol.upper(), last_update_id=self.depth.last_update_id)
                                    self.last_orderbook_persist_ms = now_ms
                            elif self.depth.needs_resync:
                                raise RuntimeError("depth sequence gap; reconnecting for a fresh snapshot")
                    finally:
                        reading = False
                        reader_task.cancel()
                        await asyncio.gather(reader_task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error_count += 1
                self.reconnect_count += 1
                self.health["depth_alive"] = False
                await asyncio.sleep(2)

    async def poll_derivatives(self) -> None:
        """Poll official public Futures state; cadence is intentionally slow."""
        symbol = self.symbol.upper()
        while self.running:
            now_ms = int(time.time() * 1000)
            try:
                oi = await self._rest_json("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": symbol})
                if isinstance(oi, dict) and oi.get("openInterest") is not None:
                    oi_timestamp = int(oi.get("time") or now_ms)
                    self.store.insert_oi(symbol, oi_timestamp, float(oi["openInterest"]), oi)
                    self.health["last_oi_update"] = oi_timestamp
                premium = await self._rest_json("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
                if isinstance(premium, dict) and premium.get("lastFundingRate") is not None:
                    funding_timestamp = int(premium.get("time") or now_ms)
                    self.store.insert_funding(symbol, funding_timestamp, float(premium["lastFundingRate"]), premium)
                    self.health["last_funding_update"] = funding_timestamp
                for kind, endpoint in (("accounts", "topLongShortAccountRatio"), ("positions", "topLongShortPositionRatio")):
                    for period in ("5m", "15m", "30m", "1h", "4h", "1d"):
                        data = await self._rest_json(f"https://fapi.binance.com/futures/data/{endpoint}", {"symbol": symbol, "period": period, "limit": 1})
                        if isinstance(data, list) and data:
                            item = dict(data[-1])
                            if kind == "accounts":
                                item["accountRatio"] = item.get("longShortRatio")
                            else:
                                item["positionRatio"] = item.get("longShortRatio")
                                item["longPosition"] = item.get("longPosition", item.get("longAccount"))
                                item["shortPosition"] = item.get("shortPosition", item.get("shortAccount"))
                            self.store.insert_top_trader(kind, symbol, period, int(item.get("timestamp") or now_ms), item)
                            # The payload timestamp is the market observation
                            # time and may be old for the 1d period. Health
                            # must report successful ingestion time instead.
                            self.health["last_top_trader_update"] = now_ms
            except Exception:
                # The API exposes stale/unavailable status from storage; a
                # transient poll error is not converted into a numeric value.
                self.error_count += 1
            await asyncio.sleep(60)

    async def run(self) -> None:
        lock_path = os.environ.get("NCE_CAPITAL_FLOW_LOCK", "/var/lib/nce-trading/capital_flow_collector.lock")
        lock = SingleOwnerLock(lock_path)
        if not lock.acquire():
            print("ANOTHER_COLLECTOR_ALREADY_ACTIVE", flush=True)
            return False
        self._write_heartbeat("STARTING")
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass
        # A legacy NCE futures 1s collector already owns the Futures aggTrade
        # socket on installations that set this flag.  In that mode the
        # legacy producer must publish raw trades into this store; opening a
        # second Futures socket here would violate source ownership.
        enable_futures = os.environ.get("NCE_CAPITAL_FLOW_ENABLE_FUTURES_AGGTRADE", "1").lower() not in {"0", "false", "no"}
        if not enable_futures:
            self.health["futures_ws_alive"] = False
            self.health["futures_source"] = "legacy_nce_1s_collector"
        tasks = [asyncio.create_task(fn()) for fn in (
            [self.spot_aggtrade, self.futures_aggtrade] if enable_futures else [self.spot_aggtrade]
        )]
        tasks.extend(asyncio.create_task(fn()) for fn in (self.liquidations, self.depth_stream, self.poll_derivatives))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(tasks + [stop_task], return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                self.running = False
                for socket in list(self.sockets):
                    try:
                        await socket.close()
                    except Exception:
                        pass
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.running = False
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            self._write_heartbeat("STOPPED")
            lock.release()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="NCE opt-in public Binance Capital Flow collector")
    parser.add_argument("--db", default=os.environ.get("NCE_CAPITAL_FLOW_DB", "/var/lib/nce-trading/capital_flow.sqlite3"))
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    store = CapitalFlowStore(args.db)
    try:
        result = asyncio.run(BinancePublicCollector(store, args.symbol).run())
        if result is False:
            raise SystemExit(2)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
