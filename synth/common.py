"""공용 로더와 검사 유틸. 사양(scenario/)은 손으로 쓴 고정 자료임.
데이터 폴더는 data/<이름>/ 이고, 그 안의 build/ 에 구축 기록(계획, 일상 대화 원본, audit 결과, 통계)을 둠.
LLM 응답 캐시는 .cache/, 호출 로그와 실행 요약은 runs/ 에 쌓임(둘 다 git에 올리지 않음)."""
import json, os, re, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 리포 루트
SCEN = os.path.join(ROOT, "scenario")
CACHE = os.path.join(ROOT, ".cache")                                  # LLM 응답 캐시
RUNS = os.path.join(ROOT, "runs")                                     # 호출 로그와 실행 요약
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_client import extract_json                                   # 생성과 audit이 같은 JSON 파서를 씀


def build_dir(data_dir):
    """데이터 폴더의 구축 기록 폴더(data/<이름>/build). 없으면 만듦"""
    p = os.path.join(data_dir, "build")
    os.makedirs(p, exist_ok=True)
    return p


def load_jsonl(path):
    """jsonl 파일을 dict 목록으로 읽음"""
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def dump_jsonl(path, rows):
    """dict 목록을 jsonl로 씀. 폴더가 없으면 만듦"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_json(path):
    """json 파일을 읽음"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_scenario(scen=SCEN):
    """scenario/ 폴더의 사양 전체를 dict로 읽음. personas, channels, core(정답 근거, 유사 사안 메시지), questions, topics(일상 대화 주제), forbidden(금지 규칙), forbidden_compiled(컴파일된 정규식 목록)"""
    s = {
        "personas": load_json(f"{scen}/personas.json"),
        "channels": load_json(f"{scen}/channels.json"),
        "core": load_jsonl(f"{scen}/core_messages.jsonl"),
        "questions": load_jsonl(f"{scen}/questions.jsonl"),
        "topics": {k: v for k, v in load_json(f"{scen}/topics.json").items() if not k.startswith("_")},
        "forbidden": load_json(f"{scen}/forbidden.json"),
    }
    s["forbidden_compiled"] = [(p["name"], re.compile(p["re"], re.I)) for p in s["forbidden"]["patterns"]]
    return s


def violations(text, compiled):
    """텍스트가 건드리는 금지 패턴 이름 목록"""
    return [name for name, rx in compiled if rx.search(text)]


def parse_time(s):
    """'YYYY-MM-DDTHH:MM' 문자열을 datetime으로"""
    return datetime.fromisoformat(s)


def fmt_time(dt):
    """datetime을 'YYYY-MM-DDTHH:MM' 문자열로"""
    return dt.strftime("%Y-%m-%dT%H:%M")


def approx_tokens(words):
    """단어 수로 토큰 수를 대략 추정(×1.35). tiktoken이 없을 때만 씀"""
    return int(words * 1.35)

