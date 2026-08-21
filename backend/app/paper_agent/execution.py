"""Minute-native paper matcher with fail-closed execution semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import uuid4

from app.paper_agent.models import (
    ExecutionEvent,
    MinuteBar,
    OrderIntent,
    PositionLot,
    RiskConstitution,
)


@dataclass
class _PendingOrder:
    intent: OrderIntent
    eligible_at: datetime
    remaining_shares: int


class StrictMinuteExecutor:
    """Process signals only on a later minute and never fabricate missing fills."""

    def __init__(self, constitution: RiskConstitution | None = None) -> None:
        self.constitution = constitution or RiskConstitution()
        self.cash = float(self.constitution.initial_capital)
        self.pending: dict[str, _PendingOrder] = {}
        self.lots: list[PositionLot] = []
        self.events: list[ExecutionEvent] = []
        self.last_bar_time: dict[str, datetime] = {}
        self.last_prices: dict[str, float] = {}

    def submit(self, intent: OrderIntent) -> ExecutionEvent:
        if intent.id in self.pending or any(event.order_id == intent.id for event in self.events):
            raise ValueError(f"duplicate order id: {intent.id}")
        lot_size = self.constitution.lot_size
        shares = intent.shares - intent.shares % lot_size
        if shares <= 0:
            return self._event("order_rejected", intent.signal_time, intent, reason="buy_lot_size")
        normalized = intent.model_copy(update={"shares": shares})
        self.pending[intent.id] = _PendingOrder(
            intent=normalized,
            eligible_at=intent.signal_time,
            remaining_shares=shares,
        )
        return self._event("order_submitted", intent.signal_time, normalized)

    def process_bar(self, bar: MinuteBar) -> list[ExecutionEvent]:
        if not bar.complete:
            return []
        last = self.last_bar_time.get(bar.symbol)
        if last is not None and bar.datetime <= last:
            raise ValueError(f"out-of-order minute bar for {bar.symbol}: {bar.datetime}")
        self.last_bar_time[bar.symbol] = bar.datetime
        self.last_prices[bar.symbol] = bar.raw_close
        lag = (bar.received_at - (bar.datetime + timedelta(minutes=1))).total_seconds()
        if lag > self.constitution.max_data_lag_seconds:
            return [
                self._event(
                    "data_rejected",
                    bar.received_at,
                    symbol=bar.symbol,
                    reason=f"stale_minute_bar:{int(lag)}s",
                )
            ]

        emitted: list[ExecutionEvent] = []
        for order_id, pending in list(self.pending.items()):
            intent = pending.intent
            if intent.symbol != bar.symbol or bar.datetime < pending.eligible_at:
                continue
            if intent.side == "buy":
                event = self._fill_buy(pending, bar)
            else:
                event = self._fill_sell(pending, bar)
            emitted.append(event)
            if event.event_type in {"order_filled", "order_rejected"}:
                self.pending.pop(order_id, None)
            elif event.event_type == "order_partially_filled":
                pending.remaining_shares -= event.shares
                self.pending.pop(order_id, None)
        return emitted

    def settled_shares(self, symbol: str, trade_date: date) -> int:
        return sum(
            lot.remaining_shares
            for lot in self.lots
            if lot.symbol == symbol and lot.acquired_date < trade_date
        )

    def total_shares(self, symbol: str) -> int:
        return sum(lot.remaining_shares for lot in self.lots if lot.symbol == symbol)

    def equity(self) -> float:
        marked = sum(
            lot.remaining_shares * self.last_prices.get(lot.symbol, lot.entry_price)
            for lot in self.lots
        )
        return self.cash + marked

    def export_state(self) -> dict:
        return {
            "cash": self.cash,
            "lots": [lot.model_dump(mode="json") for lot in self.lots],
            "last_prices": self.last_prices,
            "pending": [
                {
                    "intent": pending.intent.model_dump(mode="json"),
                    "eligible_at": pending.eligible_at.isoformat(),
                    "remaining_shares": pending.remaining_shares,
                }
                for pending in self.pending.values()
            ],
        }

    def restore_state(self, payload: dict) -> None:
        self.cash = float(payload.get("cash", self.constitution.initial_capital))
        self.lots = [PositionLot.model_validate(item) for item in payload.get("lots", [])]
        self.last_prices = {
            str(symbol): float(value)
            for symbol, value in (payload.get("last_prices") or {}).items()
        }
        self.pending = {}
        for item in payload.get("pending", []):
            intent = OrderIntent.model_validate(item["intent"])
            eligible_at = datetime.fromisoformat(str(item["eligible_at"]))
            self.pending[intent.id] = _PendingOrder(
                intent=intent,
                eligible_at=eligible_at,
                remaining_shares=int(item["remaining_shares"]),
            )

    def _fill_buy(self, pending: _PendingOrder, bar: MinuteBar) -> ExecutionEvent:
        intent = pending.intent
        age = bar.datetime - pending.eligible_at
        if age >= timedelta(minutes=self.constitution.buy_order_ttl_minutes):
            return self._event("order_rejected", bar.datetime, intent, reason="buy_order_expired")
        if bar.is_suspended or bar.volume <= 0:
            return self._event("order_rejected", bar.datetime, intent, reason="buy_suspended")
        if bar.is_limit_up:
            return self._event("order_rejected", bar.datetime, intent, reason="buy_limit_up")
        held_symbols = {lot.symbol for lot in self.lots if lot.remaining_shares > 0}
        if (
            intent.symbol not in held_symbols
            and len(held_symbols) >= self.constitution.max_positions
        ):
            return self._event("order_rejected", bar.datetime, intent, reason="buy_no_slot")

        price = bar.raw_open * (1 + self.constitution.slippage_bps / 10_000)
        lot_size = self.constitution.lot_size
        volume_cap = int(
            bar.volume
            * self.constitution.volume_unit_shares
            * self.constitution.max_volume_participation
        )
        volume_cap -= volume_cap % lot_size
        account_equity = self.equity()
        symbol_value = self.total_shares(intent.symbol) * price
        position_room = max(account_equity * self.constitution.max_position_pct - symbol_value, 0)
        exposure_value = sum(
            lot.remaining_shares * self.last_prices.get(lot.symbol, lot.entry_price)
            for lot in self.lots
        )
        exposure_room = max(account_equity * self.constitution.max_exposure_pct - exposure_value, 0)
        cash_room = max(self.cash - self.constitution.min_commission, 0)
        shares = min(
            pending.remaining_shares,
            volume_cap,
            int(position_room / price),
            int(exposure_room / price),
            int(cash_room / (price * (1 + self.constitution.commission_pct))),
        )
        shares -= shares % lot_size
        if shares <= 0:
            return self._event("order_rejected", bar.datetime, intent, reason="buy_capacity")
        gross = shares * price
        commission = max(self.constitution.min_commission, gross * self.constitution.commission_pct)
        total_cost = gross + commission
        if total_cost > self.cash:
            return self._event("order_rejected", bar.datetime, intent, reason="buy_cash")
        self.cash -= total_cost
        self.lots.append(
            PositionLot(
                lot_id=f"lot_{uuid4().hex}",
                symbol=intent.symbol,
                acquired_date=bar.datetime.date(),
                shares=shares,
                remaining_shares=shares,
                entry_price=price,
                entry_cost=commission,
            )
        )
        event_type = (
            "order_filled" if shares == pending.remaining_shares else "order_partially_filled"
        )
        return self._event(
            event_type,
            bar.datetime,
            intent,
            shares=shares,
            price=price,
            fees=commission,
            reason="next_minute_open",
            cash_after=self.cash,
        )

    def _fill_sell(self, pending: _PendingOrder, bar: MinuteBar) -> ExecutionEvent:
        intent = pending.intent
        available = self.settled_shares(intent.symbol, bar.datetime.date())
        if available <= 0:
            return self._event("order_rejected", bar.datetime, intent, reason="sell_t_plus_one")
        if bar.is_suspended or bar.volume <= 0:
            return self._event("order_blocked", bar.datetime, intent, reason="sell_suspended")
        if bar.is_limit_down:
            return self._event("order_blocked", bar.datetime, intent, reason="sell_limit_down")
        price = bar.raw_open * (1 - self.constitution.slippage_bps / 10_000)
        lot_size = self.constitution.lot_size
        volume_cap = int(
            bar.volume
            * self.constitution.volume_unit_shares
            * self.constitution.max_volume_participation
        )
        volume_cap -= volume_cap % lot_size
        shares = min(pending.remaining_shares, available, volume_cap)
        shares -= shares % lot_size
        if shares <= 0:
            return self._event("order_blocked", bar.datetime, intent, reason="sell_liquidity")
        remaining = shares
        cost_basis = 0.0
        for index, lot in enumerate(self.lots):
            if lot.symbol != intent.symbol or lot.acquired_date >= bar.datetime.date():
                continue
            take = min(lot.remaining_shares, remaining)
            cost_basis += take * lot.entry_price + lot.entry_cost * (take / lot.shares)
            self.lots[index] = lot.model_copy(
                update={"remaining_shares": lot.remaining_shares - take}
            )
            remaining -= take
            if remaining == 0:
                break
        self.lots = [lot for lot in self.lots if lot.remaining_shares > 0]
        gross = shares * price
        commission = max(self.constitution.min_commission, gross * self.constitution.commission_pct)
        stamp_tax = gross * self.constitution.stamp_tax_pct
        fees = commission + stamp_tax
        realized_pnl = gross - fees - cost_basis
        self.cash += gross - fees
        event_type = (
            "order_filled" if shares == pending.remaining_shares else "order_partially_filled"
        )
        return self._event(
            event_type,
            bar.datetime,
            intent,
            shares=shares,
            price=price,
            fees=fees,
            realized_pnl=realized_pnl,
            reason="next_minute_open",
            cash_after=self.cash,
        )

    def _event(
        self,
        event_type: str,
        occurred_at,
        intent: OrderIntent | None = None,
        *,
        symbol: str | None = None,
        shares: int = 0,
        price: float | None = None,
        fees: float = 0.0,
        realized_pnl: float | None = None,
        reason: str = "",
        cash_after: float | None = None,
    ) -> ExecutionEvent:
        event = ExecutionEvent(
            id=f"evt_{uuid4().hex}",
            event_type=event_type,
            occurred_at=occurred_at,
            order_id=intent.id if intent else None,
            symbol=intent.symbol if intent else symbol,
            side=intent.side if intent else None,
            shares=shares,
            price=price,
            fees=fees,
            realized_pnl=realized_pnl,
            reason=reason,
            cash_after=cash_after,
        )
        self.events.append(event)
        return event
