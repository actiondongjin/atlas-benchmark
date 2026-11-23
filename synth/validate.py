"""완성된 데이터 폴더 검사와 통계.

  uv run python synth/validate.py data/luna2

검사: 일상 대화가 금지 패턴을 건드리는지, 질문의 asked_after가 존재하는지, 정답 근거 메시지의 상대 순서가 사양과 같은지,
답글이 원글보다 늦고 같은 채널인지, 시간순 정렬인지, 질문 라벨과 문구 규칙. 결과는 build/report.json으로도 씀."""
import json, os, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_scenario, load_jsonl, violations, approx_tokens, build_dir


def check(messages, tags, scn):
    """규칙 위반 목록을 돌려줌. 시간순, 정답 근거 순서, 답글 시각, 채널, 일상 대화의 금지 패턴, 정답 근거 주말 여부, 질문의 asked_after 존재, 질문 문구 규칙(3인칭 자기 이름, 정답이 질문에 포함, 정답, 금지 겹침)"""
    errs = []
    ids = [m["id"] for m in messages]
    pos = {i: k for k, i in enumerate(ids)}
    if len(ids) != len(set(ids)):
        errs.append(("dup_id", "메시지 id 중복"))
    for a, b in zip(messages, messages[1:]):
        if a["time"] > b["time"]:
            errs.append((b["id"], "시간순 아님"))
    core_ids = [c["id"] for c in scn["core"]]
    if [i for i in ids if i in set(core_ids)] != core_ids:
        errs.append(("core_order", "정답 근거 메시지 순서가 사양과 다름"))
    for m in messages:
        tg = tags.get(m["id"], [])
        if m["reply_to"]:
            if m["reply_to"] not in pos:
                errs.append((m["id"], "reply_to 대상 없음"))
            else:
                r = messages[pos[m["reply_to"]]]
                if r["time"] > m["time"] or r["channel"] != m["channel"]:
                    errs.append((m["id"], "답글이 원글보다 앞이거나 다른 채널"))
        if "basis" in tg:
            continue
        v = violations(m["text"], scn["forbidden_compiled"])
        if v:
            errs.append((m["id"], "금지 패턴 " + ",".join(v)))
    from datetime import date
    for c in scn["core"]:
        if date.fromisoformat(c["time"][:10]).weekday() >= 5:
            errs.append((c["id"], "정답 근거가 주말에 있음"))
    have = set(ids)
    import re as _re
    """문자열 대조용 정규화. 소문자로 바꾸고 영숫자와 공백만 남김"""
    def _norm(s): return _re.sub(r"[^a-z0-9 ]", " ", s.lower())
    def _has(a, p):
        """단어 경계 기준 포함 여부"""
        p = _re.escape(_norm(p).strip()); return _re.search(r"(?<![a-z0-9])" + p + r"(?![a-z0-9])", _norm(a)) is not None
    for q in scn["questions"]:
        if q["asked_after"] not in have:
            errs.append((q["id"], "asked_after 없음"))
        if _re.search(r"\b" + q["asker"] + r"\b", q["question"]):
            errs.append((q["id"], "질문자가 자기 이름을 3인칭으로 씀"))
        for g in q["gold"]:
            if _norm(g).strip() not in ("yes", "no") and _has(q["question"], g):
                errs.append((q["id"], f"정답 별칭 '{g}'이 질문 문장에 그대로 있음(되받으면 정답)"))
            for f in q["forbidden"]:
                if _has(g, f) or _has(f, g):
                    errs.append((q["id"], f"정답 '{g}'과 금지 '{f}'가 겹침"))
        # 라벨 값. type(Question Type) 하나, access(Asker Access) 하나, qtags(Question Pattern) 여럿
        if q["type"] not in TYPES: errs.append((q["id"], f"type '{q['type']}'는 {TYPES} 중 하나여야 함"))
        if q.get("access") not in ACCESS: errs.append((q["id"], f"access '{q.get('access')}'는 {ACCESS} 중 하나여야 함"))
        for t in q.get("qtags", []):
            if t not in PATTERNS: errs.append((q["id"], f"qtags '{t}'는 {PATTERNS} 중 하나여야 함"))
        if "unprivileged_probe" in q.get("qtags", []) and q.get("access") != "unprivileged": errs.append((q["id"], "unprivileged_probe는 access가 unprivileged여야 함"))
        if "privileged_probe" in q.get("qtags", []) and q.get("access") != "privileged": errs.append((q["id"], "privileged_probe는 access가 privileged여야 함"))
        # forbidden은 질문자가 볼 수 없는 제한된 사실만. 그래서 forbidden이 있으면 unprivileged여야 함. 옛 값, 다른 사안 값은 stale_values에
        if not isinstance(q.get("stale_values"), list): errs.append((q["id"], "stale_values 목록이 없음(빈 목록이라도 있어야 함)"))
        if q["forbidden"] and q.get("access") != "unprivileged": errs.append((q["id"], "forbidden이 있으면 access가 unprivileged여야 함. 옛 값이면 stale_values로"))
        for g in q["gold"]:
            for f in q.get("stale_values", []):
                if _has(g, f) or _has(f, g):
                    errs.append((q["id"], f"정답 '{g}'과 옛 값 '{f}'가 겹침"))
    return errs


NEG_HINTS = ("not ", "none", "nothing", "unknown", "don't", "hasn't", "haven't", "unclear", "undecided", "can't", "cannot")
# 질문 분포 상한. 넘으면 경고만 냄(실패 아님). 값은 v0.1 설계 목표
DIST_CAPS = {"unprivileged_share_max": 0.35, "stance_share_min": 0.10, "asker_share_min": 0.15, "asker_share_max": 0.25,
             "negative_gold_max": 0.30, "paraphrase_min": 0.12, "paraphrase_max": 0.20, "arc_share_max": 0.20}
TYPES = ("current_state", "personal_stance", "history")           # Question Type. 질문마다 하나
ACCESS = ("privileged", "unprivileged", "unrestricted")           # Asker Access. 질문마다 하나
PATTERNS = ("paraphrase", "cross_channel", "unprivileged_probe", "privileged_probe", "public_line", "decoy", "provenance", "update_chain")  # Question Pattern. 여러 개 가능


def negative_gold(q):
    """정답 후보가 부정, 회피형인지(모른다고 해도 맞는 질문). 접근 유형을 권한자, 비권한자로 나눌 때 씀"""
    return any(g.strip().lower() == "no" or any(h in g.lower() for h in NEG_HINTS) for g in q["gold"])


def distribution_warnings(questions):
    """질문 분포가 DIST_CAPS를 벗어나면 경고 문자열 목록을 돌려줌. 유형, 질문자, 부정형, 패러프레이즈, 사안 편중"""
    import re as _re
    n = max(1, len(questions)); w = []
    types = Counter(q["type"] for q in questions); acc = Counter(q.get("access") for q in questions)
    if acc["unprivileged"] / n > DIST_CAPS["unprivileged_share_max"]: w.append(f"Unprivileged 질문 비율 {acc['unprivileged']/n:.0%} > {DIST_CAPS['unprivileged_share_max']:.0%}")
    if types["personal_stance"] / n < DIST_CAPS["stance_share_min"]: w.append(f"Personal Stance 비율 {types['personal_stance']/n:.0%} < {DIST_CAPS['stance_share_min']:.0%}")
    for a, c in Counter(q["asker"] for q in questions).items():
        if not DIST_CAPS["asker_share_min"] <= c / n <= DIST_CAPS["asker_share_max"]: w.append(f"질문자 {a} 비율 {c/n:.0%}")
    neg = sum(1 for q in questions if negative_gold(q)) / n
    if neg > DIST_CAPS["negative_gold_max"]: w.append(f"부정형 정답 비율 {neg:.0%} > {DIST_CAPS['negative_gold_max']:.0%}")
    para = sum(1 for q in questions if "paraphrase" in q.get("qtags", [])) / n
    if not DIST_CAPS["paraphrase_min"] <= para <= DIST_CAPS["paraphrase_max"]: w.append(f"패러프레이즈 비율 {para:.0%}")
    arcs = Counter(_re.match(r"([A-Z]+)", q["asked_after"]).group(1) for q in questions)
    for a, c in arcs.items():
        if c / n > DIST_CAPS["arc_share_max"]: w.append(f"사안 {a} 질문 비중 {c/n:.0%} > {DIST_CAPS['arc_share_max']:.0%}")
    return w


def stats(messages, tags, questions):
    """데이터 통계 dict. 메시지, 근거, 유사 사안, 일상 대화 수, 답글, 스레드 수, 단어, 토큰, 채널별 수, 교란 수와 비율, 질문 수, 유형, 질문자 변형 세트 수"""
    n = len(messages)
    basis = sum(1 for m in messages if "basis" in tags.get(m["id"], []) and "distractor" not in tags.get(m["id"], []))
    distractor = sum(1 for m in messages if "distractor" in tags.get(m["id"], []))
    replies = sum(1 for m in messages if m["reply_to"])
    words = sum(len(m["text"].split()) for m in messages)
    pert = Counter()
    for m in messages:
        for t in tags.get(m["id"], []):
            if t.startswith("perturb:disagreement"):
                pert["disagreement"] += 1
            elif t.startswith("perturb:noise"):
                pert["noise"] += 1
            elif t.startswith("perturb:style"):
                pert["style"] += 1
            elif t.startswith("decision_change"):
                pert["decision_change"] += 1
            elif t.startswith("fallback"):
                pert["fallback_template"] += 1
    by_ch = Counter(m["channel"] for m in messages)
    threads = len({m["id"] for m in messages if not m["reply_to"]})
    tok = None
    try:
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        tok = {"text": sum(len(enc.encode(m["text"])) for m in messages),
               "transcript_lines": sum(len(enc.encode(f"[{m['time']}] {m['channel']} {m['speaker']}: {m['text']}")) for m in messages)}
    except Exception:
        pass
    return {"messages": n, "basis": basis, "distractor": distractor, "filler": n - basis - distractor, "replies": replies, "threads": threads, "words": words,
            "approx_tokens": approx_tokens(words), "tokens_o200k": tok, "by_channel": dict(by_ch),
            "perturbations": dict(pert),
            "ratios": {"noise_of_all": round(pert["noise"] / n, 3), "disagreement_of_replies": round(pert["disagreement"] / max(1, replies), 3),
                       "style_of_all": round(pert["style"] / n, 3)},
            "questions": len(questions), "question_types": dict(Counter(q["type"] for q in questions)),
            "pairs": len({q["pair"] for q in questions if q["pair"]})}


def main(data_dir):
    """데이터 폴더를 검사하고 build/report.json을 씀. 위반이 있으면 1을 반환"""
    scn = load_scenario()
    messages = load_jsonl(os.path.join(data_dir, "messages.jsonl"))
    tags = {r["id"]: r["tags"] for r in load_jsonl(os.path.join(data_dir, "message_tags.jsonl"))}
    questions = load_jsonl(os.path.join(data_dir, "questions.jsonl"))
    errs = check(messages, tags, scn)
    st = stats(messages, tags, questions)
    warns = distribution_warnings(questions)
    report = {"ok": not errs, "errors": errs, "warnings": warns, "stats": st}
    with open(os.path.join(build_dir(data_dir), "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print(f"validate {data_dir}: {'OK' if not errs else str(len(errs)) + ' errors'}")
    for e in errs[:20]:
        print("  ", e)
    tk = st.get("tokens_o200k")
    tok_s = f"tokens(o200k) text {tk['text']:,} / transcript {tk['transcript_lines']:,}" if tk else f"approx tokens {st['approx_tokens']:,} (tiktoken 없음)"
    print(f"  messages {st['messages']} (basis {st['basis']}, distractor {st['distractor']}, filler {st['filler']}) | replies {st['replies']} | threads {st['threads']} | words {st['words']} | {tok_s}")
    print(f"  ratios {st['ratios']} | perturbations {st['perturbations']}")
    print(f"  questions {st['questions']} {st['question_types']} | pairs {st['pairs']}")
    for w in warns:
        print("  분포 경고:", w)
    return 0 if not errs else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용: python synth/validate.py data/<이름>")
    sys.exit(main(sys.argv[1]))
