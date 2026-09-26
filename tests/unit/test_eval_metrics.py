import importlib.util
from pathlib import Path

MODULE = Path(__file__).parents[2] / "scripts" / "evaluate_retrieval.py"
spec = importlib.util.spec_from_file_location("evalmod", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_metrics_perfect():
    result = mod.metrics([["d::s"]], [{"d::s"}], ks=(5,))
    assert result["recall@5"] == 1.0
    assert result["hitrate@5"] == 1.0
    assert result["mrr"] == 1.0
