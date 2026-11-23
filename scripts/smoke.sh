#!/usr/bin/env bash
# 엔드포인트 연결과 출력 형식만 빠르게 점검. 스레드 8개만 렌더해 data/_smoke/build/ 에 씀(git에 올라가지 않음).
# 사용: bash scripts/smoke.sh MODEL     # 키는 루트 .env 또는 환경변수
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
PY=${PYTHON:-"uv run python"}
[ -f .env ] && PY="uv run --env-file .env python"          # 루트 .env에 OPENAI_API_KEY 등을 두면 자동으로 읽음
MODEL=${1:?MODEL 필요}
$PY synth/filler.py --model "$MODEL" --limit 8 --workers 2 --out data/_smoke/build
$PY - <<'PYEOF'
import json
for l in open("data/_smoke/build/filler.jsonl"):
    r = json.loads(l); print(r["channel"], r["speaker"], "|", r["text"][:110], "|", [t for t in r["tags"] if t.startswith("perturb")])
PYEOF
