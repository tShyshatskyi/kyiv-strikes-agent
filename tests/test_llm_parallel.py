"""Model calls are throttled so a run cannot start a dozen CLI processes."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline import llm


def test_at_most_max_parallel_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "CACHE", tmp_path)
    monkeypatch.setattr(llm, "_SLOTS", threading.BoundedSemaphore(2))
    live, peak, lock = [0], [0], threading.Lock()

    def fake(role, model, prompt, schema, timeout):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return {"data": {"p": prompt}, "cost_usd": 0, "ms": 0}

    monkeypatch.setattr(llm, "_claude", fake)
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("KSR_CACHE_ONLY", raising=False)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: llm.ask("reader", f"p{i}", {}), range(8)))
    assert peak[0] == 2
