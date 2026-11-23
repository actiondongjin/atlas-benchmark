"""정답 근거 메시지(scenario/core_messages.jsonl)와 일상 대화(build/filler.jsonl)를 합쳐 데이터 폴더를 만듦.

  uv run python synth/assemble.py --filler data/luna2/build/filler.jsonl --out data/luna2 --drop-threads data/luna2/build/audit.json

시각이 겹치면 일상 대화를 1분씩 미룸(정답 근거가 자리를 먼저 잡음). 일상 대화 id는 시간순으로 F0001부터.
금지 패턴에 걸린 일상 대화는 스레드 단위로 뺌(--on-violation fail 이면 중단). 결과에 validate를 바로 돌림.
LLM을 부르지 않으므로 같은 filler.jsonl, audit.json, scenario/ 에서는 항상 같은 결과가 나옴."""
import argparse, json, os, shutil, sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCEN, load_scenario, load_jsonl, dump_jsonl, parse_time, fmt_time, violations, build_dir
import validate


def assemble(filler_path, out_dir, drop_threads_path=None, on_violation="drop"):
    """위반 스레드 제거 -> 근거와 일상 대화 합치기 -> 파일 쓰기. 쓴 메시지 수를 돌려줌.
    out_dir에 messages.jsonl, message_tags.jsonl, questions.jsonl, channels.json, personas.json을, out_dir/build에 assemble_stats.json을 씀"""
    scn = load_scenario()
    filler = load_jsonl(filler_path)
    audited = set()
    if drop_threads_path and os.path.exists(drop_threads_path):
        audited = set(json.load(open(drop_threads_path, encoding="utf-8")).get("drop_threads", []))
        before = len(filler); filler = [r for r in filler if r["thread_id"] not in audited]
        print(f"LLM audit으로 뺀 스레드 {len(audited)}개 (메시지 {before - len(filler)}개)")

    # 1) 금지 패턴에 걸린 스레드 제거
    bad_threads = {}
    for r in filler:
        v = violations(r["text"], scn["forbidden_compiled"])
        if v:
            bad_threads.setdefault(r["thread_id"], []).append((r["k"], v, r["text"][:80]))
    if bad_threads and on_violation == "fail":
        for t, v in list(bad_threads.items())[:10]:
            print("위반:", t, v)
        sys.exit(f"금지 패턴 위반 스레드 {len(bad_threads)}개. 렌더 프롬프트나 금지 패턴을 고칠 것")
    filler = [r for r in filler if r["thread_id"] not in bad_threads]
    if bad_threads:
        print(f"금지 패턴 위반으로 뺀 스레드 {len(bad_threads)}개:")
        for t, v in list(bad_threads.items())[:8]:
            print("   ", t, v[0])

    # 2) 합치기. 정답 근거가 먼저 시각을 잡고, 일상 대화는 겹치면 1분씩 밀기
    items = []
    for c in scn["core"]:
        items.append({**c, "_t": parse_time(c["time"]), "tags": ["basis"] + c["tags"], "_fixed": True})
    for r in filler:
        items.append({"_t": parse_time(r["time"]), "channel": r["channel"], "speaker": r["speaker"], "text": r["text"],
                      "tags": r["tags"], "_thread": r["thread_id"], "_k": r["k"], "_fixed": False})
    seen = set()
    for it in sorted(items, key=lambda z: (0 if z["_fixed"] else 1, z["_t"])):
        while it["_t"] in seen:
            it["_t"] += timedelta(minutes=1)
        seen.add(it["_t"])
    items.sort(key=lambda z: z["_t"])
    k = 0
    root_of = {}
    for it in items:
        if not it["_fixed"]:
            k += 1
            it["id"] = f"F{k:04d}"
            if it["_k"] == 0:
                root_of[it["_thread"]] = it["id"]
    for it in items:
        if not it["_fixed"]:
            it["reply_to"] = None if it["_k"] == 0 else root_of.get(it["_thread"])
    messages = [{"id": it["id"], "time": fmt_time(it["_t"]), "channel": it["channel"], "speaker": it["speaker"],
                 "reply_to": it["reply_to"], "text": it["text"]} for it in items]
    tags = {it["id"]: it["tags"] for it in items}

    # 3) 쓰기
    os.makedirs(out_dir, exist_ok=True)
    dump_jsonl(os.path.join(out_dir, "messages.jsonl"), messages)
    dump_jsonl(os.path.join(out_dir, "message_tags.jsonl"), [{"id": m["id"], "tags": tags[m["id"]]} for m in messages])
    shutil.copy(os.path.join(SCEN, "questions.jsonl"), os.path.join(out_dir, "questions.jsonl"))
    shutil.copy(os.path.join(SCEN, "channels.json"), os.path.join(out_dir, "channels.json"))
    shutil.copy(os.path.join(SCEN, "personas.json"), os.path.join(out_dir, "personas.json"))
    with open(os.path.join(build_dir(out_dir), "assemble_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"filler_source": filler_path, "dropped_threads": len(bad_threads), "dropped_detail": bad_threads, "audit_dropped_threads": sorted(audited)}, f, indent=1, ensure_ascii=False)
    print(f"assembled {len(messages)} messages -> {out_dir}")
    return len(messages)


def main():
    """인자를 읽어 assemble을 돌리고 validate 결과 코드를 반환"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--filler", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--on-violation", choices=["drop", "fail"], default="drop")
    ap.add_argument("--drop-threads", default=None, help="audit_filler.py 결과(audit.json). drop_threads에 든 스레드를 뺌")
    a = ap.parse_args()
    assemble(a.filler, a.out, a.drop_threads, a.on_violation)
    return validate.main(a.out)


if __name__ == "__main__":
    sys.exit(main())
