"""HTTP 层测试：页面可达、合法计算 200、非法输入 422 且不产生结果。"""

import pytest

httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _cfg():
    return {
        "t_start": 0, "t_end": 5,
        "tanks": [
            {"id": "A", "bottom": 5, "area": 10, "capacity": 100, "initial_volume": 0},
            {"id": "B", "bottom": 0, "area": 1000, "capacity": 1000000,
             "initial_volume": 0},
        ],
        "pipes": [{"id": "P1", "upstream": "A", "downstream": "B", "coeff": 5}],
        "pumps": [],
        "inflows": [{"tank": "A", "t_start": 0, "t_end": 5, "rate": 3}],
    }


def test_index_and_static_served():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_valid_simulation_200():
    r = client.post("/api/simulate", json=_cfg())
    assert r.status_code == 200
    data = r.json()
    assert "tanks" in data and "balance" in data and "switches" in data
    assert abs(data["balance"]["residual"]) < 1e-6


def test_invalid_nonfinite_422():
    cfg = _cfg()
    cfg["tanks"][0]["area"] = "NaN-PLACEHOLDER"
    body = (
        __import__("json").dumps(cfg)
        .replace('"NaN-PLACEHOLDER"', "NaN")
    )
    r = client.post("/api/simulate", content=body,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "有限" in r.json()["detail"]


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c["tanks"][0].__setitem__("area", 0), "截面积"),
    (lambda c: c["pipes"][0].__setitem__("coeff", -2), "系数"),
    (lambda c: c["tanks"][0].__setitem__("initial_volume", 200), "初始水量"),
    (lambda c: c["inflows"][0].__setitem__("rate", -1), "不得为负"),
])
def test_invalid_inputs_422(mutate, msg):
    cfg = _cfg()
    mutate(cfg)
    r = client.post("/api/simulate", json=cfg)
    assert r.status_code == 422
    assert msg in r.json()["detail"]


def test_pump_threshold_and_initial_state_rejected():
    cfg = _cfg()
    cfg["pumps"] = [{"id": "p", "tank": "A", "flow": 1,
                     "depth_on": 2, "depth_off": 2, "initial_on": False}]
    r = client.post("/api/simulate", json=cfg)
    assert r.status_code == 422 and "停泵水深" in r.json()["detail"]


def test_stateless_failure_keeps_no_result():
    # 服务端无会话状态：失败请求后再次合法请求仍正常，且失败本身不返回结果体。
    bad = _cfg()
    bad["tanks"][0]["capacity"] = -1
    r1 = client.post("/api/simulate", json=bad)
    assert r1.status_code == 422
    assert "tanks" not in r1.json()
    r2 = client.post("/api/simulate", json=_cfg())
    assert r2.status_code == 200 and "balance" in r2.json()
