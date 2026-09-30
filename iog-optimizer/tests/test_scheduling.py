"""스케줄러: 공식 코드와 같은 makespan, 매뉴얼 예시, IG 결정성·개선."""
import datetime as dt

import numpy as np
import pytest

from iog import ptimes
from iog import scheduling as S


@pytest.fixture(scope="module")
def P_mon():
    return ptimes.load_table("P1", "mon")


def test_tables_shape_and_mapping():
    for p in ("P1", "P2"):
        for w in ("mon", "sun"):
            assert ptimes.load_table(p, w).shape == (500, 20)
    # P2 는 t2_ 파일: 9/27(일) 76 jobs makespan 11,588 이 SPT·NEH 범위 안
    P = ptimes.first_n("P2", dt.date(2026, 9, 27), 76)
    lo, hi = S.neh(P)[1], S.makespan(P, np.arange(76))
    assert lo - 50 <= 11588 <= hi + 50


def test_matches_official_library(P_mon):
    rng = np.random.default_rng(0)
    for n in (5, 30, 120):
        for _ in range(5):
            seq = rng.permutation(n)
            assert S.makespan(P_mon[:n], seq) == S.official_makespan(P_mon[:n], S.to_job_ids(seq))


def test_manual_example_five_jobs(P_mon):
    P5 = P_mon[:5]
    assert S.makespan(P5, [0, 1, 2, 3, 4]) == 1649                 # 매뉴얼 ID 순 1,649
    seq, ms = S.spt(P5)
    assert S.format_sequence(seq) == "3,5,4,2,1" and ms == 1474    # (매뉴얼 표기 1,395 는 공식 코드로 재현 안 됨)
    seq, ms = S.neh(P5)
    assert S.format_sequence(seq) == "5,2,4,3,1" and ms == 1252    # 전수탐색 최적과 같음


def test_ig_deterministic_and_not_worse_than_neh(P_mon):
    P = P_mon[:120]
    a = S.iterated_greedy(P, time_limit=None, max_iters=50, seed=7)
    b = S.iterated_greedy(P, time_limit=None, max_iters=50, seed=7)
    assert a[1] == b[1] and (a[0] == b[0]).all()
    assert a[1] <= S.neh(P)[1]
    S.validate_sequence(a[0], 120)
    assert a[1] >= S.lower_bound(P)


def test_sequence_roundtrip():
    seq = np.array([2, 0, 1])
    assert S.format_sequence(seq) == "3,1,2"
    assert (S.from_job_ids([3, 1, 2]) == seq).all()
