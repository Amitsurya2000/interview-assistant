import asyncio
import json
import os
import time
import re
import hashlib
from typing import Optional, List, Dict, Any
from collections import deque
from dataclasses import dataclass

import numpy as np
import websockets
from faster_whisper import WhisperModel

HOST = os.getenv("SERVER_HOST", "0.0.0.0")
PORT = int(os.getenv("SERVER_PORT", "8123"))
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

SAMPLE_RATE = 16000
WINDOW_SECONDS = 6.0
HOP_SECONDS = 0.8
ENERGY_GATE = 1e-4
INITIAL_PROMPT = "Software engineering, system design, algorithms, data structures, cloud computing, technical interview."
DEBUG = os.getenv("DEBUG", "1") == "1"

SENTENCE_SPLIT_RE = re.compile(r'(?<=[.?!])\s+')
WIN_BYTES = int(SAMPLE_RATE * 2 * WINDOW_SECONDS)
HOP_BYTES = int(SAMPLE_RATE * 2 * HOP_SECONDS)
MAX_BUFFER_BYTES = WIN_BYTES * 2

clients: Dict[websockets.WebSocketServerProtocol, Any] = {}
latest_text = ""
sentence_buf = ""
detected = deque(maxlen=500)
qa_log = deque(maxlen=200)
transcript_lines: List[str] = []
MAX_TRANSCRIPT_LINES = 5000
pcm_buf = bytearray()
bytes_since_last = 0
audio_lock = asyncio.Lock()
server_shutdown = asyncio.Event()
transcriber_task: Optional[asyncio.Task] = None
whisper: Optional[WhisperModel] = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


COACHING_PATTERNS = [
    r"\bHappy to elaborate if useful\b.*", r"\bLet me know if you'd like more details\b.*",
    r"\bYou (?:should|could|can)\b.*", r"\bYou demonstrate\b.*",
    r"\bOne (?:improvement|area to improve)\b.*", r"\bYour answer\b.*",
]


def sanitize_candidate_voice(text: str) -> str:
    for pat in COACHING_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE)
    text = re.sub(r"\byou\b", "the interviewer", text, flags=re.IGNORECASE)
    text = re.sub(r"\byour\b", "the", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" .")


@dataclass
class ConversationSegment:
    text: str
    timestamp: float


@dataclass
class QuestionCandidate:
    question: str
    context: str
    timestamp: float
    confidence: float = 0.7
    urgency: str = "medium"
    topic_area: str = "general"


class LLMAnalyzer:
    def __init__(self):
        self.enabled = True
        self.ollama_model = OLLAMA_MODEL
        self.last_llm_emit = 0.0
        self.seen_questions: Dict[str, float] = {}
        self.answers_timestamps = deque(maxlen=64)
        self.sem = asyncio.Semaphore(3)

        try:
            import ollama
            ollama.list()
            print(f"[llm] Ollama ready (model: {OLLAMA_MODEL})")
        except Exception as e:
            print(f"[llm] Ollama not available: {e}. Disabling LLM.")
            self.enabled = False

    async def analyze_segment(self, segment: ConversationSegment) -> List[QuestionCandidate]:
        if not self.enabled:
            return []
        text = segment.text.strip()
        text_lower = text.lower()
        question_indicators = [
            "what", "how", "why", "when", "where", "which", "who", "whose",
            "can", "could", "would", "should", "will", "shall", "may", "might",
            "do", "does", "did", "is", "are", "was", "were", "has", "have",
            "tell me", "explain", "describe", "define",
        ]
        has_indicator = any(indicator in text_lower for indicator in question_indicators)
        if not has_indicator and len(text.split()) < 4:
            return []

        async with self.sem:
            try:
                import ollama
                client = ollama.Client(host=OLLAMA_BASE_URL)
                prompt = f"""Extract interview questions from this dialogue transcript.
Return ONLY a JSON array of question strings. Examples:

Input: "What's the best way to handle VPC peering?"
Output: ["What's the best way to handle VPC peering?"]

Input: "Can you tell me about your experience with AWS?"
Output: ["Can you tell me about your experience with AWS?"]

Dialogue: {text}

JSON array:"""
                response = client.chat(
                    model=self.ollama_model,
                    messages=[
                        {'role': 'system', 'content': 'Extract questions from dialogue. Return only JSON arrays.'},
                        {'role': 'user', 'content': prompt}
                    ]
                )
                content = response['message']['content'].strip()
                if '```' in content:
                    for part in content.split('```'):
                        if part.strip().startswith('json'):
                            content = part.replace('json', '', 1).strip()
                        elif part.strip().startswith('['):
                            content = part.strip()
                if '[' in content:
                    start = content.index('[')
                    end = content.rindex(']') + 1
                    content = content[start:end]
                try:
                    questions = json.loads(content)
                    if not isinstance(questions, list):
                        return []
                    candidates = []
                    for q in questions[:3]:
                        if isinstance(q, str) and len(q.strip()) > 3:
                            q_clean = q.strip()
                            if not q_clean.endswith('?'):
                                q_clean += '?'
                            candidates.append(QuestionCandidate(
                                question=q_clean, context=text,
                                timestamp=time.time(), confidence=0.8,
                                urgency="medium", topic_area="technical"
                            ))
                    if DEBUG and candidates:
                        print(f"[llm] questions detected: {[c.question for c in candidates]}")
                    return candidates
                except json.JSONDecodeError:
                    return []
            except Exception as e:
                if DEBUG:
                    print(f"[llm] detection error: {e}")
                return []

    async def generate_answer(self, candidate: QuestionCandidate) -> str:
        try:
            import ollama
            client = ollama.Client(host=OLLAMA_BASE_URL)
            q = (candidate.question or "").strip()
            if DEBUG:
                print(f"[llm] generating answer for: {q[:60]}")

            system_prompt = """You are an expert AI assistant helping during a technical interview.

ROLE: Provide intelligent, concise answers in real-time.

OUTPUT STYLE:
- Use first-person ("I would...") when asked for opinion
- Provide 2-4 concise, actionable points
- Include specific examples
- Be professional but conversational

AVOID:
- Long paragraphs
- Bullet lists (use flowing sentences)
- Coaching language"""

            context_parts = []
            full_tx = "\n".join([f"{i+1}. {line}" for i, line in enumerate(transcript_lines)])
            if full_tx:
                context_parts.append(f"Recent conversation:\n{full_tx[-3000:]}")
            context_parts.append("Domain: Cloud computing, system design, software engineering.")
            context_parts.append(f"Current question: {q}")
            context_parts.append("Provide a helpful, contextual response.")
            user_content = "\n\n".join(context_parts)

            response = client.chat(
                model=self.ollama_model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_content}
                ]
            )
            raw_text = response['message']['content'].strip()
            cleaned = sanitize_candidate_voice(raw_text)
            if not cleaned.strip():
                return "Based on the context, I would recommend starting with a proof-of-concept to validate the approach."
            if DEBUG:
                print(f"[llm] answer generated ({len(cleaned)} chars)")
            return cleaned
        except Exception as e:
            if DEBUG:
                print(f"[llm] generation error: {e}")
            return f"[Answer error] {str(e)[:120]}"


@dataclass
class ClientState:
    ws: websockets.WebSocketServerProtocol
    queue: asyncio.Queue
    sender_task: Optional[asyncio.Task] = None
    wants_broadcast: bool = True
    authenticated: bool = False


async def _client_sender(client: ClientState):
    try:
        while True:
            await client.ws.send(await client.queue.get())
    except (websockets.ConnectionClosedOK, websockets.ConnectionClosedError):
        pass
    finally:
        try:
            await client.ws.close()
        except Exception:
            pass


async def broadcast(data: dict):
    if not clients:
        return
    msg = json.dumps(data, ensure_ascii=False)
    for c in list(clients.values()):
        if c.wants_broadcast:
            try:
                if c.queue.full():
                    _ = c.queue.get_nowait()
                c.queue.put_nowait(msg)
            except Exception:
                clients.pop(c.ws, None)


async def handler(ws: websockets.WebSocketServerProtocol):
    global transcriber_task, bytes_since_last
    ip = ws.remote_address[0] if ws.remote_address else "unknown"
    print(f"[ws] client connecting from {ip}")
    client = ClientState(ws=ws, queue=asyncio.Queue(maxsize=100))
    client.sender_task = asyncio.create_task(_client_sender(client))
    clients[ws] = client

    try:
        await ws.send(json.dumps({
            "snapshot": {
                "transcript": latest_text,
                "detected": list(detected),
                "lines": transcript_lines[-200:]
            }
        }))
    except Exception:
        pass

    if transcriber_task is None or transcriber_task.done():
        transcriber_task = asyncio.create_task(read_and_transcribe_loop())

    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                if not client.authenticated and AUTH_TOKEN:
                    print(f"[ws] unauthenticated stream from {ip}, dropping")
                    continue
                async with audio_lock:
                    pcm_buf.extend(msg)
                    bytes_since_last += len(msg)
                    if len(pcm_buf) > MAX_BUFFER_BYTES:
                        del pcm_buf[:len(pcm_buf) - MAX_BUFFER_BYTES // 2]
            else:
                try:
                    data = json.loads(msg)
                    cmd = (data.get("cmd") or "").lower()
                    if cmd == "auth":
                        token = data.get("token", "")
                        if not AUTH_TOKEN or token == AUTH_TOKEN:
                            client.authenticated = True
                            await ws.send(json.dumps({"auth": "ok"}))
                            print(f"[ws] client authenticated from {ip}")
                        else:
                            await ws.send(json.dumps({"auth": "fail"}))
                            print(f"[ws] auth failed from {ip}")
                    elif cmd == "hello":
                        ctype = (data.get("client") or "").lower()
                        if ctype in ("audio_streamer", "ingest"):
                            client.wants_broadcast = False
                            print(f"[ws] streamer connected from {ip}")
                        else:
                            client.authenticated = True
                    elif cmd == "reset":
                        async with audio_lock:
                            pcm_buf.clear()
                            bytes_since_last = 0
                        reset_state()
                except Exception:
                    pass
    except (websockets.ConnectionClosedOK, websockets.ConnectionClosedError):
        pass
    finally:
        clients.pop(ws, None)
        if client.sender_task:
            client.sender_task.cancel()
        print(f"[ws] client disconnected from {ip}")


async def read_and_transcribe_loop():
    global bytes_since_last, latest_text, sentence_buf, transcript_lines
    loop = asyncio.get_running_loop()
    last_hashes = deque(maxlen=8)
    print("[stt] transcription loop started")

    PROMPT_WORDS = {w.strip(".,").lower() for w in INITIAL_PROMPT.split()} if INITIAL_PROMPT else set()

    while not server_shutdown.is_set():
        await asyncio.sleep(0.05)
        async with audio_lock:
            if not (bytes_since_last >= HOP_BYTES and len(pcm_buf) >= WIN_BYTES):
                continue
            bytes_since_last = 0
            window_bytes = bytes(pcm_buf[-WIN_BYTES:])

        arr = np.frombuffer(window_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if np.mean(arr * arr) < ENERGY_GATE:
            continue

        def _transcribe():
            try:
                segments, _ = whisper.transcribe(
                    arr, language=None, vad_filter=True,
                    initial_prompt=INITIAL_PROMPT if not latest_text else None
                )
                return list(segments)
            except Exception as e:
                print(f"[stt] transcribe error: {e}")
                return []

        segments = await loop.run_in_executor(None, _transcribe)
        if not segments:
            continue

        for segment in segments:
            text = _norm(segment.text)
            if not text or len(text) < 2:
                continue

            toks = [w.strip(".,").lower() for w in text.split()]
            if toks and PROMPT_WORDS and sum(1 for w in toks if w in PROMPT_WORDS) / max(1, len(toks)) > 0.6:
                if DEBUG:
                    print(f"[stt] dropped (prompt echo): {text[:60]}")
                continue

            h = hashlib.md5(text.encode("utf-8")).hexdigest()
            if h in last_hashes:
                continue
            last_hashes.append(h)

            sentence_buf = (sentence_buf + " " + text).strip()
            parts = SENTENCE_SPLIT_RE.split(sentence_buf)

            complete = (parts[:-1] if len(parts) > 1 and sentence_buf[-1] not in ".?!"
                        else (parts if sentence_buf and sentence_buf[-1] in ".?!" else []))
            sentence_buf = "" if complete == parts else parts[-1]
            latest_text = sentence_buf

            for s in complete:
                s = _norm(s)
                if not s:
                    continue
                transcript_lines.append(s)
                if len(transcript_lines) > MAX_TRANSCRIPT_LINES:
                    del transcript_lines[:MAX_TRANSCRIPT_LINES // 10]
                if DEBUG:
                    print(f"[stt] line {len(transcript_lines)}: {s}")
                await broadcast({"line": {"n": len(transcript_lines), "text": s}})

            await broadcast({"partial": text})

            if llm_analyzer and llm_analyzer.enabled:
                try:
                    cands = await llm_analyzer.analyze_segment(
                        ConversationSegment(text=text, timestamp=time.time())
                    )
                    for cand in cands:
                        item = {"q": cand.question, "t": int(cand.timestamp * 1000), "a": None}
                        detected.append(item)
                        await broadcast({"question_detected": item})
                        ans = await llm_analyzer.generate_answer(cand)
                        item["a"] = ans
                        qa_log.append(item)
                        await broadcast({"qa": item})
                        if DEBUG:
                            print(f"[llm] answer: {ans[:60]}...")
                except Exception as e:
                    print(f"[llm] analysis error: {e}")


def reset_state():
    global latest_text, sentence_buf, transcript_lines
    latest_text = ""
    sentence_buf = ""
    transcript_lines.clear()
    detected.clear()
    qa_log.clear()
    if llm_analyzer:
        llm_analyzer.seen_questions.clear()
        llm_analyzer.answers_timestamps.clear()


async def main():
    global whisper, llm_analyzer
    print(f"[startup] Whisper model: {MODEL_NAME} ({COMPUTE_TYPE})")
    print(f"[startup] Ollama: {OLLAMA_BASE_URL} / model: {OLLAMA_MODEL}")
    print(f"[startup] Auth: {'enabled' if AUTH_TOKEN else 'disabled'}")
    print("[startup] loading Whisper model...")
    whisper = WhisperModel(MODEL_NAME, compute_type=COMPUTE_TYPE)
    print("[startup] initializing LLM analyzer...")
    llm_analyzer = LLMAnalyzer()
    print(f"[startup] server starting on ws://0.0.0.0:{PORT}")

    async with websockets.serve(handler, HOST, PORT, max_size=2**20, ping_interval=10, ping_timeout=30):
        print(f"[startup] ready on port {PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    llm_analyzer: Optional[LLMAnalyzer] = None
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        server_shutdown.set()
        print("\n[startup] server stopped")
    except Exception as e:
        print(f"[error] fatal: {e}")
        import traceback
        traceback.print_exc()
