"""Tests for the load-balance coordinator: strategies + circuit breaker."""
from app.models_layer.loadbalance import FAIL_THRESHOLD, LBCoordinator, weighted_shuffle
from app.models_layer.router import ResolvedAlias, ResolvedTarget, _attempt_order, _strategy_of


class Tg:
    """Minimal load-balance target stand-in."""
    def __init__(self, label, weight=1):
        self.label = label
        self.weight = weight


KEY = lambda t: t.label          # noqa: E731
WT = lambda t: t.weight          # noqa: E731


# ---------- weighted ----------

def test_weighted_shuffle_respects_weight_distribution():
    pool = [Tg("A", 3), Tg("B", 1)]
    first_counts = {"A": 0, "B": 0}
    for _ in range(4000):
        first_counts[weighted_shuffle(pool, WT)[0].label] += 1
    ratio = first_counts["A"] / first_counts["B"]
    assert 2.3 < ratio < 3.9        # ~3:1 with sampling slack
    # always a full permutation, no drops/dupes
    assert {t.label for t in weighted_shuffle(pool, WT)} == {"A", "B"}


# ---------- round robin ----------

def test_round_robin_cycles():
    lb = LBCoordinator()
    pool = [Tg("A"), Tg("B"), Tg("C")]
    firsts = [lb.order("g", pool, "round_robin", key_of=KEY, weight_of=WT)[0].label
              for _ in range(6)]
    assert firsts == ["A", "B", "C", "A", "B", "C"]


# ---------- least connections ----------

def test_least_conn_prefers_idle_target():
    lb = LBCoordinator()
    pool = [Tg("A"), Tg("B"), Tg("C")]
    lb.acquire("A"); lb.acquire("A")   # A has 2 in-flight
    lb.acquire("B")                    # B has 1
    order = [t.label for t in lb.order("g", pool, "least_conn", key_of=KEY, weight_of=WT)]
    assert order == ["C", "B", "A"]    # ascending in-flight
    lb.release("A"); lb.release("A"); lb.release("B")


# ---------- circuit breaker ----------

def test_circuit_opens_after_threshold_and_moves_target_back():
    lb = LBCoordinator()
    pool = [Tg("A"), Tg("B")]
    for _ in range(FAIL_THRESHOLD):
        lb.record_failure("A")
    assert lb.is_open("A") is True
    # round_robin would normally start at A, but open circuit pushes it last
    order = [t.label for t in lb.order("g", pool, "round_robin", key_of=KEY, weight_of=WT)]
    assert order[-1] == "A"
    assert order[0] == "B"


def test_success_resets_circuit():
    lb = LBCoordinator()
    for _ in range(FAIL_THRESHOLD):
        lb.record_failure("A")
    assert lb.is_open("A")
    lb.record_success("A")
    assert lb.is_open("A") is False


def test_partial_failures_below_threshold_keep_closed():
    lb = LBCoordinator()
    for _ in range(FAIL_THRESHOLD - 1):
        lb.record_failure("A")
    assert lb.is_open("A") is False


# ---------- router integration / backward compat ----------

def _rt(label, group="", weight=1):
    return ResolvedTarget(kind="openai_compat", base_url="http://x", api_key="",
                          model="m", label=label, weight=weight, lb_group=group)


def test_attempt_order_preserves_fallback_chain_without_groups():
    # No lb_group -> pure fallback, order preserved (backward compatible).
    targets = [_rt("P"), _rt("F1"), _rt("F2")]
    order = [t.label for t in _attempt_order(targets, "weighted")]
    assert order == ["P", "F1", "F2"]


def test_attempt_order_groups_then_fallback():
    # Two targets share group "g" (a pool), then a solo fallback after.
    targets = [_rt("A", "g", 1), _rt("B", "g", 1), _rt("FB")]
    order = [t.label for t in _attempt_order(targets, "round_robin")]
    assert set(order[:2]) == {"A", "B"}   # the pool comes first (any order)
    assert order[2] == "FB"               # fallback stays last


def test_strategy_of_defaults_and_validates():
    assert _strategy_of(ResolvedAlias(alias="x", targets=[])) == "weighted"
    assert _strategy_of(ResolvedAlias(alias="x", targets=[], params={"lb_strategy": "round_robin"})) == "round_robin"
    assert _strategy_of(ResolvedAlias(alias="x", targets=[], params={"lb_strategy": "least_conn"})) == "least_conn"
    assert _strategy_of(ResolvedAlias(alias="x", targets=[], params={"lb_strategy": "least_vram"})) == "least_vram"
    assert _strategy_of(ResolvedAlias(alias="x", targets=[], params={"lb_strategy": "bogus"})) == "weighted"


def test_least_vram_orders_by_gpu_memory():
    co = LBCoordinator()
    mem = {"A": 80.0, "B": 20.0, "C": 50.0}  # B's GPU is least loaded
    order = co.order("g", ["A", "B", "C"], "least_vram",
                     key_of=lambda t: t, weight_of=lambda t: 1,
                     gpu_mem_of=lambda t: mem[t])
    assert order == ["B", "C", "A"]  # ascending VRAM usage


def test_least_vram_without_mem_falls_back():
    co = LBCoordinator()
    order = co.order("g", ["A", "B"], "least_vram",
                     key_of=lambda t: t, weight_of=lambda t: 1)
    assert set(order) == {"A", "B"}  # no gpu_mem_of -> weighted behaviour


def test_attempt_order_pin_gpu():
    from app.models_layer.router import ResolvedTarget, _attempt_order

    def rt(label, gpu):
        return ResolvedTarget(kind="x", base_url="", api_key="", model="m", label=label, gpu_index=gpu)

    ts = [rt("A", "1"), rt("B", "0"), rt("C", "0")]  # solo targets, order preserved
    order = [t.label for t in _attempt_order(ts, "weighted", pin_gpu="0")]
    assert order == ["B", "C", "A"]   # gpu0 targets first, gpu1 last
    # no pin -> original order
    assert [t.label for t in _attempt_order(ts, "weighted")] == ["A", "B", "C"]
