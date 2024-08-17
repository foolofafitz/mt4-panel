#!/usr/bin/env -S python -u

import sys
import os
import json
import time
import zmq
import threading
from getchlib import getkey

from dataclasses import dataclass

from rich.console import Console
from rich.align import Align
from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live

from rich import print as print
from rich import box

TTL = 5
RESET_TERMINAL_INTERVAL = 10

OP_BUY = 0
OP_SELL = 1

ORDER_TYPES = ["BUY", "SELL", "BUY LIMIT", "BUY STOP", "SELL LIMIT", "SELL STOP"]

SHOWFILE = '/home/jesse/.show-profit'

balance = 0.0
profit = 0.0
equity = 0.0

quit = False


modes = ["positions", "orders", "pending"]
mode_index = 0
mode = modes[mode_index]

if os.path.exists(SHOWFILE):
    hide = False
else:
    hide = True

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
    bid: float
    ask: float
    digits: int
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
        count = 0
        for o in self.orders:
            if o.type == OP_BUY:
                lots += o.size
                count += 1
            elif o.type == OP_SELL:
                lots -= o.size
                count += 1
            else:
                continue

        if count > 1:
            s = f" ({count})"
        else:
            s = ""

        if lots > 0:
            #return f"⬆️ {lots:0.2f}"
            return f"LONG {lots:0.2f}" + s
        elif lots < 0:
            #return f"⬇️ {lots:0.2f}"
            return f"SHORT {lots:0.2f}" + s
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

    #symbol = j["symbol"]

    # current time
    t = int(time.time())

    for order in j["orders"]:
        ticket = order["ticket"]
        try:
            o = orders[ticket]
            o.profit = order["profit"]
            o.swap = order["swap"]
            o.bid = order["bid"]
            o.ask = order["ask"]
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


def ctf(num: float) -> Text:
    """Convert float to colored text"""
    if num < 0:
        return Text(f"{num:,.2f}", style="bright_red")
    else:
        return Text(f"{num:,.2f}", style="bright_green")

def draw_records():
    #table = Table.grid(padding=(0,2), expand=True)
    table = Table(box=box.SIMPLE, min_width=40, expand=True)
    table.add_column("Symbol")
    table.add_column("Position")
    table.add_column("Profit", justify="right")
    table.add_column("Swap", justify="right")
    table.add_column("Total", justify="right")

    #table.add_row("Symbol", "Position", "Swap", "Profit", "Total")
    #table.add_section()
    #table.add_section()

    for key in sorted(records):
        r = records[key]
        if not r.has_open_orders():
            continue
        if r.total() < 0:
            style = "bright_red"
        else:
            style = "bright_green"

        table.add_row(r.symbol, r.position(), ctf(r.profit()), ctf(r.swap()),
                      Text(f"{r.total():,.2f}", style=style))

    return table

def draw_orders():
    table = Table(box=box.SIMPLE, min_width=40, expand=True)
    table.add_column("Ticket")
    table.add_column("Symbol")
    table.add_column("Size")
    table.add_column("Swap", justify="right")
    table.add_column("Profit", justify="right")

    #table.add_section()
    for k in list(orders.keys()):
        o = orders[k]
        if o.type in [0,1]:
            swap = Text(f"{o.swap:,.2f}")
            if o.swap < 0:
                swap.stylize("bright_red")
            else:
                swap.stylize("bright_green")

            profit = Text(f"{o.profit:,.2f}")
            if o.profit < 0:
                profit.stylize("bright_red")
            else:
                profit.stylize("bright_green")

            table.add_row(
                f"{o.ticket}",
                f"{o.symbol}",
                f"{o.size:.2f}",
                swap,
                profit
        )

    return table


def draw_pending():
    table = Table(box=box.SIMPLE, min_width=40, expand=True)
    table.add_column("Ticket")
    table.add_column("Symbol", justify="center")
    table.add_column("Type"  , justify="center")
    table.add_column("Size"  , justify="center")
    table.add_column("Price" , justify="center")
    table.add_column("Bid"   , justify="center")
    table.add_column("Ask"   , justify="right")

    for k in list(orders.keys()):
        o = orders[k]
        if o.type > 1:
            table.add_row(
                f"{o.ticket}",
                f"{o.symbol}",
                f"{ORDER_TYPES[o.type]}",
                f"{o.size:.2f}",
                f"{o.open_price:.{o.digits}f}",
                f"{o.bid:.{o.digits}f}",
                f"{o.ask:.{o.digits}f}",
        )

    return Align.center(Panel(table, title="Pending Orders"), vertical="middle")


def draw_panel(mode):
    global layout
    global profit

    style = "bright_red" if profit < 0 else "bright_green"
    footer = Text(justify="center")
    if not hide:
        footer.append(f"{balance:,.2f} | ")
        footer.append(f"{profit:,.2f} ({abs(profit / balance * 100):.2f}%)", style = style)
        footer.append(f" | {equity:,.2f}")
    else:
        footer.append("Profit: ")
        footer.append(f"${profit:,.2f}", style = style)

    match mode:
        case "positions":
            table = draw_records()
        case "orders":
            table = draw_orders()
        case "pending":
            table = draw_pending()

    #layout["upper"].update(Align.center(table, vertical="middle"))
    if mode == "pending":
        layout["upper"].update(table)
    else:
        layout["upper"].update(Align.center(table))
    layout["lower"].update(footer)

#def wait_for_key():
#    global quit
#    global mode_index
#    global layout
#    global hide
#
#    while True:
#        key = getkey()
#        match key:
#            case ' ':
#                mode_index = (mode_index + 1) % len(modes)
#            case 'h':
#                hide = not hide
#            case 'q':
#                quit = True

def wait_for_message():
    global layout
    global quit
    global mode
    global mode_index
    global stop
    global live

    subscriber.setsockopt_string(zmq.SUBSCRIBE, account)
    layout = Layout()
    layout.split_column(
            Layout(name="upper"),
            Layout(name="lower")
            )
    layout["lower"].size = 2
    layout["lower"].visible = False

    nodata = Panel(Align.center(
        Text(f"{account} - NO DATA", justify="center"),
        vertical="middle"), style="red")

    layout["upper"].update(nodata)

    last_message_time = int(time.time()) - 5
    lock = threading.Lock()

    with Live(layout, auto_refresh=False, transient=True) as live:
        with lock:
            live.update(layout, refresh=True)
        while not quit:
            socks = dict(poller.poll(100))
            if subscriber in socks and socks[subscriber] == zmq.POLLIN:
                msg = subscriber.recv()
                with lock:
                    topic, data = msg.split()
                    last_message_time = int(time.time())
                    update_records(data)
                    layout["lower"].visible = True
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            elif int(time.time()) - last_message_time > TTL:
                with lock:
                    layout["upper"].update(nodata)
                    layout["lower"].visible = False
                    live.update(layout, refresh=True)

def main():
    global layout
    global account
    global hide
    global quit
    global mode
    global stop
    global mode_index
    global live

    data_thread = threading.Thread(target=wait_for_message)

    data_thread.start()

    stop = False

    lock = threading.Lock()

    while not quit:
        key = getkey()
        match key:
            case ' ':
                with lock:
                    mode_index = (mode_index + 1) % len(modes)
                    mode = modes[mode_index]
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            case 'h':
                with lock:
                    hide = not hide
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            case 'p':
                with lock:
                    mode_index = 0
                    mode = modes[mode_index]
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            case 'o':
                with lock:
                    mode_index = 1
                    mode = modes[mode_index]
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            case 'P':
                with lock:
                    mode_index = 2
                    mode = modes[mode_index]
                    draw_panel(mode)
                    live.update(layout, refresh=True)
            case 'q':
                quit = True

        if quit:
            data_thread.join()
            sys.exit(0)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        account = "711700"
    else:
        account = sys.argv[1]
    main()

