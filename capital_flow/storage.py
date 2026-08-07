from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .engine import AggTrade


SCHEMA = """
CREATE TABLE IF NOT EXISTS spot_aggtrades_raw (
  symbol TEXT NOT NULL, aggregate_trade_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
  price REAL NOT NULL, quantity_btc REAL NOT NULL, notional_usd REAL NOT NULL,
  buyer_is_maker INTEGER NOT NULL, aggressor_side TEXT NOT NULL,
  received_at_ms INTEGER NOT NULL, PRIMARY KEY(symbol, aggregate_trade_id)
);
CREATE TABLE IF NOT EXISTS futures_aggtrades_raw (
  symbol TEXT NOT NULL, aggregate_trade_id INTEGER NOT NULL, timestamp_ms INTEGER NOT NULL,
  price REAL NOT NULL, quantity_btc REAL NOT NULL, notional_usd REAL NOT NULL,
  buyer_is_maker INTEGER NOT NULL, aggressor_side TEXT NOT NULL,
  received_at_ms INTEGER NOT NULL, PRIMARY KEY(symbol, aggregate_trade_id)
);
CREATE TABLE IF NOT EXISTS orderbook_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
  last_update_id INTEGER, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oi_raw (
  symbol TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, open_interest REAL,
  payload_json TEXT NOT NULL, PRIMARY KEY(symbol, timestamp_ms)
);
CREATE TABLE IF NOT EXISTS funding_raw (
  symbol TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, funding_rate REAL,
  payload_json TEXT NOT NULL, PRIMARY KEY(symbol, timestamp_ms)
);
CREATE TABLE IF NOT EXISTS liquidations_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
  side TEXT, notional_usd REAL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS top_trader_accounts_raw (
  symbol TEXT NOT NULL, period TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
  payload_json TEXT NOT NULL, PRIMARY KEY(symbol, period, timestamp_ms)
);
CREATE TABLE IF NOT EXISTS top_trader_positions_raw (
  symbol TEXT NOT NULL, period TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
  payload_json TEXT NOT NULL, PRIMARY KEY(symbol, period, timestamp_ms)
);
CREATE TABLE IF NOT EXISTS coinmetrics_raw (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_ms INTEGER, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graphsense_transactions_raw (txid TEXT PRIMARY KEY, timestamp_ms INTEGER, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS etf_source_raw (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_ms INTEGER, fund TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sec_filings_raw (accession_number TEXT PRIMARY KEY, filed_at TEXT, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS smart_money_raw (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_ms INTEGER, payload_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_spot_trades_time ON spot_aggtrades_raw(symbol, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_futures_trades_time ON futures_aggtrades_raw(symbol, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_orderbook_time ON orderbook_raw(symbol, timestamp_ms);
"""


class CapitalFlowStore:
    """Separate additive store. It never drops or overwrites an existing schema."""

    def __init__(self, path: str | os.PathLike[str] = "data/capital_flow.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_trade(self, trade: AggTrade, received_at_ms: int) -> None:
        table = "spot_aggtrades_raw" if trade.market == "spot" else "futures_aggtrades_raw"
        self.conn.execute(
            f"INSERT OR IGNORE INTO {table} (symbol, aggregate_trade_id, timestamp_ms, price, quantity_btc, notional_usd, buyer_is_maker, aggressor_side, received_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade.symbol, trade.aggregate_trade_id, trade.timestamp, trade.price, trade.quantity_btc, trade.notional_usd, int(trade.buyer_is_maker), trade.aggressor_side, received_at_ms),
        )
        self.conn.commit()

    def insert_json(self, table: str, payload: dict[str, Any], timestamp_ms: int, **fields: Any) -> None:
        allowed = {
            "orderbook_raw", "coinmetrics_raw", "graphsense_transactions_raw", "etf_source_raw",
            "sec_filings_raw", "smart_money_raw", "liquidations_raw",
        }
        if table not in allowed:
            raise ValueError(f"unsupported raw table: {table}")
        if table == "orderbook_raw":
            self.conn.execute("INSERT INTO orderbook_raw(symbol, timestamp_ms, last_update_id, payload_json) VALUES(?, ?, ?, ?)", (fields.get("symbol", "BTCUSDT"), timestamp_ms, fields.get("last_update_id"), json.dumps(payload, separators=(",", ":"))))
        elif table == "liquidations_raw":
            self.conn.execute("INSERT INTO liquidations_raw(symbol, timestamp_ms, side, notional_usd, payload_json) VALUES(?, ?, ?, ?, ?)", (fields.get("symbol", "BTCUSDT"), timestamp_ms, fields.get("side"), fields.get("notional_usd"), json.dumps(payload, separators=(",", ":"))))
        elif table == "graphsense_transactions_raw":
            self.conn.execute("INSERT OR REPLACE INTO graphsense_transactions_raw(txid, timestamp_ms, payload_json) VALUES(?, ?, ?)", (fields.get("txid", "unknown"), timestamp_ms, json.dumps(payload, separators=(",", ":"))))
        elif table == "sec_filings_raw":
            self.conn.execute("INSERT OR REPLACE INTO sec_filings_raw(accession_number, filed_at, payload_json) VALUES(?, ?, ?)", (fields.get("accession_number", "unknown"), fields.get("filed_at"), json.dumps(payload, separators=(",", ":"))))
        elif table == "etf_source_raw":
            self.conn.execute("INSERT INTO etf_source_raw(timestamp_ms, fund, payload_json) VALUES(?, ?, ?)", (timestamp_ms, fields.get("fund"), json.dumps(payload, separators=(",", ":"))))
        elif table == "smart_money_raw":
            self.conn.execute("INSERT INTO smart_money_raw(timestamp_ms, payload_json) VALUES(?, ?)", (timestamp_ms, json.dumps(payload, separators=(",", ":"))))
        else:
            self.conn.execute("INSERT INTO coinmetrics_raw(timestamp_ms, payload_json) VALUES(?, ?)", (timestamp_ms, json.dumps(payload, separators=(",", ":"))))
        self.conn.commit()

    def insert_oi(self, symbol: str, timestamp_ms: int, value: float, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO oi_raw(symbol, timestamp_ms, open_interest, payload_json) VALUES(?, ?, ?, ?)", (symbol, timestamp_ms, value, json.dumps(payload, separators=(",", ":"))))
        self.conn.commit()

    def insert_funding(self, symbol: str, timestamp_ms: int, value: float, payload: dict[str, Any]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO funding_raw(symbol, timestamp_ms, funding_rate, payload_json) VALUES(?, ?, ?, ?)", (symbol, timestamp_ms, value, json.dumps(payload, separators=(",", ":"))))
        self.conn.commit()

    def insert_top_trader(self, kind: str, symbol: str, period: str, timestamp_ms: int, payload: dict[str, Any]) -> None:
        table = "top_trader_accounts_raw" if kind == "accounts" else "top_trader_positions_raw"
        self.conn.execute(f"INSERT OR REPLACE INTO {table}(symbol, period, timestamp_ms, payload_json) VALUES(?, ?, ?, ?)", (symbol, period, timestamp_ms, json.dumps(payload, separators=(",", ":"))))
        self.conn.commit()

    def liquidation_window(self, symbol: str = "BTCUSDT", since_ms: int | None = None) -> dict[str, float]:
        where = "WHERE symbol = ?" + (" AND timestamp_ms >= ?" if since_ms else "")
        params: list[Any] = [symbol]
        if since_ms:
            params.append(since_ms)
        rows = self.conn.execute(f"SELECT side, COALESCE(SUM(notional_usd), 0) AS total FROM liquidations_raw {where} GROUP BY side", params).fetchall()
        return {
            "long_liquidation_usd": float(next((r["total"] for r in rows if r["side"] == "SELL"), 0.0)),
            "short_liquidation_usd": float(next((r["total"] for r in rows if r["side"] == "BUY"), 0.0)),
        }

    def trades(self, market: str, symbol: str = "BTCUSDT", since_ms: int | None = None, limit: int = 100000) -> list[AggTrade]:
        table = "spot_aggtrades_raw" if market == "spot" else "futures_aggtrades_raw"
        where = "WHERE symbol = ?" + (" AND timestamp_ms >= ?" if since_ms else "")
        params: list[Any] = [symbol]
        if since_ms:
            params.append(since_ms)
        params.append(limit)
        rows = self.conn.execute(f"SELECT * FROM {table} {where} ORDER BY timestamp_ms, aggregate_trade_id LIMIT ?", params).fetchall()
        return [AggTrade(market, row["symbol"], row["timestamp_ms"], row["aggregate_trade_id"], row["price"], row["quantity_btc"], row["notional_usd"], bool(row["buyer_is_maker"]), row["aggressor_side"]) for row in rows]

    def latest(self, table: str, symbol: str = "BTCUSDT") -> dict[str, Any] | None:
        if table not in {"oi_raw", "funding_raw", "top_trader_accounts_raw", "top_trader_positions_raw", "orderbook_raw"}:
            raise ValueError("unsupported latest table")
        rows = self.conn.execute(f"SELECT * FROM {table} WHERE symbol = ? ORDER BY timestamp_ms DESC LIMIT 1", (symbol,)).fetchall()
        if not rows:
            return None
        row = dict(rows[0])
        if "payload_json" in row:
            row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def health(self) -> dict[str, Any]:
        tables = {}
        for table in ("spot_aggtrades_raw", "futures_aggtrades_raw", "orderbook_raw", "oi_raw", "funding_raw", "liquidations_raw"):
            tables[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return {"path": str(self.path), "status": "PASS", "tables": tables}


def read_heartbeat(path: str | os.PathLike[str] = "data/capital_flow_heartbeat.json") -> dict[str, Any]:
    heartbeat_path = Path(path)
    if not heartbeat_path.exists():
        return {"status": "UNAVAILABLE", "collector_alive": False, "path": str(heartbeat_path), "reason": "heartbeat file missing"}
    try:
        payload = json.loads(heartbeat_path.read_text())
        updated_at = int(payload.get("timestamp_ms", 0))
        age = (int(__import__("time").time() * 1000) - updated_at) / 1000 if updated_at else None
        payload["age_seconds"] = age
        payload["status"] = "FRESH" if age is not None and age <= 30 else "STALE"
        payload["collector_alive"] = payload["status"] == "FRESH"
        return payload
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "UNRELIABLE", "collector_alive": False, "path": str(heartbeat_path), "reason": type(exc).__name__}
