"""
server.py
---------
라이브 중계 서버. 브라우저에서 "다음 이닝 진행"을 누를 때마다 실제로 학생 알고리즘을
호출해서 그 하프이닝을 시뮬레이션한다 (미리 다 계산해둔 로그를 재생하는 방식이 아님).

실행:
    python -m kbo_sim.server                      # 브라우저에서 팀/알고리즘 선택
    python -m kbo_sim.server --port 8000
    python -m kbo_sim.server --a-name 김학생 --a-team 삼성 --a-algo submissions/kim.py \
                             --b-name 이학생 --b-team KT   --b-algo submissions/lee.py
    (--a-* / --b-* 를 주면 셋업 화면이 미리 채워진 상태로 열린다)

그다음 브라우저로 http://127.0.0.1:8000 접속.

API
---
GET  /                 라이브 뷰어 HTML
GET  /api/teams        구단 목록(이름/코드/색상/로스터 수)
GET  /api/state        현재 전체 상태 (새로고침 복구용)
GET  /logo/<팀코드>     viewer/logos/<팀코드>.(png|svg|jpg)가 있으면 그 이미지 (없으면 404 ->
                       뷰어가 자체 생성 엠블럼으로 대체)
POST /api/upload       {filename, content, team} 학생 제출 .py 업로드 + 코드 검증
                       (문법/시그니처/난수 규칙/위험 호출 정적검사 + 실제 1회 호출 스모크 테스트)
POST /api/check        {path, team} 이미 올린 파일을 팀만 바꿔서 다시 검증
POST /api/start_series {a:{student_name,team,algo_path}, b:{...}, seed} 3연전 시작
POST /api/next_lineup  1단계: 이닝 시작 시 양 팀 알고리즘 4회 호출 -> 그 이닝의 공격/수비 명단 확정
                       (초/말 공수교대 때는 다시 뽑지 않는다 — 이미 확정된 명단을 그대로 쓴다)
POST /api/play_half    2단계: 확정된 명단으로 다음 하프이닝 공격 진행
POST /api/next_half    (호환) 1+2단계를 한 번에
POST /api/next_game    3연전 중 다음 경기 시작
POST /api/reset        진행 중인 세션을 버리고 처음(팀 선택 화면) 상태로 되돌림

보안 메모: 이 서버는 "학생이 제출한 파이썬 코드를 실행"하는 것이 목적이므로, 기본적으로
127.0.0.1(로컬)에만 바인딩한다. 외부에 노출하지 말 것.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .bootstrap import ensure_data
from .broadcast_export import TEAM_COLORS, _json_safe
from .live_session import SeriesSession
from .match import Contestant
from .student_check import full_check

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(BASE_DIR, "viewer")
LOGO_DIR = os.path.join(VIEWER_DIR, "logos")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

SAFE_NAME = re.compile(r"[^0-9A-Za-z가-힣._-]")


class AppState:
    """서버 전역 상태. 요청 간 경기 세션을 유지한다."""

    def __init__(self, league, timeout_sec=10.0, output_dir="output", preset=None):
        self.league = league
        self.timeout_sec = timeout_sec
        self.output_dir = output_dir
        self.preset = preset or {}
        self.session: Optional[SeriesSession] = None
        self.lock = threading.Lock()


APP: Optional[AppState] = None


class Handler(BaseHTTPRequestHandler):
    server_version = "KBOOwnerGame/1.0"

    # ---------------- 공통 유틸 ----------------
    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, obj, code: int = 200):
        body = json.dumps(_json_safe(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, message: str, code: int = 400):
        self._json({"ok": False, "error": message}, code)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def log_message(self, fmt, *args):  # 콘솔을 조용하게 (에러만 표시)
        if str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)

    # ---------------- GET ----------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_file(os.path.join(VIEWER_DIR, "live_viewer.html"), "text/html; charset=utf-8")
        if path == "/replay":
            return self._serve_file(os.path.join(VIEWER_DIR, "broadcast_viewer.html"),
                                     "text/html; charset=utf-8")
        if path.startswith("/logo/"):
            return self._serve_logo(path[len("/logo/"):])
        if path == "/api/teams":
            return self._api_teams()
        if path == "/api/state":
            return self._api_state()
        return self._error("not found", 404)

    def _serve_file(self, filepath: str, content_type: str):
        if not os.path.exists(filepath):
            return self._error(f"파일이 없습니다: {os.path.basename(filepath)}", 404)
        with open(filepath, "rb") as f:
            self._send(200, f.read(), content_type)

    def _serve_logo(self, code: str):
        code = SAFE_NAME.sub("", code)
        for ext, ctype in ((".svg", "image/svg+xml"), (".png", "image/png"),
                            (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                            (".webp", "image/webp")):
            p = os.path.join(LOGO_DIR, code + ext)
            if os.path.exists(p):
                return self._serve_file(p, ctype)
        return self._error("no custom logo", 404)

    def _api_teams(self):
        league = APP.league
        teams = []
        for _, row in league.teams.iterrows():
            name = row["teamName"]
            primary, secondary = TEAM_COLORS.get(name, ("#333333", "#888888"))
            roster = league.roster_by_team.get(name, {})
            teams.append({
                "name": name, "code": row["teamCode"], "full_name": row["teamFullName"],
                "color": primary, "color2": secondary,
                "counts": {pos: len(v) for pos, v in roster.items()},
            })
        self._json({"ok": True, "teams": teams, "preset": APP.preset,
                    "defaults": {"timeout_sec": APP.timeout_sec}})

    def _api_state(self):
        with APP.lock:
            if APP.session is None:
                return self._json({"ok": True, "started": False, "preset": APP.preset})
            state = APP.session.full_state()
            state.update({"ok": True, "started": True})
            self._json(state)

    # ---------------- POST ----------------
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            payload = self._read_json()
        except Exception as e:
            return self._error(f"요청 본문 파싱 실패: {e}")

        try:
            if path == "/api/upload":
                return self._api_upload(payload)
            if path == "/api/check":
                return self._api_check(payload)
            if path == "/api/start_series":
                return self._api_start_series(payload)
            if path == "/api/next_lineup":
                return self._api_step("lineup")
            if path == "/api/play_half":
                return self._api_step("play")
            if path == "/api/next_half":
                return self._api_step("both")
            if path == "/api/next_game":
                return self._api_next_game()
            if path == "/api/reset":
                return self._api_reset()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._error(f"{type(e).__name__}: {e}", 500)
        return self._error("not found", 404)

    def _api_upload(self, payload):
        filename = SAFE_NAME.sub("_", str(payload.get("filename") or "algo.py"))
        if not filename.endswith(".py"):
            filename += ".py"
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            return self._error("빈 파일입니다.")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        submission_dir = tempfile.mkdtemp(prefix="submission_", dir=UPLOAD_DIR)
        path = os.path.join(submission_dir, filename)
        # newline="": 브라우저가 보낸 content에는 원본 파일의 줄바꿈(\r\n 등)이 이미 문자로
        # 들어있다. 텍스트 모드 기본 동작(newline=None)은 매 \n을 os.linesep으로 다시 바꾸므로,
        # Windows에서 CRLF 원본을 올리면 \r\n이 \r\r\n으로 겹쳐 써져 파일이 깨진다.
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        report = full_check(path, APP.league, payload.get("team"), payload.get("opponent"),
                            APP.timeout_sec)
        report.update({"path": path, "filename": filename})
        # ok=False여도 HTTP 200으로 돌려줘야 화면이 상세 사유를 그릴 수 있다
        self._json({"result": report, "ok": True})

    def _api_check(self, payload):
        path = payload.get("path")
        if not path or not os.path.exists(path):
            return self._error("검사할 파일이 없습니다.")
        report = full_check(path, APP.league, payload.get("team"), payload.get("opponent"),
                            APP.timeout_sec)
        report.update({"path": path, "filename": os.path.basename(path)})
        self._json({"result": report, "ok": True})

    def _api_start_series(self, payload):
        a = payload.get("a") or {}
        b = payload.get("b") or {}
        for label, c in (("A", a), ("B", b)):
            for key in ("student_name", "team", "algo_path"):
                if not c.get(key):
                    return self._error(f"학생 {label}의 '{key}' 값이 비어 있습니다.")
            if not os.path.exists(c["algo_path"]):
                return self._error(f"학생 {label}의 알고리즘 파일을 찾을 수 없습니다: {c['algo_path']}")
        valid_teams = set(APP.league.teams_list())
        for label, c in (("A", a), ("B", b)):
            if c["team"] not in valid_teams:
                return self._error(f"학생 {label}의 팀 '{c['team']}'을(를) 찾을 수 없습니다.")
        if a["team"] == b["team"]:
            return self._error("두 학생이 같은 팀을 선택할 수 없습니다.")

        seed = payload.get("seed")
        seed = int(seed) if seed not in (None, "") else None
        # 제한시간은 화면에서 받지 않고 서버 기본값(--timeout, 기본 10초)을 그대로 쓴다
        timeout = APP.timeout_sec

        with APP.lock:
            APP.session = SeriesSession(
                APP.league,
                Contestant(a["student_name"], a["algo_path"], a["team"]),
                Contestant(b["student_name"], b["algo_path"], b["team"]),
                seed=seed, timeout_sec=timeout, output_dir=APP.output_dir)
            APP.session.start_next_game()
            state = APP.session.full_state()
        state.update({"ok": True, "started": True})
        self._json(state)

    def _api_step(self, mode: str):
        """mode: 'lineup'(1단계 라인업 선발) / 'play'(2단계 공격 진행) / 'both'(한 번에)"""
        with APP.lock:
            if APP.session is None:
                return self._error("아직 경기가 시작되지 않았습니다.")
            if mode == "lineup":
                out = APP.session.prepare_next_inning()
            elif mode == "play":
                out = APP.session.play_prepared_half()
            else:
                out = APP.session.play_next_half()
        out["ok"] = True
        out["phase"] = mode
        self._json(out)

    def _api_next_game(self):
        with APP.lock:
            if APP.session is None:
                return self._error("아직 3연전이 시작되지 않았습니다.")
            meta = APP.session.start_next_game()
            state = APP.session.full_state()
        state.update({"ok": True, "started": True, "game": meta})
        self._json(state)

    def _api_reset(self):
        with APP.lock:
            APP.session = None
        self._json({"ok": True})


def main(argv=None):
    global APP
    ap = argparse.ArgumentParser(description="너, 내 구단주가 돼라 - 라이브 중계 서버")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--out", default="output")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--a-name", default=None)
    ap.add_argument("--a-team", default=None)
    ap.add_argument("--a-algo", default=None)
    ap.add_argument("--b-name", default=None)
    ap.add_argument("--b-team", default=None)
    ap.add_argument("--b-algo", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    league = ensure_data(args.data_dir) if args.data_dir else ensure_data()

    preset = {}
    if args.a_name or args.a_team or args.a_algo:
        preset["a"] = {"student_name": args.a_name, "team": args.a_team,
                        "algo_path": os.path.abspath(args.a_algo) if args.a_algo else None}
    if args.b_name or args.b_team or args.b_algo:
        preset["b"] = {"student_name": args.b_name, "team": args.b_team,
                        "algo_path": os.path.abspath(args.b_algo) if args.b_algo else None}
    if args.seed is not None:
        preset["seed"] = args.seed

    APP = AppState(league, timeout_sec=args.timeout, output_dir=args.out, preset=preset)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"\n[라이브 서버] {url} 에서 실행 중 (Ctrl+C로 종료)")
    print("   브라우저에서 위 주소를 열고 팀/알고리즘을 지정한 뒤 '다음 이닝 진행'을 누르세요.\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[라이브 서버] 종료")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
