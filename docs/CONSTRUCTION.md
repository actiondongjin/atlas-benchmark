# 구축 절차

배포 데이터 `data/luna2/`가 어떻게 만들어졌는지와 다시 만드는 방법을 적음. 데이터가 담는 상황의 설계는 [DESIGN.md](DESIGN.md)에 있음.

## 1. 직접 작성한 부분과 생성한 부분

| 구분 | 내용 | 수 | 위치 |
|---|---|---|---|
| 직접 작성 | 등장인물과 말투 | 5명 | `scenario/personas.json` |
| 직접 작성 | 채널과 멤버 | 17개 | `scenario/channels.json` |
| 직접 작성 | 정답 근거 메시지 | 174개(사안 40개) | `scenario/core_messages.jsonl` |
| 직접 작성 | 유사 사안 메시지 | 30개(사안 24개) | `scenario/core_messages.jsonl` |
| 직접 작성 | 질문, 정답, 라벨 | 415개 | `scenario/questions.jsonl` |
| 직접 작성 | 일상 대화 주제 후보 | 채널별 합계 84개 | `scenario/topics.json` |
| 직접 작성 | 일상 대화 금지 규칙 | 정규식 40개, 보호 사실 설명 40개, 언급을 권하는 주제 24개 | `scenario/forbidden.json` |
| LLM 생성 | 일상 대화 메시지 | 4,149개 | `data/luna2/messages.jsonl`에서 id가 `F`로 시작하는 것 |

정답에 영향을 주는 것은 전부 직접 작성한 쪽에 있음. LLM이 만든 일상 대화는 정답 근거가 정하는 값을 말하지 못하게 통제했으므로, 일상 대화를 어느 모델로 만들어도 질문과 정답은 같음.

구축은 다음 순서로 진행됨. `scripts/build.sh`가 2번부터 7번까지를 차례로 실행함.

1. 사양 작성(`scenario/`)
2. 계획(`synth/plan.py`)
3. 렌더(`synth/render_llm.py`, 실행은 `synth/filler.py`)
4. LLM audit(`synth/audit_filler.py`)
5. 조립(`synth/assemble.py`)
6. 검사(`synth/validate.py`)
7. 검수 자료 생성(`synth/transcript.py`, `synth/tokens.py`, `synth/quality.py`)

## 2. 사양 작성

사안을 먼저 정하고, 사안마다 메시지를 시간순으로 썼음. 메시지에는 시각, 채널, 화자, 답글 대상, 본문과 함께 분석용 라벨(사안 이름, 제한된 채널의 발언인지, 결정이나 정정인지 등)을 붙였음. 정답 근거는 모두 평일에 두었음.

질문은 메시지를 쓴 뒤에 작성했음. 사안의 특정 메시지 직후를 묻는 시점으로 잡고, 그 시점에 질문자가 볼 수 있는 채널을 기준으로 정답과 답에 나오면 안 되는 값을 정했음. 같은 시점에 다른 사람이 같은 것을 묻는 질문을 함께 만들어 질문자 변형 세트를 구성했음.

`scenario/topics.json`은 일상 대화의 주제 후보임. 채널마다 그 채널에서 나올 법한 업무 이야기(배포, 알림, 리뷰 요청, 회의실, 점심 등)를 적었고, 정답 근거가 다루는 주제는 넣지 않았음.

`scenario/forbidden.json`은 일상 대화가 지켜야 할 규칙임. 5절에서 설명함.

## 3. 계획

`synth/plan.py`의 `make_plan(scenario, seed, scale)`이 일상 대화의 구조를 정함. 문장은 만들지 않고, 스레드마다 채널, 날짜, 주제와 메시지별 화자, 역할, 문체 결함, 시각을 정함. 같은 seed와 scale이면 결과가 항상 같음. 배포 데이터는 seed 7, scale 3임.

### 3.1 스레드의 수와 시각

- 기간. 정답 근거의 첫 날 하루 전부터 마지막 날까지의 평일.
- 원글 수. 평일마다 채널별로 `기대 수 x scale x 요일 가중치`만큼 뽑음. 값이 정수가 아니면 소수 부분의 확률로 하나를 더함.
- 요일 가중치. 월 1.15, 화 1.10, 수 1.00, 목 0.95, 금 0.80.
- 시각. 9시부터 17시 사이의 시를 가중치(9시 1.0, 10시 1.3, 11시 1.3, 12시 0.5, 13시 0.8, 14시 1.2, 15시 1.2, 16시 1.0, 17시 0.7)로 뽑고 분은 균등하게 뽑음. 오전과 오후에 많고 점심에 적음.

채널별 하루 원글 기대 수(scale 1 기준):

| 채널 | 기대 수 | 채널 | 기대 수 |
|---|---|---|---|
| `#model-dev` | 4.0 | `dm:Mina-Priya` | 1.0 |
| `#platform-ops` | 4.0 | `dm:Alex-Jordan-Priya` | 1.0 |
| `#atlas-general` | 3.0 | `dm:Mina-Alex` | 0.8 |
| `dm:Alex-Priya` | 1.5 | `dm:Priya-Jordan` | 0.8 |
| `dm:Alex-Jordan` | 1.2 | `dm:Mina-Jordan` | 0.5 |
| `#leadership` | 1.0 | `dm:Alex`, `dm:Priya` | 0.4 |
| `#partners` | 1.0 | `dm:Jordan`, `dm:Mina` | 0.3 |
| | | `dm:Sofia` | 0.2 |

### 3.2 정답 근거 주변에 두는 스레드

정답 근거 메시지가 일상 대화 없이 연달아 붙어 있으면 위치만으로 근거를 찾을 수 있음. 이를 줄이려고 두 가지 스레드를 추가로 둠.

- 직접 작성한 원글(정답 근거와 유사 사안)마다 0.8의 확률로, 같은 채널의 앞뒤 25분 안에 일상 대화 스레드를 하나 둠.
- 시간순으로 연속한 직접 작성 메시지 두 개의 간격이 30분 이내이면 0.7의 확률로 그 사이에 일상 대화 원글을 하나 둠. 채널은 앞 메시지와 같은 채널이거나 공개 채널 가운데 하나임.

배포 데이터에서 이렇게 둔 스레드는 166개이고, 시간순으로 이웃한 정답 근거 메시지 사이에 든 일상 대화 수의 중앙값은 6개임.

### 3.3 스레드 안의 구조

원글 다음에 답글이 이어질 확률은 매번 0.55이고 답글은 최대 4개임. 답글 사이의 간격은 2분에서 90분임. 답글의 역할은 다음 확률로 뽑음.

| 역할 | 추첨 확률 | 뜻 |
|---|---|---|
| `disagree` | 0.33 | 앞 내용에 반대하거나 조건을 달아 받아들임 |
| `question` | 0.15 | 앞 내용에 대한 짧은 확인 질문 |
| `ultrashort` | 0.15 | 초단답. 한 단어에서 세 단어의 반응 |
| `agree` | 0.37 | 동의하거나 확인함 |

추첨한 뒤에 스레드가 하나의 대화로 읽히도록 다음 규칙을 적용함.

- 답글 화자는 아직 말하지 않은 사람 가운데서 먼저 뽑고, 직전 화자와 겹치지 않게 함. 둘뿐인 DM에서는 번갈아 말하게 됨.
- 반대는 한 스레드에 한 사람만 함. 이미 다른 사람이 반대했으면 동의로 바꿈.
- 같은 사람이 다시 말할 때는 앞에서 취한 입장을 지킴.
- 원글 작성자가 다시 말할 때는 반대나 초단답을 하지 않음.
- 직전 메시지가 질문이면 다음 답글의 역할은 `answer`이고, 원글 작성자가 답함. 질문한 사람이 원글 작성자이면 다른 멤버가 답함.
- 스레드가 질문으로 끝나면 0.85의 확률로 답을 하나 더 붙임. 나머지는 답이 없는 질문으로 남음.

원글의 5%는 업무와 무관한 이야기(`drift`)임. 주제는 날씨, 점심, 통근, 프린터 고장 등 18가지를 스레드 번호에 따라 돌려 씀. 주제 이탈은 원글에만 두고 답글에는 두지 않음.

문체 결함은 메시지의 약 20%에 하나씩 붙임. 종류는 오타 하나(`typo`), 말줄임으로 끝나는 문장 조각(`fragment`), 끝에 붙는 자기 수정(`selfcorrection`), 전부 소문자(`lowercase`) 넷임.

주제 이탈 5%, 반대 25%(답글 기준), 문체 결함 20%라는 목표 비율은 GroupMemBench의 설정을 따랐음. 반대의 추첨 확률이 0.33인 것은 위 규칙을 거친 뒤 답글의 약 25%가 되도록 맞춘 값임.

### 3.4 정답 주제의 언급과 채널 간 참조

일상 대화가 정답 근거의 주제를 전혀 언급하지 않으면, 주제 단어만으로 정답 근거를 찾을 수 있게 됨. 그래서 스레드의 25%는 원글에서 정답 근거의 주제 하나를 값 없이 지나가며 언급하게 함(`touch`). 언급할 주제는 `scenario/forbidden.json`의 `touch_topics` 24개에서 뽑음. 예: "launch prep in general (checklist, comms plan, rollback notes) without any date or timing".

스레드의 10%는 원글에서 다른 공개 채널을 이름으로 언급하게 함(`xref`). 예: "as posted in #platform-ops".

### 3.5 배포 데이터의 계획

| 항목 | 값 |
|---|---|
| 스레드 | 2,037개 |
| 메시지 | 4,210개 |
| 메시지 1개짜리 스레드 | 1,012개 |
| 역할별 메시지 수 | 원글 1,942, 주제 이탈 원글 95, 동의 897, 반대 489, 질문 276, 초단답 264, 답변 247 |
| `touch` 스레드 | 517개 |
| `xref` 스레드 | 222개 |

계획은 `data/luna2/build/plan.jsonl`에 있음.

## 4. 렌더

`synth/render_llm.py`가 계획의 스레드 하나를 chat completion 호출 한 번으로 문장화함. 스레드의 모든 메시지를 한 번에 요청하므로 답글이 앞 메시지의 내용을 받아서 이어짐.

### 4.1 호출 설정

| 항목 | 값 |
|---|---|
| 모델 | gpt-5.6-luna |
| temperature | 0.8 |
| max_tokens | 4,096(추론 토큰 포함) |
| seed | 7(API의 seed 인자로도 전달) |
| 스레드당 시도 | 최대 3회 |
| 동시 호출 | 8 |

### 4.2 시스템 프롬프트

모든 호출에 같은 시스템 프롬프트를 씀. 내용은 다음과 같고 전문은 `render_llm.system_prompt()`에 있음.

- 배경. Atlas라는 검색 제품을 만드는 팀의 사내 채팅 메시지를 쓴다는 것, 팀원 5명과 역할, 쓸 수 있는 서비스 이름 8개.
- 식별자. 티켓 번호, PR 번호, 데이터 컷은 요청에 적힌 것만 쓰고 새로 지어내지 않음.
- 화자. 말투 카드를 따르고 1인칭으로 씀. 자기 이름을 부르지 않음. 채널에 없는 사람을 언급할 수는 있지만 직접 부르지 않음.
- 스레드. 순서대로 읽히는 하나의 대화임. 답글은 앞 메시지에 반응하고, 같은 화자가 앞에서 한 말과 어긋나지 않으며, 질문 다음 항목은 그 질문에 답함.
- 팀의 진행 중인 주제(출시, 리랭커, 품질 게이트, 스탠드업, 마이그레이션, 프리즈 등)는 자연스러우면 지나가며 언급해도 됨.
- 보호 사실 40개의 목록. 이에 대해 어떤 값도 말하거나, 추측하거나, 암시하지 않음. "next week", "the new date", "the one we picked" 같은 모호한 표현도 금지함.
- 출력은 JSON만. `{"messages": [{"k": 0, "text": "..."}, ...]}`

### 4.3 스레드별 요청

유저 메시지는 JSON이고 다음을 담음.

- 채널, 멤버, 날짜, 주제(`scenario/topics.json`에서 뽑은 것).
- 이 스레드에서만 쓸 수 있는 식별자: 티켓 번호 2개(`ATL-200`에서 `ATL-480`), PR 번호 1개, 데이터 컷(`v2.2`에서 `v2.9`), 서비스 버전. 스레드 번호로 정해지므로 다시 실행해도 같음.
- 스레드에 등장하는 사람의 말투 카드(`scenario/personas.json`).
- 메시지별 항목: 순번, 화자, 역할, 시각, 지시문.

식별자를 스레드마다 따로 주는 것은, 프롬프트에 예시 식별자를 하나만 두었을 때 모델이 모든 메시지에 같은 번호를 복사했기 때문임. 데이터 컷을 v2.x로 한정한 것은 정답 근거의 데이터 컷(v3.4, v3.5, v3.6)과 겹치지 않게 하기 위함임.

역할별 지시문:

| 역할 | 지시문 |
|---|---|
| `post` | Start a new thread about this topic: {topic}. Two to four sentences. Concrete details are fine; when you need an id, use only the ones in ids_you_may_use. |
| `agree` | Reply to the thread. Agree with or acknowledge what was actually said in the message(s) before, in one or two short sentences. Do not offer to take on work that nobody asked this speaker to do. |
| `disagree` | Reply to the thread. Push back on, or accept only conditionally, what was said before, and give one concrete reason. One or two sentences. Stay consistent with anything this speaker already said in the thread. |
| `question` | Reply to the thread with one short clarifying question about something specific that was said in it. |
| `answer` | Reply to the thread by answering the question asked in the message just before this one. Be concrete and consistent with what the thread already says. One or two sentences. |
| `ultrashort` | Reply with a reaction of one to three words that fits the message just before this one (a brief acknowledgement or agreement). Do not claim to take on a task unless the thread asked this speaker to do something. |
| `drift` | Start a casual off-topic aside that is not about work, about: {drift_theme}. One or two sentences. |

원글에 `touch`나 `xref`가 있으면 지시문 뒤에 한 문장이 붙고("Also mention, in passing and without stating any protected fact: ..."), 문체 결함이 있으면 그 지시("Include exactly one realistic typo." 등)가 붙음. 멤버가 한 명인 DM은 어시스턴트에게 보내는 메시지라는 설명을, 주제 이탈로 시작하는 스레드는 답글도 그 이야기에 가볍게 반응하라는 설명을 덧붙임.

### 4.4 재요청

출력을 받으면 메시지마다 금지 정규식을 검사함. 하나라도 걸리면 어느 항목이 어느 규칙에 걸렸는지를 덧붙여 스레드 전체를 다시 요청함. JSON 파싱이 안 되거나 항목이 빠진 경우도 다시 요청함. 3회 안에 통과하지 못한 스레드는 데이터에서 뺌.

## 5. 금지 규칙

일상 대화는 정답 근거의 주제 단어(launch, reranker, threshold, migration 등)를 자유롭게 쓰되, 그 주제의 값(날짜, 수치, 선택, 담당자, 사유, 결정)은 말하거나 암시하지 못함. 일상 대화가 값을 말하면 정답 근거와 어긋나는 사실이 데이터에 생기거나, 제한된 채널의 사실이 다른 채널에 나타나 질문의 정답이 달라질 수 있기 때문임. 유사 사안의 값도 같은 방식으로 막음.

처음에는 주제 자체를 일상 대화에서 막았음. 그러자 주제 단어가 정답 근거에만 남아서 단어 검색만으로 정답 근거를 쉽게 찾을 수 있었음. 그래서 주제는 허용하고 값만 막는 방식으로 바꿨음.

규칙은 세 단계로 적용됨.

| 단계 | 방법 | 적용 시점 |
|---|---|---|
| 프롬프트 | 보호 사실 40개의 목록(`facts`)을 시스템 프롬프트에 넣고 암시도 금지함 | 렌더 |
| 정규식 | 패턴 40개(`patterns`). 값 자체(September 30, 0.82, v3.x, 9:30 등)와 "주제 단어와 값이나 변화 표현이 한 문장 안에서 50자 이내"인 조합 | 렌더 직후(재요청), 조립, 검사 |
| LLM audit | 정규식이 놓친 암시를 LLM에 물어 확인함 | 렌더 뒤, 조립 전 |

정규식의 예(`threshold_value`):
```
\b0\.[78]\d\b|regression[- ]gate[^.!?\n]{0,50}\d|(gate|threshold|cutoff)[^.!?\n]{0,50}(is|to|at|of|=)\s*0?\.\d|(quality|minimum) score[^.!?\n]{0,50}\d
```

정규식은 정상 문장에 잘못 걸리기 쉬움. "signs off"의 off, "Alex covers ingest"의 covers, "dropdown"의 down이 그런 예였음. 그래서 패턴을 주장 단위로 좁히고 단어 경계를 두었으며, 새 패턴은 이미 만들어 둔 일상 대화에 먼저 돌려 잘못 걸리는 문장이 없는지 확인한 뒤에 넣었음.

금지 규칙은 일상 대화에만 적용함. 정답 근거와 유사 사안은 값을 말하는 것이 목적이므로 대상이 아님.

## 6. LLM audit

`synth/audit_filler.py`가 렌더된 일상 대화를 한 번 더 확인함.

- 대상. 주제와 관련된 단어(launch, rerank, gate, threshold, freeze, hiring, key, on-call 등)가 하나라도 들어 있는 메시지만 보냄.
- 요청. 메시지 15개를 한 배치로 보내고, 보호 사실 목록을 준 뒤 메시지마다 그 가운데 어느 값이라도 말하거나 암시하는지를 물음. temperature 0, 출력은 JSON 스키마로 받음.
- 허용 목록. 프롬프트에 걸지 말아야 할 것을 명시함. v2.x 데이터 컷, 날짜 없는 출시 준비 이야기, 누가 주말 당번인지 말하지 않는 온콜 이야기, 일반적인 채용 절차나 휴가 이야기, 일상 업무에 대한 개인 의견 등임. 이 목록이 없으면 주제를 언급했다는 이유만으로 과하게 걸었음.
- 처리. 걸린 메시지가 속한 스레드를 통째로 뺌. 3회 요청해도 출력을 해석할 수 없는 배치는 그 배치의 스레드를 모두 뺌.

프롬프트 전문은 `audit_filler.SYSTEM`에 있고, 결과는 `data/luna2/build/audit.json`에 걸린 메시지와 이유까지 남아 있음.

## 7. 조립

`synth/assemble.py`가 정답 근거와 일상 대화를 합침. LLM을 쓰지 않으므로 같은 입력에서는 항상 같은 결과가 나옴.

1. audit에서 걸린 스레드와 금지 정규식에 걸리는 스레드를 뺌.
2. 정답 근거와 유사 사안은 사양에 적힌 시각을 그대로 씀. 일상 대화는 계획의 시각을 쓰되, 이미 다른 메시지가 있는 시각이면 1분씩 뒤로 밀림. 그래서 모든 메시지의 시각이 서로 다름.
3. 시간순으로 정렬하고 일상 대화에 `F0001`부터 id를 붙임. 답글의 `reply_to`는 스레드 원글의 id임.
4. `messages.jsonl`과 `message_tags.jsonl`을 쓰고, 질문, 채널, 등장인물 파일은 사양에서 그대로 복사함.

## 8. 검사와 검수

`synth/validate.py`는 규칙 위반을 자동으로 검사하고 통계를 냄(`build/report.json`).

- 메시지가 시간순인지, id가 겹치지 않는지
- 정답 근거 메시지의 순서가 사양과 같은지, 모두 평일에 있는지
- 답글이 원글보다 늦고 같은 채널에 있는지
- 일상 대화가 금지 정규식에 걸리지 않는지
- 질문의 `asked_after`가 가리키는 메시지가 있는지
- 질문 문구와 라벨 규칙([DESIGN.md](DESIGN.md) 6.7절)
- 질문 분포가 목표 범위 안인지(벗어나면 경고)

`synth/quality.py`는 규칙으로 잡기 어려워 사람이 읽으며 확인하던 것을 숫자로 냄(`build/quality.json`). `synth/transcript.py`는 날짜, 채널, 스레드 순으로 펼친 읽기용 화면을 만들고(`transcript.html`), 검수할 때 정답 근거 주변의 일상 대화가 흐름을 깨지 않는지를 하루 단위로 읽었음.

배포 데이터의 품질 지표:

| 지표 | 값 | 뜻 |
|---|---|---|
| `drift_in_reply` | 0 | 답글 자리에 든 주제 이탈 |
| `stance_flip_threads` | 0 | 같은 사람이 동의와 반대를 모두 한 스레드 |
| `repeat_speaker_threads` | 192 | 같은 사람이 답글을 두 번 이상 단 스레드. 둘뿐인 DM에서 번갈아 말한 경우임 |
| `unanswered_questions` | 27 | 답이 붙지 않은 질문 |
| `avg_words` | 27.7 | 일상 대화 메시지의 평균 단어 수 |
| `ticket_kinds`, `ticket_top_share` | 281, 0.01 | 등장한 티켓 번호의 종류와 가장 흔한 번호의 비중 |
| `self_name_mentions` | 17 | 화자가 자기 이름을 언급한 메시지 |
| `nonmember_address` | 0 | 채널에 없는 사람을 직접 부른 메시지 |
| `exact_dups_excl_ultrashort` | 0 | 완전히 같은 문장(초단답 제외) |
| `basis_gap_median` | 6 | 이웃한 정답 근거 사이에 든 일상 대화 수의 중앙값 |

정답 근거의 주제 단어가 일상 대화에 얼마나 나오는지도 같은 파일에 있음(`topic_word_filler`). dashboard 167, credential 관련 126, reindex 81, launch 61, gpu 53, reranker 48, freeze 45 등임.

실제 비율은 주제 이탈이 전체 메시지의 2.3%, 반대가 답글의 22.2%, 문체 결함이 전체 메시지의 22.6%임(`build/report.json`의 `ratios`). 주제 이탈이 목표인 5%보다 낮은 것은 원글에만 두기 때문이고, 문체 결함에는 초단답이 함께 집계됨.

## 9. 배포 데이터의 실행 기록

| 항목 | 값 |
|---|---|
| 실행일 | 2026-09-06 |
| 명령(이 리포의 스크립트 기준) | `bash scripts/build.sh gpt-5.6-luna luna2 3 7 8` |
| 모델 | 생성과 audit 모두 gpt-5.6-luna |
| 계획 | 스레드 2,037개, 메시지 4,210개 |
| 렌더 호출 | 2,177회(재요청 포함). 입력 3.66M 토큰, 출력 0.56M 토큰 |
| 재요청으로 통과한 스레드 | 12개 |
| 3회 안에 통과하지 못해 뺀 스레드 | 14개 |
| 렌더 결과 | 스레드 2,023개, 메시지 4,174개 |
| audit 대상 | 메시지 1,980개, 132배치. 입력 0.33M 토큰, 출력 0.06M 토큰 |
| audit에서 걸린 것 | 메시지 13개, 스레드 12개. 그 스레드의 메시지 25개를 뺌 |
| 최종 일상 대화 | 스레드 2,011개, 메시지 4,149개 |
| 비용 | 생성 약 1.4달러, audit 약 0.14달러(입력 100만 토큰당 0.2달러, 출력 1.2달러 기준) |
| 소요 시간 | 생성 약 15분, audit 약 1분 |

재요청의 사유는 대부분 `dashboard_outage` 패턴이었음(48건). 일상 대화 주제에 대시보드가 자주 나오는데 이 패턴이 대시보드와 장애, 인증서를 함께 말하는 문장을 막기 때문임. 나머지는 `key_rotation` 4건과 그 밖의 패턴 각 1건임. 기록은 `data/luna2/build/render_meta.jsonl`에 있음.

## 10. 재현 범위

| 단계 | 재현 | 확인 방법 |
|---|---|---|
| 계획 | seed와 scale이 같으면 완전히 같음 | `scripts/verify.sh`가 `build/plan.jsonl`과 대조 |
| 렌더 | LLM 출력이라 실행마다 문장이 달라짐 | 배포 데이터에 쓰인 출력은 `build/filler.jsonl`에 그대로 있음 |
| LLM audit | LLM 출력이라 달라질 수 있음 | 배포 데이터에 쓰인 결과는 `build/audit.json`에 있음 |
| 조립, 검사 | 같은 입력이면 완전히 같음 | `scripts/verify.sh`가 다시 조립해 다섯 파일을 바이트 단위로 대조 |

`scripts/build.sh`로 처음부터 다시 만들면 계획은 같고 일상 대화의 문장이 다른 데이터가 나옴. 정답 근거, 유사 사안, 질문은 그대로이므로 같은 질문으로 평가할 수 있음. 다른 모델로 생성하려면 모델 이름만 바꾸면 되고, OpenAI 호환 서버(vLLM 등)는 `OPENAI_BASE_URL`로 지정함.

LLM 응답은 `.cache/`에 저장됨. 사양과 프롬프트가 같으면 다시 실행할 때 저장된 응답을 쓰므로 비용이 들지 않고, 바뀐 스레드만 새로 호출함. 호출 기록은 `runs/<실행 id>/calls.jsonl`에 남음.

## 부록. 개정 이력

현재 사양은 v0.1이고, 배포 데이터 luna2는 v0.1 사양으로 만든 두 번째 데이터임. 그 전의 데이터는 이 리포에 없음.

| 시점 | 내용 |
|---|---|
| 2026-09-03 | 사양 v0. 정답 근거 96개, 질문 181개, 채널 12개. gpt-5.6-luna, scale 2.5로 일상 대화 생성 |
| v0 검수 중 | 예시 티켓 번호가 모든 일상 대화에 복사됨 -> 스레드별 식별자 지정. 주제 이탈이 날씨와 점심에 몰림 -> 주제를 돌려 씀. 정답 근거가 주말에 있고, 요일별 차이가 없고, 정답 근거끼리 붙어 있음 -> 사양의 시각 이동, 요일과 시간대 가중치, 정답 근거 주변의 일상 대화 추가. 주제 단어만으로 정답 근거가 찾아짐 -> 금지 규칙을 주제 단위에서 사실 단위로 바꾸고 LLM audit과 유사 사안 추가 |
| 2026-09-05 | 사양 v0.1. 다섯 형태의 사안 23개, 유사 사안 메시지 16개, 채널 5개(`#partners`, `dm:Mina-Alex`, `dm:Mina-Jordan`, `dm:Priya-Jordan`, `dm:Alex-Jordan-Priya`), 질문 234개를 추가하고 기간을 10월 9일까지 늘림. 금지 정규식 20개를 새로 쓰고 기존 일상 대화 2,690개에서 잘못 걸리는 문장이 없도록 다듬음 |
| 2026-09-05 | v0.1의 첫 데이터(luna1). 읽어 보니 스레드가 대화로 읽히지 않았음. 답글 자리의 주제 이탈 96개, 같은 사람이 두 번 이상 답한 스레드 460개, 동의한 사람이 뒤에서 반대하는 스레드 196개, 답이 없는 질문 다수. 계획 단계가 답글의 역할과 화자를 서로 독립으로 뽑았기 때문이었음 |
| 2026-09-06 | 계획 규칙 수정(3.3절의 규칙) 뒤 다시 생성한 것이 luna2. 답글 자리의 주제 이탈 0, 입장이 뒤집힌 스레드 0 |
