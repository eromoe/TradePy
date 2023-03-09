from functools import cached_property
import pandas as pd
from dataclasses import asdict, dataclass, field
from typing import TypedDict, Iterable
from typing_extensions import NotRequired

from tradepy.backtesting.holdings import Holdings
from tradepy.backtesting.position import Position
from tradepy.types import TradeActions, TradeActionType
from tradepy.utils import round_val


class TradeLog(TypedDict):
    timestamp: str
    tag: str

    pos_id: str
    code: str
    shares: int
    price: float
    total_value: float
    chg: NotRequired[float | None]
    pct_chg: NotRequired[float | None]
    total_return: NotRequired[float | None]


class CapitalsLog(TypedDict):
    timestamp: str
    positions_value: float
    free_cash_amount: float


class TradeBook:

    def __init__(self) -> None:
        self.trade_logs: list[TradeLog] = list()
        self.capital_logs: list[CapitalsLog] = list()

    @cached_property
    def trade_logs_df(self) -> pd.DataFrame:
        df = pd.DataFrame(self.trade_logs)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def reset(self):
        self.trade_logs = list()
        self.capital_logs = list()

    def __sell(self, timestamp: str, pos: Position, action: TradeActionType):
        chg = pos.chg_at(pos.latest_price)
        pct_chg = pos.pct_chg_at(pos.latest_price)
        assert pos.latest_price

        self.trade_logs.append({
            "timestamp": timestamp,
            "tag": action,
            "pos_id": pos.id,
            "code": pos.code,
            "shares": pos.shares,
            "price": pos.latest_price,
            "total_value": pos.latest_price * pos.shares,
            "chg": chg,
            "pct_chg": pct_chg,
            "total_return": (pos.price * pct_chg * 1e-2) * pos.shares
        })

    def buy(self, timestamp: str, pos: Position):
        self.trade_logs.append({
            "timestamp": timestamp,
            "tag": TradeActions.OPEN,
            "pos_id": pos.id,
            "code": pos.code,
            "shares": pos.shares,
            "price": pos.price,
            "total_value": pos.price * pos.shares,
        })

    def exit(self, *args, **kwargs):
        kwargs["action"] = TradeActions.CLOSE
        self.__sell(*args, **kwargs)

    def stop_loss(self, *args, **kwargs):
        kwargs["action"] = TradeActions.STOP_LOSS
        self.__sell(*args, **kwargs)

    def take_profit(self, *args, **kwargs):
        kwargs["action"] = TradeActions.TAKE_PROFIT
        self.__sell(*args, **kwargs)

    def close_position(self, *args, **kwargs):
        kwargs["action"] = TradeActions.CLOSE
        self.__sell(*args, **kwargs)

    def log_capitals(self, timestamp, cash_amount: float, positions_value: float):
        self.capital_logs.append({
            "timestamp": timestamp,
            "positions_value": positions_value,
            "free_cash_amount": cash_amount,
        })

    @staticmethod
    def describe(trade_logs: list[TradeLog] | pd.DataFrame):
        if isinstance(trade_logs, list):
            df = pd.DataFrame(trade_logs)
        else:
            df = trade_logs.copy().reset_index()

        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)

        close_outcomes = df.query('tag == "平仓"').copy()
        close_outcomes["盈利平仓"] = close_outcomes["pct_chg"] > 0
        total_outcomes = df.groupby("tag").size()

        wins = total_outcomes["止盈"] + (close_wins := close_outcomes["盈利平仓"].sum())
        loses = total_outcomes["止损"] + (close_lose := (~close_outcomes["盈利平仓"]).sum())

        print(f'''
===========
开仓 = {total_outcomes["开仓"]}
止损 = {total_outcomes["止损"]}
止盈 = {total_outcomes["止盈"]}
平仓亏损 = {close_lose}
平仓盈利 = {close_wins}

胜率 {100 * round(wins / (wins + loses), 4)}%
===========
        ''')

        print(close_outcomes.groupby("盈利平仓")["pct_chg"].describe().round(2))


@dataclass
class Account:

    buy_commission_rate: float = 0
    sell_commission_rate: float = 0

    cash_amount: float = 0
    holdings: Holdings = field(default_factory=Holdings)

    def tick(self, price_lookup: Holdings.PriceLookupFun):
        if any(self.holdings):
            self.holdings.tick(price_lookup)

    def buy(self, positions: Iterable[Position]):
        if cost_total := self.holdings.buy(positions):
            self.cash_amount -= self.add_buy_commissions(cost_total)

    def sell(self, positions: Iterable[Position]):
        if close_total := self.holdings.sell(positions):
            self.cash_amount += self.take_sell_commissions(close_total)

    def clear(self):
        all_positions = [
            pos
            for _, pos in self.holdings
        ]
        self.sell(all_positions)

    def clone(self):
        return self.__class__(**{
            k: v
            for k, v in asdict(self).items()
        })

    @round_val
    def add_buy_commissions(self, amount: float) -> float:
        return amount * (1 + self.buy_commission_rate * 1e-2)

    @round_val
    def take_sell_commissions(self, amount: float) -> float:
        return amount * (1 - self.sell_commission_rate * 1e-2)

    def get_total_asset_value(self) -> float:
        return self.holdings.get_total_worth() + self.cash_amount

    def get_positions_value(self) -> float:
        return self.holdings.get_total_worth()