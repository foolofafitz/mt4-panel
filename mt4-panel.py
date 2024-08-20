#!/usr/bin/env -S python -u

import sys
import os
import json
import time
import zmq
import threading
from getch import getch

from dataclasses import dataclass

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

SHOWFILE = os.path.expanduser('~/.show-profit')

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
symbols = {}
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


class Symbol:
    """Class to sum multiple orders of the same symbol"""
    def __init__(self, name, bid, ask, digits, atr):
        self.name = name
        self.bid = bid
        self.ask = ask
        self.digits = digits
        self.atr = atr
        self.orders = {}

    def add_order(self, order) -> None:
        self.orders[order.ticket] = order

    def remove_order(self, ticket) -> None:
        if ticket in self.orders:
            del self.orders[ticket]

    def swap(self) -> float:
        p = 0
        for k in self.orders.keys():
            o = orders[k]
            p += o.swap
        return p

    def profit(self) -> float:
        p = 0
        for k in self.orders.keys():
            o = orders[k]
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
        for k in self.orders.keys():
            o = orders[k]
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
        for k in self.orders.keys():
            o = self.orders[k]
            if o.type in [OP_BUY, OP_SELL]:
                return True
        return False

    def total(self) -> float:
        return self.profit() + self.swap()

    def get_row(self):
        return (self.name, self.position, self.swap, self.profit, self.swap() + self.profit())

    def __str__(self):
        return f"{self.name:6} {len(self.orders):4d} {self.position():>14} {self.profit():12,.2f}"


def update_symbols(msg):
    global balance
    global profit 
    global equity 
    global orders
    global symbols

    j = json.loads(msg)

    # Update account info
    balance = j["balance"]
    profit = j["profit"]
    equity = j["equity"]

    # Current time as int
    t = int(time.time())

    # Get symbol name
    name = j["symbol"]["name"]

    # Create tickets/orders
    for order in j["orders"]:
        ticket = order["ticket"]
        try:
            # Get order object by ticket
            o = orders[ticket]

            # Open price might change for pending orders
            o.open_price = order["open_price"]

            # Order type will change when pending order is activated
            o.type = order["type"]

            # Update profit and swap for open orders
            o.profit = order["profit"]
            o.swap = order["swap"]

            # Update timestamp
            o.timestamp = t

        except KeyError:
            # Looks like a new order
            order["timestamp"] = t
            o = Order(**order)
            orders[ticket] = o

        if name in symbols:
            # Add order to existing symbol object
            if not o.ticket in symbols[name].orders:
                symbols[name].add_order(o)
        else:
            # Create new symbol object
            symbols[name] = Symbol(
                o.symbol,
                j["symbol"]["bid"],
                j["symbol"]["ask"],
                j["symbol"]["digits"],
                j["symbol"]["atr"],
                )

            # Add order to new symbol object
            symbols[name].add_order(o)

        # Update bid, ask and atr
        symbols[name].bid = j["symbol"]["bid"]
        symbols[name].ask = j["symbol"]["ask"]
        symbols[name].atr = j["symbol"]["atr"]

    # Cleanup
    delete_old_orders()


def ctf(num: float) -> Text:
    """Convert float to colored text"""
    if num < 0:
        return Text(f"{num:,.2f}", style="bright_red")
    else:
        return Text(f"{num:,.2f}", style="bright_green")

def draw_symbols():
    global symbols
    global orders

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

    lock = threading.Lock()
    with lock:
        for key in sorted(symbols):
            r = symbols[key]
            if not r.has_open_orders():
                continue
            if r.total() < 0:
                style = "bright_red"
            else:
                style = "bright_green"

            table.add_row(r.name, r.position(), ctf(r.profit()), ctf(r.swap()),
                          Text(f"{r.total():,.2f}", style=style))

    #return Panel(Align.center(table, vertical="middle"), title="Open Positions")
    return table

def draw_orders():
    global symbols
    global orders
    table = Table(box=box.SIMPLE, min_width=40, expand=True)

    table.add_column("Symbol")
    table.add_column("Ticket", justify="center")
    table.add_column("Type", justify="center")
    table.add_column("Size", justify="center")
    table.add_column("Swap", justify="right")
    table.add_column("Profit", justify="right")

    #table.add_section()
    lock = threading.Lock()
    with lock:
        for key in list(sorted(symbols.keys())):
            for k in list(sorted(symbols[key].orders.keys())):
                o = orders[k]
                if o.type in [0,1,2,3,4,5]:
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

                    if o.type < 2 or o.type > 1 and not hide_pending:
                        table.add_row(
                            f"{o.symbol}",
                            f"{o.ticket}",
                            f"{ORDER_TYPES[o.type]}",
                            f"{o.size:.2f}",
                            swap,
                            profit
                            )
            #table.add_section()
        #layout["debug"].update(Text(f"{t - o.timestamp}"))
        live.update(layout, refresh=True)

    #return table
    if hide_pending:
        #return Align.center(Panel(table, title="Open Orders"), vertical="middle")
        return table
    else:
        #return Align.center(Panel(table, title="All Orders"), vertical="middle")
        return table


def draw_pending():
    global symbols
    global orders

    table = Table(box=box.SIMPLE, min_width=40, expand=True)
    table.add_column("Symbol")
    table.add_column("Ticket", justify="center")
    table.add_column("Type"  , justify="center")
    table.add_column("Size"  , justify="center")
    table.add_column("Price" , justify="center")
    table.add_column("Bid"   , justify="center")
    table.add_column("Ask"   , justify="right")

    lock = threading.Lock()
    with lock:
        for key in list(sorted(symbols.keys())):
            s = symbols[key]
            bid = s.bid
            ask = s.ask
            digits = s.digits
            for k in list(sorted(symbols[key].orders.keys())):
                o = s.orders[k]
                if o.type < 2:
                    continue
                match ORDER_TYPES[o.type]:
                    case "BUY LIMIT":
                        if s.ask - o.open_price < s.atr:
                            style = "white on dark_green"
                        else:
                            style = "white"
                    case "SELL LIMIT":
                        if o.open_price - s.bid < s.atr:
                            style = "white on dark_green"
                        else:
                            style = "white"
                table.add_row(
                    f"{s.name}",
                    f"{o.ticket}",
                    f"{ORDER_TYPES[o.type]}",
                    f"{o.size:.2f}",
                    f"{o.open_price:.{digits}f}",
                    f"{bid:.{digits}f}",
                    f"{ask:.{digits}f}",
                    style=style
                    )

    #return Align.center(Panel(table, title="Pending Orders"), vertical="middle")
    return table


def draw_panel(mode):
    global layout
    global profit
    global orders
    global symbols

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
            table = draw_symbols()
        case "orders":
            table = draw_orders()
        case "pending":
            table = draw_pending()
        case _:
            table = draw_symbols()

    #layout["upper"].update(Align.center(table, vertical="middle"))
    #if mode == "pending":
    #    layout["upper"].update(table)
    #else:
    layout["upper"].update(Align.center(table))
    layout["lower"].update(footer)

def delete_old_orders():
    global orders
    global symbols
    global layout
    global live


    t = int(time.time())
    lock = threading.Lock()

    with lock:
        for key in list(sorted(symbols.keys())):
            for k in list(sorted(symbols[key].orders.keys())):
                o = symbols[key].orders[k]
                #layout["debug"].update(Text(f"{t - o.timestamp}"))

                if t - o.timestamp > TTL:
                    symbols[o.symbol].remove_order(o.ticket)
                    if len(symbols[o.symbol].orders) == 0:
                        del symbols[o.symbol]
                    del orders[k]
                    del o


def wait_for_message():
    global layout
    global quit
    global mode
    global mode_index
    global live
    global orders
    global symbols
    global last_message_time

    subscriber.setsockopt_string(zmq.SUBSCRIBE, account)
    layout = Layout()
    layout.split_column(
            #Layout(name="debug"),
            Layout(name="upper"),
            Layout(name="lower")
            )
    layout["lower"].size = 2
    layout["lower"].visible = False

    nodata = Panel(Align.center(
        Text(f"{account} - NO DATA", justify="center"),
        vertical="middle"), style="white")

    layout["upper"].update(nodata)

    last_message_time = int(time.time()) - 5
    lock = threading.Lock()

    with Live(layout, auto_refresh=False, transient=True) as live:
        with lock:
            live.update(layout, refresh=True)
        while not quit:
            socks = dict(poller.poll(1000))
            if subscriber in socks and socks[subscriber] == zmq.POLLIN:
                msg = subscriber.recv()
                with lock:
                    _, data = msg.split()
                    last_message_time = int(time.time())
                    update_symbols(data)
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
    global mode_index
    global live
    global orders
    global hide_pending

    hide_pending = False
    data_thread = threading.Thread(target=wait_for_message)
    data_thread.start()
    lock = threading.Lock()

    while not quit:
        #key = getkey(False)
        #key = getch()
        key = chr(getch())
        if key:
            t = int(time.time()) - last_message_time
            if t < TTL:
                match key:
                    case ' ':
                        with lock:
                            #mode_index = (mode_index + 1) % len(modes)
                            if mode_index > 0:
                                mode_index = 0
                            else:
                                mode_index = 1
                            mode = modes[mode_index]
                            draw_panel(mode)
                            live.update(layout, refresh=True)
                    case 'H':
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
                    case 'h':
                        with lock:
                            if mode == "orders":
                                hide_pending = not hide_pending
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
            elif key == 'q':
                quit = True

        if quit:
            data_thread.join()
            #sys.exit(0)

if __name__ == '__main__':
    #libname = pathlib.Path().absolute() / "libgetch.so"
    #c_lib = ctypes.CDLL("/usr/local/lib/libgetch.so")

    if len(sys.argv) < 2:
        account = "711700"
    else:
        account = sys.argv[1]
    main()

