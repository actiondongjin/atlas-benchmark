#!/usr/bin/env bash
# OpenAI 호환 API로 일상 대화를 생성해 데이터를 만듦. 결과는 data/NAME/ 에, 구축 기록은 data/NAME/build/ 에 저장함.
# 사용: bash scripts/build.sh MODEL NAME [SCALE=2.5] [SEED=7] [WORKERS=4]     # 키와 base_url은 루트 .env 또는 환경변수. 배포 데이터 luna2는 scale 3, seed 7
# 예:  bash scripts/build.sh gpt-5.6-luna luna2-rerun 3 7 8
#      OPENAI_BASE_URL=http://localhost:8002/v1 bash scripts/build.sh Qwen/Qwen3-32B qwen3-32b 3 7 8
#      AUDIT_MODEL=gpt-5.6-luna bash scripts/build.sh gpt-5.6-sol sol 3 7 8      # 생성과 audit에 다른 모델
# 일상 대화 생성 뒤 LLM audit이 한 번 더 돎(비용 약 10분의 1 추가). AUDIT=0 으로 끄거나 AUDIT_MODEL=다른모델 로 바꿀 수 있음.
# 긴 작업이므로 tmux 안에서 돌리는 것을 권함. 같은 모델, seed, scale로 다시 돌리면 캐시(.cache/)를 써서 비용이 들지 않음.
set -euo pipefail
cd "$(dirname "$0")/.."                                    # 리포 루트
export PYTHONUNBUFFERED=1
PY=${PYTHON:-"uv run python"}
[ -f .env ] && PY="uv run --env-file .env python"          # 루트 .env에 OPENAI_API_KEY 등을 두면 자동으로 읽음
MODEL=${1:?MODEL 필요}; NAME=${2:?NAME 필요}; SCALE=${3:-2.5}; SEED=${4:-7}; WORKERS=${5:-4}
OUT="data/$NAME"; B="$OUT/build"
# 키와 주소는 루트 .env 또는 셸 환경변수에서 읽음(둘 다 있으면 셸 환경변수가 우선). 여기서 기본값을 export하면 .env 값이 가려지므로 하지 않음
echo "model=$MODEL name=$NAME scale=$SCALE seed=$SEED workers=$WORKERS base_url=${OPENAI_BASE_URL:-(.env 또는 https://api.openai.com/v1)}"
$PY synth/filler.py --model "$MODEL" --scale "$SCALE" --seed "$SEED" --workers "$WORKERS" --out "$B"
if [ "${AUDIT:-1}" = "1" ]; then
  # 정규식이 놓친 '암시'를 LLM으로 확인. AUDIT=0 이면 건너뜀. AUDIT_MODEL로 다른 모델 지정 가능
  $PY synth/audit_filler.py --filler "$B/filler.jsonl" --model "${AUDIT_MODEL:-$MODEL}" --workers "$WORKERS" --out "$B/audit.json"
fi
$PY synth/assemble.py --filler "$B/filler.jsonl" --out "$OUT" --on-violation drop --drop-threads "$B/audit.json"
$PY synth/transcript.py "$OUT"
$PY synth/tokens.py "$OUT"
$PY synth/quality.py "$OUT"
