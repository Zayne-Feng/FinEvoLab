"""Share overlapping computations by an exact, caller-scoped identity."""

from concurrent.futures import Future
from copy import deepcopy
from threading import Lock


class SingleFlight:
    def __init__(self):
        self._lock = Lock()
        self._pending = {}

    def run(self, key, compute, timeout=None):
        with self._lock:
            shared = key in self._pending
            future = self._pending.get(key)
            if not shared:
                future = Future()
                self._pending[key] = future
        if shared:
            return deepcopy(future.result(timeout=timeout)), True
        try:
            result = compute()
            future.set_result(deepcopy(result))
            return result, False
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                if self._pending.get(key) is future:
                    del self._pending[key]


SINGLEFLIGHT = SingleFlight()
