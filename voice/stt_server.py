"""Local speech to text on banditbox's GPU, OpenAI compatible, for the hub on the tailnet.

POST /v1/audio/transcriptions takes a multipart file and answers {"text", "language",
"duration"}; GET /v1/health answers {"ok"}. Every call needs the bearer token in
~/.config/diablo-stt/token. One faster-whisper model, loaded on the first request and
dropped after DIABLO_STT_IDLE seconds without one by exiting, so the
CUDA context goes with it and systemd starts a fresh, empty process, and only loaded while the GPU has
DIABLO_STT_MIN_FREE_MB free, so a game keeps its VRAM and the hub falls back to the cloud.

    python -m voice.stt_server --host <this box tailscale ip> --port 8757
"""

import argparse
import email.parser
import email.policy
import gc
import hmac
import io
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("diablo.stt_server")

TOKEN_PATH = Path(os.environ.get("DIABLO_STT_TOKEN", Path.home() / ".config/diablo-stt/token"))
MODEL = os.environ.get("DIABLO_STT_MODEL", "large-v3-turbo")
DEVICE = os.environ.get("DIABLO_STT_DEVICE", "cuda")
COMPUTE = os.environ.get("DIABLO_STT_COMPUTE", "int8_float16")
IDLE = float(os.environ.get("DIABLO_STT_IDLE", "600"))
MIN_FREE_MB = int(os.environ.get("DIABLO_STT_MIN_FREE_MB", "2500"))
MAX_BYTES = 25 * 1024 * 1024
FREE_TTL = 10.0
LOAD_COOLDOWN = 60.0


class Busy(RuntimeError):
    pass


def free_vram_mb():
    if DEVICE != "cuda":
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


def load_whisper():
    """The cached model without a call to Hugging Face; the hub only when there is no copy."""
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE, local_files_only=True)
    except Exception:
        return WhisperModel(MODEL, device=DEVICE, compute_type=COMPUTE)


class Engine:
    def __init__(self, loader=load_whisper, free=free_vram_mb, idle=IDLE, min_free=MIN_FREE_MB):
        self.loader = loader
        self.free = free
        self.idle = idle
        self.min_free = min_free
        self.model = None
        self.used = 0.0
        self.load_failed = None
        self.free_seen = None
        self.inflight = 0
        self.lock = threading.Lock()
        self.count = threading.Lock()

    def room(self):
        if self.model is not None:
            return True
        if self.load_failed is not None and time.monotonic() - self.load_failed < LOAD_COOLDOWN:
            return False
        if self.free_seen is None:
            self.refresh()
        free = self.free_seen
        return free is None or free >= self.min_free

    def refresh(self):
        self.free_seen = self.free()

    def enter(self):
        with self.count:
            self.inflight += 1

    def leave(self):
        with self.count:
            self.inflight -= 1

    def _ensure(self):
        if self.model is None:
            if not self.room():
                raise Busy("not enough free VRAM to load the model")
            start = time.monotonic()
            try:
                self.model = self.loader()
            except Exception:
                self.load_failed = time.monotonic()
                raise
            self.load_failed = None
            log.info("loaded %s %s on %s in %.1fs", MODEL, COMPUTE, DEVICE, time.monotonic() - start)
        return self.model

    def transcribe(self, data, language=None):
        with self.lock:
            model = self._ensure()
            self.used = time.monotonic()
            segments, info = model.transcribe(io.BytesIO(data), language=language or None, beam_size=5,
                                              vad_filter=True, condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
            self.used = time.monotonic()
            return {"text": text, "language": info.language, "duration": round(info.duration, 2), "model": MODEL}

    def reap(self, now=None, then=None):
        now = time.monotonic() if now is None else now
        with self.lock, self.count:
            if self.model is not None and self.inflight == 0 and now - self.used >= self.idle:
                self.model = None
                gc.collect()
                log.info("unloaded after %.0fs idle", now - self.used)
                if then is not None:
                    then()
                return True
        return False

    def status(self):
        return {"ok": self.room(), "loaded": self.model is not None, "model": MODEL, "compute": COMPUTE}


def read_token(path=TOKEN_PATH):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def parse_form(content_type, body):
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        raise HTTPException(400, "expected multipart/form-data")
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files[name] = payload
        else:
            fields[name] = payload.decode("utf-8", "replace").strip()
    return fields, files


def create_app(engine=None, token=None):
    engine = engine or Engine()
    secret = token if token is not None else read_token()
    app = FastAPI()

    def check(request):
        sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secret or not hmac.compare_digest(sent.encode("utf-8", "replace"), secret.encode()):
            raise HTTPException(401, "bad token")

    @app.get("/v1/health")
    @app.get("/health")
    def health(request: Request):
        check(request)
        body = engine.status()
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request):
        check(request)
        engine.enter()
        try:
            return await _transcriptions(request)
        finally:
            engine.leave()

    async def _transcriptions(request):
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > MAX_BYTES:
            raise HTTPException(413, "audio too large")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, "audio too large")
            chunks.append(chunk)
        fields, files = await anyio.to_thread.run_sync(
            parse_form, request.headers.get("content-type", ""), b"".join(chunks))
        audio = files.get("file")
        if not audio:
            raise HTTPException(400, "missing file")
        start = time.monotonic()
        try:
            result = await anyio.to_thread.run_sync(engine.transcribe, audio, fields.get("language"))
        except Busy as error:
            raise HTTPException(503, str(error))
        except Exception as error:
            log.exception("transcription failed")
            raise HTTPException(500, f"transcription failed: {type(error).__name__}")
        log.info("%.1fs of audio in %.2fs", result["duration"], time.monotonic() - start)
        return result

    app.state.engine = engine
    return app


def reaper(engine, every=FREE_TTL, leave=os._exit):
    while True:
        if engine.model is None:
            engine.refresh()
        time.sleep(every)
        engine.reap(then=lambda: _leave(leave))


def _leave(leave):
    log.info("exiting so the CUDA context goes too; systemd starts a fresh process")
    logging.shutdown()
    leave(0)


def main():
    parser = argparse.ArgumentParser(prog="diablo-stt")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8757)
    args = parser.parse_args()
    if not args.host or args.host in ("0.0.0.0", "::"):
        raise SystemExit("refusing to listen on every interface; pass the tailnet address")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not read_token():
        raise SystemExit(f"no token at {TOKEN_PATH}; run deploy/stt-install.sh")
    import uvicorn
    app = create_app()
    threading.Thread(target=reaper, args=(app.state.engine,), daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
