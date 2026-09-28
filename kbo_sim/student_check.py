"""
student_check.py
-----------------
학생이 제출한 .py 파일을 업로드하는 즉시 검사해서, 문제를 화면에 바로 보여주기 위한 모듈.

두 단계로 검사한다.

1) 정적 검사 (static_check) — 코드를 실행하지 않고 AST로 훑어서 규칙 위반/위험 요소를 찾는다.
   - ERROR   : 이대로면 경기를 진행할 수 없음 (업로드 거부)
   - WARNING : 경기는 되지만 규칙 위반이거나 손해를 보게 되는 부분 (예: 전역 random 사용)
   - INFO    : 알아두면 좋은 주의사항

2) 스모크 테스트 (smoke_test) — 실제로 decide_lineup()을 한 번 호출해서
   ① 예외 없이 끝나는지 ② 제한시간 안에 들어오는지 ③ 반환한 {"defense":[...10], "offense":[...9]}가
   규칙에 맞는지 확인한다.
   실행은 student_api.run_student_decision()을 그대로 쓰므로 실제 경기와 동일한 격리 환경이다.
"""
from __future__ import annotations

import ast
import random
from dataclasses import dataclass, field
from typing import List, Optional

from .data_pipeline import LeagueData
from .models import GameRosterState, build_team
from .student_api import (REQUIRED_FUNC_NAME, matchup_dataframe, run_student_decision,
                           team_status_dataframe, validate_lineups)

REQUIRED_ARGS = ["my_team", "opponent_team", "matchups", "context", "rng"]

# 엔진이 학생 프로세스에 넣어주는 표준 라이브러리 외에, 있어도 되는 모듈
ALLOWED_IMPORTS = {
    "random", "math", "copy", "itertools", "functools", "collections", "heapq", "bisect",
    "operator", "statistics", "typing", "dataclasses", "enum", "abc", "numbers", "decimal",
    "fractions", "array", "json", "re", "string", "sys", "time", "pandas", "numpy", "np", "pd",
}
RISKY_IMPORTS = {"os", "subprocess", "socket", "shutil", "urllib", "requests", "pickle",
                  "multiprocessing", "threading", "ctypes", "importlib", "builtins"}

RANDOM_FUNCS = {"random", "randint", "randrange", "choice", "choices", "shuffle", "sample",
                 "uniform", "gauss", "normalvariate", "betavariate", "expovariate", "triangular",
                 "getrandbits", "seed"}


@dataclass
class CheckResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    infos: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self):
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "infos": self.infos}


class _Visitor(ast.NodeVisitor):
    def __init__(self, res: CheckResult):
        self.res = res
        self.alias_to_module = {}    # 'rd' -> 'random'
        self.name_to_dotted = {}     # 'shuffle' -> 'random.shuffle'
        self.found_func = None
        self.func_depth = 0
        self.class_depth = 0

    # ---- import 추적 ----
    def visit_Import(self, node):
        for a in node.names:
            root = a.name.split(".")[0]
            self.alias_to_module[a.asname or a.name] = a.name
            if root in RISKY_IMPORTS:
                self.res.warnings.append(
                    f"{node.lineno}행: '{a.name}' 모듈을 import했습니다. 라인업 결정에는 필요 없는 "
                    f"모듈이며, 파일/네트워크/프로세스 조작은 채점 환경에서 차단되거나 실격 사유가 될 수 있습니다.")
            elif root not in ALLOWED_IMPORTS:
                self.res.warnings.append(
                    f"{node.lineno}행: '{a.name}' 모듈은 채점 환경에 설치되어 있지 않을 수 있습니다. "
                    f"표준 라이브러리와 pandas/numpy만 사용하세요.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        root = mod.split(".")[0]
        for a in node.names:
            self.name_to_dotted[a.asname or a.name] = f"{mod}.{a.name}"
        if root in RISKY_IMPORTS:
            self.res.warnings.append(
                f"{node.lineno}행: '{mod}' 모듈에서 import했습니다. 라인업 결정에는 필요 없는 모듈입니다.")
        elif root and root not in ALLOWED_IMPORTS:
            self.res.warnings.append(
                f"{node.lineno}행: '{mod}' 모듈은 채점 환경에 없을 수 있습니다.")
        self.generic_visit(node)

    # ---- 함수 정의 ----
    def visit_ClassDef(self, node):
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_FunctionDef(self, node):
        if node.name == REQUIRED_FUNC_NAME and self.func_depth == 0 and self.class_depth == 0:
            self.found_func = node
            self._check_signature(node)
        self.func_depth += 1
        self.generic_visit(node)
        self.func_depth -= 1

    def _check_signature(self, node):
        args = [a.arg for a in node.args.args]
        if len(args) != len(REQUIRED_ARGS):
            self.res.errors.append(
                f"{node.lineno}행: decide_lineup의 인자가 {len(args)}개입니다. "
                f"정확히 {len(REQUIRED_ARGS)}개여야 합니다 → ({', '.join(REQUIRED_ARGS)})")
            return
        if args != REQUIRED_ARGS:
            self.res.errors.append(
                f"{node.lineno}행: 인자 이름이 다릅니다. 엔진이 키워드 인자로 호출하므로 이름과 순서가 "
                f"정확히 일치해야 합니다.\n"
                f"        제출: ({', '.join(args)})\n"
                f"        필요: ({', '.join(REQUIRED_ARGS)})")
        has_return = any(isinstance(n, ast.Return) and n.value is not None
                          for n in ast.walk(node))
        if not has_return:
            self.res.errors.append(
                f"{node.lineno}행: decide_lineup 안에 값을 돌려주는 return 문이 없습니다. "
                f"pCode 리스트를 반환해야 합니다.")

    # ---- 호출 검사 ----
    def _dotted(self, func) -> Optional[str]:
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        else:
            return None
        parts.reverse()
        # 별칭 치환 (import numpy as np -> np.random.shuffle => numpy.random.shuffle)
        if parts[0] in self.alias_to_module:
            parts[0] = self.alias_to_module[parts[0]]
        dotted = ".".join(parts)
        # from random import shuffle -> shuffle(...) => random.shuffle
        if len(parts) == 1 and parts[0] in self.name_to_dotted:
            dotted = self.name_to_dotted[parts[0]]
        return dotted

    def visit_Call(self, node):
        d = self._dotted(node.func)
        if d:
            ln = node.lineno
            if d == "random.seed":
                self.res.warnings.append(
                    f"{ln}행: random.seed()를 호출했습니다. 시뮬레이션 엔진의 난수와는 완전히 분리되어 "
                    f"있어 경기 결과를 바꾸지는 못하지만, 규칙상 난수는 인자로 받은 rng만 사용해야 합니다. "
                    f"(rng는 이미 시드가 정해진 상태로 전달됩니다)")
            elif d.startswith("random.") and d.split(".")[-1] in RANDOM_FUNCS:
                self.res.warnings.append(
                    f"{ln}행: 전역 random 모듈({d})을 사용했습니다. 규칙상 난수는 인자로 받은 "
                    f"rng만 사용해야 합니다 → rng.{d.split('.')[-1]}(...) 로 바꾸세요.")
            elif d.startswith("numpy.random.") or d.startswith("np.random."):
                self.res.warnings.append(
                    f"{ln}행: numpy 난수({d})를 사용했습니다. 규칙상 난수는 인자로 받은 rng만 "
                    f"사용해야 합니다 (numpy 난수는 재현이 보장되지 않습니다).")
            elif d in ("time.sleep",):
                self.res.warnings.append(
                    f"{ln}행: time.sleep()은 제한시간만 소모합니다. 타임아웃 실격으로 이어질 수 있습니다.")
            elif d in ("input",):
                self.res.errors.append(
                    f"{ln}행: input()은 입력을 기다리며 멈추기 때문에 반드시 타임아웃 실격됩니다. 제거하세요.")
            elif d in ("exit", "quit", "sys.exit", "os._exit"):
                self.res.errors.append(
                    f"{ln}행: {d}() 호출은 프로세스를 종료시켜 결과를 반환하지 못하게 만듭니다. 제거하세요.")
            elif d in ("open",):
                self.res.warnings.append(
                    f"{ln}행: 파일을 직접 여는 코드가 있습니다. 필요한 데이터는 모두 인자로 전달되므로 "
                    f"파일 접근은 필요 없습니다.")
            elif d.startswith("os.system") or d.startswith("subprocess."):
                self.res.errors.append(
                    f"{ln}행: 외부 명령 실행({d})은 허용되지 않습니다.")
            elif d in ("print",):
                self.res.infos.append(
                    f"{ln}행: print()는 매 이닝 호출될 때마다 실행됩니다. 디버깅이 끝나면 지우는 게 좋습니다.")
        self.generic_visit(node)

    def visit_Global(self, node):
        self.res.infos.append(
            f"{node.lineno}행: global 문을 사용했습니다. 학생 코드는 매 호출마다 새 프로세스에서 실행되므로, "
            f"전역 변수에 저장한 값은 다음 이닝으로 이어지지 않습니다 (캐시 목적이라면 함수 안에서 매번 새로 만드세요).")
        self.generic_visit(node)

    def visit_While(self, node):
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.res.warnings.append(
                f"{node.lineno}행: 'while True' 루프가 있습니다. 탈출 조건이 확실하지 않으면 무한루프로 "
                f"타임아웃 실격이 됩니다 (엔진이 강제 종료합니다).")
        self.generic_visit(node)


def static_check(path: str) -> CheckResult:
    res = CheckResult()
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except UnicodeDecodeError:
        res.errors.append("파일을 UTF-8로 읽을 수 없습니다. 인코딩을 UTF-8로 저장해 주세요.")
        return res
    except OSError as e:
        res.errors.append(f"파일을 읽을 수 없습니다: {e}")
        return res

    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        res.errors.append(f"{e.lineno}행: 문법 오류 — {e.msg}")
        return res

    v = _Visitor(res)
    v.visit(tree)

    if v.found_func is None:
        # 최상위에 없고 클래스 안에만 있는 경우도 잡아준다
        nested = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == REQUIRED_FUNC_NAME]
        if nested:
            res.errors.append(
                f"decide_lineup 함수가 다른 함수/클래스 안에 들어 있습니다. 파일 최상위(들여쓰기 없이)에 "
                f"정의해야 합니다.")
        else:
            res.errors.append(
                f"'{REQUIRED_FUNC_NAME}' 함수가 없습니다. "
                f"def {REQUIRED_FUNC_NAME}({', '.join(REQUIRED_ARGS)}): 형태로 정의해야 합니다.")

    # 모듈 최상위에 실행되는 무거운 코드가 있는지
    heavy_top = [n for n in tree.body
                 if not isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
                                        ast.Assign, ast.AnnAssign, ast.Expr))]
    if heavy_top:
        res.infos.append(
            f"{heavy_top[0].lineno}행 부근: 파일 최상위에 실행문이 있습니다. 이 코드는 이닝마다 "
            f"모듈을 새로 불러올 때마다 다시 실행되므로 무거우면 제한시간을 갉아먹습니다.")
    return res


# ---------------------------------------------------------------------------
# 스모크 테스트 — 실제로 한 번 호출해 본다
# ---------------------------------------------------------------------------
def smoke_test(path: str, league: LeagueData, team_name: str, opponent_name: Optional[str] = None,
               timeout_sec: float = 10.0) -> dict:
    teams = league.teams_list()
    if team_name not in teams:
        team_name = teams[0]
    if not opponent_name or opponent_name == team_name:
        opponent_name = next(t for t in teams if t != team_name)

    my = build_team(league, team_name)
    opp = build_team(league, opponent_name)
    rs = GameRosterState(league, game_seed=20260903)
    my_df = team_status_dataframe(league, my, rs)
    opp_df = team_status_dataframe(league, opp, rs)

    out = {"team": team_name, "opponent": opponent_name, "cases": [], "ok": True}

    # 실제 경기(game.py _decide)와 똑같이 양 팀 투수진 '전원'으로 맞대결 표를 만든다.
    # 사전검사가 실제 경기보다 좁은 표를 주면 "통과했는데 경기에서 터지는" 제출물이 나온다.
    mu = matchup_dataframe(league, list(my.pitcher_pcodes) + list(opp.pitcher_pcodes),
                           list(opp.batter_pcodes) + list(my.batter_pcodes))
    ctx = {"inning": 1, "half": "bottom", "my_score": 0, "opponent_score": 0, "outs": 0,
           "batting_order_start_index": 0,
           "my_prev_offense": None, "my_prev_defense": None,
           "opp_prev_offense": None, "opp_prev_defense": None,
           "opp_pitcher_pcode": None, "opp_catcher_pcode": None,
           "time_budget_sec": timeout_sec}
    res = run_student_decision(path, "smoke_lineup", my_team=my_df, opponent_team=opp_df,
                               matchups=mu, context=ctx, rng=random.Random(12345),
                               timeout_sec=timeout_sec)
    case = {"label": "라인업(수비 10 + 공격 9)", "status": res.status,
            "elapsed_sec": round(res.elapsed, 3), "error": res.error, "detail": None}
    if res.status == "ok":
        checked = validate_lineups(league, my, res.lineups)
        if isinstance(checked, str):
            case["status"], case["error"] = "invalid", checked
        else:
            offense, defense = checked
            case["detail"] = ("수비 " + " / ".join(league.player(p)["shortName"] for p in defense)
                              + "\n타순 " + " / ".join(league.player(p)["shortName"] for p in offense))
    if case["status"] != "ok":
        out["ok"] = False
    out["cases"].append(case)
    return out


def full_check(path: str, league: LeagueData, team_name: Optional[str] = None,
               opponent_name: Optional[str] = None, timeout_sec: float = 10.0) -> dict:
    """정적 검사 + (통과 시) 스모크 테스트를 한 번에."""
    static = static_check(path)
    result = static.to_dict()
    result["smoke"] = None
    if static.ok and league is not None:
        try:
            smoke = smoke_test(path, league, team_name or league.teams_list()[0],
                                opponent_name, timeout_sec)
            result["smoke"] = smoke
            if not smoke["ok"]:
                for c in smoke["cases"]:
                    if c["status"] != "ok":
                        msg = {"timeout": f"{timeout_sec:.0f}초 제한시간을 넘겼습니다",
                                "error": "실행 중 예외가 발생했습니다",
                                "crash": "프로세스가 비정상 종료했습니다",
                                "invalid": "반환한 명단이 규칙에 맞지 않습니다"}.get(c["status"], c["status"])
                        detail = (c["error"] or "").strip().splitlines()
                        detail = detail[0] if detail else ""
                        result["errors"].append(f"[{c['label']} 실행 테스트] {msg} — {detail}")
                result["ok"] = False
        except Exception as e:  # noqa: BLE001
            # 실행 테스트 자체가 실패하면 안전성을 확인할 방법이 없다는 뜻이므로, 경고로만
            # 넘기지 않고 검사를 실패(ok=False) 처리한다 — "검증 못 함"과 "통과"는 다르다.
            result["errors"].append(
                f"실행 테스트 도중 예상치 못한 오류가 발생해 안전성을 확인하지 못했습니다: "
                f"{type(e).__name__}: {e}")
            result["ok"] = False
    return result
