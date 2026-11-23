"""일상 대화 생성 CLI. 계획을 세우고(plan.py) OpenAI 호환 LLM으로 문장을 만들어 filler.jsonl과 plan.jsonl을 씀.

  uv run python synth/filler.py --model gpt-5.6-luna --scale 3 --seed 7 --workers 8 --out data/luna2/build
  uv run python synth/filler.py --model any --mock --limit 20 --out data/_smoke/build     # 네트워크 없이 배관 점검

환경변수 OPENAI_BASE_URL, OPENAI_API_KEY를 읽음. --limit N은 앞 N개 스레드만 렌더(연결 점검용).
세 번 시도해도 금지 규칙을 못 피한 스레드는 데이터에서 빠지고 render_meta.jsonl에 기록됨."""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, CACHE, RUNS, load_scenario, dump_jsonl
from plan import make_plan, tags_for
import render_llm
from llm_client import LLMClient


def main():
    """계획 생성 -> LLM 렌더(병렬) -> filler.jsonl, plan.jsonl, render_meta.jsonl, filler_stats.json 쓰기. 실행 요약은 runs/<run_id>/summary.json"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=2.5, help="일상 대화 밀도. 1.0이면 하루 채널당 plan.RATE 만큼")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True, help="출력 폴더(data/<이름>/build). plan.jsonl, filler.jsonl, render_meta.jsonl, filler_stats.json")
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--max-tokens", type=int, default=4096, help="응답 토큰 한도. 추론 토큰이 포함되므로 여유를 둠(실측 최대 948)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--attempts", type=int, default=3, help="금지 주제 위반이나 파싱 실패 시 재요청 횟수")
    ap.add_argument("--limit", type=int, default=0, help="앞 N개 스레드만 렌더. 0이면 전부")
    ap.add_argument("--mock", action="store_true", help="LLM 호출 없이 가짜 응답으로 배관만 점검")
    ap.add_argument("--cache-dir", default=CACHE)
    ap.add_argument("--runs-dir", default=RUNS)
    a = ap.parse_args()

    scn = load_scenario()
    threads = make_plan(scn, a.seed, a.scale)
    if a.limit:
        threads = threads[:a.limit]
    os.makedirs(a.out, exist_ok=True)
    dump_jsonl(os.path.join(a.out, "plan.jsonl"), threads)
    n_msgs = sum(len(t["messages"]) for t in threads)
    print(f"plan: threads {len(threads)} | messages {n_msgs} | seed {a.seed} | scale {a.scale}")

    t0 = time.time()
    metas = {}
    run_id = time.strftime("%Y%m%d-%H%M%S") + "_" + a.model.replace("/", "_")
    log_path = os.path.join(a.runs_dir, run_id, "calls.jsonl")
    client = LLMClient(a.model, a.base_url, a.api_key, temperature=a.temperature, max_tokens=a.max_tokens,
                       cache_dir=a.cache_dir, log_path=log_path, mock=a.mock, seed=a.seed)
    renderer_tag = f"renderer:llm:{a.model}"
    texts = {}
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(render_llm.render_thread, client, scn, t, a.attempts, a.seed): t for t in threads}
        for fu in as_completed(futs):
            t = futs[fu]
            texts[t["thread_id"]], metas[t["thread_id"]] = fu.result()
            done += 1
            if done % 50 == 0 or done == len(threads):
                print(f"  rendered {done}/{len(threads)} | llm calls {client.stats['calls']} cached {client.stats['cached']} "
                      f"| tokens in {client.stats['prompt_tokens']} out {client.stats['completion_tokens']} | {time.time() - t0:.0f}s")
    dropped = [tid for tid, tx in texts.items() if tx is None]
    summary = {"run_id": run_id, "model": a.model, "base_url": client.base_url, "seed": a.seed, "scale": a.scale,
               "threads": len(threads), "stats": client.stats, "dropped_threads": len(dropped),
               "retries": sum(m["attempts"] - 1 for m in metas.values()), "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(a.runs_dir, run_id, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
    dump_jsonl(os.path.join(a.out, "render_meta.jsonl"),
               [{"thread_id": t["thread_id"], **metas[t["thread_id"]]} for t in threads if metas[t["thread_id"]]["attempts"] > 1 or metas[t["thread_id"]]["fallback"]])
    print("run summary:", json.dumps(summary, ensure_ascii=False))
    print(f"render_meta.jsonl: 재시도나 제외가 있었던 스레드만 기록 ({sum(1 for m in metas.values() if m['attempts'] > 1 or m['fallback'])}개)")
    if client.fatal or (threads and len(dropped) == len(threads)):
        why = client.fatal or "모든 스레드가 3번 시도 뒤 빠짐(응답 형식이나 금지 규칙 위반이 반복됨)"
        print(f"filler: 중단. {why[:300]}\n  호출 기록: {log_path}", file=sys.stderr)
        sys.exit(2)  # filler.jsonl을 쓰지 않음. 스크립트(set -e)도 여기서 멈춤

    rows = []
    for t in threads:
        if texts.get(t["thread_id"]) is None:
            continue  # 세 번 실패한 스레드는 뺌
        for m in t["messages"]:
            tags = tags_for(m, t) + [renderer_tag]
            rows.append({"thread_id": t["thread_id"], "k": m["k"], "time": m["time"], "channel": t["channel"],
                         "speaker": m["speaker"], "reply_to_k": None if m["k"] == 0 else 0,
                         "text": texts[t["thread_id"]][m["k"]], "tags": tags})
    dump_jsonl(os.path.join(a.out, "filler.jsonl"), rows)
    words = sum(len(r["text"].split()) for r in rows)
    stats = {"model": a.model, "seed": a.seed, "scale": a.scale, "threads": len(threads), "dropped_threads": len(dropped),
             "messages": len(rows), "words": words}
    with open(os.path.join(a.out, "filler_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1, ensure_ascii=False)
    print(f"filler: messages {len(rows)} | words {words} | dropped threads {len(dropped)} -> {a.out}/filler.jsonl")


if __name__ == "__main__":
    main()
