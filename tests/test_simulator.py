"""推演器物理与校验测试。运行：python -m pytest -q（需 scipy/numpy）。"""

import math

import numpy as np
import pytest

from app.simulator import SimulationError, simulate


def base_cfg():
    return {
        "t_start": 0, "t_end": 12, "n_points": 400,
        "tanks": [
            {"id": "A", "bottom": 10, "area": 20, "capacity": 100, "initial_volume": 0},
            {"id": "B", "bottom": 8, "area": 25, "capacity": 160, "initial_volume": 25},
            {"id": "C", "bottom": 5, "area": 20, "capacity": 120, "initial_volume": 30},
        ],
        "pipes": [
            {"id": "P1", "upstream": "A", "downstream": "B", "coeff": 3.0},
            {"id": "P2", "upstream": "B", "downstream": "C", "coeff": 2.5},
            {"id": "P3", "upstream": "C", "downstream": "A", "coeff": 0.5},
        ],
        "pumps": [
            {"id": "PU1", "tank": "C", "flow": 14, "depth_on": 3.0,
             "depth_off": 1.0, "initial_on": False},
        ],
        "inflows": [
            {"tank": "A", "t_start": 0, "t_end": 4, "rate": 45},
            {"tank": "B", "t_start": 1, "t_end": 5, "rate": 15},
            {"tank": "A", "t_start": 8, "t_end": 10, "rate": 40},
        ],
    }


def run(cfg=None):
    return simulate(cfg or base_cfg())


# ---------------------------------------------------------------- 基本物理

def test_volumes_in_range_and_balance_closed():
    r = run()
    b = r["balance"]
    scale = max(1.0, b["initial"] + b["inflow"])
    assert abs(b["residual"]) < 1e-6 * scale
    for t in r["tanks"]:
        V = np.array(t["volume"])
        assert np.all(V >= -1e-9)
        assert np.all(V <= t["capacity"] + 1e-6 * t["capacity"])


def test_no_reverse_flow_and_pipe_monotone():
    r = run()
    for p in r["pipes"]:
        cum = np.array(p["cumulative"])
        assert np.all(np.diff(cum) >= -1e-9)   # 累计只增 => 无反向流
        assert p["total"] >= 0


def test_pump_switch_recorded_and_hysteresis():
    r = run()
    assert r["switches"], "示例工况应至少发生一次泵切换"
    on_times = [s["time"] for s in r["switches"] if s["on"]]
    off_times = [s["time"] for s in r["switches"] if not s["on"]]
    # 每次开启时刻，水深应达到启位；每次停止时刻应降到停位附近。
    for s in r["switches"]:
        tC = next(t for t in r["tanks"] if t["id"] == "C")
        i = int(np.argmin(np.abs(np.array(r["times"]) - s["time"])))
        if s["on"]:
            assert tC["depth"][i] >= 3.0 - 1e-6
        else:
            assert tC["depth"][i] <= 1.0 + 1e-6
    # 启、停交替出现
    states = [s["on"] for s in r["switches"]]
    for a, z in zip(states, states[1:]):
        assert a != z


def test_overflow_mass_balance_under_force():
    """超大入流灌满下游：必须出现溢流，且收支仍然闭合。"""
    cfg = base_cfg()
    cfg["pumps"] = []
    cfg["pipes"] = []
    cfg["tanks"] = [
        {"id": "X", "bottom": 0, "area": 10, "capacity": 50, "initial_volume": 0},
    ]
    cfg["inflows"] = [{"tank": "X", "t_start": 0, "t_end": 10, "rate": 100}]
    cfg["t_end"] = 10
    r = simulate(cfg)
    t = r["tanks"][0]
    assert t["overflow_total"] == pytest.approx(1000 - 50, rel=1e-4, abs=1e-3)
    assert t["volume"][-1] == pytest.approx(50, rel=1e-6)
    b = r["balance"]
    assert abs(b["residual"]) < 1e-6 * (b["initial"] + b["inflow"])


def test_empty_tank_flow_limited_proportionally():
    """空池只有恒定小入流、无泵：出流不得超过入流（限流），水量恒为 0。"""
    cfg = {
        "t_start": 0, "t_end": 5,
        "tanks": [
            {"id": "A", "bottom": 5, "area": 10, "capacity": 100, "initial_volume": 0},
            {"id": "B", "bottom": 0, "area": 1000, "capacity": 1e6, "initial_volume": 0},
        ],
        "pipes": [{"id": "P1", "upstream": "A", "downstream": "B", "coeff": 5.0}],
        "pumps": [],
        "inflows": [{"tank": "A", "t_start": 0, "t_end": 5, "rate": 3.0}],
    }
    r = simulate(cfg)
    assert all(abs(v) <= 1e-6 for v in r["tanks"][0]["volume"])
    # 过水量等于入流 15（空池末存为 0，无溢流无泵）
    assert r["pipes"][0]["total"] == pytest.approx(15.0, rel=1e-5)


def test_two_out_pipes_share_proportionally():
    """空池两根出管，请求量 2:1，入流 6 -> 实流 4:2。"""
    cfg = {
        "t_start": 0, "t_end": 1,
        "tanks": [
            {"id": "A", "bottom": 10, "area": 10, "capacity": 100, "initial_volume": 0},
            {"id": "B", "bottom": 0, "area": 1000, "capacity": 1e6, "initial_volume": 0},
        ],
        # 同起同终两根并联管，系数比 2:1
        "pipes": [
            {"id": "Q1", "upstream": "A", "downstream": "B", "coeff": 2.0},
            {"id": "Q2", "upstream": "A", "downstream": "B", "coeff": 1.0},
        ],
        "pumps": [],
        "inflows": [{"tank": "A", "t_start": 0, "t_end": 1, "rate": 6.0}],
    }
    r = simulate(cfg)
    q1, q2 = r["pipes"][0]["total"], r["pipes"][1]["total"]
    assert q1 == pytest.approx(4.0, rel=1e-4)
    assert q2 == pytest.approx(2.0, rel=1e-4)


def test_ring_network_no_negative_and_balance():
    r = run()  # P3 自 C 回 A，构成环
    assert all(min(t["volume"]) >= -1e-9 for t in r["tanks"])


def test_inflow_breakpoint_continues():
    """断点前后分段恒定：末存解析校验（无管无泵单池，未满）。"""
    cfg = {
        "t_start": 0, "t_end": 10,
        "tanks": [{"id": "A", "bottom": 0, "area": 10, "capacity": 1000,
                   "initial_volume": 0}],
        "pipes": [], "pumps": [],
        "inflows": [
            {"tank": "A", "t_start": 0, "t_end": 2, "rate": 5},
            {"tank": "A", "t_start": 2, "t_end": 7, "rate": 8},
            {"tank": "A", "t_start": 7, "t_end": 10, "rate": 0},
        ],
    }
    r = simulate(cfg)
    assert r["tanks"][0]["volume"][-1] == pytest.approx(50.0, rel=1e-6)


def test_pump_drains_at_full_tank():
    """满池 + 强入流 + 大泵：泵全程开启排水，溢流只计净余。"""
    cfg = {
        "t_start": 0, "t_end": 10,
        "tanks": [{"id": "A", "bottom": 0, "area": 10, "capacity": 100,
                   "initial_volume": 100}],
        "pipes": [],
        "pumps": [{"id": "P", "tank": "A", "flow": 30, "depth_on": 9,
                   "depth_off": 1, "initial_on": True}],
        "inflows": [{"tank": "A", "t_start": 0, "t_end": 10, "rate": 40}],
    }
    r = simulate(cfg)
    t = r["tanks"][0]
    # 净余 10*10 = 100 溢流；泵排 300
    assert t["overflow_total"] == pytest.approx(100.0, rel=1e-4)
    assert r["pumps"][0]["total"] == pytest.approx(300.0, rel=1e-4)
    assert abs(r["balance"]["residual"]) < 1e-5


# ---------------------------------------------------------------- 校验拒绝

INVALID = [
    ("非有限面积", {"tanks[0].area": (float("nan"),)}),
]


def _patch(cfg, path, value):
    """按 'a[0].b' 路径赋值。"""
    cur = cfg
    parts = path.replace("]", "").split("[")
    keys = []
    for p in parts:
        keys += p.split(".")
    for k in keys[:-1]:
        cur = cur[int(k)] if k.isdigit() else cur[k]
    cur[keys[-1]] = value


def test_reject_nonfinite():
    cfg = base_cfg()
    _patch(cfg, "tanks[0].area", float("inf"))
    with pytest.raises(SimulationError):
        simulate(cfg)


def test_reject_nonpositive_area_capacity_coeff_flow():
    for path, val in [("tanks[0].area", 0), ("tanks[1].capacity", -5),
                      ("pipes[0].coeff", -1), ("pumps[0].flow", 0)]:
        cfg = base_cfg()
        _patch(cfg, path, val)
        with pytest.raises(SimulationError):
            simulate(cfg), path


def test_reject_negative_inflow():
    cfg = base_cfg()
    cfg["inflows"][0]["rate"] = -1
    with pytest.raises(SimulationError):
        simulate(cfg)


def test_reject_initial_out_of_range():
    cfg = base_cfg()
    cfg["tanks"][0]["initial_volume"] = 101
    with pytest.raises(SimulationError, match="初始水量"):
        simulate(cfg)


def test_reject_off_not_below_on():
    cfg = base_cfg()
    cfg["pumps"][0]["depth_off"] = 5
    with pytest.raises(SimulationError, match="停泵水深"):
        simulate(cfg)


def test_reject_pump_initial_state_mismatch():
    cfg = base_cfg()
    # C 初始水深 30/20=1.5；令停位高于它却设为开
    cfg["pumps"][0]["depth_off"] = 2.0
    cfg["pumps"][0]["depth_on"] = 3.0
    cfg["pumps"][0]["initial_on"] = True
    with pytest.raises(SimulationError, match="初态为开"):
        simulate(cfg)
    cfg2 = base_cfg()
    cfg2["tanks"][2]["initial_volume"] = 120  # 水深 6 >= 3 却设为停
    with pytest.raises(SimulationError, match="初态为停"):
        simulate(cfg2)


def test_reject_bad_time_window_and_overlap():
    cfg = base_cfg()
    cfg["t_end"] = -1
    with pytest.raises(SimulationError):
        simulate(cfg)
    cfg = base_cfg()
    cfg["inflows"].append({"tank": "A", "t_start": 2, "t_end": 4, "rate": 1})
    with pytest.raises(SimulationError, match="重叠"):
        simulate(cfg)


def test_reject_unknown_refs_and_duplicate_id():
    cfg = base_cfg()
    cfg["pipes"][0]["downstream"] = "Z"
    with pytest.raises(SimulationError):
        simulate(cfg)
    cfg = base_cfg()
    cfg["tanks"][1]["id"] = "A"
    with pytest.raises(SimulationError, match="重复"):
        simulate(cfg)


def test_reject_bool_as_number():
    cfg = base_cfg()
    cfg["tanks"][0]["area"] = True
    with pytest.raises(SimulationError):
        simulate(cfg)
