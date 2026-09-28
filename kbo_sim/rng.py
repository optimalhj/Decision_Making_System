"""
rng.py
------
"학생 코드의 난수와 엔진 난수를 반드시 분리된 RNG 인스턴스로" (구현고려 #1).

학생이 자기 알고리즘 안에서 random.seed(...)를 호출하거나 전역 random 모듈을 사용해도
엔진(경기 시뮬레이션)의 결과가 절대 영향을 받지 않도록, 두 개의 완전히 독립된
random.Random 인스턴스를 만든다.

- ENGINE RNG: 경기 시뮬레이션(타석 결과, 투구 시퀀스, 수비 실책, 도루 시도 등)에만 사용.
  학생 코드는 절대 접근할 수 없다.
- STUDENT RNG: student_api.py가 학생 함수를 호출할 때 인자로 넘겨주는 전용 random.Random
  인스턴스. 학생은 이 인스턴스만 사용하도록 안내받는다(가이드 문서 참고). 설령 학생이
  전역 random.seed()를 호출해도 그건 학생 프로세스 내부의 전역 random 모듈일 뿐,
  엔진의 ENGINE RNG(별도 인스턴스, 별도 프로세스)에는 어떤 영향도 주지 못한다.
"""
from __future__ import annotations

import hashlib
import random


def _stable_seed(*parts) -> int:
    """문자열이 섞인 값으로부터 프로세스가 바뀌어도 동일한 정수 시드를 만든다.
    파이썬 내장 hash()는 문자열에 대해 PYTHONHASHSEED로 매 실행마다 값이 달라지므로
    '같은 시드 = 같은 경기'가 프로세스를 넘나들면 깨진다. 그래서 md5를 쓴다."""
    raw = ":".join(str(p) for p in parts).encode("utf-8")
    return int(hashlib.md5(raw).hexdigest()[:8], 16)


class GameRNG:
    """한 경기(Game)에 귀속되는 RNG 묶음. 시드를 지정하면 같은 경기를 재현할 수 있다."""

    def __init__(self, seed: int | None = None):
        base_seed = seed if seed is not None else random.SystemRandom().randrange(2**31)
        self.seed = base_seed
        # 서로 다른 시드로 파생시켜 두 스트림이 절대 같은 시퀀스를 갖지 않도록 함
        self.engine = random.Random(base_seed * 2 + 1)
        # 학생 RNG 호출 횟수 (팀/역할별). 호출할 때마다 다른 스트림을 주기 위해 사용한다.
        self._student_call_count: dict[str, int] = {}

    def student_rng(self, team_key: str) -> random.Random:
        """학생 알고리즘 호출 1회분 전용 RNG.

        ⚠️ 매 호출마다 **새로** 만들어야 한다. 학생 코드는 fork된 자식 프로세스에서 실행되므로
        난수 소비가 자식 쪽에서만 일어나고 부모의 인스턴스 상태는 그대로 남는다. 예전처럼
        인스턴스를 캐싱해 재사용하면 학생은 매 이닝 완전히 똑같은 난수열을 받게 되어,
        무작위성을 쓰는 알고리즘이 9이닝 내내 같은 라인업만 내놓는다(실제 버그였음).

        호출 순번을 시드에 섞어 파생시키므로 (a) 매 호출 다른 스트림 (b) 같은 시드면 재현 가능
        (c) 팀/역할별로 완전히 분리, 세 가지를 모두 만족한다."""
        n = self._student_call_count.get(team_key, 0)
        self._student_call_count[team_key] = n + 1
        return random.Random(_stable_seed(self.seed, team_key, n))
