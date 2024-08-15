#!/usr/bin/env -S python -u

import sys
import os
import json
import time
import math
import zmq
from dataclasses import dataclass

import rich
from rich.console import Console
from rich.align import Align
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live

from rich import print as print

from getchlib import getkey

console = Console()
console.show_cursor(False)


TTL = 5
RESET_TERMINAL_INTERVAL = 10

OP_BUY = 0
OP_SELL = 1

balance = 0.0
profit = 0.0
equity = 0.0

ctx = zmq.Context()
records = {}
orders = {}

subscriber = ctx.socket(zmq.SUB)
url = "tcp://localhost:5559"
subscriber.connect(url)


poller = zmq.Poller()
poller.register(subscriber, zmq.POLLIN)


@dataclass
class Order:
    """Class for a Metatrader order"""
    ticket: int
    time: int
    type: str
    size: float
    symbol: str
    open_price: float
    sl: float
    tp: float
    swap: float
    profit: float
    timestamp: int


class Record:
    """Class to sum multiple orders of the same symbol"""
    def __init__(self, order):
        self.symbol = order.symbol
        self.orders = [order]

    def add_order(self, order) -> None:
        self.orders.append(order)

    def swap(self) -> float:
        p = 0
        for o in self.orders:
            p += o.swap
        return p
    
    def profit(self) -> float:
        p = 0
        for o in self.orders:
            p += o.profit
        return p
    
    def lots(self) -> float:
        l = 0
        for o in self.orders:
            l += o.size
        return l

    def position(self) -> str:
        lots = 0
        for o in self.orders:
            if o.type == OP_BUY:
                lots += o.size
            elif o.type == OP_SELL:
                lots -= o.size
            else:
                continue

        if lots > 0:
            return f"LONG {lots:0.2f}"
        elif lots < 0:
            return f"SHORT {lots:0.2f}"
        else:
            return "NONE"

    def has_open_orders(self) -> bool:
        for o in self.orders:
            if o.type in [OP_BUY, OP_SELL]:
                return True
        return False

    def total(self) -> float:
        return self.profit() + self.swap()

    def get_row(self):
        return (self.symbol, self.position, self.swap, self.profit, self.swap() + self.profit())

    def __str__(self):
        return f"{self.symbol:6} {len(self.orders):4d} {self.position():>14} {self.profit():12,.2f}"


def update_records(msg):
    j = json.loads(msg)

    global balance
    global profit 
    global equity 

    balance = j["balance"]
    profit = j["profit"]
    equity = j["equity"]

    symbol = j["symbol"]

    # current time
    t = int(time.time())

    for order in j["orders"]:
        ticket = order["ticket"]
        try:
            o = orders[ticket]
            o.profit = order["profit"]
            o.swap = order["swap"]
            o.timestamp = t
        except KeyError:
            orders[ticket] = Order(*order.values(), t)

    records.clear()
    for k in list(orders.keys()):
        o = orders[k]

        if t - o.timestamp > TTL:
            del orders[k]
            continue

        if o.symbol in records.keys():
            records[o.symbol].add_order(o)
        else:
            records[o.symbol] = Record(o)


def draw_panel(mode="records", symbol=None):
    global layout

    footer_style = "bright_red" if profit < 0 else "bright_green"
    footer = Text(f"{balance:,.2f} | {profit:,.2f} ({abs(profit / balance * 100):.2f}%) | {equity:,.2f}", style=footer_style, justify="center")

    table = Table(show_header=False, box=None, min_width=40, expand=True)
    table.add_column("Symbol")
    table.add_column("Position", justify="left")
    table.add_column("Total", justify="right")

    for key in sorted(records):
        r = records[key]
        if not r.has_open_orders():
            continue
        if r.total() < 0:
            style = "bright_red"
        else:
            style = "bright_green"

        table.add_row(r.symbol, r.position(), str(f"{r.total():,.2f}"), style=style)

    layout["upper"].update(Align.center(table, vertical="middle"))
    layout["lower"].update(footer)


def main():
    global layout
    global account

    subscriber.setsockopt_string(zmq.SUBSCRIBE, account)

    last_message_time = int(time.time()) - 5
    mode = "records"

    layout = Layout()
    layout.split_column(
            Layout(name="upper"),
            Layout(name="lower")
            )
    layout["lower"].size = 1
    layout["lower"].visible = False

    nodata = Panel(Align.center(
        Text(f"{account} - NO DATA", justify="center"),
        vertical="middle"), style="white on red")

    layout["upper"].update(nodata)

    with Live(layout, console=console, auto_refresh=False) as live:
        live.update(layout, refresh=True)
        while True:
            socks = dict(poller.poll(100))
            if subscriber in socks and socks[subscriber] == zmq.POLLIN:
                msg = subscriber.recv()
                topic, data = msg.split()
                last_message_time = int(time.time())
                update_records(data)
                layout["lower"].visible = True
                draw_panel()
                live.update(layout, refresh=True)
            elif int(time.time()) - last_message_time > TTL:
                layout["upper"].update(nodata)
                layout["lower"].visible = False
                live.update(layout, refresh=True)


if __name__ == '__main__':                
    global account

    if len(sys.argv) < 2:
        account = "711700"
    else:
        account = sys.argv[1]
    main()

