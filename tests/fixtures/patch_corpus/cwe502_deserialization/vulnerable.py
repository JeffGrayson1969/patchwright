from __future__ import annotations

import pickle


def load_config(data):
    return pickle.loads(data)
