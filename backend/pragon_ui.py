#!/usr/bin/env python3
"""
P.R.A.G.O.N — Web UI for JARVIS Backend
=======================================
A cyberpunk web-based interface that connects to JARVIS's live audio backend.
Serves the HTML/CSS/JS interface and bridges WebSocket messages to the JarvisLive class.
"""

import asyncio
import json
import re
import base64
import threading
import os
import sys
import time
import uuid
import hmac
import hashlib
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler
import socketserver
from urllib.request import urlopen, Request
from urllib.parse import quote

try:
    from pragon_phoneview import make_qr_data_uri
except Exception:
    def make_qr_data_uri(*_a, **_kw):
        return None

try:
    import customforge_launcher
except Exception:
    class _NoForge:
        FORGE_URL = "http://127.0.0.1:5000"
        @staticmethod
        def ensure_forge_running(*_a, **_kw):
            return False
    customforge_launcher = _NoForge()

# ── WebSocket support ──
try:
    import websockets
    HAVE_WS = True
except ImportError:
    HAVE_WS = False
    print("[P.R.A.G.O.N] websockets not installed. Install: pip install websockets")

# ── Audio utilities for browser mic JARVIS ──

# HTML interface (embedded so no external files needed)
# ── Favicon (P.R.A.G.O.N dragon mark), multi-res .ico served at /favicon.ico ──
FAVICON_ICO_BYTES = base64.b64decode(
    "AAABAAEAEBAAAAAAIAAUAwAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAAEAgGAAAAH/P/YQAAAttJREFUeJx9UltIk2EYfr7v///NteFhHmq4WtqBMjJlKzVHpUKZTjvQP9LqQqiRJHVRlNHFP6MYRHdRRBBRFx3cRWkFCcEqyghylKYOyvwVD5kH1Oama9vXxVJWme/V937Py/s+7/M+BPOEJEl09j2m1QpXTpyYma8OAMj/gNk443Bkf5ue7v7UMhhoeXLD9zdOoxNRrOcA0E0HJH3hKWem8XJjUu9wt/97Z2e5WGYUIgPZH0P56MTptIZKq6sTNq813ExIicn44GnzDspjd/pGe12tH9z7ANz8TZoAYHMMGGPEYrMsqj59usq4dOWLxRo0vXHdv9D6+qm+o6t9M5kJFvR9lS/aak9e5yggSdIcCwKAQJKIbWJi8behkaa+HvLD3TxQY8jqz/P7fJPaeG0oOTZum0Yl2AKadN93klrZdldqFMV66nRaQ3OKm4u3FelWZ7qQVjukTK6wR/ofVC9btkd36NjtxNJi836t+Xijesu5R4jQntuFGI0WVZB8dHweL9QHUvN305cXy+I2qt1TU1lLVPHeLr6fJA71NMgAsDIzL1sZD7791fYWoI5RAKzXr6Jp3vHzgbisCaZQBDn6cznHM+USfnhAM0W4oZ4GOSNDVOSKomp67K0HEJBb+l4HgFEAGF4n+h97fowiyCkhKEHCRL7XfK1/fDSgGKexhRDruY4OZ2CGM6yKWVq0Ji4Y9OwwmQajDijxDCDKVPsRfm8T47itlvVmc0JUgQbYUrKA3xgBATTYmiQU3A1yJseVyJ3M+fzOugaSXvVKqHnG+Hz7cyB2xW8BaZSRCAOr57ywjghyjoMm68+GoLsqHD76kE2yZKoLI9wlB8gGcxGn1z0IEWICYwSERFvZGgYk+rP7kh39XW6iLqgIt8m3yDs/aGgD8Padj7h6wuTLQOSM9vl3IQAQszzPoEjcUw4AnKpyF5/iuA9VSQ5Qnr2ADn82iWjL6D/oX3+/ADcfC8xS0AQeAAAAAElFTkSuQmCC"
)

PRAGON_HTML = ""

# HTML interface for PRAGON COMPASS — the live map / 3D globe / satellite
# tracking tool. Served at /compass and loaded into the main UI's
# #compass-frame iframe, same pattern as CustomDraw/FORGE above.
PRAGON_COMPASS_HTML = ""

PRAGON_PULSE_HTML = ""

# ── C.U.S.T.O.M FORGE backend ────────────────────────────────────────────
# Backs the CustomDraw drawing-pad app builder above: sketch a layout, type
# a brief, hit FORGE. This is the same pipeline as the standalone
# customforge/server.py bridge, ported in-process so it can be served
# directly from this HTTP handler (no separate Flask server needed) and
# share the same api/api_keys.json the rest of PRAGON already uses.
def _forge_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


FORGE_BASE_DIR = _forge_base_dir()
FORGE_CONFIG_PATH = FORGE_BASE_DIR / "api" / "api_keys.json"

# ══════ P.R.A.G.O.N GALLERY ══════
# Every generated image is persisted here (not just kept in the browser tab),
# so it survives reloads/restarts and is browsable from the Gallery panel.
GALLERY_DIR = FORGE_BASE_DIR / "pragon_gallery"
GALLERY_INDEX_PATH = GALLERY_DIR / "index.json"
_GALLERY_MIME_EXT = {
    'image/png': 'png', 'image/jpeg': 'jpg', 'image/jpg': 'jpg',
    'image/webp': 'webp', 'image/gif': 'gif',
}

def _gallery_load_index():
    try:
        GALLERY_DIR.mkdir(parents=True, exist_ok=True)
        if GALLERY_INDEX_PATH.is_file():
            return json.loads(GALLERY_INDEX_PATH.read_text(encoding='utf-8') or '[]')
    except Exception:
        pass
    return []

def _gallery_save_index(items):
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    GALLERY_INDEX_PATH.write_text(json.dumps(items), encoding='utf-8')


def _forge_get_api_key() -> str:
    with open(FORGE_CONFIG_PATH, "r", encoding="utf-8") as f:
        key = json.load(f).get("gemini_api_key", "")
    if not key or "YOUR_" in key.upper():
        raise RuntimeError("gemini_api_key is not set in api/api_keys.json")
    return key


# ══════ LiveKit voice agent bridge ══════
# Backs GET /livekit/token, which the "LiveKit" toggle in the input bar calls
# to mint a short-lived room token for the browser's livekit-client, so the
# mic/speaker can hand off from the browser Web Speech API + Omnix (Gemini)
# REST path over to the real-time livekit_agent.py voice pipeline (also
# Gemini, via LiveKit's google plugin) running against a LiveKit server.
def _livekit_get_config() -> dict:
    cfg = {}
    try:
        with open(FORGE_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        pass
    url = os.environ.get("LIVEKIT_URL") or cfg.get("livekit_url", "")
    api_key = os.environ.get("LIVEKIT_API_KEY") or cfg.get("livekit_api_key", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET") or cfg.get("livekit_api_secret", "")
    if not url or not api_key or not api_secret or "YOUR_" in api_key.upper():
        raise RuntimeError(
            "LiveKit is not configured. Add \"livekit_url\", \"livekit_api_key\" and "
            "\"livekit_api_secret\" to api/api_keys.json (or set the LIVEKIT_URL / "
            "LIVEKIT_API_KEY / LIVEKIT_API_SECRET environment variables), then make "
            "sure `python livekit_agent.py dev` is running against that server."
        )
    return {"url": url, "api_key": api_key, "api_secret": api_secret}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _livekit_create_token(api_key: str, api_secret: str, room: str, identity: str,
                           name: str = "", ttl_seconds: int = 6 * 3600) -> str:
    """Mints a LiveKit access token by hand (it's just an HS256 JWT with a
    'video' grant) so we don't need to add the separate livekit-api package
    just to sign one claim set."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": api_key,
        "sub": identity,
        "iat": now,
        "nbf": now,
        "exp": now + ttl_seconds,
        "name": name or identity,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")) + "." +
        _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    sig = hmac.new(api_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _b64url(sig)


def _strip_code_fences(raw: str) -> str:
    """Generic version of _forge_clean_code for any language/format — some
    models wrap output in ``` fences despite being told not to."""
    code = raw.strip()
    code = re.sub(r"^```[a-zA-Z0-9_+-]*\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    return code.strip()


# ── PRAGON multi-model text-generation backend ──────────────────────────────
# Backs POST /api/generate-file (the chat's "create me a <file>" auto-detect).
# Reuses the same api_keys.json + google-genai client pattern as
# C.U.S.T.O.M FORGE above, just for a plain text reply instead of HTML.
PRAGON_ASK_MODEL = "gemini-2.5-flash"

# ── Per-model hooks so file generation can reach whichever engine is
# active in the toolbar, not just Omnix/Omnix. main.py can plug real
# backends in via these register_* functions, exactly like FRIDAY already
# does below. Any engine left unregistered just raises a clean RuntimeError
# that the frontend surfaces as an error message instead of silently
# falling back to Omnix.
_JARVIS_ASK_PROCESSOR = None
_GHOST_ASK_PROCESSOR = None


def register_jarvis_ask_processor(fn):
    """fn(prompt_text: str) -> str. Wires J.A.R.V.I.S into file generation."""
    global _JARVIS_ASK_PROCESSOR
    _JARVIS_ASK_PROCESSOR = fn


def register_ghost_ask_processor(fn):
    """fn(prompt_text: str) -> str. Wires G.H.O.S.T (Ollama) into file generation."""
    global _GHOST_ASK_PROCESSOR
    _GHOST_ASK_PROCESSOR = fn


def _pragon_build_prompt(payload: dict, persona: str) -> str:
    """Builds the shared instruction+content prompt used regardless of which
    model ends up answering it."""
    instruction = (payload.get("instruction") or "Analyze and summarize this content.").strip()
    kind = (payload.get("kind") or "note").strip()
    text = (payload.get("text") or "").strip()

    header_lines = [f"You are {persona}, PRAGON's on-demand content analyst.", ""]
    if kind == "transformer":
        header_lines.append(instruction)
    else:
        label_bits = []
        if payload.get("title"):
            label_bits.append(f'title "{payload["title"]}"')
        if payload.get("tag"):
            label_bits.append(f'tag "{payload["tag"]}"')
        if payload.get("name"):
            label_bits.append(f'file "{payload["name"]}"')
        if payload.get("path"):
            label_bits.append(f'path "{payload["path"]}"')
        label = f" ({', '.join(label_bits)})" if label_bits else ""
        header_lines += [
            f"SOURCE: {kind}{label}",
            "",
            f"INSTRUCTION: {instruction}",
        ]

    prompt_text = "\n".join(header_lines)
    if text:
        prompt_text += f"\n\nCONTENT:\n{text}"
    return prompt_text


def _pragon_ask_omnix(payload: dict) -> str:
    """Runs the payload through Omnix (Omnix) and returns the plain-text
    reply. Raises RuntimeError if no API key is configured, so the caller
    can surface a clean error to the frontend. Only Omnix supports the
    inline image/file attachment (base64) path today."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_forge_get_api_key(),
        http_options={"api_version": "v1beta"},
    )

    prompt_text = _pragon_build_prompt(payload, "Omnix")

    parts = []
    mime_type = payload.get("mimeType")
    base64_data = payload.get("base64")
    if base64_data and mime_type:
        try:
            parts.append(types.Part.from_bytes(data=base64.b64decode(base64_data), mime_type=mime_type))
        except Exception:
            pass
    parts.append(types.Part.from_text(text=prompt_text))

    gen_config = types.GenerateContentConfig(max_output_tokens=4096, temperature=0.5)
    response = client.models.generate_content(
        model=PRAGON_ASK_MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=gen_config,
    )

    reply = (response.text or "").strip() if getattr(response, "text", None) else ""
    if not reply and response.candidates:
        reply = "".join(
            p.text for p in response.candidates[0].content.parts if getattr(p, "text", None)
        ).strip()
    return reply or "(Omnix returned an empty response.)"


def _pragon_ask_generate(payload: dict) -> str:
    """Dispatches a text-generation request to whichever model the toolbar
    had active (payload['model'], set from J.A.R.V.I.S / F.R.I.D.A.Y /
    O.M.N.I.X / G.H.O.S.T on the frontend). Defaults to Omnix/Omnix if no
    model — or an unknown one — was supplied, so existing behavior for old
    clients is unchanged."""
    model = (payload.get("model") or "omnix").strip().lower()

    if model == "friday":
        if _FRIDAY_PROCESSOR is None:
            raise RuntimeError("F.R.I.D.A.Y backend not ready yet")
        prompt_text = _pragon_build_prompt(payload, "FRIDAY")
        return _FRIDAY_PROCESSOR(prompt_text, None, None) or "(FRIDAY returned an empty response.)"

    if model == "jarvis":
        if _JARVIS_ASK_PROCESSOR is None:
            raise RuntimeError("J.A.R.V.I.S backend not wired up for 'Send to PRAGON' yet")
        prompt_text = _pragon_build_prompt(payload, "JARVIS")
        return _JARVIS_ASK_PROCESSOR(prompt_text) or "(JARVIS returned an empty response.)"

    if model in ("ghost", "ollama"):
        if _GHOST_ASK_PROCESSOR is None:
            raise RuntimeError("G.H.O.S.T (Ollama) backend not wired up for 'Send to PRAGON' yet")
        prompt_text = _pragon_build_prompt(payload, "GHOST")
        return _GHOST_ASK_PROCESSOR(prompt_text) or "(GHOST returned an empty response.)"

    # "omnix" or anything unrecognized falls back here.
    return _pragon_ask_omnix(payload)


# ── "Create me a <file>" auto-detect (chat) ─────────────────────────────────
# Backs POST /api/generate-file. The frontend's isFileRequest()/detectFileType()
# decide when a chat message wants a generated file and what kind; this just
# gets raw content for that file_type out of whichever model is active
# (reusing _pragon_ask_generate, so it works for Omnix/JARVIS/FRIDAY/GHOST
# alike) and hands it back for the browser to assemble into a real file with
# the libraries already loaded (jsPDF/SheetJS/JSZip) and save into the
# Library, exactly like generatePDF()/saveSheetToLibrary()/saveDocToLibrary().
_FILE_GEN_INSTRUCTIONS = {
    "zip": (
        "The user wants a bundle of multiple files. Respond with ONLY a raw JSON "
        "array, no markdown fences, no commentary, in this exact shape: "
        '[{"filename": "example.txt", "content": "..."}, ...]. Pick sensible '
        "filenames and extensions for each file based on the request."
    ),
    "xlsx": "Respond with ONLY raw CSV data (comma-separated, one row per line, header row first). No markdown fences, no commentary, no explanation.",
    "csv": "Respond with ONLY raw CSV data (comma-separated, one row per line, header row first). No markdown fences, no commentary, no explanation.",
    "json": "Respond with ONLY valid raw JSON. No markdown fences, no commentary, no explanation.",
    "html": "Respond with ONLY a complete, valid HTML document (starting with <!DOCTYPE html>). No markdown fences, no commentary.",
    "py": "Respond with ONLY raw, runnable Python code. No markdown fences, no commentary, no explanation before or after the code.",
    "js": "Respond with ONLY raw, runnable JavaScript code. No markdown fences, no commentary, no explanation before or after the code.",
    "bat": "Respond with ONLY raw Windows batch script content. No markdown fences, no commentary, no explanation.",
    "pdf": "Respond with ONLY the plain-text body for a document (blank lines between paragraphs; lines starting with '#' are section headings). No markdown fences, no commentary about what you're doing.",
    "doc": "Respond with ONLY the plain-text body for a document (blank lines between paragraphs; lines starting with '#' are section headings). No markdown fences, no commentary about what you're doing.",
    "txt": "Respond with ONLY the plain-text content requested. No markdown fences, no commentary about what you're doing.",
}


def _pragon_generate_file_content(payload: dict) -> tuple:
    """Pure function: given {"prompt", "file_type", "model", "key", ...},
    returns (content, file_type). Raises RuntimeError/Exception on failure,
    same as _pragon_ask_generate, so the HTTP handler can surface a clean
    error to the frontend."""
    prompt = (payload.get("prompt") or "").strip()
    file_type = (payload.get("file_type") or "txt").strip().lower()
    if not prompt:
        raise ValueError("Please describe what to create.")

    gen_payload = dict(payload)
    gen_payload["kind"] = "transformer"
    gen_payload["instruction"] = _FILE_GEN_INSTRUCTIONS.get(file_type, _FILE_GEN_INSTRUCTIONS["txt"])
    gen_payload["text"] = prompt

    content = _pragon_ask_generate(gen_payload)
    return _strip_code_fences(content), file_type


# ── FRIDAY backend bridge ────────────────────────────────────────────────
# Set by PragonUI.set_friday_processor() once FridayEngine is constructed
# in main.py. Lets FRIDAY's chat reach the same memory/tools/agentic core
# JARVIS uses, via a plain HTTP POST from the browser (see /friday/act
# below and queryFriday() in the frontend JS). Purely additive — nothing
# existing is changed by this.
_FRIDAY_PROCESSOR = None


def register_friday_processor(fn):
    global _FRIDAY_PROCESSOR
    _FRIDAY_PROCESSOR = fn


def ask_friday(text: str) -> str:
    """Convenience helper for feature modules (Code Helper, Background
    Monitor, etc.): routes a prompt through FRIDAY's registered processor --
    the same Ollama-backed brain used by chat and PRAGON WhatsUp -- without
    each caller needing its own host/model/HTTP plumbing. Returns the
    reply text, or a short explanation if FRIDAY isn't reachable."""
    if _FRIDAY_PROCESSOR is None:
        return "FRIDAY isn't running yet, so I can't process this."
    host = os.environ.get("PRAGON_FRIDAY_HOST", "http://localhost:11434")
    model = os.environ.get("PRAGON_FRIDAY_MODEL", "qwen3:8b")
    try:
        return _FRIDAY_PROCESSOR(text, host, model)
    except Exception as e:
        return f"FRIDAY couldn't respond: {e}"


# ── GHOST backend bridge ─────────────────────────────────────────────────
# Mirrors the FRIDAY bridge directly above, giving G.H.O.S.T the same
# memory/tools/agentic-core access FRIDAY has (see /ghost/act below and
# queryGhost() in the frontend JS). The one deliberate difference from
# FRIDAY is that GHOST has no RAG/knowledge-base grounding step — that
# stays FRIDAY-only. This is separate from _GHOST_ASK_PROCESSOR above,
# which only backs one-shot "Send to PRAGON" file generation; this one
# backs GHOST's actual chat turns.
_GHOST_PROCESSOR = None


def register_ghost_processor(fn):
    """fn(text, host, model) -> str. Wires G.H.O.S.T into /ghost/act,
    exactly like register_friday_processor does for FRIDAY."""
    global _GHOST_PROCESSOR
    _GHOST_PROCESSOR = fn


class PragonHTTPHandler(BaseHTTPRequestHandler):
    """Serve the P.R.A.G.O.N HTML interface."""

    def do_POST(self):
        if self.path.startswith('/friday/act'):
            self._handle_friday_act()
        elif self.path.startswith('/ghost/act'):
            self._handle_ghost_act()
        elif self.path.startswith('/api/upload'):
            self._handle_upload()
        elif self.path.startswith('/api/generate-file'):
            self._handle_generate_file()
        elif self.path.startswith('/generate-image'):
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                payload = {}
            self._handle_generate_image(post_payload=payload)
        elif self.path.startswith('/api/gallery/save'):
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                payload = {}
            self._handle_gallery_save(payload)
        elif self.path.startswith('/api/gallery/delete'):
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            try:
                payload = json.loads(raw.decode('utf-8') or '{}')
            except Exception:
                payload = {}
            self._handle_gallery_delete(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_generate_file(self):
        """Chat 'create me a <file>' auto-detect: generates raw content for
        the requested file_type via whichever model is active, and hands it
        back for the frontend to assemble into a real file
        (pdf/xlsx/zip/doc/code)."""
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            payload = json.loads(raw.decode('utf-8') or '{}')
        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": f"Bad request: {e}"}).encode('utf-8'))
            return

        def respond(status, body):
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(body).encode('utf-8'))

        try:
            content, file_type = _pragon_generate_file_content(payload)
            respond(200, {"ok": True, "content": content, "file_type": file_type})
        except ValueError as e:
            respond(400, {"ok": False, "error": str(e)})
        except RuntimeError as e:
            respond(500, {"ok": False, "error": str(e)})
        except Exception as e:
            respond(500, {"ok": False, "error": f"Generation failed: {e}"})

    def _handle_friday_act(self):
        """Runs FRIDAY's message through FridayEngine (memory + tools +
        agentic core) on the backend, and returns the reply as JSON."""
        global _FRIDAY_PROCESSOR
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            payload = json.loads(raw.decode('utf-8') or '{}')
            text = payload.get('text', '') or ''
            host = payload.get('host')
            model = payload.get('model')

            if _FRIDAY_PROCESSOR is None:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "FRIDAY backend not ready yet"}).encode('utf-8'))
                return

            reply = _FRIDAY_PROCESSOR(text, host, model)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply}).encode('utf-8'))
        except Exception as e:
            print(f"[FRIDAY] /friday/act failed: {e}", file=sys.stderr)
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _handle_ghost_act(self):
        """Runs GHOST's message through the same backend bridge FRIDAY uses
        (memory + tools + agentic core), and returns the reply as JSON.
        Mirrors _handle_friday_act exactly — the only difference is GHOST
        never gets RAG-augmented text (that augmentation happens, or
        doesn't, entirely on the frontend before this is called)."""
        global _GHOST_PROCESSOR
        try:
            length = int(self.headers.get('Content-Length', 0) or 0)
            raw = self.rfile.read(length) if length else b'{}'
            payload = json.loads(raw.decode('utf-8') or '{}')
            text = payload.get('text', '') or ''
            host = payload.get('host')
            model = payload.get('model')

            if _GHOST_PROCESSOR is None:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "GHOST backend not ready yet"}).encode('utf-8'))
                return

            reply = _GHOST_PROCESSOR(text, host, model)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply}).encode('utf-8'))
        except Exception as e:
            print(f"[GHOST] /ghost/act failed: {e}", file=sys.stderr)
            self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_vendor_file(self):
        """Serve locally-vendored third-party JS/CSS from ./vendor/, so the
        page never has to load libraries from a public CDN (cdnjs, in
        particular, is flagged by browser Tracking Prevention as a
        third-party tracker origin, which blocks storage access for it and
        spams the console with warnings). Path-traversal safe: resolves the
        requested path and refuses anything that escapes the vendor dir."""
        rel = self.path.split('?', 1)[0][len('/vendor/'):]
        vendor_root = (FORGE_BASE_DIR / 'vendor').resolve()
        target = (vendor_root / rel).resolve()
        if vendor_root not in target.parents and target != vendor_root:
            self.send_response(403)
            self.end_headers()
            return
        if not target.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ext = target.suffix.lower()
        content_type = {
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.map': 'application/json',
        }.get(ext, 'application/octet-stream')
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'public, max-age=604800, immutable')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/vendor/'):
            self._serve_vendor_file()
        elif self.path.startswith('/favicon.ico'):
            self.send_response(200)
            self.send_header('Content-Type', 'image/x-icon')
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.send_header('Content-Length', str(len(FAVICON_ICO_BYTES)))
            self.end_headers()
            self.wfile.write(FAVICON_ICO_BYTES)
        elif self.path.startswith('/generate-image'):
            self._handle_generate_image()
        elif self.path.startswith('/customdraw/status'):
            ok = customforge_launcher.ensure_forge_running()
            payload = json.dumps({"ok": ok, "url": customforge_launcher.FORGE_URL}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)
        elif self.path.startswith('/customdraw/app'):
            customforge_launcher.ensure_forge_running()
            self.send_response(302)
            self.send_header('Location', customforge_launcher.FORGE_URL + '/')
            self.end_headers()
        elif self.path.startswith('/pulse/status'):
            # Pulse's content is self-hosted (PRAGON_PULSE_HTML, served directly
            # below), so it never actually needs the forge process — but we still
            # ping it so the frontend can show forge connectivity as info. A dead
            # or missing forge must never block Pulse from loading, so any
            # failure here just reports forge_ok=False rather than failing the
            # whole request.
            try:
                forge_ok = bool(customforge_launcher.ensure_forge_running())
            except Exception:
                forge_ok = False
            payload = json.dumps({
                "ok": True,
                "forge_ok": forge_ok,
                "url": customforge_launcher.FORGE_URL,
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)
        elif self.path.startswith('/pulse/app'):
            body = PRAGON_PULSE_HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))
        elif self.path.startswith('/compass'):
            body = PRAGON_COMPASS_HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))
        elif self.path.startswith('/livekit/token'):
            self._handle_livekit_token()
        elif self.path.startswith('/api/gallery/list'):
            self._handle_gallery_list()
        elif self.path.startswith('/api/gallery/image/'):
            image_id = self.path.split('/api/gallery/image/', 1)[1].split('?')[0].split('/')[0]
            self._handle_gallery_image(image_id)
        else:
            body = PRAGON_HTML
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body.encode('utf-8'))

    def _handle_generate_image(self, post_payload=None):
        """Proxy image generation (server-side, bypasses browser CORS/referrer blocks).
        GET (query string): text-to-image only, unchanged.
        POST (JSON body): supports {prompt, model, key, reference_image (base64,
        no data: prefix), reference_mime} for true image-to-image / "make an
        image like this one" generation -- the reference image is sent to
        Omnix as an inlineData part alongside the text part."""
        from urllib.parse import parse_qs, urlparse
        import json, base64, random
        try:
            if post_payload is not None:
                prompt = (post_payload.get('prompt') or '').strip()
                api_key = post_payload.get('key', '') or ''
                model = post_payload.get('model') or 'gemini-3.1-flash-image'
                reference_image = post_payload.get('reference_image') or None
                reference_mime = post_payload.get('reference_mime') or 'image/png'
            else:
                query = parse_qs(urlparse(self.path).query)
                prompt = query.get('prompt', [''])[0]
                api_key = query.get('key', [''])[0]
                model = query.get('model', ['gemini-3.1-flash-image'])[0]
                reference_image = None
                reference_mime = None

            if not prompt:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing prompt parameter')
                return

            # Try 1: Omnix (if key provided and model is omnix)
            if api_key and 'gemini' in model.lower():
                try:
                    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    parts = []
                    if reference_image:
                        parts.append({"inlineData": {"mimeType": reference_mime, "data": reference_image}})
                        # Nudge the model toward image-to-image ("like this one") rather
                        # than treating the reference as unrelated context.
                        parts.append({"text": f"Using the attached reference image as a style/subject guide, {prompt}"})
                    else:
                        parts.append({"text": prompt})
                    gemini_body = json.dumps({
                        "contents": [{"parts": parts}],
                        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}
                    }).encode('utf-8')
                    req = Request(gemini_url, data=gemini_body, headers={'Content-Type': 'application/json'}, method='POST')
                    with urlopen(req, timeout=30) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        resp_parts = data.get('candidates', [{}])[0].get('content', {}).get('parts', [])
                        for part in resp_parts:
                            if part.get('inlineData'):
                                image_bytes = base64.b64decode(part['inlineData']['data'])
                                self.send_response(200)
                                self.send_header('Content-Type', part['inlineData'].get('mimeType', 'image/png'))
                                self.send_header('Cache-Control', 'no-cache')
                                self.end_headers()
                                self.wfile.write(image_bytes)
                                return
                except Exception as e:
                    if reference_image:
                        # The fallback providers below (HF SDXL, Pollinations) are
                        # text-to-image only -- they can't honor a reference image,
                        # so silently falling through would return an unrelated
                        # picture. Fail loudly instead.
                        self.send_response(502)
                        self.end_headers()
                        self.wfile.write(f'Omnix image editing failed: {e}'.encode('utf-8'))
                        return
                    pass # Fall through to next option

            if reference_image:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Image-to-image generation requires a valid Omnix API key and an *-image model.')
                return

            # Try 2: Hugging Face (free, no key needed for public models)
            try:
                hf_url = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
                hf_body = json.dumps({"inputs": prompt}).encode('utf-8')
                req = Request(hf_url, data=hf_body, headers={'Content-Type': 'application/json'}, method='POST')
                with urlopen(req, timeout=60) as response:
                    image_data = response.read()
                    if len(image_data) > 1000:
                        self.send_response(200)
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Cache-Control', 'no-cache')
                        self.end_headers()
                        self.wfile.write(image_data)
                        return
            except Exception as e:
                pass # Fall through to Pollinations

            # Try 3: Pollinations.AI
            seed = random.randint(1, 999999)
            encoded = quote(prompt)
            pollinations_url = f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=512&seed={seed}&nologo=true"
            req = Request(pollinations_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=30) as response:
                image_data = response.read()
                content_type = response.headers.get('Content-Type', 'image/jpeg')
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(image_data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(f'All image services failed. Last error: {str(e)}'.encode('utf-8'))

    def _handle_gallery_save(self, post_payload):
        """POST /api/gallery/save — persist a generated image to disk so it
        survives reloads/restarts, and record it in the JSON index."""
        import uuid
        try:
            b64 = post_payload.get('base64') or ''
            mime = post_payload.get('mimeType') or 'image/png'
            prompt = (post_payload.get('prompt') or '')[:500]
            if not b64:
                self.send_response(400); self.end_headers()
                self.wfile.write(b'Missing base64 image data'); return
            ext = _GALLERY_MIME_EXT.get(mime.lower(), 'png')
            image_id = uuid.uuid4().hex
            filename = f'{image_id}.{ext}'
            GALLERY_DIR.mkdir(parents=True, exist_ok=True)
            (GALLERY_DIR / filename).write_bytes(base64.b64decode(b64))
            items = _gallery_load_index()
            entry = {"id": image_id, "filename": filename, "mimeType": mime, "prompt": prompt, "ts": time.time()}
            items.append(entry)
            _gallery_save_index(items)
            body = json.dumps(entry).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500); self.end_headers()
            self.wfile.write(f'Gallery save failed: {e}'.encode('utf-8'))

    def _handle_livekit_token(self):
        """GET /livekit/token?room=<room>&identity=<id>&name=<display name>
        Mints a LiveKit room token for the browser's livekit-client so the
        'LiveKit' toggle can join the same room livekit_agent.py's
        VoicePipelineAgent is listening on."""
        from urllib.parse import parse_qs, urlparse
        try:
            query = parse_qs(urlparse(self.path).query)
            room = (query.get('room', ['pragon-room'])[0] or 'pragon-room').strip()
            identity = (query.get('identity', [''])[0] or '').strip() or f"pragon-user-{uuid.uuid4().hex[:8]}"
            name = (query.get('name', [''])[0] or '').strip()
            cfg = _livekit_get_config()
            token = _livekit_create_token(cfg['api_key'], cfg['api_secret'], room, identity, name)
            body = json.dumps({"token": token, "url": cfg['url'], "room": room, "identity": identity}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode('utf-8')
            self.send_response(200)  # 200 w/ {"error":...} so the frontend can show the message directly
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)

    def _handle_gallery_list(self):
        """GET /api/gallery/list — returns saved images (newest last), each
        with a browser-loadable url served by _handle_gallery_image."""
        try:
            items = _gallery_load_index()
            out = [{"id": it["id"], "prompt": it.get("prompt", ""), "ts": it.get("ts", 0),
                    "url": f"/api/gallery/image/{it['id']}"} for it in items]
            body = json.dumps(out).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500); self.end_headers()
            self.wfile.write(f'Gallery list failed: {e}'.encode('utf-8'))

    def _handle_gallery_image(self, image_id):
        """GET /api/gallery/image/<id> — serves one saved image's raw bytes."""
        try:
            items = _gallery_load_index()
            entry = next((it for it in items if it['id'] == image_id), None)
            if not entry:
                self.send_response(404); self.end_headers(); return
            path = GALLERY_DIR / entry['filename']
            if not path.is_file():
                self.send_response(404); self.end_headers(); return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', entry.get('mimeType', 'image/png'))
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_response(500); self.end_headers()

    def _handle_gallery_delete(self, post_payload):
        """POST /api/gallery/delete — removes one image from disk + index."""
        try:
            image_id = post_payload.get('id') or ''
            items = _gallery_load_index()
            match = next((it for it in items if it['id'] == image_id), None)
            items = [it for it in items if it['id'] != image_id]
            _gallery_save_index(items)
            if match:
                try:
                    (GALLERY_DIR / match['filename']).unlink(missing_ok=True)
                except Exception:
                    pass
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_response(500); self.end_headers()
            self.wfile.write(f'Gallery delete failed: {e}'.encode('utf-8'))

    def log_message(self, format, *args):
        pass # Suppress HTTP logs

class PragonUI:
    """
    P.R.A.G.O.N Web UI that replaces JarvisUI.
    Bridges between the web interface and JarvisLive backend via WebSocket.
    """
    
    def __init__(self, face_image_path: str = None):
        self.muted = False
        self.wake_word_enabled = False # Synced from consciousness.config_manager at startup by JarvisLive
        self.on_wake_word_toggle = None # Set by JarvisLive -- (enabled: bool) -> None
        self._state = "OFFLINE"
        self._log_history = []
        self._ws_clients = set()
        self._http_port = 8080
        self._ws_port = 8765
        self._http_server = None
        self._ws_server = None
        self._loop = None
        self._thread = None
        self.on_text_command = None # Set by JarvisLive
        self.on_remote_clicked = None # Set by JarvisLive
        self.on_whatsapp_connect_clicked = None # Set by JarvisLive (PRAGON WhatsUp)
        self.on_whatsapp_set_model = None # Set by JarvisLive -- (host, model) toggle for WhatsApp replies
        self.on_create_shortcut_clicked = None # Set by JarvisLive (desktop shortcut)
        self.on_toggle_autostart_clicked = None # Set by JarvisLive (auto-start on boot)
        self.on_bg_monitor_set_topics = None # Set by JarvisLive (Background Monitor)
        self.on_bg_monitor_get_topics = None
        self.on_bg_monitor_check_now = None
        self.on_toggle_clipboard_watch = None # Set by JarvisLive (Clipboard Intelligence)
        self.on_interrupt = None # Set by JarvisLive
        self.current_file = None # Path of the last file uploaded via the web UI

        # Real confirmation gate — UI issues the token, never the model. Set by JarvisLive.
        self.on_confirm_response = None # (request_id: str, approved: bool) -> None

        # Audio device picker. Set by JarvisLive.
        self.on_list_audio_devices = None # () -> dict(inputs, outputs, current_input, current_output)
        self.on_set_audio_device = None # (kind: 'input'|'output', name: str) -> str

        # A.C.T.I.O.N macro recorder — Settings > Action tab. Set by JarvisLive.
        self.on_macro_start = None # () -> str
        self.on_macro_stop = None # () -> tuple[str, str | None] (message, saved_name)
        self.on_macro_list = None # () -> list[str]
        self.on_macro_rename = None # (old: str, new: str) -> str
        self.on_macro_delete = None # (name: str) -> str
        self.on_macro_play = None # (name: str) -> str

        # Integration tab — Settings > Integration. Set by JarvisLive.
        self.on_plugin_list = None # () -> list[dict] (name, description, file, valid, error, enabled)
        self.on_plugin_toggle = None # (name: str, enabled: bool) -> str
        self.on_plugin_upload = None # (filename: str, content_b64: str) -> tuple[bool, str]
        self.on_plugin_delete = None # (filename: str) -> str

        # Integration tab — "Full Projects" (zip). Set by JarvisLive.
        self.on_integration_list = None # () -> list[dict]
        self.on_integration_upload = None # (filename: str, content_b64: str) -> tuple[bool, str]
        self.on_integration_start = None # (slug: str) -> str
        self.on_integration_stop = None # (slug: str) -> str
        self.on_integration_delete = None # (slug: str) -> str
        self.on_integration_logs = None # (slug: str) -> str

        # Start servers
        self._start_servers()
        
    def _start_servers(self):
        """Start HTTP and WebSocket servers in background threads."""
        # HTTP server
        def run_http():
            handler = PragonHTTPHandler
            socketserver.ThreadingTCPServer.allow_reuse_address = True
            self._http_server = socketserver.ThreadingTCPServer(("", self._http_port), handler)
            print(f"[P.R.A.G.O.N] HTTP server on http://localhost:{self._http_port}")
            self._http_server.serve_forever()
        
        threading.Thread(target=run_http, daemon=True).start()
        
          # WebSocket server
        if HAVE_WS:
            def run_ws():
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                
                async def handle_ws(websocket):
                    self._ws_clients.add(websocket)
                    print(f"[P.R.A.G.O.N] Client connected")
                    try:
                        await self._send_to_ws(websocket, {
                            "type": "status", "text": "JARVIS Ready", "speaking": False
                        })
                        await self._send_to_ws(websocket, {
                            "type": "wake_word_status", "enabled": self.wake_word_enabled
                        })
                        async for message in websocket:
                            await self._handle_ws_message(websocket, message)
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    finally:
                        self._ws_clients.discard(websocket)
                        print(f"[P.R.A.G.O.N] Client disconnected")
                
                async def start_server():
                    self._ws_server = await websockets.serve(handle_ws, "localhost", self._ws_port)
                    print(f"[P.R.A.G.O.N] WebSocket server on ws://localhost:{self._ws_port}")
                
                self._loop.run_until_complete(start_server())
                self._loop.run_forever()
            
            threading.Thread(target=run_ws, daemon=True).start()
        else:
            print("[P.R.A.G.O.N] WebSocket unavailable — install websockets")
        
        # Open browser
        time.sleep(3)
        webbrowser.open(f"http://localhost:{self._http_port}")
        
    async def _handle_ws_message(self, websocket, message):
        """Handle incoming messages from web client."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "text" and self.on_text_command:
                # Forward text command to JarvisLive
                self.on_text_command(data.get("text", ""))
                
            elif msg_type == "mute":
                self.muted = data.get("muted", False)
                self.set_state("MUTED" if self.muted else "LISTENING")
                
            elif msg_type == "toggle_wake_word":
                enabled = bool(data.get("enabled", False))
                self.wake_word_enabled = enabled
                if self.on_wake_word_toggle:
                    self.on_wake_word_toggle(enabled)
                self._broadcast({"type": "wake_word_status", "enabled": enabled})

            elif msg_type == "test_voice":
                self._broadcast({
                    "type": "toast",
                    "text": "Voice test — all systems operational"
                })

            elif msg_type == "macro_start":
                if self.on_macro_start:
                    text = self.on_macro_start()
                    await self._send_to_ws(websocket, {"type": "macro_status", "recording": True, "text": text})

            elif msg_type == "macro_stop":
                if self.on_macro_stop:
                    text, saved_name = self.on_macro_stop()
                    await self._send_to_ws(websocket, {"type": "macro_status", "recording": False, "text": text})
                    if saved_name and self.on_macro_list:
                        await self._send_to_ws(websocket, {"type": "macro_list", "macros": self.on_macro_list()})

            elif msg_type == "macro_list":
                if self.on_macro_list:
                    await self._send_to_ws(websocket, {"type": "macro_list", "macros": self.on_macro_list()})

            elif msg_type == "plugin_list":
                if self.on_plugin_list:
                    await self._send_to_ws(websocket, {"type": "plugin_list", "plugins": self.on_plugin_list()})

            elif msg_type == "plugin_toggle":
                if self.on_plugin_toggle:
                    text = self.on_plugin_toggle(data.get("name", ""), bool(data.get("enabled")))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_plugin_list:
                        await self._send_to_ws(websocket, {"type": "plugin_list", "plugins": self.on_plugin_list()})

            elif msg_type == "plugin_upload":
                if self.on_plugin_upload:
                    ok, msg = self.on_plugin_upload(data.get("filename", ""), data.get("content", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": msg})
                    if self.on_plugin_list:
                        await self._send_to_ws(websocket, {"type": "plugin_list", "plugins": self.on_plugin_list()})

            elif msg_type == "plugin_delete":
                if self.on_plugin_delete:
                    text = self.on_plugin_delete(data.get("filename", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_plugin_list:
                        await self._send_to_ws(websocket, {"type": "plugin_list", "plugins": self.on_plugin_list()})

            elif msg_type == "integration_list":
                if self.on_integration_list:
                    await self._send_to_ws(websocket, {"type": "integration_list", "integrations": self.on_integration_list()})

            elif msg_type == "integration_upload":
                if self.on_integration_upload:
                    ok, msg = self.on_integration_upload(data.get("filename", ""), data.get("content", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": msg})
                    if self.on_integration_list:
                        await self._send_to_ws(websocket, {"type": "integration_list", "integrations": self.on_integration_list()})

            elif msg_type == "integration_start":
                if self.on_integration_start:
                    text = self.on_integration_start(data.get("slug", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_integration_list:
                        await self._send_to_ws(websocket, {"type": "integration_list", "integrations": self.on_integration_list()})

            elif msg_type == "integration_stop":
                if self.on_integration_stop:
                    text = self.on_integration_stop(data.get("slug", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_integration_list:
                        await self._send_to_ws(websocket, {"type": "integration_list", "integrations": self.on_integration_list()})

            elif msg_type == "integration_delete":
                if self.on_integration_delete:
                    text = self.on_integration_delete(data.get("slug", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_integration_list:
                        await self._send_to_ws(websocket, {"type": "integration_list", "integrations": self.on_integration_list()})

            elif msg_type == "integration_logs":
                if self.on_integration_logs:
                    text = self.on_integration_logs(data.get("slug", ""))
                    await self._send_to_ws(websocket, {"type": "integration_logs", "slug": data.get("slug", ""), "text": text})

            elif msg_type == "remote_connect":
                if not self.on_remote_clicked:
                    await self._send_to_ws(websocket, {
                        "type": "phoneview_pairing", "ok": False,
                        "error": "PhoneView unavailable. Run: pip install fastapi \"uvicorn[standard]\" cryptography"
                    })
                else:
                    result = self.on_remote_clicked()
                    if not result:
                        await self._send_to_ws(websocket, {
                            "type": "phoneview_pairing", "ok": False,
                            "error": "PhoneView unavailable. Run: pip install fastapi \"uvicorn[standard]\" cryptography"
                        })
                    else:
                        url, key, auto_url, manual = result
                        await self._send_to_ws(websocket, {
                            "type": "phoneview_pairing", "ok": True,
                            "url": url, "key": key, "auto_url": auto_url, "manual": manual,
                            "qr": make_qr_data_uri(auto_url),
                        })

            elif msg_type == "whatsapp_connect":
                if not self.on_whatsapp_connect_clicked:
                    await self._send_to_ws(websocket, {
                        "type": "whatsapp_pairing", "ok": False,
                        "error": "PRAGON WhatsUp unavailable."
                    })
                else:
                    ok, err = self.on_whatsapp_connect_clicked()
                    if not ok:
                        await self._send_to_ws(websocket, {
                            "type": "whatsapp_pairing", "ok": False,
                            "error": err or "PRAGON WhatsUp failed to start."
                        })
                    # else: QR/ready events arrive asynchronously via
                    # self._broadcast() from the bridge's callbacks.

            elif msg_type == "whatsapp_set_model":
                host = (data.get("host") or "").strip()
                model = (data.get("model") or "").strip()
                if self.on_whatsapp_set_model and model:
                    self.on_whatsapp_set_model(host or None, model)

            elif msg_type == "toggle_clipboard_watch":
                enabled = self.on_toggle_clipboard_watch() if self.on_toggle_clipboard_watch else False
                await self._send_to_ws(websocket, {"type": "clipboard_watch_state", "enabled": enabled})

            elif msg_type == "bg_monitor_get_topics":
                topics = self.on_bg_monitor_get_topics() if self.on_bg_monitor_get_topics else []
                await self._send_to_ws(websocket, {"type": "bg_monitor_topics", "topics": topics})

            elif msg_type == "bg_monitor_set_topics":
                if self.on_bg_monitor_set_topics:
                    self.on_bg_monitor_set_topics(data.get("topics", []))
                await self._send_to_ws(websocket, {"type": "bg_monitor_saved"})

            elif msg_type == "bg_monitor_check_now":
                if self.on_bg_monitor_check_now:
                    self.on_bg_monitor_check_now()
                await self._send_to_ws(websocket, {"type": "bg_monitor_checking"})

            elif msg_type == "toggle_autostart":
                if not self.on_toggle_autostart_clicked:
                    await self._send_to_ws(websocket, {
                        "type": "autostart_result", "ok": False,
                        "message": "Auto-start unavailable."
                    })
                else:
                    ok, enabled, message = self.on_toggle_autostart_clicked()
                    await self._send_to_ws(websocket, {
                        "type": "autostart_result", "ok": ok,
                        "enabled": enabled, "message": message
                    })

            elif msg_type == "create_shortcut":
                if not self.on_create_shortcut_clicked:
                    await self._send_to_ws(websocket, {
                        "type": "shortcut_result", "ok": False,
                        "message": "Shortcut creation unavailable."
                    })
                else:
                    ok, message = self.on_create_shortcut_clicked()
                    await self._send_to_ws(websocket, {
                        "type": "shortcut_result", "ok": ok, "message": message
                    })

            elif msg_type == "macro_rename":
                if self.on_macro_rename:
                    text = self.on_macro_rename(data.get("old", ""), data.get("name", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_macro_list:
                        await self._send_to_ws(websocket, {"type": "macro_list", "macros": self.on_macro_list()})

            elif msg_type == "macro_delete":
                if self.on_macro_delete:
                    text = self.on_macro_delete(data.get("name", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                    if self.on_macro_list:
                        await self._send_to_ws(websocket, {"type": "macro_list", "macros": self.on_macro_list()})

            elif msg_type == "confirm_response":
                if self.on_confirm_response:
                    self.on_confirm_response(data.get("id", ""), bool(data.get("approved")))

            elif msg_type == "list_audio_devices":
                if self.on_list_audio_devices:
                    await self._send_to_ws(websocket, {
                        "type": "audio_devices", **self.on_list_audio_devices()
                    })

            elif msg_type == "set_audio_device":
                if self.on_set_audio_device:
                    text = self.on_set_audio_device(data.get("kind", ""), data.get("name", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})

            elif msg_type == "macro_play":
                if self.on_macro_play:
                    text = self.on_macro_play(data.get("name", ""))
                    await self._send_to_ws(websocket, {"type": "toast", "text": text})
                
        except Exception as e:
            print(f"[P.R.A.G.O.N] WS error: {e}")
    
    async def _send_to_ws(self, websocket, data):
        """Send data to a specific WebSocket client."""
        try:
            await websocket.send(json.dumps(data))
        except Exception:
            pass
    
    def _broadcast(self, data):
        """Broadcast data to all connected WebSocket clients."""
        if not self._ws_clients or not self._loop:
            return
        try:
            coro = self._broadcast_async(data)
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            pass # Loop not ready yet

    def broadcast(self, data: dict) -> None:
        """Public wrapper — used by pragoncore/confirm_gate.py and other
        non-UI modules that need to push a message without reaching into
        PragonUI's internals."""
        self._broadcast(data)
    
    async def _broadcast_async(self, data):
        """Async broadcast to all clients."""
        msg = json.dumps(data)
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
    
    # ── FRIDAY bridge (additive — JARVIS's methods above are unchanged) ──

    def set_friday_processor(self, fn):
        """Register FridayEngine.process so /friday/act can reach it."""
        register_friday_processor(fn)

    def write_friday_log(self, text: str):
        """Push a FRIDAY transcript line to connected clients."""
        self._log_history.append(text)
        if text.startswith("You: "):
            self._broadcast({"type": "friday_transcript", "role": "user", "text": text[5:]})
        elif text.startswith("Friday: "):
            self._broadcast({"type": "friday_transcript", "role": "assistant", "text": text[8:]})
        else:
            self._broadcast({"type": "friday_transcript", "role": "system", "text": text})

    def set_friday_state(self, state: str):
        """Update FRIDAY's status dot/label (THINKING, STANDBY, ACTIVE)."""
        text_map = {"THINKING": "Thinking…", "STANDBY": "STANDBY", "ACTIVE": "ACTIVE"}
        self._broadcast({"type": "friday_status", "text": text_map.get(state, state)})

    # ── GHOST bridge (additive — mirrors the FRIDAY bridge above, minus RAG) ──

    def set_ghost_processor(self, fn):
        """Register the GHOST engine's process fn so /ghost/act can reach it."""
        register_ghost_processor(fn)

    def write_ghost_log(self, text: str):
        """Push a GHOST transcript line to connected clients."""
        self._log_history.append(text)
        if text.startswith("You: "):
            self._broadcast({"type": "ghost_transcript", "role": "user", "text": text[5:]})
        elif text.startswith("Ghost: "):
            self._broadcast({"type": "ghost_transcript", "role": "assistant", "text": text[7:]})
        else:
            self._broadcast({"type": "ghost_transcript", "role": "system", "text": text})

    def set_ghost_state(self, state: str):
        """Update GHOST's status dot/label (THINKING, STANDBY, ACTIVE)."""
        text_map = {"THINKING": "Thinking…", "STANDBY": "STANDBY", "ACTIVE": "ACTIVE"}
        self._broadcast({"type": "ghost_status", "text": text_map.get(state, state)})

    # ── JarvisUI-compatible interface ──
    
    def set_state(self, state: str):
        """Update status indicator (LISTENING, SPEAKING, THINKING, MUTED, OFFLINE)."""
        self._state = state
        state_map = {
            "LISTENING": ("Listening…", False),
            "SPEAKING": ("JARVIS Speaking…", True),
            "THINKING": ("Processing…", False),
            "MUTED": ("Muted", False),
            "OFFLINE": ("Offline", False),
        }
        text, speaking = state_map.get(state, (state, False))
        self._broadcast({
            "type": "status",
            "text": text,
            "speaking": speaking
        })
    
    def write_log(self, text: str):
        """Add a log entry to the transcript panel."""
        self._log_history.append(text)
        # Parse "You: ..." and "Jarvis: ..." format
        if text.startswith("You: "):
            self._broadcast({
                "type": "transcript",
                "role": "user",
                "text": text[5:]
            })
        elif text.startswith("Jarvis: "):
            self._broadcast({
                "type": "transcript",
                "role": "assistant",
                "text": text[8:]
            })
        elif text.startswith("SYS: "):
            self._broadcast({
                "type": "toast",
                "text": text[5:]
            })
        else:
            self._broadcast({
                "type": "transcript",
                "role": "system",
                "text": text
            })
    
    def send_code(self, code: str, lang: str = "html", title: str = "P.R.A.G.O.N"):
        """Push generated code to the F.O.R.G.E editor and show an 'Open' card in the log.

        lang: one of "html", "css", "js" — selects which Forge file/tab the code lands in.
        title: label shown on the code card (defaults to "P.R.A.G.O.N").
        """
        if lang not in ("html", "css", "js"):
            lang = "html"
        self._broadcast({
            "type": "code",
            "code": code,
            "lang": lang,
            "title": title
        })

    # ── Bridge methods expected by JarvisLive (main.py) ─────────────────────

    def show_content(self, label: str, content: str):
        """Mirror tool output (e.g. web_search results) into the transcript panel.

        This UI has no separate content panel, so results are pushed as a
        system-role transcript entry, labeled and length-capped so they don't
        flood the log panel.
        """
        text = content if len(content) <= 4000 else content[:4000] + " …[truncated]"
        self._broadcast({
            "type": "transcript",
            "role": "system",
            "text": f"[{label}]\n{text}"
        })

    def start_camera_stream(self):
        """No-op — this web UI has no live camera preview element.

        JARVIS still captures and analyzes camera frames via screen_process;
        this just skips showing a live feed in the browser.
        """
        self._broadcast({"type": "toast", "text": "Camera capture in progress…"})

    def stop_camera_stream(self):
        """No-op counterpart to start_camera_stream (see above)."""
        pass

    def notify_phone_connected(self):
        """Toast + status update when a phone connects via PhoneView."""
        self._broadcast({"type": "toast", "text": " Phone connected"})
        self._broadcast({"type": "phoneview_status", "connected": True})

    def wait_for_api_key(self):
        """No-op for web UI — API key is configured in JARVIS backend."""
        pass
    
    def root_mainloop(self):
        """Keep-alive for the UI thread."""
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    
    def _handle_upload(self):
        """Handle file uploads from drag-and-drop / file picker."""
        import cgi, uuid
        try:
            ctype, pdict = cgi.parse_header(self.headers.get('Content-Type', ''))
            if ctype == 'multipart/form-data':
                pdict['boundary'] = bytes(pdict['boundary'], 'utf-8')
                pdict.setdefault('CONTENT-LENGTH', int(self.headers.get('Content-Length', 0)))
                fields = cgi.parse_multipart(self.rfile, pdict)
                file_data = fields.get('file', [None])[0]
                filename = fields.get('filename', ['upload'])[0]
            else:
                length = int(self.headers.get('Content-Length', 0) or 0)
                file_data = self.rfile.read(length)
                filename = 'upload'
        except Exception as e:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode('utf-8'))
            return
        if not file_data:
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "No file data"}).encode('utf-8'))
            return
        upload_dir = Path(__file__).parent / 'uploads'
        upload_dir.mkdir(exist_ok=True)
        safe_name = str(uuid.uuid4())[:8] + '_' + Path(filename).name
        filepath = upload_dir / safe_name
        with open(filepath, 'wb') as f:
            f.write(file_data if isinstance(file_data, bytes) else file_data.encode('utf-8'))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True,
            "filename": filename,
            "saved_as": safe_name,
            "path": str(filepath)
        }).encode('utf-8'))

    # Cleanup
    def shutdown(self):
        """Shutdown servers."""
        if self._http_server:
            self._http_server.shutdown()
        if self._ws_server and self._loop:
            self._loop.call_soon_threadsafe(self._ws_server.close)
            self._loop.call_soon_threadsafe(self._loop.stop)