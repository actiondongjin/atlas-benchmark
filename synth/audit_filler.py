"""일상 대화 LLM audit. 정규식이 놓치는 '암시'를 잡음. 보호 사실 목록(scenario/forbidden.json의 facts)을 주고,
일상 대화 메시지가 그중 어느 값이라도 말하거나 암시하는지 물음. 걸린 스레드는 assemble 단계에서 뺌.

  uv run python synth/audit_filler.py --filler data/luna2/build/filler.jsonl --model gpt-5.6-luna --out data/luna2/build/audit.json
  uv run python synth/audit_filler.py --filler data/_smoke/build/filler.jsonl --model m --mock --out data/_smoke/build/audit.json   # 배관 점검

주제 단어(launch, reranker, gate, standup, migration, freeze, review, hiring, key, off, cut, demo, gpu, collection, on-call 등)가 들어간
일상 대화만 audit에 보냄. 비용은 gpt-5.6-luna 기준 일상 대화 1,980개에 약 $0.14."""
import argparse, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, CACHE, RUNS, load_scenario, load_jsonl, extract_json
from llm_client import LLMClient, json_schema_format

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["flags"],
          "properties": {"flags": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["k", "flag", "why"],
                                                              "properties": {"k": {"type": "integer"}, "flag": {"type": "boolean"}, "why": {"type": "string"}}}}}}
RESPONSE_FORMAT = json_schema_format("audit_flags", SCHEMA)

TOPIC = re.compile(r"launch|rerank|gate|threshold|stand-?up|migration|freeze|review|hir|candidate|offer|interview|key|token|credential|rotat|"
                   r"\boff\b|vacation|pto|leave|cut|demo|gpu|h100|node|collection|rebuild|on-?call|pager|incident|budget|contractor|vendor|quote|reorg|"
                   r"eval report|timeline|worried|p99|latency|bootcamp|onboard|rota|reindex|shard|intern|spike|postmortem|merge|p0|cutover|announce|"
                   r"headcount|hotfix|blog|retro|points|partner|dashboard|cert|failover|contingency|board|independent|cost|bill", re.I)
SYSTEM = ("You audit synthetic team-chat messages against a list of PROTECTED FACTS. The facts are decided elsewhere; these messages must not state, hint at, "
          "or imply any VALUE for them. Mentioning a topic is fine; conveying a value, decision, owner, date, reason or outcome is not.\n"
          "Flag = true ONLY for things like: a date or timing for the launch or a freeze ('next week', 'the new date'); which reranker is preferred or chosen; "
          "a number for the CI quality gate; a standup time; who owns the index migration; a hiring decision, level or start date; why a key was rotated or that "
          "the embed-service key was rotated; Priya's time-off dates; a count of new GPU nodes or their approval; who presents at the partner demo; why Alex is out "
          "Friday afternoon; the cause of Tuesday's query-api incident; any reorg; contractor budget numbers; who has weekend on-call; the vendor quote; "
          "that Priya is worried or behind; bootcamp dates; the launch-week on-call rota; reindex finish dates; the monthly node cost; the intern onboarding owner; "
          "the launch-day spike or its cause; freeze exceptions; announcement or cutover times; the extra headcount; the hotfix gate; blog dates; the retro facilitator; "
          "day-1 improvement figures; the dashboard outage cause; the live failover request; the launch-day contingency; contractor end dates; the reorg being shelved; anyone's private stance.\n"
          "Do NOT flag: v2.x data cuts (allowed); generic exec or board summaries; generic on-call or pager talk without naming who has the weekend; "
          "generic time-off talk without Priya's dates; generic hiring-process talk; generic credential hygiene; generic launch prep or freeze prep without dates; "
          "generic reranker engineering (training, profiling, dashboards) without a preference or choice; generic incident chatter on other services or times; "
          "generic onboarding prep, launch-day logistics, reindex progress, retro format or budget-season talk without the protected values; "
          "a shard needing reindex or a rollout hold without a shard number, finish date or owner change; personal opinions, priorities or conditional agreement "
          "on everyday work (only the specific private stances in the protected list count).\n"
          "PROTECTED FACTS:\n{facts}\n"
          'Output JSON only: {{"flags": [{{"k": 0, "flag": false, "why": ""}}, ...]}} with one entry per item.')


def main():
    """주제 단어가 든 일상 대화를 배치로 LLM에 보내 보호 사실 암시 여부를 묻고 audit.json(제외할 스레드 목록 포함)을 씀"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--filler", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--cache-dir", default=CACHE)
    ap.add_argument("--runs-dir", default=RUNS)
    a = ap.parse_args()
    scn = load_scenario()
    rows = load_jsonl(a.filler)
    cand = [r for r in rows if TOPIC.search(r["text"])]
    sys_p = SYSTEM.format(facts="\n".join(f"- {f}" for f in scn["forbidden"]["facts"]))
    run_id = time.strftime("%Y%m%d-%H%M%S") + "_audit_" + a.model.replace("/", "_")
    client = LLMClient(a.model, a.base_url, a.api_key, temperature=0.0, max_tokens=4096, cache_dir=a.cache_dir,  # 추론 토큰 포함. 실측 최대 824
                       log_path=os.path.join(a.runs_dir, run_id, "calls.jsonl"), mock=a.mock)
    batches = [cand[i:i + a.batch] for i in range(0, len(cand), a.batch)]
    print(f"audit: filler {len(rows)} | 주제 단어 포함 {len(cand)} | batches {len(batches)} | model {a.model}")
    flagged = []
    t0 = time.time()

    def judge(batch, attempts=3):
        """일상 대화 배치 하나를 audit. flag=true인 항목을 (thread_id, k, text, why) 목록으로 돌려줌.
        빈 응답이나 파싱 실패면 지적을 덧붙여 다시 요청함. 끝까지 실패하면 그 배치의 스레드를 모두 제외 대상으로 돌려줌(놓치는 쪽보다 빼는 쪽이 안전)"""
        items = [{"k": i, "channel": r["channel"], "speaker": r["speaker"], "text": r["text"]} for i, r in enumerate(batch)]
        user = json.dumps({"items": items}, ensure_ascii=False)
        note, last = "", None
        for _ in range(attempts):
            content = client.chat([{"role": "system", "content": sys_p}, {"role": "user", "content": user + note}], tag="audit", response_format=RESPONSE_FORMAT)
            try:
                obj = extract_json(content)
                out = []
                for f in obj.get("flags", []):
                    try:
                        k = int(f.get("k"))
                    except (TypeError, ValueError):
                        continue
                    if f.get("flag") is True and 0 <= k < len(batch):
                        r = batch[k]
                        out.append({"thread_id": r["thread_id"], "k": r["k"], "text": r["text"], "why": f.get("why", "")})
                return out
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:80]}"
                note = "\n\nNote: your previous output could not be used (" + last + "). Return the JSON object only, one entry per item."
        print(f"  audit batch unparseable after {attempts} attempts ({last}); dropping its {len(batch)} messages' threads")
        return [{"thread_id": r["thread_id"], "k": r["k"], "text": r["text"], "why": "audit unparseable"} for r in batch]

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(judge, b) for b in batches]), 1):
            flagged += f.result()
            if i % 20 == 0 or i == len(batches):
                print(f"  batches {i}/{len(batches)} | flagged {len(flagged)} | {time.time() - t0:.0f}s")
    threads = sorted({f["thread_id"] for f in flagged})
    result = {"model": a.model, "checked": len(cand), "flagged_messages": len(flagged), "drop_threads": threads, "flags": flagged,
              "stats": client.stats, "seconds": round(time.time() - t0, 1)}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(f"audit: flagged {len(flagged)} messages in {len(threads)} threads -> {a.out}")
    for f in flagged[:8]:
        print("   ", f["thread_id"], "|", f["text"][:90], "|", f["why"][:60])


if __name__ == "__main__":
    main()
