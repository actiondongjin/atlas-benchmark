#!/usr/bin/env bash
# 배포 데이터가 이 리포의 사양과 코드에서 나온 것인지 LLM 호출 없이 확인함. 키가 필요 없음.
# 사용: bash scripts/verify.sh [NAME=luna2]
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
PY=${PYTHON:-"uv run python"}
$PY synth/verify.py "data/${1:-luna2}"
