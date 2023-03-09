import abc
import inspect
import pandas as pd
from typing import Any, TypedDict, Generic, TypeVar

from tradepy.backtesting.context import Context
from tradepy.backtesting.backtester import Backtester
from tradepy.backtesting.account import TradeBook
from tradepy.backtesting.position import Position
from tradepy.types import IndSeries
from tradepy.utils import calc_pct_chg


class TickData(TypedDict):
    code: str
    timestamp: str
    open: float
    close: float
    high: float
    low: float
    vol: int

    chg: float | None
    pct_chg: float | None


TickDataType = TypeVar("TickDataType", bound=TickData)


class StrategyBase(Generic[TickDataType]):

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.buy_indicators: list[str] = inspect.getfullargspec(self.should_buy).args[1:]
        self.close_indicators: list[str] = inspect.getfullargspec(self.should_close).args[1:]

    def __getattr__(self, name: str):
        return getattr(self.ctx, name)

    def pre_process(self, bars_df: pd.DataFrame):
        return bars_df

    def post_process(self, bars_df: pd.DataFrame):
        return bars_df

    @abc.abstractmethod
    def should_buy(self, *indicators) -> bool:
        raise NotImplementedError

    def should_close(self, *indicators) -> bool:
        return False

    def should_stop_loss(self, tick: TickDataType, position: Position) -> float | None:
        # During opening
        open_pct_chg = calc_pct_chg(position.price, tick["open"])
        if open_pct_chg <= -self.stop_loss:
            return tick["open"]

        # During exchange
        low_pct_chg = calc_pct_chg(position.price, tick["low"])
        if low_pct_chg <= -self.stop_loss:
            return position.price_at_pct_change(-self.stop_loss)

    def should_take_profit(self, tick: TickDataType, position: Position) -> float | None:
        # During opening
        open_pct_chg = calc_pct_chg(position.price, tick["open"])
        if open_pct_chg >= self.take_profit:
            return tick["open"]

        # During exchange
        high_pct_chg = calc_pct_chg(position.price, tick["high"])
        if high_pct_chg >= self.take_profit:
            return position.price_at_pct_change(self.take_profit)

    def get_pool_and_budget(self, bars_df: pd.DataFrame, buy_indices: list[Any], budget: float) -> tuple[pd.DataFrame, float]:
        pool_df = bars_df.loc[buy_indices].copy()

        # Limit number of new opens
        if len(pool_df) > self.max_position_opens:
            pool_df = bars_df.sample(self.max_position_opens)

        # Limit position budget allocation
        min_position_allocation = budget // len(pool_df)
        max_position_value = self.max_position_size * self.account.get_total_asset_value()

        if min_position_allocation > max_position_value:
            budget = len(pool_df) * max_position_value

        return pool_df, budget

    def generate_positions(self, pool_df: pd.DataFrame, budget: float) -> list[Position]:
        if pool_df.empty or budget <= 0:
            return []
        # Calculate the minimum number of shares to buy per stock
        num_stocks = len(pool_df)

        pool_df["trade_price"] = pool_df["close"] * self.trading_unit
        pool_df["trade_shares"] = budget / num_stocks // pool_df["trade_price"]

        # Gather the remaining budget
        remaining_budget = budget - (pool_df["trade_price"] * pool_df["trade_shares"]).sum()

        # Distribute that budget again, starting from the big guys
        pool_df.sort_values("trade_price", inplace=True, ascending=False)
        for row in pool_df.itertuples():
            if residual_shares := remaining_budget // row.trade_price:
                pool_df.at[row.Index, "trade_shares"] += residual_shares
                remaining_budget -= residual_shares * row.trade_price

        return [
            Position(
                timestamp=row.Index[0],
                code=row.Index[1],
                company=row.company,
                price=row.close,
                shares=row.trade_shares * self.trading_unit,
            )
            for row in pool_df.itertuples()
            if row.trade_shares > 0
        ]

    @classmethod
    def backtest(cls, ticks_data: pd.DataFrame, ctx: Context) -> tuple[pd.DataFrame, TradeBook]:
        instance = cls(ctx)
        bt = Backtester(ctx)
        return bt.run(ticks_data.copy(), instance)

    @classmethod
    def get_indicators_df(cls, ticks_data: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        bt = Backtester(ctx)
        strategy = cls(ctx)
        return bt.get_indicators_df(ticks_data.copy(), strategy)


class Strategy(StrategyBase[TickData]):

    def chg(self, close: IndSeries):
        return (close - close.shift(1)).fillna(0).round(2)

    def pct_chg(self, chg: IndSeries, close: IndSeries):
        return (100 * chg / close.shift(1)).fillna(0).round(2)