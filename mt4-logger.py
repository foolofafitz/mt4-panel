#!/usr/bin/env -S python -u

import sys
import os
import json
import zmq
import time

ctx = zmq.Context()
subscriber = ctx.socket(zmq.SUB)
url = "tcp://localhost:5559"

poller = zmq.Poller()
poller.register(subscriber, zmq.POLLIN)

account = "711700"
subscriber.connect(url)
subscriber.setsockopt_string(zmq.SUBSCRIBE, account)

socks = dict(poller.poll(1000))
if subscriber in socks and socks[subscriber] == zmq.POLLIN:
    msg = subscriber.recv()
    topic, data = msg.split()
    j = json.loads(data)
    balance = j["balance"]
    equity = j["equity"]

print(int(time.time()), balance, equity)
