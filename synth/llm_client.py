"""OpenAI 호환 chat completions 클라이언트. 표준 라이브러리만 씀. 일상 대화 생성(filler.py)과 LLM audit(audit_filler.py)이 함께 씀.

base_url 예: https://api.openai.com/v1 , http://localhost:8002/v1 (vLLM)
캐시: (model, messages, temperature, seed)가 같으면 cache_dir에서 바로 돌려줌. 재실행 비용 0
로그: 모든 호출을 log_path(jsonl)에 남김. 요청 messages 원문, 응답 원문, 프롬프트 해시, 토큰 사용량, 지연, 캐시 여부
mock: 네트워크 없이 배관을 시험하기 위한 가짜 응답. 마지막 user 메시지의 JSON에서 items를 읽어 그대로 돌려줌
"""
import hashlib, json, os, re, sys, threading, time, urllib.error, urllib.request


def extract_json(text):
    """모델 출력에서 첫 JSON 객체를 꺼냄. 코드펜스와 앞뒤 잡문 허용. 생성과 audit 코드가 같은 파서를 씀"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("JSON 객체 없음")
    return json.loads(t[a:b + 1])


# urllib 기본 User-Agent(Python-urllib/x.y)는 Cloudflare 뒤의 프록시가 403 'error code: 1010'으로 막음. 이름을 하나 정해서 보냄
USER_AGENT = os.environ.get("OPENAI_USER_AGENT", "atlas-llm-client/1.0")


def json_schema_format(name, schema):
    """structured outputs용 response_format. strict라 스키마에 맞는 JSON만 나옴(OpenAI, vLLM guided decoding).
    서버가 400으로 거부하면 클라이언트가 이 인자를 빼고 같은 요청을 다시 보내므로 호출 쪽은 파싱 실패에 대비해야 함"""
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


class LLMClient:
    """OpenAI 호환 chat completions 호출기. 캐시, 로그, 재시도, 파라미터 자동 조정을 맡음. 스레드 여러 개에서 동시에 써도 됨"""
    def __init__(self, model, base_url=None, api_key=None, temperature=0.8, max_tokens=1500, timeout=120,
                 retries=4, cache_dir=None, log_path=None, mock=False, seed=None, reasoning_effort=None):
        """model과 접속 정보(base_url, api_key는 환경변수 OPENAI_BASE_URL/OPENAI_API_KEY로 대체 가능), 샘플링 인자, 캐시 폴더, 로그 경로, mock 여부를 받음"""
        self.model = model
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or "EMPTY"
        self.temperature, self.max_tokens, self.timeout, self.retries = temperature, max_tokens, timeout, retries
        self.cache_dir, self.log_path, self.mock, self.seed = cache_dir, log_path, mock, seed
        self.reasoning_effort = reasoning_effort  # None이면 보내지 않음(API 기본값). 추론 모델은 "low"/"medium"/"high"
        self.lock = threading.Lock()
        self.max_key, self.dropped = "max_tokens", set()  # 400 응답에 따라 바뀜
        self.fatal = None  # 401/403/404처럼 다시 보내도 같은 결과인 오류 메시지. 한 번 나면 이후 호출은 서버에 보내지 않고 바로 실패
        self.stats = {"calls": 0, "cached": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        if self.log_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)

    # ------------------------------------------------------------------ 공개 API
    def chat(self, messages, tag="", response_format=None):
        """messages(chat 형식)를 보내 답 문자열을 돌려줌. 같은 요청은 캐시에서 돌려주고, 호출마다 log_path에 한 줄 남김.
        response_format은 json_schema_format()으로 만든 structured outputs 지정(없으면 자유 텍스트).
        본문이 비었거나 길이 제한으로 잘린 응답은 캐시하지 않음. 추론 모델이 max_tokens를 추론에 다 써서 빈 답을 낸 경우가 캐시에 굳는 것을 막기 위함"""
        kd = {"m": self.model, "msgs": messages, "t": self.temperature, "s": self.seed, "re": self.reasoning_effort}
        if response_format is not None:
            kd["rf"] = response_format  # 지정한 경우에만 키에 넣어 기존 캐시 키를 그대로 유지
        key = hashlib.sha256(json.dumps(kd, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        cached = self._cache_get(key)
        if cached is not None and not cached.get("content"):
            cached = None  # 예전 버그로 남은 빈 응답은 없는 것으로 침
        if cached is not None:
            with self.lock:
                self.stats["cached"] += 1
            self._log(tag, key, cached.get("usage", {}), 0.0, True, None, messages, cached["content"], cached.get("finish_reason"))
            return cached["content"]
        if self.fatal:
            raise RuntimeError(f"LLM 호출 중단: {self.fatal}")
        t0 = time.time()
        try:
            if self.mock:
                content, usage, finish = self._mock(messages), {}, "stop"
            else:
                content, usage, finish = self._post(messages, response_format)
        except RuntimeError as e:
            self._log(tag, key, {}, time.time() - t0, False, str(e)[:400], messages, None, None)
            raise
        dt = time.time() - t0
        with self.lock:
            self.stats["calls"] += 1
            self.stats["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
            self.stats["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        if content and finish != "length":
            self._cache_put(key, {"content": content, "usage": usage, "model": self.model, "finish_reason": finish})
        self._log(tag, key, usage, dt, False, None if content else "empty content (finish_reason=%s)" % finish, messages, content, finish)
        return content

    # ------------------------------------------------------------------ 내부
    def _body(self, messages, response_format=None):
        """현재 파라미터 상태로 요청 본문을 만듦. 400으로 거부된 파라미터는 빠지거나 이름이 바뀜"""
        body = {"model": self.model, "messages": messages}
        if response_format is not None and "response_format" not in self.dropped:
            body["response_format"] = response_format
        if "temperature" not in self.dropped:
            body["temperature"] = self.temperature
        if self.seed is not None and "seed" not in self.dropped:
            body["seed"] = self.seed
        body[self.max_key] = self.max_tokens
        if self.reasoning_effort and "reasoning_effort" not in self.dropped:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    def _adapt_400(self, msg):
        """서버가 거부한 파라미터를 빼거나 이름을 바꿈. OpenAI 최신 모델은 max_tokens 대신 max_completion_tokens를 받고,
        일부 모델은 temperature, seed, reasoning_effort를 받지 않음. vLLM은 둘 다 받음. 고치면 True."""
        low = msg.lower()
        if "max_tokens" in low:
            self.max_key = "max_completion_tokens"; return True   # 다른 워커가 이미 고쳤어도 이 요청은 다시 보냄
        for p in ("temperature", "seed", "reasoning_effort"):
            if p in low:
                self.dropped.add(p); return True
        if "response_format" in low or "json_schema" in low:
            self.dropped.add("response_format"); return True  # structured outputs 미지원 서버. 자유 텍스트로 받고 호출 쪽이 파싱함
        return False

    def _post(self, messages, response_format=None):
        """실제 HTTP 호출. 반환: (본문, usage, finish_reason). 408/429/5xx는 지수 백오프로 재시도, 400은 _adapt_400으로 파라미터를 고쳐 재요청, 그 외는 실패로 올림.
        401/403/404(키, 권한, 모델명 문제)와 크레딧 소진 429는 self.fatal에 기록해 이후 모든 호출을 막음. 수백 개 스레드가 같은 오류로 헛돌지 않게 하기 위함.
        성공한 응답은 캐시에 남으므로 크레딧을 채운 뒤 같은 명령을 다시 돌리면 실패한 호출만 새로 나감"""
        last = None
        for attempt in range(self.retries + 1):
            data = json.dumps(self._body(messages, response_format)).encode()
            req = urllib.request.Request(self.base_url + "/chat/completions", data=data, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    out = json.loads(r.read().decode())
                choice = out["choices"][0]
                return choice["message"].get("content") or "", out.get("usage", {}) or {}, choice.get("finish_reason")
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="ignore")[:400]
                last = f"HTTP {e.code}: {msg}"
                if e.code == 400 and self._adapt_400(msg):
                    continue  # 파라미터를 고쳐 바로 재요청
                quota = e.code == 429 and ('insufficient_quota' in msg or 'credit_balance_exhausted' in msg)  # 크레딧 소진. 속도 제한 429와 다름
                if e.code in (401, 403, 404) or quota:
                    with self.lock:
                        first, self.fatal = self.fatal is None, last
                    if first:
                        print(f"[llm_client] {last[:300]}\n[llm_client] 키, 권한, 모델명, 크레딧 오류라 이후 호출을 보내지 않음. "
                              f"OPENAI_API_KEY, OPENAI_BASE_URL, 모델명을 확인 (base_url={self.base_url}, model={self.model})", file=sys.stderr)
                    break
                if e.code not in (408, 409, 425, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, OSError, KeyError, json.JSONDecodeError) as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(min(30, 2 ** attempt))
        with self.lock:
            self.stats["errors"] += 1
        raise RuntimeError(f"LLM 호출 실패: {last}")   # 로그는 chat()이 요청 원문과 함께 남김

    def _mock(self, messages):
        """네트워크 없이 배관을 시험하기 위한 가짜 응답. 마지막 user 메시지의 JSON items를 읽어 형식만 맞춘 답을 돌려줌"""
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        try:
            obj = extract_json(user)
        except Exception:
            return json.dumps({"messages": []})
        items = obj.get("items", [])
        out = []
        for it in items:
            text = it.get("text") or f"{it.get('speaker', '?')} on {obj.get('topic', 'something')} [{it.get('role', '')}]"
            if it.get("text"):
                text = "(rewritten) " + text
            out.append({"k": it.get("k"), "text": text})
        return json.dumps({"messages": out})

    def _cache_path(self, key):
        """캐시 파일 경로(cache_dir/모델명/키.json)"""
        return os.path.join(self.cache_dir, self.model.replace("/", "_"), key + ".json") if self.cache_dir else None

    def _cache_get(self, key):
        """캐시에 있으면 저장된 응답 dict, 없으면 None. 다른 프로세스가 쓰는 중이라 비었거나 깨진 파일은 없는 것으로 침"""
        p = self._cache_path(key)
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _cache_put(self, key, obj):
        """응답을 캐시에 저장"""
        p = self._cache_path(key)
        if p:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"   # 임시 파일에 다 쓴 뒤 바꿔치기. 같은 캐시를 쓰는 다른 실행이 반쯤 쓴 파일을 읽지 않게 함
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            os.replace(tmp, p)

    def _log(self, tag, key, usage, dt, cached, error, messages=None, content=None, finish=None):
        """호출 한 건을 jsonl 한 줄로 기록(시각, 태그, 키, 모델, 토큰 사용량, 지연, 캐시 여부, 오류, 요청 messages 원문, 응답 원문)"""
        if not self.log_path:
            return
        row = {"kind": "llm", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "key": key, "model": self.model, "usage": usage,
               "latency_s": round(dt, 2), "cached": cached, "finish_reason": finish, "error": error, "messages": messages, "content": content}
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
