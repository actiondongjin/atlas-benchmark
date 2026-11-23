# Atlas

여러 사람이 함께 쓰는 채팅 환경에서 동작하는 메모리 시스템을 평가하기 위한 합성 데이터셋임. 가상의 제품 팀 5명이 약 6주 동안 채널 17개에서 나눈 메시지 4,353개와, 그 대화 중간의 특정 시점에 특정 팀원이 어시스턴트에게 던지는 질문 415개로 이루어져 있음.

팀원마다 볼 수 있는 채널이 다름. 그래서 같은 질문이라도 누가 묻느냐에 따라 정답이 달라지고, 답에 넣으면 안 되는 내용도 달라짐.
e.g. 리더십 채널에서 출시일이 10월 7일로 정해졌지만 아직 팀에 공지되지 않은 시점이라면, 리더십 채널 멤버인 Mina가 묻는 출시일의 정답은 10월 7일이고, 그 채널을 볼 수 없는 Alex가 묻는 출시일의 정답은 이미 공지된 9월 30일임. Alex에게 10월 7일을 말하면 볼 수 없는 정보를 알려준 것이 됨.

이 리포에는 완성된 데이터, 데이터의 바탕이 된 사양(직접 작성), 대화 합성 코드, 구축 과정의 기록이 들어 있음.

## Overview

| 항목 | 값 |
|---|---|
| 메시지 | 4,353개. 정답 근거 174, 유사 사안 30, 일상 대화 4,149 |
| 기간 | 2026-09-01부터 2026-10-09까지 평일 29일. 하루 111개에서 200개 |
| 등장인물 | 5명. Mina(매니저), Sofia(디렉터), Alex(백엔드), Priya(ML 평가), Jordan(SRE) |
| 채널 | 17개. 공개 채널 5, 사람 사이의 DM 7(셋이 쓰는 그룹 DM 1 포함), 각자가 어시스턴트와 쓰는 1:1 DM 5 |
| 스레드 | 원글 2,136개, 답글 2,217개 |
| 분량 | 117,920 단어. 본문 기준 157,523 토큰, `[시각] 채널 화자: 본문` 형태로 236,801 토큰(tiktoken o200k_base) |
| 질문 | 415개. 묻는 시점은 119곳 |
| 질문 라벨 | Question Type 3종, Asker Access 3종, Question Pattern 8종 |
| 질문자 변형 세트 | 47개. 같은 시점에 같은 내용을 서로 다른 사람이 묻는 질문들 |
| 언어 | 메시지와 질문은 영어. 작성자 메모(`note`)와 문서, 코드 주석은 한국어 |
| 일상 대화 생성 모델 | gpt-5.6-luna (seed 7, scale 3) |

## Background Settings

어시스턴트는 모든 채널과 DM의 메시지를 시간순으로 받음. 사람은 자기가 멤버인 채널만 볼 수 있음. 질문은 대화 도중에 들어오고, 그 시점 이후의 메시지는 아직 존재하지 않는 것으로 봄.

메시지는 세 종류임.

- 정답 근거(174개). 질문의 답이 되는 사실을 담은 메시지. 사안 40개로 나뉘고 전부 직접 작성했음. 출시일 변경, 리랭커 선택, 품질 기준값 수정, 담당자 교체, 채용, 키 교체, 휴가 조정 같은 사안임.
- 유사 사안(30개). 정답 근거와 단어는 겹치지만 사실은 다른 메시지. 회귀 게이트 기준값(0.85)과 알림 기준값(350ms), 모델 리뷰와 디자인 리뷰처럼 헷갈리기 쉬운 것을 일부러 넣었음. 직접 작성했음.
- 일상 대화(4,149개). 위 두 종류 사이를 채우는 업무 잡담. LLM으로 생성했고, 정답 근거가 다루는 주제(출시, 리랭커, 온콜 등)를 자유롭게 언급하되 날짜, 수치, 선택, 담당자 같은 값은 말하지 않게 통제했음.

질문은 전부 직접 작성했고 질문마다 라벨 세 가지가 붙어 있음.

| 라벨 | 키 | 값 |
|---|---|---|
| Question Type | `type` | `current_state`(지금의 값) 272, `history`(바뀌기 전 값과 경위) 78, `personal_stance`(개인의 입장) 65 |
| Asker Access | `access` | `privileged`(질문자가 볼 수 있는 제한된 사실이 정답) 125, `unprivileged`(질문자가 볼 수 없는 사실이 걸림) 116, `unrestricted`(누가 물어도 같은 답) 174 |
| Question Pattern | `qtags` | `paraphrase` 54, `public_line` 38, `cross_channel` 29, `decoy` 27, `unprivileged_probe` 17, `provenance` 15, `update_chain` 9, `privileged_probe` 5. 없는 질문 236 |

설계의 자세한 내용은 [docs/DESIGN.md](docs/DESIGN.md)에 있음.

## Directories

```
data/luna2/              배포 데이터
  messages.jsonl         메시지 4,353개(시간순)
  questions.jsonl        질문 415개
  channels.json          채널별 멤버
  personas.json          등장인물 5명의 역할과 말투
  message_tags.jsonl     메시지별 라벨(정답 근거 여부, 사안 이름 등). 분석용이고 평가 대상 시스템에는 주지 않음
  transcript.html        날짜, 채널, 스레드 순으로 펼쳐 읽는 화면. 정답 근거가 강조되어 있음
  build/                 구축 기록. 계획, 일상 대화 원본, audit 결과, 통계
scenario/                직접 작성한 사양. 정답 근거와 유사 사안 메시지, 질문, 등장인물, 채널, 일상 대화 주제, 금지 규칙
synth/                   일상 대화 합성 파이프라인(계획, 렌더, audit, 조립, 검사, 검수)
scripts/                 실행 스크립트. build.sh(전체 구축), smoke.sh(연결 점검), verify.sh(배포 데이터 대조)
docs/                    설계, 구축 절차, 데이터 형식
```

## Data Format

메시지 한 건(`messages.jsonl`):
```json
{"id": "E16", "time": "2026-09-14T09:05", "channel": "#atlas-general", "speaker": "Mina", "reply_to": null, "text": "Legal approval is complete. We're officially moving the launch from September 30 to October 7."}
```

질문 한 건(`questions.jsonl`):
```json
{"id": "Q20", "asked_after": "E15", "asker": "Alex", "question": "What date are we launching?", "gold": ["September 30", "Sep 30"], "type": "current_state", "access": "unprivileged", "forbidden": ["October 7", "Oct 7", "Legal"], "stale_values": [], "pair": "P_launch_date", "note": "공지된 날짜만. 결정은 났지만 공지 전", "qtags": []}
```

`asked_after`는 그 id의 메시지까지 들어온 직후에 묻는다는 뜻임. `gold`는 정답으로 인정하는 표현들, `forbidden`은 질문자가 볼 수 없어서 답에 나오면 안 되는 사실, `stale_values`는 바뀌기 전 값이나 다른 사안의 값처럼 답으로 내면 틀리는 값임. 모든 파일과 필드의 설명은 [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md)에 있음.

## Construction

데이터는 직접 작성한 부분과 LLM으로 생성한 부분으로 나뉨. 정답에 영향을 주는 것(정답 근거, 유사 사안, 질문, 정답과 라벨)은 전부 직접 작성했고, LLM은 그 사이를 채우는 일상 대화의 문장만 만들었음. 그래서 일상 대화를 어느 모델로 다시 만들어도 정답 근거와 질문은 그대로임.

1. 사양 작성. 등장인물, 채널, 사안별 정답 근거 메시지, 유사 사안 메시지, 질문을 손으로 씀(`scenario/`).
2. 계획. 어느 날 어느 채널에서 누가 어떤 역할(원글, 동의, 반대, 질문, 답변 등)로 말하는지를 난수 seed로 정함. 문장은 아직 없음(`synth/plan.py`).
3. 렌더. 스레드 하나를 LLM 호출 한 번으로 문장화함. 등장인물의 말투와 메시지별 지시를 주고, 일상 대화가 말하면 안 되는 사실 40가지를 프롬프트에 명시함(`synth/render_llm.py`).
4. 금지 규칙 적용. 정규식 40개로 값이 새는지 검사해 걸리면 다시 요청하고, 정규식이 놓친 암시는 LLM audit으로 한 번 더 확인해 스레드 단위로 뺌(`synth/audit_filler.py`).
5. 조립. 정답 근거와 일상 대화를 시간순으로 합침(`synth/assemble.py`).
6. 검사와 검수. 시간 순서, 답글 관계, 금지 패턴, 질문 문구와 라벨 규칙을 자동으로 검사하고, 품질 지표와 읽기용 화면을 만들어 사람이 확인함(`synth/validate.py`, `synth/quality.py`, `synth/transcript.py`).

단계별 파라미터, 프롬프트, 배포 데이터를 만들 때의 실제 수치(호출 수, 제외된 스레드 수, 비용)는 [docs/CONSTRUCTION.md](docs/CONSTRUCTION.md)에 있음.

## Recreation

환경은 [uv](https://docs.astral.sh/uv/)로 구성함. 파이썬 3.12와 의존성(tiktoken)이 설치됨.

```bash
uv sync
```

배포 데이터가 이 리포의 사양과 코드에서 나온 것인지는 API 키 없이 확인할 수 있음. 계획을 seed로 다시 세워 기록과 대조하고, 기록된 일상 대화 원본을 다시 조립해 `messages.jsonl` 등 다섯 파일이 바이트 단위로 같은지 봄.

```bash
bash scripts/verify.sh
```

일상 대화를 처음부터 다시 생성하려면 OpenAI 호환 API가 필요함. `.env.example`을 `.env`로 복사해 키를 넣은 뒤 실행함. 결과는 `data/<이름>/`에 생김.

```bash
bash scripts/build.sh gpt-5.6-luna luna2-rerun 3 7 8
```

인자는 차례로 모델, 데이터 이름, scale(일상 대화 밀도), seed, 동시 호출 수임. 같은 seed와 scale이면 계획(누가 언제 어떤 역할로 말하는지)은 배포 데이터와 같고, 문장은 LLM 출력이라 실행마다 달라짐. 배포 데이터를 만든 실행의 일상 대화 원본은 `data/luna2/build/filler.jsonl`에 그대로 있음.

## Docs

- [docs/DESIGN.md](docs/DESIGN.md): 설정, 사안과 유사 사안, 질문과 라벨의 설계
- [docs/CONSTRUCTION.md](docs/CONSTRUCTION.md): 구축 절차, 파라미터, 프롬프트, 실행 기록
- [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md): 파일과 필드, 메시지 라벨 목록
