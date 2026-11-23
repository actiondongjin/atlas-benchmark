"""일상 대화 품질 지표. validate.py가 규칙 위반을 보는 반면, 여기는 사람이 검수할 때 보던 것을 숫자로 냄.

  uv run python synth/quality.py data/luna2
  uv run python synth/quality.py data/<새 이름> --compare data/luna2

지표: 길이, 식별자 다양성(예시 id 복사 여부), 화자 자기 이름 언급, 채널 밖 사람 직접 호명, 주제 이탈 메시지의 다양성,
완전 중복 문장, 화자별 평균 길이(페르소나 분리), 재요청, 대체 스레드와 그 이유(render_meta.jsonl), 포함 여부가 애매한 단어들."""
import argparse, json, os, re, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_jsonl, load_json, SCEN, build_dir

NAMES = ["Mina", "Sofia", "Alex", "Priya", "Jordan"]
BLAND = re.compile(r"rain|weather|lunch|coffee|commute", re.I)


def metrics(data_dir):
    """데이터 폴더 하나의 일상 대화 품질 지표를 dict로 계산. 스레드 대화 성립(답글 자리 주제 이탈, 반복 화자, 입장 뒤집힘, 답 없는 질문), 길이, 식별자 다양성, 자기 이름 언급, 채널 밖 호명, 주제 이탈 다양성, 중복, 근거 간격, 요일 분포, 정답 주제 어휘 빈도, 화자별 길이, 소문자 비율, 재요청 이유"""
    M = load_jsonl(os.path.join(data_dir, "messages.jsonl"))
    T = {r["id"]: r["tags"] for r in load_jsonl(os.path.join(data_dir, "message_tags.jsonl"))}
    ch = load_json(os.path.join(data_dir, "channels.json")) if os.path.exists(os.path.join(data_dir, "channels.json")) else load_json(f"{SCEN}/channels.json")
    F = [m for m in M if "filler" in T[m["id"]]]
    if not F:
        return {"filler": 0}
    words = [len(m["text"].split()) for m in F]
    tickets = Counter(x for m in F for x in re.findall(r"ATL-\d+", m["text"]))
    prs = Counter(x for m in F for x in re.findall(r"#\d{3,4}\b", m["text"]))
    selfref = sum(1 for m in F if re.search(r"\b" + m["speaker"] + r"\b", m["text"]))
    address = sum(1 for m in F for n in NAMES if n not in ch[m["channel"]] and re.search(r"(^|[.!?]\s+|,\s*)" + n + r"[,:]", m["text"]))
    drift = [m["text"] for m in F if "perturb:noise:drift" in T[m["id"]]]
    non_short = [m["text"].lower() for m in F if "perturb:style:ultrashort" not in T[m["id"]]]
    dup = sum(c - 1 for c in Counter(non_short).values() if c > 1)
    per_sp = defaultdict(list)
    for m in F:
        if not m["reply_to"]:
            per_sp[m["speaker"]].append(len(m["text"].split()))
    lower_start = {s: sum(1 for m in F if m["speaker"] == s and m["text"][:1].islower()) / max(1, sum(1 for m in F if m["speaker"] == s)) for s in NAMES}
    fallback = sum(1 for m in F if "fallback:template" in T[m["id"]])
    meta_p = os.path.join(build_dir(data_dir), "render_meta.jsonl")
    retried, reasons = 0, Counter()
    if os.path.exists(meta_p):
        for r in load_jsonl(meta_p):
            retried += 1
            for b in r.get("violations", []):
                for ks in b.values():
                    reasons.update(ks)
    renderer = next((t for t in T[F[0]["id"]] if t.startswith("renderer:")), "?")
    idx = [i for i, m in enumerate(M) if "basis" in T[m["id"]] and "distractor" not in T[m["id"]]]
    topic_rx = {"launch": r"\blaunch", "reranker": r"reranker", "gate/threshold": r"threshold|quality gate|regression gate", "standup": r"stand-?up",
                "migration": r"migration", "freeze": r"\bfreeze", "review": r"model review|design review", "hiring": r"hiring|candidate|interview",
                "credential": r"rotat|credential|\bkey\b|token", "time off": r"time off|vacation|\bpto\b|day off", "demo": r"\bdemo", "gpu": r"\bgpu|h100", "collection": r"collection", "bootcamp": r"bootcamp", "cutover": r"cutover", "retro": r"\bretro", "reindex": r"reindex", "headcount": r"headcount", "blog": r"\bblog", "dashboard": r"dashboard"}
    topic_hits = {k: sum(1 for m in F if re.search(rx, m["text"], re.I)) for k, rx in topic_rx.items()}
    touch = sum(1 for m in F if "touch" in T[m["id"]]); xref = sum(1 for m in F if "xref" in T[m["id"]])
    gaps = sorted(b - a - 1 for a, b in zip(idx, idx[1:]))
    gap_median = gaps[len(gaps) // 2] if gaps else 0
    from datetime import date
    wd = Counter(date.fromisoformat(m["time"][:10]).weekday() for m in M)
    n_days = Counter(date.fromisoformat(m["time"][:10]).weekday() for m in {m["time"][:10]: m for m in M}.values())
    per_wd = {"월화수목금"[d]: round(wd[d] / n_days[d]) for d in sorted(wd) if d < 5}
    near = sum(1 for m in F if "near_basis" in T[m["id"]])
    # 스레드가 대화로 읽히는지. 답글 자리의 주제 이탈, 같은 사람의 반복 답글, 동의와 반대 공존, 답 없는 질문(role: 라벨이 있는 데이터에서만)
    th = defaultdict(list)
    for m in F:
        if m["reply_to"]:
            th[m["reply_to"]].append(m)
    drift_in_reply = sum(1 for rs in th.values() for m in rs if "perturb:noise:drift" in T[m["id"]])
    repeat_speaker = sum(1 for rs in th.values() if max(Counter(m["speaker"] for m in rs).values()) > 1)
    def stance(m):
        tg = T[m["id"]]
        return "disagree" if "perturb:disagreement" in tg else ("agree" if "role:agree" in tg or (not any(t.startswith("role:") for t in tg) and "perturb:style:ultrashort" not in tg) else None)
    flip = 0
    for rs in th.values():
        by = defaultdict(set)
        for m in rs:
            st = stance(m)
            if st: by[m["speaker"]].add(st)
        flip += any(len(v) > 1 for v in by.values())
    has_roles = any(t.startswith("role:") for m in F for t in T[m["id"]])
    unanswered = None
    if has_roles:
        unanswered = 0
        for rs in th.values():
            rs = sorted(rs, key=lambda m: m["time"])
            for i, m in enumerate(rs):
                if "role:question" in T[m["id"]] and not (i + 1 < len(rs) and "role:answer" in T[rs[i + 1]["id"]]):
                    unanswered += 1
    return {
        "threads_with_replies": len(th), "drift_in_reply": drift_in_reply, "repeat_speaker_threads": repeat_speaker,
        "stance_flip_threads": flip, "unanswered_questions": unanswered,
        "renderer": renderer, "filler": len(F), "avg_words": round(sum(words) / len(words), 1), "max_words": max(words),
        "ticket_kinds": len(tickets), "ticket_top_share": round(tickets.most_common(1)[0][1] / max(1, sum(tickets.values())), 2) if tickets else 0,
        "pr_kinds": len(prs), "self_name_mentions": selfref, "nonmember_address": address,
        "drift": len(drift), "drift_unique": len({d.lower() for d in drift}), "drift_bland_share": round(sum(1 for d in drift if BLAND.search(d)) / max(1, len(drift)), 2),
        "exact_dups_excl_ultrashort": dup, "fallback_msgs": fallback, "retried_threads": retried,
        "basis_gap_median": gap_median, "basis_gap_zero_pairs": sum(1 for g in gaps if g == 0), "basis_gap_max": gaps[-1] if gaps else 0,
        "near_basis_filler": near, "touch_posts": touch, "xref_posts": xref, "topic_word_filler": topic_hits,
        "retry_reasons": dict(reasons), "msgs_per_weekday": per_wd,
        "post_words_by_speaker": {s: round(sum(v) / len(v), 1) for s, v in sorted(per_sp.items())},
        "lowercase_start_share": {s: round(v, 2) for s, v in lower_start.items()},
    }


def main():
    """지표를 표로 출력하고 build/quality.json으로 저장. --compare로 두 폴더를 나란히"""
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--compare", default=None, help="나란히 볼 다른 데이터 폴더")
    a = ap.parse_args()
    cols = [(a.data_dir, metrics(a.data_dir))] + ([(a.compare, metrics(a.compare))] if a.compare else [])
    keys = [k for k in cols[0][1] if not isinstance(cols[0][1][k], dict)]
    w = max(len(k) for k in keys) + 2
    print(" " * w + "".join(f"{os.path.basename(os.path.normpath(d)):>22}" for d, _ in cols))
    for k in keys:
        print(f"{k:<{w}}" + "".join(f"{str(m.get(k, '')):>22}" for _, m in cols))
    for k in ("retry_reasons", "msgs_per_weekday", "topic_word_filler", "post_words_by_speaker", "lowercase_start_share"):
        for d, m in cols:
            print(f"{k} [{os.path.basename(os.path.normpath(d))}]: {json.dumps(m.get(k, {}), ensure_ascii=False)}")
    with open(os.path.join(build_dir(a.data_dir), "quality.json"), "w", encoding="utf-8") as f:
        json.dump(cols[0][1], f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
