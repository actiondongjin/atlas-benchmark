"""배포 데이터가 이 리포의 사양과 코드에서 나온 것인지 LLM 호출 없이 확인함.

  uv run python synth/verify.py data/luna2

확인하는 것 셋.
1. 계획: build/filler_stats.json의 seed와 scale로 계획을 다시 세워 build/plan.jsonl과 같은지
2. 조립: build/filler.jsonl과 build/audit.json을 임시 폴더에 다시 조립해 messages.jsonl, message_tags.jsonl, questions.jsonl, channels.json, personas.json이 바이트 단위로 같은지
3. 검사: validate가 오류 없이 통과하는지
일상 대화 문장 자체(build/filler.jsonl)는 LLM 출력이라 다시 생성하면 달라짐. 그래서 그 파일을 기록으로 두고, 그 앞뒤 단계만 대조함."""
import filecmp, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_scenario, load_jsonl, build_dir
from plan import make_plan
from assemble import assemble
import validate

DATA_FILES = ["messages.jsonl", "message_tags.jsonl", "questions.jsonl", "channels.json", "personas.json"]


def main(data_dir):
    """세 가지를 차례로 확인하고 결과를 출력함. 하나라도 다르면 1을 반환"""
    b = build_dir(data_dir)
    ok = True

    st = json.load(open(os.path.join(b, "filler_stats.json"), encoding="utf-8"))
    plan_now = make_plan(load_scenario(), st["seed"], st["scale"])
    plan_rel = load_jsonl(os.path.join(b, "plan.jsonl"))
    same = plan_now == plan_rel
    ok &= same
    print(f"[1] 계획 (seed {st['seed']}, scale {st['scale']}): 스레드 {len(plan_now)}개, 배포본과 {'같음' if same else '다름'}")

    with tempfile.TemporaryDirectory() as tmp:
        assemble(os.path.join(b, "filler.jsonl"), tmp, os.path.join(b, "audit.json"))
        for fn in DATA_FILES:
            same = filecmp.cmp(os.path.join(tmp, fn), os.path.join(data_dir, fn), shallow=False)
            ok &= same
            print(f"[2] 조립 {fn}: {'같음' if same else '다름'}")
        rc = validate.main(tmp)
        ok &= rc == 0
        print(f"[3] 검사: {'통과' if rc == 0 else '오류 있음'}")

    print("결과:", "모두 일치" if ok else "불일치 있음")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용: python synth/verify.py data/<이름>")
    sys.exit(main(sys.argv[1]))
