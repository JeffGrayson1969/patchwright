from __future__ import annotations

import json

import pickle


def load_config(data):
    return json.loads(data.decode("utf-8"))
