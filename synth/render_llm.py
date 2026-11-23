"""LLM 렌더러. 계획(plan)의 스레드 하나를 chat completion 한 번으로 문장화함.
페르소나 카드, 역할 지시(원글/동의/질문/답변/초단답/반대/주제 이탈 원글), 문체 결함 지시를 프롬프트에 넣고 JSON으로 받음.
시스템 프롬프트에 스레드가 순서대로 읽히는 한 대화라는 규칙을 둠(앞 메시지에 반응, 같은 화자의 입장 유지, 질문 뒤 항목은 그 질문에 답함).
금지 규칙(scenario/forbidden.json)에 걸리면 지적을 덧붙여 다시 요청하고, 끝까지 실패하면 그 스레드를 데이터에서 뺌."""
import json, random

from common import extract_json, violations

ROLE_TEXT = {
    "post": "Start a new thread about this topic: {topic}. Two to four sentences. Concrete details are fine; when you need an id, use only the ones in ids_you_may_use.",
    "agree": "Reply to the thread. Agree with or acknowledge what was actually said in the message(s) before, in one or two short sentences. Do not offer to take on work that nobody asked this speaker to do.",
    "question": "Reply to the thread with one short clarifying question about something specific that was said in it.",
    "answer": "Reply to the thread by answering the question asked in the message just before this one. Be concrete and consistent with what the thread already says. One or two sentences.",
    "ultrashort": "Reply with a reaction of one to three words that fits the message just before this one (a brief acknowledgement or agreement). Do not claim to take on a task unless the thread asked this speaker to do something.",
    "disagree": "Reply to the thread. Push back on, or accept only conditionally, what was said before, and give one concrete reason. One or two sentences. Stay consistent with anything this speaker already said in the thread.",
    "drift": "Start a casual off-topic aside that is not about work, about: {drift_theme}. One or two sentences.",
}
DRIFT_THEMES = ["the weather", "lunch plans", "the commute", "a printer jam", "cake in the kitchen", "a podcast episode", "a desk plant",
                "the office AC", "parking", "a sports result", "a new coffee place", "a broken elevator", "someone's birthday", "a weekend hike",
                "the building fire drill", "a conference someone is attending", "headphones", "a keyboard shortcut discovery"]
STYLE_TEXT = {
    "typo": "Include exactly one realistic typo.",
    "fragment": "Write it as a sentence fragment that trails off with three dots.",
    "selfcorrection": "Append a short self-correction at the end, like '*meant Thursday'.",
    "lowercase": "All lowercase, no final period.",
}
SERVICES = "ingest-worker, embed-service, query-api, index-builder, eval-runner, dashboard, feature-store, auth-proxy"


def system_prompt(scn):
    """시스템 프롬프트 문자열. 팀 배경, 서비스 이름, id 사용 규칙, 1인칭 규칙, 보호 사실 목록, JSON 형식"""
    fb = scn["forbidden"]
    facts = "\n".join(f"- {t}" for t in fb["facts"])
    return (
        "You write realistic internal team chat messages for a synthetic dataset. The team builds a search product called Atlas. "
        "Members: Mina (manager), Sofia (director), Alex (backend), Priya (ML evaluation), Jordan (SRE). "
        f"Service names you may use: {SERVICES}. Each request lists the ticket ids, PR numbers and data cut you may reference; "
        "use only those, and only when the message needs one. Do not invent other ids.\n"
        "Write only the messages requested. Stay in character for each speaker's voice card. Write in first person as the speaker; never name or address yourself. "
        "Vary openings. Never write paragraphs. You may mention people who are not in the channel, but do not address them directly.\n"
        "A thread is one conversation read in order: every reply reacts to the message(s) before it, usually the latest one; a speaker never contradicts "
        "what they said earlier in the same thread; when an item is a question, the item that follows answers it. Do not repeat a previous message.\n"
        "The team has ongoing topics (the launch, the reranker work, the CI quality gate, standup, the index migration, the freeze, the model review, hiring, "
        "credentials, time off, data cuts, the partner demo, GPU capacity, the collection rebuild, on-call, onboarding, the reindex, launch-day logistics, "
        "the retro, budgets, the dashboard, postmortems). You may refer to them in passing when it is natural. "
        "But the following PROTECTED FACTS are decided elsewhere in the dataset. Never state, guess, hint at, or imply any value for them, "
        "not even vaguely (no 'next week', no 'the new date', no 'the one we picked', no 'the usual time'):\n"
        f"{facts}\n"
        'Output JSON only, no markdown: {"messages": [{"k": 0, "text": "..."}, ...]} with one entry per requested item, in order.'
    )


def id_pool(thread):
    """스레드마다 다른 식별자. 모델이 예시 id 하나를 전체에 복사하는 것을 막음. thread_id로 결정적."""
    rng = random.Random(int(thread["thread_id"][1:]) * 31 + 7)
    return {"tickets": [f"ATL-{rng.randint(200, 480)}" for _ in range(2)], "prs": [f"#{rng.randint(1100, 1499)}"],
            "data_cut": f"v2.{rng.randint(2, 9)}", "service_version": f"1.{rng.randint(3, 9)}.{rng.randint(0, 6)}"}


def user_prompt(scn, thread):
    """스레드 하나의 유저 프롬프트(JSON 문자열). 채널, 멤버, 날짜, 주제, 스레드 전용 id 풀, 등장인물의 목소리 카드, 메시지별 역할, 문체, touch, xref 지시"""
    speakers = sorted({m["speaker"] for m in thread["messages"]})
    items = []
    for m in thread["messages"]:
        theme = DRIFT_THEMES[(int(thread["thread_id"][1:]) * 7 + m["k"]) % len(DRIFT_THEMES)]
        inst = ROLE_TEXT[m["role"]].format(topic=thread["topic"], drift_theme=theme)
        if m["k"] == 0 and m["role"] == "post":
            if thread.get("touch"):
                inst += f" Also mention, in passing and without stating any protected fact: {thread['touch']}."
            if thread.get("xref"):
                inst += f" Refer to the channel {thread['xref']} by name in passing (for example 'as posted in {thread['xref']}'), about a non-protected topic."
        for s in m["style"]:
            inst += " " + STYLE_TEXT[s]
        items.append({"k": m["k"], "speaker": m["speaker"], "role": m["role"], "time": m["time"][11:], "instruction": inst})
    obj = {"channel": thread["channel"], "members": thread["members"], "date": thread["date"], "topic": thread["topic"],
           "ids_you_may_use": id_pool(thread), "voice_cards": {s: scn["personas"][s] for s in speakers}, "items": items}
    if thread["channel"].startswith("dm:") and len(thread["members"]) == 1:
        obj["note"] = "This is a direct message from the member to a silent team assistant. Write only the member's message."
    if thread["messages"][0]["role"] == "drift":
        obj["note"] = (obj.get("note", "") + " The first message is an off-topic aside, not a work post. Replies must react to that aside "
                       "in a casual way (a joke, a question about it, a brief agreement); do not reply about work items or tickets.").strip()
    return json.dumps(obj, ensure_ascii=False)


def parse(content, thread):
    """모델 출력에서 {k: text}를 꺼냄. 계획의 모든 k가 없으면 ValueError"""
    obj = extract_json(content)
    got = {int(m["k"]): str(m["text"]).strip() for m in obj.get("messages", []) if "k" in m and m.get("text")}
    need = [m["k"] for m in thread["messages"]]
    if any(k not in got for k in need):
        raise ValueError(f"항목 누락: {[k for k in need if k not in got]}")
    return {k: got[k] for k in need}


def render_thread(client, scn, thread, attempts=3, seed=0):
    """반환: (k->text, meta). meta에 시도 횟수, 대체 여부, 위반 목록.
    재요청은 매번 계획 전체를 다시 보내고 지적을 덧붙임(대화 맥락에 의존하지 않음)."""
    sys_p, base = system_prompt(scn), user_prompt(scn, thread)
    note = ""
    meta = {"attempts": 0, "fallback": False, "violations": []}
    for i in range(attempts):
        meta["attempts"] = i + 1
        msgs = [{"role": "system", "content": sys_p}, {"role": "user", "content": base + note}]
        try:
            content = client.chat(msgs, tag=thread["thread_id"])
            texts = parse(content, thread)
        except Exception as e:  # 파싱 실패, 호출 실패 모두 재시도
            note = f"\n\nNote: your previous output could not be used ({type(e).__name__}: {str(e)[:120]}). Return valid JSON only, one entry per item."
            continue
        bad = {k: violations(t, scn["forbidden_compiled"]) for k, t in texts.items()}
        bad = {k: v for k, v in bad.items() if v}
        if not bad:
            return texts, meta
        meta["violations"].append(bad)
        names = sorted({n for v in bad.values() for n in v})
        note = f"\n\nNote: in your previous answer, items {sorted(bad)} touched reserved topics ({', '.join(names)}). Rewrite all items avoiding them. JSON only."
    meta["fallback"] = True  # 세 번 시도해도 실패. 이 스레드는 데이터에서 빠짐
    return None, meta
