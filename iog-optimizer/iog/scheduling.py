"""순열 flow shop (n jobs × 20 machines) Makespan 최소화.

- makespan()            : 게임 공식 코드(scheduling_lib.py)와 같은 값 (tests/test_scheduling.py 로 고정)
- spt()                 : 게임 Auto / "Job qty" 입력과 같은 규칙 (선택 설비 기준 처리시간 짧은 순)
- neh()                 : NEH (Taillard 가속)
- iterated_greedy()     : Ruiz & Stützle (2007) IG. NEH에서 시작, 파괴·재건 + 삽입 지역탐색

시퀀스는 내부적으로 0-based 행 번호(np.int64 배열). 게임 입력은 1-based JobID → to_job_ids().
numba 가 없으면 순수 파이썬으로 돌지만 매우 느리다 (pip install numba 권장).
"""
from __future__ import annotations

import math
import time

import numpy as np

try:  # pragma: no cover - 환경에 따라 다름
    from numba import njit

    HAS_NUMBA = True
except ImportError:  # pragma: no cover
    HAS_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f


# ---------------------------------------------------------------- 저수준 (numba)
@njit(cache=True)
def _makespan(P, seq, n):
    m = P.shape[1]
    c = np.zeros(m)
    for idx in range(n):
        j = seq[idx]
        c[0] += P[j, 0]
        for k in range(1, m):
            a = c[k]
            b = c[k - 1]
            c[k] = (a if a > b else b) + P[j, k]
    return c[m - 1]


@njit(cache=True)
def _best_insert(P, seq, k, job, e, q):
    """seq[0:k] 에 job 을 넣을 최적 위치와 그때의 makespan (Taillard 가속, O(k·m))."""
    m = P.shape[1]
    for i in range(1, k + 1):
        for j in range(1, m + 1):
            a = e[i - 1, j]
            b = e[i, j - 1]
            e[i, j] = (a if a > b else b) + P[seq[i - 1], j - 1]
    for j in range(m + 2):
        q[k + 1, j] = 0.0
    for i in range(k + 2):
        q[i, m + 1] = 0.0
    for i in range(k, 0, -1):
        for j in range(m, 0, -1):
            a = q[i + 1, j]
            b = q[i, j + 1]
            q[i, j] = (a if a > b else b) + P[seq[i - 1], j - 1]
    best = 1e18
    bpos = 0
    for pos in range(k + 1):
        fprev = 0.0
        val = 0.0
        for j in range(1, m + 1):
            a = fprev
            b = e[pos, j]
            fj = (a if a > b else b) + P[job, j - 1]
            v = fj + q[pos + 1, j]
            if v > val:
                val = v
            fprev = fj
        if val < best:
            best = val
            bpos = pos
    return bpos, best


@njit(cache=True)
def _insert(seq, k, pos, job):
    for i in range(k, pos, -1):
        seq[i] = seq[i - 1]
    seq[pos] = job


@njit(cache=True)
def _remove_at(seq, k, pos):
    job = seq[pos]
    for i in range(pos, k - 1):
        seq[i] = seq[i + 1]
    return job


@njit(cache=True)
def _neh(P, order):
    n, m = P.shape
    seq = np.empty(n, np.int64)
    seq[0] = order[0]
    k = 1
    e = np.zeros((n + 1, m + 1))
    q = np.zeros((n + 2, m + 2))
    for t in range(1, n):
        job = order[t]
        pos, _ = _best_insert(P, seq, k, job, e, q)
        _insert(seq, k, pos, job)
        k += 1
    return seq


@njit(cache=True)
def _seed(s):
    np.random.seed(s)


@njit(cache=True)
def _local_search(P, seq, n, cur, e, q):
    """삽입 지역탐색 (iterative improvement). 개선이 없을 때까지 반복."""
    improved = True
    while improved:
        improved = False
        jobs = seq[:n].copy()
        perm = np.random.permutation(n)
        for t in range(n):
            job = jobs[perm[t]]  # 각 job을 한 번씩, 무작위 순서로
            # 현재 위치 찾기
            p = 0
            for i in range(n):
                if seq[i] == job:
                    p = i
                    break
            _remove_at(seq, n, p)
            pos, val = _best_insert(P, seq, n - 1, job, e, q)
            _insert(seq, n - 1, pos, job)
            if val < cur - 1e-9:
                cur = val
                improved = True
    return cur


@njit(cache=True)
def _ig_chunk(P, cur, cur_ms, best, best_ms, iters, d, temperature):
    n, m = P.shape
    e = np.zeros((n + 1, m + 1))
    q = np.zeros((n + 2, m + 2))
    work = np.empty(n, np.int64)
    removed = np.empty(d, np.int64)
    for _ in range(iters):
        for i in range(n):
            work[i] = cur[i]
        k = n
        for r in range(d):
            pos = np.random.randint(0, k)
            removed[r] = _remove_at(work, k, pos)
            k -= 1
        for r in range(d):
            pos, _v = _best_insert(P, work, k, removed[r], e, q)
            _insert(work, k, pos, removed[r])
            k += 1
        ms = _makespan(P, work, n)
        ms = _local_search(P, work, n, ms, e, q)
        if ms < cur_ms - 1e-9:
            for i in range(n):
                cur[i] = work[i]
            cur_ms = ms
            if ms < best_ms - 1e-9:
                for i in range(n):
                    best[i] = work[i]
                best_ms = ms
        elif np.random.random() <= np.exp(-(ms - cur_ms) / temperature):
            for i in range(n):
                cur[i] = work[i]
            cur_ms = ms
    return cur_ms, best_ms


# ---------------------------------------------------------------- 공개 API
def _as_P(P) -> np.ndarray:
    return np.ascontiguousarray(P, dtype=np.float64)


def makespan(P, seq) -> float:
    P = _as_P(P)
    seq = np.asarray(seq, dtype=np.int64)
    if len(seq) == 0:
        return 0.0
    return float(_makespan(P, seq, len(seq)))


def spt(P, machine: int = 0):
    """게임 Auto(SPT)와 같은 규칙. machine=0 이 M1."""
    P = _as_P(P)
    seq = np.argsort(P[:, machine], kind="stable").astype(np.int64)
    return seq, makespan(P, seq)


def neh(P):
    P = _as_P(P)
    n = P.shape[0]
    if n == 0:
        return np.empty(0, np.int64), 0.0
    order = np.argsort(-P.sum(axis=1), kind="stable").astype(np.int64)
    seq = _neh(P, order)
    return seq, makespan(P, seq)


def iterated_greedy(P, time_limit: float | None = 10.0, max_iters: int | None = None,
                    seed: int = 0, d: int = 4, tp: float = 0.4, init_seq=None, chunk: int = 25):
    """Ruiz & Stützle (2007) IG. 재현이 필요하면 time_limit=None, max_iters=N 으로 돌릴 것."""
    P = _as_P(P)
    n, m = P.shape
    if n <= 2:
        seq, ms = neh(P)
        return seq, ms, {"iters": 0, "seconds": 0.0, "init_ms": ms}
    d = max(1, min(d, n - 1))
    seq = neh(P)[0] if init_seq is None else np.asarray(init_seq, dtype=np.int64).copy()
    _seed(seed)
    init_ms = makespan(P, seq)
    e = np.zeros((n + 1, m + 1))
    q = np.zeros((n + 2, m + 2))
    cur_ms = float(_local_search(P, seq, n, init_ms, e, q))
    cur = seq.copy()
    best = seq.copy()
    best_ms = cur_ms
    temperature = tp * P.sum() / (n * m * 10.0)
    t0 = time.time()
    iters = 0
    while True:
        if max_iters is not None and iters >= max_iters:
            break
        if time_limit is not None and time.time() - t0 >= time_limit:
            break
        c = chunk if max_iters is None else min(chunk, max_iters - iters)
        cur_ms, best_ms = _ig_chunk(P, cur, cur_ms, best, best_ms, c, d, temperature)
        iters += c
    return best, float(best_ms), {"iters": iters, "seconds": round(time.time() - t0, 2), "init_ms": init_ms}


def official_makespan(P, job_ids) -> float:
    """Kahkim/scheduling_lib.build_schedule 의 계산을 그대로 옮긴 참조 구현 (검증용, 느림)."""
    P = np.asarray(P, dtype=float)
    seq = [int(j) - 1 for j in job_ids]
    m = P.shape[1]
    prev = [0.0] * m
    first = seq[0]
    prev[0] = P[first, 0]
    for i in range(1, m):
        prev[i] = prev[i - 1] + P[first, i]
    for job in seq[1:]:
        cur = [0.0] * m
        cur[0] = prev[0] + P[job, 0]
        for j in range(1, m):
            start = prev[j] if cur[j - 1] < prev[j] else cur[j - 1]
            cur[j] = start + P[job, j]
        prev = cur
    return float(prev[-1])


def to_job_ids(seq) -> list[int]:
    return [int(j) + 1 for j in seq]


def from_job_ids(ids) -> np.ndarray:
    return np.asarray([int(j) - 1 for j in ids], dtype=np.int64)


def format_sequence(seq) -> str:
    """게임 'Job sequence' 칸에 붙여넣을 문자열 (1-based, 콤마 구분)."""
    return ",".join(str(j) for j in to_job_ids(seq))


def validate_sequence(seq, n: int) -> None:
    ids = sorted(int(j) for j in seq)
    if ids != list(range(n)):
        raise ValueError("sequence must be a permutation of 0..n-1 (JobID 1..n)")


def lower_bound(P) -> float:
    """간단한 하한: max_k(설비 k 총부하 + 앞·뒤 최소 꼬리). 개선 여지 가늠용."""
    P = _as_P(P)
    n, m = P.shape
    if n == 0:
        return 0.0
    heads = np.zeros(m)
    tails = np.zeros(m)
    cum = np.cumsum(P, axis=1)
    for k in range(m):
        heads[k] = (cum[:, k - 1].min() if k > 0 else 0.0)
        tails[k] = ((cum[:, -1] - cum[:, k]).min())
    return float(max(P[:, k].sum() + heads[k] + tails[k] for k in range(m)))


def best_of(P, time_limit: float = 10.0, seed: int = 0) -> dict:
    """SPT/NEH/IG 비교 결과 (리포트용)."""
    s_seq, s_ms = spt(P)
    n_seq, n_ms = neh(P)
    i_seq, i_ms, info = iterated_greedy(P, time_limit=time_limit, seed=seed, init_seq=n_seq)
    lb = lower_bound(P)
    return {
        "spt": (s_seq, s_ms), "neh": (n_seq, n_ms), "ig": (i_seq, i_ms), "ig_info": info,
        "lower_bound": lb, "gap_to_lb": (i_ms - lb) / lb if lb > 0 else math.nan,
    }
