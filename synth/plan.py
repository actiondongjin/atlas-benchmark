"""일상 대화 계획. 어느 날 어느 채널에 누가 어떤 구조(원글, 답글 역할, 문체 결함, 주제 이탈)로 말하는지를 seed로 정함.
요일, 시간대 가중치로 굴곡을 주고, 정답 근거 원글 근처에는 같은 채널 일상 대화 스레드를 하나 더 둬서 근거가 잡음 없이 뭉치지 않게 함.
텍스트는 여기서 만들지 않음. 렌더러(render_llm.py)가 이 계획을 문장으로 옮김.
교란 비율은 GroupMemBench의 설정을 따름: noise 5%, disagreement 답글의 25%, style 20%.
스레드가 대화로 읽히게 하는 규칙(luna2부터): 주제 이탈은 원글에만 둠. 답글 화자는 스레드 안에서 겹치지 않게 뽑고 멤버가 모자랄 때만 반복함.
반대는 스레드에 한 사람만 하고, 같은 사람이 다시 말할 때 입장이 바뀌지 않음. 질문 뒤에 답글이 이어지면 그 답글은 질문에 답하는 역할(answer)임."""
import random
from datetime import datetime, timedelta

from common import fmt_time

START, END = datetime(2026, 9, 1), datetime(2026, 10, 9)  # 기본값. make_plan이 시나리오의 첫, 끝 정답 근거 날짜로 덮어씀
# 하루, 채널당 원글 기대 수 (scale 1.0 기준)
RATE = {"#atlas-general": 3.0, "#model-dev": 4.0, "#platform-ops": 4.0, "#leadership": 1.0,
        "dm:Alex-Priya": 1.5, "dm:Alex-Jordan": 1.2, "dm:Mina-Priya": 1.0,
        "dm:Alex": 0.4, "dm:Priya": 0.4, "dm:Jordan": 0.3, "dm:Mina": 0.3, "dm:Sofia": 0.2,
        "#partners": 1.0, "dm:Mina-Alex": 0.8, "dm:Mina-Jordan": 0.5, "dm:Priya-Jordan": 0.8, "dm:Alex-Jordan-Priya": 1.0}
NOISE, DISAGREE, QUESTION, ULTRASHORT, STYLE = 0.05, 0.33, 0.15, 0.15, 0.20   # DISAGREE는 추첨 확률. 스레드당 한 사람, 입장 유지 규칙을 거치면 답글의 약 25%가 됨
REPLY_CONTINUE, MAX_REPLIES = 0.55, 4
ANSWER_P = 0.85   # 스레드가 질문으로 끝날 때 답글을 하나 더 붙일 확률. 나머지는 열린 질문으로 남음
# 요일 굴곡(월요일이 바쁘고 금요일이 조용함)과 시간대 굴곡(점심 전후 가장 적고, 오전, 오후에 피크)
WEEKDAY_W = {0: 1.15, 1: 1.10, 2: 1.00, 3: 0.95, 4: 0.80}
HOUR_W = {9: 1.0, 10: 1.3, 11: 1.3, 12: 0.5, 13: 0.8, 14: 1.2, 15: 1.2, 16: 1.0, 17: 0.7}
# 정답 근거 원글 근처(같은 채널, ±25분)에 일상 대화 스레드를 하나 더 둘 확률. 근거가 잡음 없이 뭉치는 것을 줄임
NEAR_BASIS_P = 0.8
# 정답 주제를 지나가며 언급(사실 없이)하는 스레드 비율과, 다른 채널을 이름으로 참조하는 스레드 비율
TOUCH_P, XREF_P = 0.25, 0.10
# 연속한 정답 근거 두 개가 30분 안에 붙어 있으면 그 사이에 일상 대화 원글을 하나 둘 확률(같은 채널 또는 공개 채널)
BETWEEN_BASIS_P = 0.7
STYLES = ["typo", "fragment", "selfcorrection", "lowercase"]


def workdays(start=None, end=None):
    """start부터 end까지 평일 날짜를 차례로 냄. 기본은 START, END"""
    d = start or START
    end = end or END
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def n_from_rate(rng, r):
    """기대값 r인 정수 표본. 정수부는 확정, 소수부는 그 확률로 1을 더함"""
    k = int(r)
    return k + (1 if rng.random() < r - k else 0)


def pick_hour(rng):
    """HOUR_W 가중치로 시(9~17)를 뽑음"""
    hours = list(HOUR_W)
    return rng.choices(hours, weights=[HOUR_W[h] for h in hours])[0]


def make_plan(scn, seed, scale):
    """일상 대화 스레드 계획 목록을 만듦. 각 스레드는 채널, 멤버, 날짜, 주제, near_basis(정답 근거 근처 여부), touch(사실 없이 언급할 정답 주제), xref(참조할 다른 채널), 메시지 목록(k, speaker, role, style, time). 같은 seed, scale이면 결과가 같음"""
    rng = random.Random(seed)
    channels, topics = scn["channels"], scn["topics"]
    threads, n = [], 0
    # 기간은 정답 근거의 첫 날 하루 전부터 마지막 날까지. 시나리오를 늘리면 일상 대화도 같이 늘어남
    core_days = sorted(datetime.fromisoformat(c["time"]).replace(hour=0, minute=0) for c in scn["core"])
    start, end = core_days[0] - timedelta(days=1), core_days[-1]
    # 스레드 시작 시각 목록: (시각, 채널, 근거 근처 여부)
    starts = []
    for day in workdays(start, end):
        for ch, rate in RATE.items():
            if ch not in channels:
                continue
            for _ in range(n_from_rate(rng, rate * scale * WEEKDAY_W[day.weekday()])):
                starts.append((day.replace(hour=pick_hour(rng), minute=rng.randint(0, 59)), ch, False))
    core_sorted = sorted(scn["core"], key=lambda c: c["time"])
    public = [c for c in channels if not c.startswith("dm:")]
    for a, b in zip(core_sorted, core_sorted[1:]):
        ta, tb = datetime.fromisoformat(a["time"]), datetime.fromisoformat(b["time"])
        gap_min = (tb - ta).total_seconds() / 60
        if 0 < gap_min <= 30 and rng.random() < BETWEEN_BASIS_P:
            t = ta + timedelta(minutes=max(1, int(gap_min * rng.uniform(0.3, 0.7))))
            chn = a["channel"] if rng.random() < 0.5 and not a["channel"].startswith("dm:") else rng.choice(public)
            starts.append((t, chn, True))
    for c in scn["core"]:
        if c["reply_to"] is None and rng.random() < NEAR_BASIS_P:
            t = datetime.fromisoformat(c["time"]) + timedelta(minutes=rng.randint(-25, 25))
            t = max(t, t.replace(hour=8, minute=30)); t = min(t, t.replace(hour=18, minute=30))
            starts.append((t, c["channel"], True))
    starts.sort(key=lambda z: (z[0], z[1]))
    for t0, ch, near in starts:
        members = channels[ch]
        day = t0
        n += 1
        root_sp = rng.choice(members)
        msgs = [{"k": 0, "speaker": root_sp, "role": "post", "style": [], "time": fmt_time(t0)}]
        if rng.random() < NOISE:
            msgs[0]["role"] = "drift"
        elif rng.random() < STYLE:
            msgs[0]["style"] = [rng.choice(STYLES)]
        others = [m for m in members if m != root_sp]
        pool = others[:]                              # 아직 답하지 않은 사람 목록. 답글 화자는 여기서 먼저 뽑고, 직전 화자와는 겹치지 않게 함
        stance = {}                                   # 화자별 입장(agree/disagree). 같은 사람이 다시 말할 때 입장을 지킴
        disagreer = None                              # 반대는 스레드에 한 사람만
        t, k = t0, 0
        while others and k < MAX_REPLIES and rng.random() < REPLY_CONTINUE:
            k += 1
            t = t + timedelta(minutes=rng.randint(2, 90))
            prev = msgs[-1]
            if prev["role"] == "question":            # 질문 다음 답글은 그 질문에 답함. 원글 작성자가 답하는 것이 자연스러움
                role = "answer"
                cands = [root_sp] if root_sp != prev["speaker"] else [m for m in members if m != prev["speaker"]]
                sp = rng.choice(cands)
            else:
                cands = [m for m in pool if m != prev["speaker"]]          # 아직 안 말한 사람 우선
                if not cands:
                    cands = [m for m in members if m != prev["speaker"]]   # 다 말했으면 직전 화자만 빼고 아무나(원글 작성자 포함). DM은 둘이 번갈아 말하게 됨
                sp = rng.choice(cands)
                if sp in pool:
                    pool.remove(sp)
                r = rng.random()                      # 주제 이탈(NOISE)은 원글에만 두므로 답글 역할은 넷 중 하나
                if r < DISAGREE:
                    role = "disagree"
                elif r < DISAGREE + QUESTION:
                    role = "question"
                elif r < DISAGREE + QUESTION + ULTRASHORT:
                    role = "ultrashort"
                else:
                    role = "agree"
                if role == "disagree" and disagreer not in (None, sp):
                    role = "agree"                    # 이미 다른 사람이 반대함
                if sp in stance and role in ("agree", "disagree") and stance[sp] != role:
                    role = stance[sp]                 # 입장 유지. 동의했던 사람이 뒤에서 반대하지 않음
                if sp == root_sp and role in ("disagree", "ultrashort"):
                    role = "agree"                    # 원글 작성자가 다시 말할 때는 이어 가는 말만
                if role == "disagree":
                    disagreer = sp
                if role in ("agree", "disagree"):
                    stance[sp] = role
            style = [rng.choice(STYLES)] if role in ("agree", "disagree", "question", "answer") and rng.random() < STYLE else []
            msgs.append({"k": k, "speaker": sp, "role": role, "style": style, "time": fmt_time(t)})
        if msgs[-1]["role"] == "question" and len(msgs) > 1:
            # 스레드가 질문으로 끝나면 대개 답이 붙음. 주 난수열을 건드리지 않도록 스레드 번호로 만든 별도 난수를 써서, 다른 스레드의 계획은 그대로 둠
            rng2 = random.Random(n * 7919 + 3)
            if rng2.random() < ANSWER_P:
                asker = msgs[-1]["speaker"]
                cands = [root_sp] if root_sp != asker else [m for m in members if m != asker]
                t = t + timedelta(minutes=rng2.randint(2, 60))
                msgs.append({"k": len(msgs), "speaker": rng2.choice(cands), "role": "answer", "style": [], "time": fmt_time(t)})
        touch = rng.choice(scn["forbidden"]["touch_topics"]) if rng.random() < TOUCH_P else None
        others_ch = [c for c in channels if c != ch and not c.startswith("dm:") and set(channels[c]) & set(members)]
        xref = rng.choice(others_ch) if others_ch and rng.random() < XREF_P else None
        threads.append({"thread_id": f"T{n:05d}", "channel": ch, "members": members, "date": day.strftime("%Y-%m-%d"),
                                "topic": rng.choice(topics[ch]), "near_basis": near, "touch": touch, "xref": xref, "messages": msgs})
    return threads


def tags_for(msg, thread):
    """계획의 메시지 하나에 붙일 라벨 목록(filler, reply, role:*, near_basis, touch, xref, perturb:*)"""
    tags = ["filler"] + (["near_basis"] if thread.get("near_basis") else [])
    if msg["k"] == 0:
        if thread.get("touch"): tags.append("touch")
        if thread.get("xref"): tags.append("xref")
    if msg["k"] > 0:
        tags.append("reply")
        tags.append("role:" + msg["role"])           # 분석용. agree, disagree, question, answer, ultrashort
    role = msg["role"]
    if role == "disagree":
        tags.append("perturb:disagreement")
    elif role == "drift":
        tags.append("perturb:noise:drift")
    elif role == "ultrashort":
        tags.append("perturb:style:ultrashort")
    for s in msg["style"]:
        tags.append("perturb:style:" + s)
    return tags
