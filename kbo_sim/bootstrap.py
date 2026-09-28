"""
bootstrap.py
------------
"프로그램 시작하면 데이터를 다운받아 초기 설정하도록 해줘" (구현고려 #5)

데이터는 패키지에 동봉된 kbo_sim/data_snapshot/ 폴더 하나가 유일한 정본(source of truth)이다.
런타임 캐시 폴더 같은 건 따로 두지 않는다 — 헷갈릴 여지를 아예 없애기 위해서다. 실행 시
ensure_data()는 data_snapshot/(또는 명시적으로 넘긴 다른 경로)에 4개 CSV가 있는지 확인하고
바로 load_league_data()로 읽어 메모리에 올린다. 별도의 복사/동기화 단계는 없다.

시즌 데이터를 갱신하려면 `python tools/collect_kbo_data.py`를 실행한다 - KBO 공식
기록실(koreabaseball.com)에서 오늘 기준 최신 데이터를 받아 data_snapshot/ 안에
teams_YYYYMMDD.csv/batters_YYYYMMDD.csv/pitchers_YYYYMMDD.csv/matchup_YYYYMMDD.csv로
저장한다. 같은 접두어로 여러 날짜의 파일이 있으면 resolve_snapshot_files()가 항상
가장 최신 날짜의 파일을 고른다(고정 파일명 teams.csv 등은 날짜 붙은 파일이 하나도
없을 때만 쓰이는 폴백). 실제로 어떤 파일을 읽었는지는 이 함수가 verbose=True일 때
화면에 그대로 출력한다.
"""
from __future__ import annotations

import os

from .data_pipeline import DATA_DIR_DEFAULT, LeagueData, load_league_data, resolve_snapshot_files

SNAPSHOT_DIR = DATA_DIR_DEFAULT  # kbo_sim/data_snapshot/ — 유일한 데이터 정본 위치


def ensure_data(data_dir: str = DATA_DIR_DEFAULT, verbose: bool = True) -> LeagueData:
    files = resolve_snapshot_files(data_dir)
    missing = [name for name, path in files.items() if not os.path.exists(path)]
    if missing:
        raise FileNotFoundError(
            f"'{missing}' 데이터가 {data_dir} 에 없습니다. "
            f"kbo_sim/data_snapshot/ 폴더에 teams/pitchers/batters/matchup 4개 CSV가 "
            f"(고정 파일명이든 teams_20260907.csv 같은 날짜 붙은 파일이든) 있는지 확인하거나, "
            f"tools/collect_kbo_data.py로 새로 수집해 주세요.")
    if verbose:
        print(f"[초기설정] KBO 시즌 데이터 로드 중... ({data_dir})")
        for name, path in files.items():
            print(f"  - {name}: {os.path.basename(path)}")
    league = load_league_data(data_dir)
    if verbose:
        print(f"[초기설정] 완료 - 팀 {len(league.teams_list())}개, "
              f"타자 {len(league.batter_by_pcode)}명, 투수 {len(league.pitcher_by_pcode)}명, "
              f"맞대결 기록 {len(league.matchup_index)}건")
    return league
