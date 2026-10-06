"""雨水调蓄池连续时间推演核心。

模型：
  水头 H_i = bottom_i + V_i / area_i
  管请求流量 q_k = coeff_k * sqrt(max(H_up - H_down, 0))   （单向、可成环）
  泵：水位升至启位 depth_on 开启，降至停位 depth_off 关闭；开启时以恒定流量排出池外
  入流：分段恒定、全程非负；未覆盖区间视为零

推演为连续时间 ODE（事件驱动）：入流断点、泵切换、空/满边界均为事件。
  * 空池（V=0）且自产不足时，按各出管请求量比例限流（α 固定点迭代）；
  * 满池（V=容量）时，泵仍可排出，净余水量计为溢流；
  * 不允许负水量与反向流；
  * 同一时刻：先更新入流与泵，再结算边界流量。

输出轨迹按分段连续解重建：每个积分段保留 solve_ivp 的稠密解，
输出时刻（均匀网格 + 泵切换时刻 + 起终点）归入所在积分段取值；
与断点/切换同刻的采样点取同刻结算（先更新入流与泵、再结算边界）
后的状态，不做跨段直线插值。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np
from scipy.integrate import solve_ivp


class SimulationError(ValueError):
    """输入非法或推演失败。"""


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _num(value, name, *, positive=False, nonneg=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationError(f"{name} 必须是数值")
    x = float(value)
    if not math.isfinite(x):
        raise SimulationError(f"{name} 必须为有限数值")
    if positive and x <= 0.0:
        raise SimulationError(f"{name} 必须为正数")
    if nonneg and x < 0.0:
        raise SimulationError(f"{name} 不得为负")
    return x


def _as_list(value, name):
    if not isinstance(value, list):
        raise SimulationError(f"{name} 必须是数组")
    return value


def validate(cfg: dict) -> dict:
    """校验并规整用户配置；任何非法项直接抛 SimulationError。"""
    if not isinstance(cfg, dict):
        raise SimulationError("请求体必须是 JSON 对象")

    t0 = _num(cfg.get("t_start"), "时段起点 t_start")
    t1 = _num(cfg.get("t_end"), "时段终点 t_end")
    if t1 <= t0:
        raise SimulationError("时段终点 t_end 必须严格大于起点 t_start")

    n_points = int(_num(cfg.get("n_points", 200), "采样点数 n_points",
                        positive=True))
    if not (2 <= n_points <= 10000):
        raise SimulationError("采样点数需在 2 ~ 10000 之间")

    # ---- 池 ----
    raw_tanks = _as_list(cfg.get("tanks"), "tanks")
    if not raw_tanks:
        raise SimulationError("至少需要一个调蓄池")
    tanks = []
    seen = set()
    for i, tk in enumerate(raw_tanks):
        if not isinstance(tk, dict):
            raise SimulationError(f"池 #{i + 1} 配置必须是对象")
        tid = str(tk.get("id", "")).strip()
        if not tid:
            raise SimulationError(f"池 #{i + 1} 缺少 id")
        if tid in seen:
            raise SimulationError(f"池 id 重复: {tid}")
        seen.add(tid)
        area = _num(tk.get("area"), f"池 {tid} 截面积", positive=True)
        capacity = _num(tk.get("capacity"), f"池 {tid} 容量", positive=True)
        initial = _num(tk.get("initial_volume"), f"池 {tid} 初始水量", nonneg=True)
        if initial > capacity:
            raise SimulationError(f"池 {tid} 初始水量超过容量")
        tanks.append(dict(id=tid,
                          bottom=_num(tk.get("bottom"), f"池 {tid} 底高程"),
                          area=area, capacity=capacity, initial=initial))

    index = {t["id"]: i for i, t in enumerate(tanks)}

    # ---- 入流（分段恒定、非负、同池段不重叠；未覆盖区间为零）----
    inflows = {i: [] for i in range(len(tanks))}
    raw_inflows = cfg.get("inflows", [])
    if not isinstance(raw_inflows, list):
        raise SimulationError("inflows 必须是数组")
    for i, seg in enumerate(raw_inflows):
        if not isinstance(seg, dict):
            raise SimulationError(f"入流段 #{i + 1} 必须是对象")
        tid = str(seg.get("tank", "")).strip()
        if tid not in index:
            raise SimulationError(f"入流段 #{i + 1} 引用了不存在的池: {tid}")
        a = _num(seg.get("t_start"), f"入流段 #{i + 1} 起点")
        b = _num(seg.get("t_end"), f"入流段 #{i + 1} 终点")
        q = _num(seg.get("rate"), f"入流段 #{i + 1} 流量", nonneg=True)
        if b <= a:
            raise SimulationError(f"入流段 #{i + 1}（池 {tid}）终点必须大于起点")
        inflows[index[tid]].append((a, b, q))
    for i, segs in inflows.items():
        segs.sort()
        for s1, s2 in zip(segs, segs[1:]):
            tol = 1e-12 * max(1.0, abs(s1[1]), abs(s2[0]))
            if s2[0] < s1[1] - tol:
                raise SimulationError(f"池 {tanks[i]['id']} 的入流段存在重叠")

    # ---- 重力管（单向，可成环）----
    raw_pipes = _as_list(cfg.get("pipes"), "pipes")
    pipes = []
    for i, pp in enumerate(raw_pipes):
        if not isinstance(pp, dict):
            raise SimulationError(f"管道 #{i + 1} 配置必须是对象")
        up = str(pp.get("upstream", "")).strip()
        dn = str(pp.get("downstream", "")).strip()
        if up not in index:
            raise SimulationError(f"管道 #{i + 1} 起点池不存在: {up}")
        if dn not in index:
            raise SimulationError(f"管道 #{i + 1} 终点池不存在: {dn}")
        pipes.append(dict(
            id=str(pp.get("id", f"P{i + 1}")).strip() or f"P{i + 1}",
            up=index[up], dn=index[dn],
            coeff=_num(pp.get("coeff"), f"管道 #{i + 1} 系数", positive=True),
            up_name=up, dn_name=dn))

    # ---- 排外泵 ----
    raw_pumps = _as_list(cfg.get("pumps"), "pumps")
    pumps = []
    for i, pm in enumerate(raw_pumps):
        if not isinstance(pm, dict):
            raise SimulationError(f"泵 #{i + 1} 配置必须是对象")
        tid = str(pm.get("tank", "")).strip()
        if tid not in index:
            raise SimulationError(f"泵 #{i + 1} 所属池不存在: {tid}")
        depth_on = _num(pm.get("depth_on"), f"泵 #{i + 1} 启泵水深", nonneg=True)
        depth_off = _num(pm.get("depth_off"), f"泵 #{i + 1} 停泵水深", nonneg=True)
        if depth_off >= depth_on:
            raise SimulationError(f"泵 #{i + 1} 停泵水深必须严格低于启泵水深")
        on0 = pm.get("initial_on")
        if not isinstance(on0, bool):
            raise SimulationError(f"泵 #{i + 1} 初态必须为布尔值 true/false")
        ti = index[tid]
        depth0 = tanks[ti]["initial"] / tanks[ti]["area"]
        tol = 1e-12 * max(1.0, depth_off, depth_on)
        # 初态与阈值不符即拒绝：低于停位必须停；达到启位必须开；滞回区内自定。
        if on0 and depth0 < depth_off - tol:
            raise SimulationError(f"泵 #{i + 1} 初态为开，但初始水深低于停泵水深")
        if (not on0) and depth0 > depth_on + tol:
            raise SimulationError(f"泵 #{i + 1} 初态为停，但初始水深已达到启泵水深")
        pumps.append(dict(
            id=str(pm.get("id", f"PU{i + 1}")).strip() or f"PU{i + 1}",
            tank=ti, tank_name=tid,
            flow=_num(pm.get("flow"), f"泵 #{i + 1} 流量", positive=True),
            depth_on=depth_on, depth_off=depth_off, on=on0))

    return dict(t0=t0, t1=t1, n_points=n_points, tanks=tanks,
                inflows=inflows, pipes=pipes, pumps=pumps)


# ---------------------------------------------------------------------------
# 推演
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    c: dict
    n: int
    np_: int
    npu: int
    bottom: np.ndarray
    area: np.ndarray
    cap: np.ndarray
    starts: list                       # 各池入流段起点（bisect 用）
    pinned_empty: set = field(default_factory=set)
    pinned_full: set = field(default_factory=set)
    switch_log: list = field(default_factory=list)

    def inflow(self, t: float) -> np.ndarray:
        """t 时刻各池外部入流；区间 [a,b)，断点右连续取新值。"""
        rate = np.zeros(self.n)
        for i in range(self.n):
            segs = self.c["inflows"][i]
            j = bisect_right(self.starts[i], t) - 1
            if j >= 0 and t < segs[j][1]:
                rate[i] = segs[j][2]
        return rate

    def head(self, V):
        return self.bottom + V / self.area

    def pipe_base(self, H):
        """管请求流量 coeff*sqrt(正水头差)，负差/零差按零计；无反向流。"""
        base = np.zeros(self.np_)
        for k, pk in enumerate(self.c["pipes"]):
            d = H[pk["up"]] - H[pk["dn"]]
            if d > 0.0:
                base[k] = pk["coeff"] * math.sqrt(d)
        return base

    def flows(self, V, rate):
        """返回 (base 请求流量, q 实际管流量)。

        任何源头池体积为 0（钉住或积分恰在边界）且自产（外入流 +
        上游来管）不足以满足全部出流请求时，其各出管按请求量等比例
        缩减 α；停泵时泵不取水（校验保证空池泵必停）。
        Gauss-Seidel 从 α=1 单调下求最大可行固定点（支持成环）。
        """
        pipes = self.c["pipes"]
        pumps = self.c["pumps"]
        base = self.pipe_base(self.head(V))
        alpha = np.ones(self.np_)

        out_pipes = [[] for _ in range(self.n)]
        for k, pk in enumerate(pipes):
            out_pipes[pk["up"]].append(k)
        pump_demand = [0.0] * self.n
        for pu in pumps:
            if pu["on"]:
                pump_demand[pu["tank"]] += pu["flow"]

        # 源头“干”池：仅对已钉在空位的池限流。自由池即使体积瞬时为 0
        # （事件点/释放瞬间）也按全额请求输水，由其正向净流入抬升水位。
        dry = sorted(self.pinned_empty)

        for _ in range(10000):
            worst = 0.0
            for i in sorted(dry):
                supply = rate[i]
                for k, pk in enumerate(pipes):
                    if pk["dn"] == i:
                        supply += alpha[k] * base[k]
                demand = pump_demand[i]
                for k in out_pipes[i]:
                    demand += base[k]
                ai = 1.0 if demand <= supply + 1e-14 else min(1.0, supply / demand)
                for k in out_pipes[i]:
                    worst = max(worst, abs(ai - alpha[k]))
                    alpha[k] = ai
            if worst < 1e-12:
                break
        else:
            raise SimulationError("空池限流比例迭代不收敛")
        return base, alpha * base


def simulate(cfg: dict) -> dict:
    c = validate(cfg)
    n, np_, npu = len(c["tanks"]), len(c["pipes"]), len(c["pumps"])
    bottom = np.array([t["bottom"] for t in c["tanks"]], float)
    area = np.array([t["area"] for t in c["tanks"]], float)
    cap = np.array([t["capacity"] for t in c["tanks"]], float)
    V = np.array([t["initial"] for t in c["tanks"]], float)

    # 物理量为自洽单位制；容差取绝对量，并按数据标度适度放大。
    scale_t = max(1.0, abs(c["t1"] - c["t0"]))
    scale_v = max([1.0] + list(cap))
    scale_q = scale_v / scale_t
    eps_t = 1e-9 * scale_t
    eps_v = 1e-8                    # m³，空/满边界贴附容差
    eps_h = 1e-9                    # m，泵水位判定的同刻容差
    eps_rate = 1e-7 * scale_q       # 判定“钉住池可离边界”的净流入阈值

    starts = [[a for a, _, _ in c["inflows"][i]] for i in range(n)]
    ctx = _Ctx(c=c, n=n, np_=np_, npu=npu, bottom=bottom, area=area, cap=cap,
               starts=starts)

    # 状态：体积 n + 管累计 np_ + 泵累计 npu + 溢流累计 n
    ns = n + np_ + npu + n
    io_p, io_u = n, n + np_
    io_f, io_o = n + np_ + npu, n + np_ + npu + n

    for i in range(n):
        if V[i] <= eps_v:
            V[i] = 0.0
            ctx.pinned_empty.add(i)
        elif V[i] >= cap[i] - eps_v:
            V[i] = cap[i]
            ctx.pinned_full.add(i)
    y = np.concatenate([V, np.zeros(np_ + npu + n)])
    t = c["t0"]

    def net_rates(t, Vc):
        """给定当前存水量，返回 (各池净流入率, 管流量 q, 外入流)。"""
        rate = ctx.inflow(t)
        _base, q = ctx.flows(Vc, rate)
        net = rate.copy()
        for k, pk in enumerate(c["pipes"]):
            net[pk["up"]] -= q[k]
            net[pk["dn"]] += q[k]
        for pu in c["pumps"]:
            if pu["on"]:
                net[pu["tank"]] -= pu["flow"]
        return net, q, rate

    def rhs(t, y):
        Vc = y[:n].copy()
        for i in ctx.pinned_empty:
            Vc[i] = 0.0
        for i in ctx.pinned_full:
            Vc[i] = cap[i]
        net, q, _rate = net_rates(t, Vc)
        dV = np.array([0.0 if (i in ctx.pinned_empty or i in ctx.pinned_full)
                       else net[i] for i in range(n)])
        d_pump = [pu["flow"] if pu["on"] else 0.0 for pu in c["pumps"]]
        d_over = [max(0.0, net[i]) if i in ctx.pinned_full else 0.0
                  for i in range(n)]
        return np.concatenate([dV, q, d_pump, d_over])

    def make_events(t_lo):
        # 事件函数在区间起点保护带 [t_lo, t_lo+guard] 内被强制取“不可能
        # 触发”的符号，杜绝 scipy 因首步插值把根误定位到 t0（即使起点
        # g=0 或已越过）。同刻的 regime 变化一律由 settle() 处理。
        guard = 1e-8 * scale_t
        evs = []
        rate0 = ctx.inflow(t_lo)
        _b0, _q0 = ctx.flows(y[:n], rate0)
        net0 = rate0.copy()
        for k, pk in enumerate(c["pipes"]):
            net0[pk["up"]] -= _q0[k]
            net0[pk["dn"]] += _q0[k]
        for pu in c["pumps"]:
            if pu["on"]:
                net0[pu["tank"]] -= pu["flow"]

        for i in range(n):
            if i in ctx.pinned_empty:
                # 起点已能离开：不挂事件（settle 会在同刻将其释放）
                if net0[i] > eps_rate:
                    continue

                def g(t, yy, i=i):
                    if t <= t_lo + guard:
                        return -1.0          # 向上事件：起点强制为负
                    Vc = yy[:n].copy()
                    net, _q, _r = net_rates(t, Vc)
                    return net[i] - eps_rate
                g.terminal, g.direction = True, 1
                evs.append(("leave_empty", i, g))
            elif i in ctx.pinned_full:
                # 起点已在回落：不挂事件（settle 同刻释放）
                if net0[i] < -eps_rate:
                    continue

                def g(t, yy, i=i):
                    if t <= t_lo + guard:
                        return -1.0          # 向上事件：起点强制为负
                    Vc = yy[:n].copy()
                    Vc[i] = cap[i]
                    net, _q, _r = net_rates(t, Vc)
                    return -net[i] - eps_rate
                g.terminal, g.direction = True, 1
                evs.append(("leave_full", i, g))
            else:
                def ge(t, yy, i=i):
                    if t <= t_lo + guard:
                        return 1.0           # 向下(触空)事件：起点强制为正
                    return yy[i]
                ge.terminal, ge.direction = True, -1
                evs.append(("empty", i, ge))

                def gf(t, yy, i=i):
                    if t <= t_lo + guard:
                        return -1.0          # 向上(触满)事件：起点强制为负
                    return yy[i] - cap[i]
                gf.terminal, gf.direction = True, 1
                evs.append(("full", i, gf))

        for j, pu in enumerate(c["pumps"]):
            i = pu["tank"]
            depth0 = y[i] / area[i]
            if pu["on"]:
                # 起点已低于停位：settle 同刻关闭，不挂事件
                if depth0 <= pu["depth_off"] + eps_h:
                    continue

                def goff(t, yy, j=j):
                    if t <= t_lo + guard:
                        return 1.0           # 向下事件：起点强制为正
                    k = c["pumps"][j]["tank"]
                    return yy[k] / area[k] - c["pumps"][j]["depth_off"]
                goff.terminal, goff.direction = True, -1
                evs.append(("pump_off", j, goff))
            else:
                # 起点已达启位：settle 同刻开启，不挂事件
                if depth0 >= pu["depth_on"] - eps_h:
                    continue

                def gon(t, yy, j=j):
                    if t <= t_lo + guard:
                        return -1.0          # 向上事件：起点强制为负
                    k = c["pumps"][j]["tank"]
                    return yy[k] / area[k] - c["pumps"][j]["depth_on"]
                gon.terminal, gon.direction = True, 1
                evs.append(("pump_on", j, gon))
        return evs

    samples = [(t, y.copy())]      # 断点/事件处的结算后状态（同刻取值用）
    segments = []                  # 各积分段 (起点, 终点, 稠密解)，拼接覆盖全程

    def push(tt, yy):
        if samples and abs(samples[-1][0] - tt) <= eps_t:
            samples[-1] = (tt, yy.copy())
        else:
            samples.append((tt, yy.copy()))

    def log_switch(j, tt):
        pu = c["pumps"][j]
        ctx.switch_log.append(dict(pump=pu["id"], tank=pu["tank_name"],
                                   time=float(tt), on=bool(pu["on"])))

    def settle(tt):
        """同刻一致性结算：先按水位更新泵，再把体积结算到空/满边界，
        循环至 regime 稳定。返回本次新翻转的泵序号。"""
        flipped = []
        # 同刻已判定“应离开空/满边界”的池：在本次 settle 中不得因体积
        # 仍在边界而被重新钉住（释放瞬间体积增量为零，属正常）。
        freed = set()
        for _ in range(2 * n + 2 * npu + 6):
            changed = False
            # 1) 泵状态（升至启位开 / 降至停位关）
            for j, pu in enumerate(c["pumps"]):
                depth = y[pu["tank"]] / area[pu["tank"]]
                if not pu["on"] and depth >= pu["depth_on"]:
                    pu["on"] = True
                    flipped.append(j)
                    changed = True
                elif pu["on"] and depth <= pu["depth_off"]:
                    pu["on"] = False
                    flipped.append(j)
                    changed = True
            # 2) 边界结算（贴边时依据净流入立即决定钉住或保持自由，避免反复钉/解）
            for i in range(n):
                if y[i] < -1e-7:
                    raise SimulationError("推演出现负水量（数值异常）")
                if i in ctx.pinned_empty:
                    net, _q, _r = net_rates(tt, y[:n])
                    if net[i] > eps_rate:
                        ctx.pinned_empty.discard(i)   # 来水转多：水位上升
                        freed.add(i)
                        changed = True
                    else:
                        y[i] = 0.0
                elif i in ctx.pinned_full:
                    net, _q, _r = net_rates(tt, y[:n])
                    if net[i] < -eps_rate:
                        ctx.pinned_full.discard(i)    # 净流出：水位回落
                        freed.add(i)
                        changed = True
                    else:
                        y[i] = cap[i]                 # 净余继续溢流，保持满位
                elif y[i] <= eps_v:
                    y[i] = 0.0
                    if i in freed:
                        continue                      # 同刻刚释放：保持自由
                    net, _q, _r = net_rates(tt, y[:n])
                    if net[i] <= eps_rate:
                        ctx.pinned_empty.add(i)       # 出多入少：钉在空位
                        changed = True
                    else:
                        freed.add(i)                  # 自空界起涨：保持自由
                elif y[i] >= cap[i] - eps_v:
                    y[i] = cap[i]
                    if i in freed:
                        continue                      # 同刻刚释放：保持自由
                    net, _q, _r = net_rates(tt, y[:n])
                    if net[i] >= -eps_rate:
                        ctx.pinned_full.add(i)        # 入多于出：钉在满位并溢流
                        changed = True
                    else:
                        freed.add(i)                  # 自满界回落：保持自由
            if not changed:
                return flipped
        raise SimulationError("同刻边界结算未能收敛")

    # 初态一致性（入流断点若在 t0，右连续取新值；泵校验后不应翻转）
    for j in settle(t):
        log_switch(j, t)
    push(t, y)

    # 全部入流断点（已含 t0、t1），单调推进
    breaks_set = {c["t0"], c["t1"]}
    for segs in c["inflows"].values():
        for a, b, _q in segs:
            if c["t0"] - eps_t <= a <= c["t1"] + eps_t:
                breaks_set.add(a)
            if c["t0"] - eps_t <= b <= c["t1"] + eps_t:
                breaks_set.add(b)
    breaks = sorted(breaks_set)
    bi = 0
    while bi + 1 < len(breaks) and breaks[bi + 1] <= t + eps_t:
        bi += 1

    stall, steps = 0, 0
    while t < c["t1"] - eps_t:
        steps += 1
        if steps > 200000:
            raise SimulationError("推演步数超限（事件过于密集）")

        evs = make_events(t)
        target = c["t1"] if bi + 1 >= len(breaks) else min(c["t1"], breaks[bi + 1])

        # 无事件需要处理：整段直接积分到下一断点
        if not evs:
            try:
                sol = solve_ivp(rhs, (t, target), y, method="DOP853",
                                dense_output=True, rtol=1e-8, atol=1e-10,
                                max_step=target - t)
            except Exception as exc:
                raise SimulationError(f"连续推演失败: {exc}") from exc
            if not sol.success:
                raise SimulationError(f"连续推演失败: {sol.message}")
            te = float(target)
            ye = sol.sol(te)
            for i in ctx.pinned_empty:
                ye[i] = 0.0
            for i in ctx.pinned_full:
                ye[i] = cap[i]
            segments.append((t, te, sol.sol))
            y, t = ye, te
            push(t, y)
            while bi + 1 < len(breaks) and breaks[bi + 1] <= t + eps_t:
                bi += 1
            for j in settle(t):
                log_switch(j, t)
            push(t, y)
            continue

        # 退化区间（target≈t）：先推进断点索引，再做同刻结算后继续
        if target <= t + eps_t:
            while bi + 1 < len(breaks) and breaks[bi + 1] <= t + eps_t:
                bi += 1
            for j in settle(t):
                log_switch(j, t)
            push(t, y)
            if t >= c["t1"] - eps_t:
                break
            continue

        try:
            sol = solve_ivp(rhs, (t, target), y, method="DOP853",
                            events=[e[2] for e in evs], dense_output=True,
                            rtol=1e-8, atol=1e-10,
                            max_step=target - t)
        except SimulationError:
            raise
        except Exception as exc:
            raise SimulationError(f"连续推演失败: {exc}") from exc
        if not sol.success:
            raise SimulationError(f"连续推演失败: {sol.message}")

        # 最早事件（忽略求解起点；与终点重合的事件也要结算）
        t_ev = None
        for ei, (_k, _x, _f) in enumerate(evs):
            for te in sol.t_events[ei]:
                if te > t + eps_t and (t_ev is None or te < t_ev):
                    t_ev = float(te)

        hit_break = t_ev is None or t_ev >= target - eps_t
        te = target if hit_break else t_ev
        ye = sol.sol(te)

        # 钉住体积回正（积分数值漂移）
        for i in ctx.pinned_empty:
            ye[i] = 0.0
        for i in ctx.pinned_full:
            ye[i] = cap[i]
        segments.append((t, te, sol.sol))
        y = ye
        t = te
        push(t, y)

        # 收集与最早时刻同刻发生的全部事件并依次施加
        if not hit_break:
            kinds = []
            for ei, (kind, idx, _f) in enumerate(evs):
                for tt2 in sol.t_events[ei]:
                    if abs(float(tt2) - t_ev) <= eps_t:
                        kinds.append((kind, idx))
            for kind, idx in kinds:
                if kind == "empty":
                    ctx.pinned_empty.add(idx)
                    y[idx] = 0.0
                elif kind == "full":
                    ctx.pinned_full.add(idx)
                    y[idx] = cap[idx]
                elif kind == "leave_empty":
                    ctx.pinned_empty.discard(idx)
                    y[idx] = 0.0
                elif kind == "leave_full":
                    ctx.pinned_full.discard(idx)
                    y[idx] = cap[idx]
                elif kind in ("pump_on", "pump_off"):
                    c["pumps"][idx]["on"] = (kind == "pump_on")
                    log_switch(idx, t)

        # 跨过断点：推进索引（同刻先取新入流——inflow() 右连续）
        if hit_break:
            while bi + 1 < len(breaks) and breaks[bi + 1] <= t + eps_t:
                bi += 1

        # 同刻：先更新入流与泵，再结算边界流量
        for j in settle(t):
            log_switch(j, t)
        push(t, y)

        if t <= c["t0"] + eps_t:
            stall += 1
            if stall > 100:
                raise SimulationError("推演在初始时刻附近反复触发事件，无法推进")
        else:
            stall = 0

    # 终点最终结算
    for j in settle(t):
        log_switch(j, t)
    push(c["t1"], y)

    # ---- 整理轨迹（分段连续解采样，不做跨段直线插值）----
    # st/sy：各断点、事件与端点处“同刻结算后”的状态（先更新入流与泵、
    # 再结算边界的结果）；segments：覆盖全程的各积分段稠密解。
    st = np.array([s[0] for s in samples])
    sy = np.array([s[1] for s in samples])
    order = np.argsort(st)
    st, sy = st[order], sy[order]
    keep = [0]
    for i in range(1, len(st)):
        if st[i] - st[keep[-1]] <= eps_t:
            keep[-1] = i
        else:
            keep.append(i)
    st, sy = st[keep], sy[keep]

    sw_times = sorted({s["time"] for s in ctx.switch_log
                       if c["t0"] < s["time"] < c["t1"]})
    query = np.unique(np.concatenate(
        [np.linspace(c["t0"], c["t1"], c["n_points"]), sw_times,
         [c["t0"], c["t1"]]]))
    Y = np.empty((len(query), ns))
    filled = np.zeros(len(query), bool)

    # 1) 与断点/切换同刻的采样点：沿用同刻结算后的状态
    pos = np.searchsorted(st, query)
    for qi in range(len(query)):
        best, best_d = -1, eps_t
        for ci in (pos[qi] - 1, pos[qi]):
            if 0 <= ci < len(st):
                d = abs(st[ci] - query[qi])
                if d <= best_d:
                    best, best_d = ci, d
        if best >= 0:
            Y[qi] = sy[best]
            filled[qi] = True

    # 2) 其余采样点归入所在积分段，用该段稠密解求值（段内连续真解）
    if segments:
        seg_start = np.array([s[0] for s in segments])
        seg_of = np.searchsorted(seg_start, query, side="right") - 1
        for qi in range(len(query)):
            if filled[qi]:
                continue
            si = seg_of[qi]
            if si < 0:
                continue
            a, b, dense = segments[si]
            if query[qi] <= b + eps_t:
                Y[qi] = dense(min(max(query[qi], a), b))
                filled[qi] = True

    # 3) 兜底（正常不会触发）：未覆盖点退化为结算样本间线性插值
    if not np.all(filled):
        for r in range(ns):
            Y[~filled, r] = np.interp(query[~filled], st, sy[:, r])

    Y[:, n:] = np.maximum.accumulate(Y[:, n:], axis=0)   # 累计量单调
    Y[:, :n] = np.clip(Y[:, :n], 0.0, cap)

    volumes = Y[:, :n]
    pipe_cum = Y[:, io_p:io_u]
    pump_cum = Y[:, io_u:io_f]
    over_cum = Y[:, io_f:io_o]
    depths = volumes / area
    heads = bottom + depths

    # ---- 各池入流总量（解析积分分段恒定入流）----
    inflow_tot = np.zeros(n)
    for i, segs in c["inflows"].items():
        for a, b, q in segs:
            aa, bb = max(a, c["t0"]), min(b, c["t1"])
            if bb > aa:
                inflow_tot[i] += q * (bb - aa)

    balance = dict(
        initial=float(np.sum([t["initial"] for t in c["tanks"]])),
        inflow=float(np.sum(inflow_tot)),
        final=float(np.sum(volumes[-1])),
        pumped=float(np.sum(pump_cum[-1])),
        overflow=float(np.sum(over_cum[-1])),
    )
    balance["residual"] = (balance["initial"] + balance["inflow"]
                           - balance["final"] - balance["pumped"]
                           - balance["overflow"])

    return dict(
        times=[float(x) for x in query],
        tanks=[dict(
            id=t["id"], bottom=t["bottom"], area=t["area"],
            capacity=t["capacity"], initial=t["initial"],
            depth=[float(x) for x in depths[:, i]],
            head=[float(x) for x in heads[:, i]],
            volume=[float(x) for x in volumes[:, i]],
            inflow_total=float(inflow_tot[i]),
            overflow_total=float(over_cum[-1, i]),
            overflow_series=[float(x) for x in over_cum[:, i]],
        ) for i, t in enumerate(c["tanks"])],
        pipes=[dict(
            id=pk["id"], upstream=pk["up_name"], downstream=pk["dn_name"],
            coeff=pk["coeff"], total=float(pipe_cum[-1, k]),
            cumulative=[float(x) for x in pipe_cum[:, k]],
        ) for k, pk in enumerate(c["pipes"])],
        pumps=[dict(
            id=pu["id"], tank=pu["tank_name"], flow=pu["flow"],
            depth_on=pu["depth_on"], depth_off=pu["depth_off"],
            total=float(pump_cum[-1, j]),
            cumulative=[float(x) for x in pump_cum[:, j]],
        ) for j, pu in enumerate(c["pumps"])],
        switches=ctx.switch_log,
        balance={k: float(v) for k, v in balance.items()},
    )
