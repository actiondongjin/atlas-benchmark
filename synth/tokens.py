"""데이터 토큰 수 집계. 기본은 tiktoken o200k_base(GPT-4o, 5 계열). HF 토크나이저 이름을 주면 그것으로도 셈.

  uv run python synth/tokens.py data/luna2
  uv run python synth/tokens.py data/luna2 --hf Qwen/Qwen3-32B     # transformers 또는 tokenizers 설치 시

둘을 냄: 본문만(text), 그리고 메모리 시스템이 실제로 받는 형태("[time] #channel speaker: text" 한 줄씩)의 합계.
결과는 data/<이름>/build/tokens.json 에도 씀."""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_jsonl, build_dir


def count_o200k(texts):
    """tiktoken o200k_base로 텍스트 목록의 토큰 합계와 인코더 이름을 돌려줌"""
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    return sum(len(enc.encode(t)) for t in texts), "tiktoken/o200k_base"


def count_hf(texts, name):
    """HF 토크나이저(tokenizers 또는 transformers)로 토큰 합계를 셈"""
    try:
        from tokenizers import Tokenizer
        tok = Tokenizer.from_pretrained(name)
        return sum(len(tok.encode(t).ids) for t in texts), f"tokenizers/{name}"
    except Exception:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(name)
        return sum(len(tok(t)["input_ids"]) for t in texts), f"transformers/{name}"


def main():
    """본문만 센 값과 '[time] #channel speaker: text' 줄 형태로 센 값을 출력하고 build/tokens.json에 저장"""
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--hf", default=None, help="HF 토크나이저 이름(선택)")
    a = ap.parse_args()
    M = load_jsonl(os.path.join(a.data_dir, "messages.jsonl"))
    texts = [m["text"] for m in M]
    lines = [f"[{m['time']}] {m['channel']} {m['speaker']}: {m['text']}" for m in M]
    out = {"messages": len(M), "words": sum(len(t.split()) for t in texts)}
    try:
        n_text, enc = count_o200k(texts); n_line, _ = count_o200k(lines)
        out["o200k"] = {"text": n_text, "transcript_lines": n_line, "encoder": enc}
    except Exception as e:
        out["o200k"] = {"error": f"{type(e).__name__}: {e}"}
    if a.hf:
        try:
            n_text, enc = count_hf(texts, a.hf); n_line, _ = count_hf(lines, a.hf)
            out["hf"] = {"text": n_text, "transcript_lines": n_line, "encoder": enc}
        except Exception as e:
            out["hf"] = {"error": f"{type(e).__name__}: {e}"}
    with open(os.path.join(build_dir(a.data_dir), "tokens.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
