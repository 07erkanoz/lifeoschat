"""Native KDE/Qt front end, Live Voice engine, Artifact studio, and MCP/CLI bridge for Cloudflare Workers AI & Groq."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSettings, QUrl, Signal, Slot
from PySide6.QtGui import QClipboard, QDesktopServices, QGuiApplication
from PySide6.QtMultimedia import QAudioInput, QMediaCaptureSession, QMediaFormat, QMediaRecorder
from PySide6.QtQml import QQmlApplicationEngine

from core import (
    AUTO_MODEL_ID,
    CHAT_TASKS,
    GROQ_FREE_LIMITS,
    conversation_payload,
    default_chat_model,
    embedding_summary,
    estimate_cloudflare_neurons,
    estimate_tokens,
    extract_artifacts_from_messages,
    file_data,
    get_groq_model_limits,
    model_sort_key,
    normalize_groq_model,
    normalize_model,
    parse_response,
    render_message_html,
    select_best_model,
)
from documents import (
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    convert_document_file,
    export_document,
    extract_document_full,
)


APP_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "lifeos-chat"
MCP_CONFIG_PATH = Path.home() / ".gemini/config/mcp_config.json"
API = "https://api.cloudflare.com/client/v4"
GROQ = "https://api.groq.com/openai/v1"
CF_DAILY_FREE_NEURONS = 10000.0


def request_with_headers(
    url: str,
    token: str,
    payload: dict | None = None,
    *,
    raw: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[bytes, str, dict[str, str]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, image/*, audio/*",
        "User-Agent": "LifeOS-Chat/2.2",
    }
    if payload is not None or raw is not None:
        headers["Content-Type"] = content_type
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else raw
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            mime = resp_headers.get("content-type", "application/json").split(";")[0]
            return response.read(), mime, resp_headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            detail = parsed.get("error", {}).get("message") or "; ".join(
                str(e.get("message", e)) for e in parsed.get("errors", [])
            )
        except (ValueError, AttributeError):
            detail = body[:350]
        raise ValueError(f"HTTP {exc.code}: {detail or exc.reason}") from exc


def request(
    url: str,
    token: str,
    payload: dict | None = None,
    *,
    raw: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[bytes, str]:
    data, mime, _ = request_with_headers(url, token, payload, raw=raw, content_type=content_type)
    return data, mime


def multipart_audio(path: str, model: str, language: str = "tr") -> tuple[bytes, str]:
    boundary = "lifeos" + uuid.uuid4().hex
    file = Path(path)
    lang_part = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\n{language}\r\n'
        if language
        else ""
    )
    raw = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f"{lang_part}"
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return raw, f"multipart/form-data; boundary={boundary}"


def synthesize_natural_tts(text: str, lang: str = "tr", out_dir: Path | None = None) -> str:
    """Synthesizes natural spoken audio (MP3/WAV) for Live Voice mode with Turkish neural voice & offline fallback."""
    target_dir = out_dir or (APP_DIR / "media")
    target_dir.mkdir(parents=True, exist_ok=True)
    clean_text = re.sub(r"[#*_`>|~]", " ", text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()[:900]
    if not clean_text:
        clean_text = "Hazırım."

    # Split into sentence chunks <= 190 chars for Google Neural TTS stream
    chunks: list[str] = []
    words = clean_text.split(" ")
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= 185:
            cur = f"{cur} {w}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = w
    if cur:
        chunks.append(cur)

    try:
        audio_bytes = bytearray()
        for ch in chunks[:5]:
            q = urllib.parse.urlencode({"ie": "UTF-8", "client": "tw-ob", "tl": lang, "q": ch})
            req = urllib.request.Request(
                f"https://translate.google.com/translate_tts?{q}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                audio_bytes.extend(resp.read())
        if len(audio_bytes) > 512:
            out_file = target_dir / f"live_tts_{uuid.uuid4().hex[:10]}.mp3"
            out_file.write_bytes(bytes(audio_bytes))
            return str(out_file)
    except Exception:
        pass

    if shutil.which("espeak-ng"):
        out_wav = target_dir / f"live_tts_{uuid.uuid4().hex[:10]}.wav"
        subprocess.run(["espeak-ng", "-v", lang, "-s", "155", "-w", str(out_wav), clean_text], check=False, timeout=15)
        if out_wav.exists():
            return str(out_wav)
    raise ValueError("Seslendirme (TTS) üretilemedi.")


class Bridge(QObject):
    accountsChanged = Signal()
    chatsChanged = Signal()
    messagesChanged = Signal()
    modelsChanged = Signal()
    busyChanged = Signal()
    quotaChanged = Signal()
    previewChanged = Signal()
    artifactsChanged = Signal()
    settingsChanged = Signal()
    liveVoiceChanged = Signal()
    liveAudioReady = Signal(str, str)  # (audioUrl, assistantText)
    notice = Signal(str)
    resultReady = Signal(str)
    recordingChanged = Signal()
    recorded = Signal(str)

    def __init__(self):
        super().__init__()
        APP_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings("LifeOS", "Chat")
        self.db = sqlite3.connect(APP_DIR / "history.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self._accounts = json.loads(self.settings.value("accounts", "[]"))
        self._active_account = str(self.settings.value("activeAccount", ""))
        if not self._active_account and self._accounts:
            self._active_account = self._accounts[0]["id"]
        self._chat = ""
        self._models: list[dict] = []
        self._account_models: dict[str, list[dict]] = {}
        self._busy = False
        self._busy_stage = ""
        self._tokens: dict[str, str] = {}
        self._rate_limits: dict[str, dict] = {}
        self._preview_attachment: dict = {}
        self._capture = None
        self._recorder = None
        self._recording = False
        self._record_path = ""
        self._live_mode = False
        self._live_state = "idle"  # idle | listening | thinking | speaking
        self._live_transcript = ""
        self._live_reply = ""
        self._dark_mode = True

    def _init_db(self) -> None:
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS chats ("
            "id TEXT PRIMARY KEY, title TEXT, account TEXT, model TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT, role TEXT, content TEXT, "
            "attachment TEXT DEFAULT '', kind TEXT DEFAULT 'text', media TEXT DEFAULT '', "
            "created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS usage_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, account TEXT, provider TEXT, model TEXT, "
            "prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, "
            "total_tokens INTEGER DEFAULT 0, neurons REAL DEFAULT 0.0, created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS artifacts ("
            "id TEXT PRIMARY KEY, chat TEXT, category TEXT, kind TEXT, title TEXT, "
            "badge TEXT, path TEXT, media TEXT, content TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        existing_cols = {
            row["name"] for row in self.db.execute("PRAGMA table_info(messages)").fetchall()
        }
        migrations = [
            ("attachment_text", "TEXT DEFAULT ''"),
            ("attachment_meta", "TEXT DEFAULT ''"),
            ("thinking", "TEXT DEFAULT ''"),
            ("model_used", "TEXT DEFAULT ''"),
            ("auto_reason", "TEXT DEFAULT ''"),
            ("prompt_tokens", "INTEGER DEFAULT 0"),
            ("completion_tokens", "INTEGER DEFAULT 0"),
            ("total_tokens", "INTEGER DEFAULT 0"),
            ("neurons", "REAL DEFAULT 0.0"),
            ("elapsed_ms", "INTEGER DEFAULT 0"),
            ("truncated", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_def in migrations:
            if col_name not in existing_cols:
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {col_name} {col_def}")

        rows = self.db.execute(
            "SELECT id, content, attachment, attachment_text FROM messages WHERE role='user' AND content LIKE '%[Ek belge:%'"
        ).fetchall()
        for r in rows:
            raw_c = r["content"] or ""
            marker = "\n\n[Ek belge:"
            if marker in raw_c:
                user_part, doc_part = raw_c.split(marker, 1)
                clean_user = user_part.strip() or f"{r['attachment'] or 'Belge'} incelendi."
                self.db.execute(
                    "UPDATE messages SET content=?, attachment_text=? WHERE id=?",
                    (clean_user, "[Ek belge:" + doc_part, r["id"]),
                )
        self.db.commit()

    def _run(self, fn, stage: str = "Yanıt üretiliyor…"):
        def work():
            try:
                fn()
            except Exception as exc:
                self.notice.emit(str(exc))
                if self._live_mode:
                    self._live_state = "idle"
                    self.liveVoiceChanged.emit()
            finally:
                self._busy = False
                self._busy_stage = ""
                self.busyChanged.emit()

        self._busy = True
        self._busy_stage = stage
        self.busyChanged.emit()
        threading.Thread(target=work, daemon=True).start()

    # ======================== SETTINGS & MCP MANAGEMENT ========================
    def _is_mcp_enabled(self) -> bool:
        try:
            if not MCP_CONFIG_PATH.exists():
                return False
            cfg = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
            srv = cfg.get("mcpServers", {}).get("lifeoschat")
            return bool(srv and not srv.get("disabled", False))
        except Exception:
            return False

    @Property(str, notify=settingsChanged)
    def settingsJson(self):
        return json.dumps(
            {
                "fontSize": int(self.settings.value("fontSize", 14)),
                "maxTokens": int(self.settings.value("maxTokens", 4096)),
                "temperature": float(self.settings.value("temperature", 0.6)),
                "showThinking": self.settings.value("showThinking", "true") in (True, "true", "1", 1),
                "autoOpenRightPanel": self.settings.value("autoOpenRightPanel", "true") in (True, "true", "1", 1),
                "liveVoiceHandsFree": self.settings.value("liveVoiceHandsFree", "true") in (True, "true", "1", 1),
                "ttsEngine": str(self.settings.value("ttsEngine", "auto")),
                "systemPrompt": str(self.settings.value("systemPrompt", "")),
                "mcpEnabled": self._is_mcp_enabled(),
                "mcpConfigPath": str(MCP_CONFIG_PATH),
                "repoUrl": "https://github.com/07erkanoz/lifeoschat",
            },
            ensure_ascii=False,
        )

    @Slot(str)
    def updateSettings(self, json_str: str):
        try:
            data = json.loads(json_str)
        except ValueError:
            return
        for key in ("fontSize", "maxTokens", "temperature", "showThinking", "autoOpenRightPanel", "liveVoiceHandsFree", "ttsEngine", "systemPrompt"):
            if key in data:
                self.settings.setValue(key, data[key])
        if "mcpEnabled" in data:
            self.setMcpEnabled(bool(data["mcpEnabled"]))
        self.settingsChanged.emit()
        self.messagesChanged.emit()
        self.notice.emit("Ayarlar kaydedildi ve uygulandı.")

    @Slot(bool)
    def setMcpEnabled(self, enabled: bool):
        try:
            MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            cfg = {}
            if MCP_CONFIG_PATH.exists():
                try:
                    cfg = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
                except ValueError:
                    cfg = {}
            servers = cfg.setdefault("mcpServers", {})
            app_py_path = str((Path(__file__).parent / "app.py").resolve())
            servers["lifeoschat"] = {
                "command": "python3",
                "args": [app_py_path, "--mcp"],
                "disabled": not enabled,
            }
            MCP_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.settingsChanged.emit()
            self.notice.emit(
                "🔌 'lifeoschat' MCP sunucusu aktif edildi (agy CLI ve diğer araçlar tüm modellere erişebilir)."
                if enabled
                else "🔌 'lifeoschat' MCP sunucusu devre dışı bırakıldı."
            )
        except Exception as exc:
            self.notice.emit(f"MCP ayarı güncellenemedi: {exc}")

    # ======================== LIVE VOICE CONVERSATION MODE ========================
    @Property(bool, notify=liveVoiceChanged)
    def liveVoiceActive(self):
        return self._live_mode

    @Property(str, notify=liveVoiceChanged)
    def liveVoiceState(self):
        return self._live_state

    @Property(str, notify=liveVoiceChanged)
    def liveTranscript(self):
        return self._live_transcript

    @Property(str, notify=liveVoiceChanged)
    def liveReply(self):
        return self._live_reply

    @Slot()
    def startLiveVoiceSession(self):
        if not self._accounts:
            self.notice.emit("Önce bir API hesabı ekleyin.")
            return
        if not self._chat:
            self.newChat(self._active_account, AUTO_MODEL_ID)
        self._live_mode = True
        self._live_transcript = ""
        self._live_reply = "Sizi dinliyorum, konuşabilirsiniz…"
        self.liveVoiceChanged.emit()
        self.startLiveListening()

    @Slot()
    def stopLiveVoiceSession(self):
        if self._recording and self._recorder:
            self._recorder.stop()
            self._recording = False
            self.recordingChanged.emit()
        self._live_mode = False
        self._live_state = "idle"
        self.liveVoiceChanged.emit()

    @Slot()
    def startLiveListening(self):
        if not self._live_mode or self._busy:
            return
        self._live_state = "listening"
        self.liveVoiceChanged.emit()
        if not self._recording:
            self.toggleRecording()

    @Slot()
    def finishLiveListeningAndRespond(self):
        """Stops microphone recording in Live Voice mode and immediately runs ASR -> LLM -> TTS."""
        if not self._live_mode:
            return
        if self._recording and self._recorder:
            self._live_state = "thinking"
            self.liveVoiceChanged.emit()
            self._recorder.stop()
            self._recording = False
            self.recordingChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=busyChanged)
    def busyStage(self):
        return self._busy_stage

    @Property(bool, notify=recordingChanged)
    def recording(self):
        return self._recording

    @Slot(bool)
    def setDarkMode(self, dark: bool):
        if self._dark_mode != dark:
            self._dark_mode = dark
            self.messagesChanged.emit()

    @Slot()
    def toggleRecording(self):
        if self._recording:
            self._recorder.stop()
            self._recording = False
            self.recordingChanged.emit()
            return
        if self._capture is None:
            self._capture = QMediaCaptureSession(self)
            self._audio_input = QAudioInput(self)
            self._recorder = QMediaRecorder(self)
            fmt = QMediaFormat()
            fmt.setFileFormat(QMediaFormat.FileFormat.Wave)
            self._recorder.setMediaFormat(fmt)
            self._capture.setAudioInput(self._audio_input)
            self._capture.setRecorder(self._recorder)
            self._recorder.recorderStateChanged.connect(self._record_state)
            self._recorder.errorOccurred.connect(lambda _error, message: self.notice.emit(message))
        recordings = APP_DIR / "recordings"
        recordings.mkdir(exist_ok=True)
        self._record_path = str(recordings / (str(uuid.uuid4()) + ".wav"))
        self._recorder.setOutputLocation(QUrl.fromLocalFile(self._record_path))
        self._recorder.record()
        self._recording = True
        self.recordingChanged.emit()

    def _record_state(self, state):
        if state == QMediaRecorder.RecorderState.StoppedState and self._record_path and Path(self._record_path).exists():
            if self._live_mode:
                self._process_live_voice_turn(self._record_path)
            else:
                url_str = QUrl.fromLocalFile(self._record_path).toString()
                self.inspectAttachment(url_str)
                self.recorded.emit(url_str)

    def _process_live_voice_turn(self, wav_path: str):
        if not self._chat:
            self.newChat(self._active_account, AUTO_MODEL_ID)
        current_chat = self._chat
        chat = self.db.execute("SELECT * FROM chats WHERE id=?", (current_chat,)).fetchone()
        account = self._account(chat["account"])
        rows = self.db.execute(
            "SELECT role, content, attachment, attachment_text, kind, media FROM messages WHERE chat=? ORDER BY id",
            (current_chat,),
        ).fetchall()
        history = [dict(r) for r in rows]

        def work():
            self._live_state = "thinking"
            self.liveVoiceChanged.emit()
            if not self._models:
                self._models = self._fetch_models_for_account(account)
                self.modelsChanged.emit()

            # 1. ASR (Speech to Text) across active or any available account
            asr_account, asr_model = self._find_best_account_and_model("automatic-speech-recognition", preferred_account=account)
            if not asr_model:
                raise ValueError("Ses tanıma (Whisper) modeli bulunamadı.")
            token = self._token(asr_account["id"])
            if asr_account["provider"] == "groq":
                body, content_type = multipart_audio(wav_path, asr_model)
                asr_data, asr_mime, asr_headers = request_with_headers(
                    GROQ + "/audio/transcriptions", token, raw=body, content_type=content_type
                )
            else:
                asr_payload = {"audio": file_data(wav_path)[1]}
                asr_data, asr_mime, asr_headers = request_with_headers(
                    f"{API}/accounts/{asr_account['accountId']}/ai/run/{asr_model}", token, asr_payload
                )
            asr_res = parse_response(asr_data, asr_mime, prompt_text="live_audio", task="automatic-speech-recognition", model_id=asr_model)
            self._record_usage_and_headers(asr_account, asr_model, asr_res.get("usage", {}), asr_headers)
            spoken_text = (asr_res.get("text") or "").strip()
            hallucinations = {"thank you.", "thank you", "thanks for watching.", "altyazı m.k.", "subtitles by", "you", "."}
            if not spoken_text or spoken_text.lower().strip() in hallucinations:
                self._live_reply = "Ses net algılanamadı, lütfen mikrofona konuşup tekrar gönderin."
                self._live_state = "idle"
                self.liveVoiceChanged.emit()
                return

            self._live_transcript = spoken_text
            self.liveVoiceChanged.emit()

            # 2. Fast Conversational LLM Response
            chat_model, _ = select_best_model(self._models, spoken_text, preferred_task="fast")
            live_sys = (
                "Sen LifeOS Canlı Sesli Asistanısın (Gemini Live tarzı gerçek zamanlı sesli sohbet). "
                "Kullanıcı seninle sesli konuşuyor. Yanıtını doğrudan seslendirilecek şekilde doğal, akıcı, "
                "samimi ve öz (genellikle 2-4 cümle) olarak Türkçe ver."
            )
            llm_res, elapsed_ms = self._execute_model_call(
                account=account,
                model=chat_model,
                task="text-generation",
                history=history,
                prompt=spoken_text,
                attachment=None,
                system_prompt=live_sys,
            )
            reply_text = (llm_res.get("text") or "").strip()
            self._live_reply = reply_text
            self.liveVoiceChanged.emit()

            # 3. Synthesize Speech (TTS)
            audio_file = synthesize_natural_tts(reply_text, lang="tr")
            audio_url = QUrl.fromLocalFile(audio_file).toString()

            # Save both turns to chat history
            usage = llm_res.get("usage") or {}
            model_title = next((m["title"] for m in self._models if m["id"] == chat_model), chat_model)
            self.db.execute(
                "INSERT INTO messages(chat,role,content,attachment,kind,media) VALUES(?,?,?,?,?,?)",
                (current_chat, "user", f"🎙️ {spoken_text}", "", "text", ""),
            )
            self.db.execute(
                "INSERT INTO messages(chat,role,content,thinking,kind,media,model_used,auto_reason,"
                "prompt_tokens,completion_tokens,total_tokens,neurons,elapsed_ms,truncated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    current_chat,
                    "assistant",
                    reply_text,
                    llm_res.get("thinking", ""),
                    "audio",
                    audio_url,
                    model_title,
                    "🎙️ Canlı Sesli Sohbet",
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("neurons") or 0.0),
                    int(round(elapsed_ms)),
                    0,
                ),
            )
            self.db.execute(
                "UPDATE chats SET title=CASE WHEN title='Yeni sohbet' THEN ? ELSE title END, updated=CURRENT_TIMESTAMP WHERE id=?",
                (spoken_text[:55], current_chat),
            )
            self.db.commit()
            self.chatsChanged.emit()
            self.messagesChanged.emit()
            self.artifactsChanged.emit()
            self.quotaChanged.emit()

            self._live_state = "speaking"
            self.liveVoiceChanged.emit()
            self.liveAudioReady.emit(audio_url, reply_text)

        self._run(work, stage="Canlı ses yanıtlanıyor…")

    @Slot()
    def onLiveSpeechFinished(self):
        """Called by QML MediaPlayer when assistant finishes speaking in Live Voice mode."""
        if not self._live_mode:
            return
        hands_free = self.settings.value("liveVoiceHandsFree", "true") in (True, "true", "1", 1)
        if hands_free:
            self.startLiveListening()
        else:
            self._live_state = "idle"
            self.liveVoiceChanged.emit()

    # ======================== PROPERTIES & DATA JSON ========================
    @Property(str, notify=accountsChanged)
    def accountsJson(self):
        return json.dumps(self._accounts, ensure_ascii=False)

    @Property(str, notify=accountsChanged)
    def activeAccount(self):
        return self._active_account

    @Property(str, notify=chatsChanged)
    def chatsJson(self):
        rows = self.db.execute("SELECT * FROM chats ORDER BY updated DESC").fetchall()
        return json.dumps([dict(r) for r in rows], ensure_ascii=False)

    @Property(str, notify=messagesChanged)
    def messagesJson(self):
        rows = self.db.execute("SELECT * FROM messages WHERE chat=? ORDER BY id", (self._chat,)).fetchall()
        font_sz = int(self.settings.value("fontSize", 14))
        show_think = self.settings.value("showThinking", "true") in (True, "true", "1", 1)
        messages = []
        for r in rows:
            m = dict(r)
            if m["kind"] == "json" and len(m["content"]) > 1000:
                try:
                    summary = embedding_summary(json.loads(m["content"]))
                except ValueError:
                    summary = None
                if summary:
                    m["content"] = summary
                    m["kind"] = "embedding"

            meta = {}
            if m.get("attachment_meta"):
                try:
                    meta = json.loads(m["attachment_meta"])
                except ValueError:
                    meta = {}
            m["meta"] = meta
            m["richHtml"] = render_message_html(
                m.get("content") or "",
                dark=self._dark_mode,
                thinking=m.get("thinking") or "",
                font_size=font_sz,
                show_thinking=show_think,
            )
            messages.append(m)
        return json.dumps(messages, ensure_ascii=False)

    @Property(str, notify=artifactsChanged)
    def artifactsJson(self):
        if not self._chat:
            return "[]"
        rows = self.db.execute("SELECT * FROM messages WHERE chat=? ORDER BY id", (self._chat,)).fetchall()
        messages = [dict(r) for r in rows]
        saved_rows = self.db.execute("SELECT * FROM artifacts WHERE chat=? ORDER BY created DESC", (self._chat,)).fetchall()
        saved_list = [dict(r) for r in saved_rows]
        items = extract_artifacts_from_messages(messages, saved_files=saved_list)
        return json.dumps(items, ensure_ascii=False)

    @Property(str, notify=messagesChanged)
    def chatId(self):
        return self._chat

    @Property(str, notify=modelsChanged)
    def modelsJson(self):
        return json.dumps(self._models, ensure_ascii=False)

    @Property(str, notify=previewChanged)
    def attachmentPreviewJson(self):
        return json.dumps(self._preview_attachment, ensure_ascii=False)

    @Property(str, notify=quotaChanged)
    def quotaSummaryJson(self):
        today_str = date.today().isoformat()
        day_row = self.db.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, COALESCE(SUM(completion_tokens),0) AS c, "
            "COALESCE(SUM(total_tokens),0) AS t, COALESCE(SUM(neurons),0.0) AS n, COUNT(*) AS reqs "
            "FROM usage_logs WHERE day=? AND model != 'models-list'",
            (today_str,),
        ).fetchone()
        chat_row = self.db.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, COALESCE(SUM(completion_tokens),0) AS c, "
            "COALESCE(SUM(total_tokens),0) AS t, COALESCE(SUM(neurons),0.0) AS n "
            "FROM messages WHERE chat=?",
            (self._chat,),
        ).fetchone()

        # Per-model usage today
        model_rows = self.db.execute(
            "SELECT model, provider, COALESCE(SUM(total_tokens),0) AS t, COALESCE(SUM(neurons),0.0) AS n, COUNT(*) AS reqs "
            "FROM usage_logs WHERE day=? AND model != 'models-list' GROUP BY model ORDER BY t DESC",
            (today_str,),
        ).fetchall()
        used_by_model = {r["model"]: {"tokens": int(r["t"]), "requests": int(r["reqs"]), "neurons": float(r["n"])} for r in model_rows}

        active_acc = next((a for a in self._accounts if a["id"] == self._active_account), None)
        provider = active_acc["provider"] if active_acc else ""
        rl = self._rate_limits.get(self._active_account, {})

        cf_neurons = float(day_row["n"] if day_row else 0.0)
        cf_pct = min(100.0, round((cf_neurons / CF_DAILY_FREE_NEURONS) * 100.0, 1))
        cf_rem_neurons = max(0.0, round(CF_DAILY_FREE_NEURONS - cf_neurons, 1))

        # Build accurate per-model Groq & Cloudflare reference table
        limits_table = []
        for m_id, lim in GROQ_FREE_LIMITS.items():
            u = used_by_model.get(m_id, {"tokens": 0, "requests": 0})
            limits_table.append({
                "provider": "Groq",
                "model": m_id,
                "dailyTokensLimit": lim["tpd"],
                "dailyRequestsLimit": lim["rpd"],
                "minuteTokensLimit": lim["tpm"],
                "todayTokensUsed": u["tokens"],
                "todayRequestsUsed": u["requests"],
            })

        if provider == "groq":
            last_model = rl.get("model") or "openai/gpt-oss-120b"
            m_lim = get_groq_model_limits(str(last_model))
            tpd_limit = m_lim["tpd"] or 200000
            rpd_limit = rl.get("limit_requests") or m_lim["rpd"] or 1000
            rem_req = rl.get("remaining_requests")
            if rem_req is None:
                rem_req = max(0, rpd_limit - int(day_row["reqs"] if day_row else 0))
            rem_tpm = rl.get("remaining_tokens")
            lim_tpm = rl.get("limit_tokens") or m_lim["tpm"] or 8000
            today_tok = int(day_row["t"] if day_row else 0)
            rem_daily_tok = max(0, tpd_limit - today_tok)

            quota_badge = f"Günlük: {rem_daily_tok:,}/{tpd_limit:,} tok · {rem_req:,}/{rpd_limit:,} istek"
            minute_badge = (
                f"Dakikalık Hız (TPM): {rem_tpm:,}/{lim_tpm:,} tok/dk"
                if rem_tpm is not None
                else f"Dakikalık Hız Limiti: {lim_tpm:,} tok/dk"
            )
            quota_pct = round(min(100.0, (today_tok / max(1, tpd_limit)) * 100.0), 1)
        elif provider == "cloudflare":
            quota_badge = f"CF Günlük: {cf_rem_neurons:,.0f} / 10.000 Neuron kaldı"
            minute_badge = f"Bugün Harcanan: {cf_neurons:.1f} Neuron (≈ 260K+ tok/gün kapasite)"
            quota_pct = cf_pct
        else:
            quota_badge = "API hesabı seçilmedi"
            minute_badge = ""
            quota_pct = 0.0

        return json.dumps(
            {
                "provider": provider,
                "todayPromptTokens": int(day_row["p"] if day_row else 0),
                "todayCompletionTokens": int(day_row["c"] if day_row else 0),
                "todayTotalTokens": int(day_row["t"] if day_row else 0),
                "todayRequests": int(day_row["reqs"] if day_row else 0),
                "todayNeurons": round(cf_neurons, 1),
                "cfRemainingNeurons": cf_rem_neurons,
                "cfUsedPercent": cf_pct,
                "chatTotalTokens": int(chat_row["t"] if chat_row else 0),
                "quotaBadge": quota_badge,
                "minuteBadge": minute_badge,
                "quotaPercent": quota_pct,
                "rateLimits": rl,
                "limitsTable": limits_table,
            },
            ensure_ascii=False,
        )

    @Slot(result=str)
    def defaultModel(self):
        return AUTO_MODEL_ID

    @Slot(str, result=str)
    def inspectAttachment(self, file_url: str) -> str:
        if not file_url:
            self._preview_attachment = {}
            self.previewChanged.emit()
            return "{}"
        path = QUrl(file_url).toLocalFile() if file_url.startswith("file://") else file_url
        file = Path(path)
        if not file.exists():
            self._preview_attachment = {}
            self.previewChanged.emit()
            return "{}"
        suffix = file.suffix.lower()
        size_kb = round(file.stat().st_size / 1024.0, 1)
        try:
            if suffix in SUPPORTED_IMAGE_EXTENSIONS and suffix not in (".tif", ".tiff"):
                info = {
                    "kind": "image",
                    "name": file.name,
                    "path": str(file),
                    "url": QUrl.fromLocalFile(str(file)).toString(),
                    "format": suffix.lstrip(".").upper(),
                    "badge": f"🖼️ Görsel · {suffix.lstrip('.').upper()} · {size_kb} KB",
                    "previewText": "",
                }
            elif suffix in SUPPORTED_AUDIO_EXTENSIONS:
                info = {
                    "kind": "audio",
                    "name": file.name,
                    "path": str(file),
                    "url": QUrl.fromLocalFile(str(file)).toString(),
                    "format": suffix.lstrip(".").upper(),
                    "badge": f"🎙️ Ses Dosyası · {suffix.lstrip('.').upper()} · {size_kb} KB",
                    "previewText": "",
                }
            else:
                doc = extract_document_full(str(file), limit=80000)
                info = {
                    "kind": "document",
                    "name": doc.name,
                    "path": doc.path,
                    "format": doc.format_label,
                    "pages": doc.page_count,
                    "words": doc.word_count,
                    "chars": doc.char_count,
                    "signed": doc.signed,
                    "signers": doc.signers,
                    "badge": f"📄 {doc.summary_badge}",
                    "previewText": doc.text[:2500] + ("\n…" if len(doc.text) > 2500 else ""),
                }
        except Exception as exc:
            info = {
                "kind": "error",
                "name": file.name,
                "path": str(file),
                "badge": f"⚠️ {exc}",
                "previewText": str(exc),
            }
            self.notice.emit(str(exc))
        self._preview_attachment = info
        self.previewChanged.emit()
        return json.dumps(info, ensure_ascii=False)

    @Slot()
    def clearAttachment(self):
        self._preview_attachment = {}
        self.previewChanged.emit()

    @Slot(str)
    def copyText(self, text: str):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text, QClipboard.Mode.Clipboard)
            self.notice.emit("Panoya kopyalandı.")

    @Slot(str)
    def openFileExternally(self, path_or_url: str):
        if not path_or_url:
            return
        url = QUrl(path_or_url) if path_or_url.startswith("file://") else QUrl.fromLocalFile(path_or_url)
        QDesktopServices.openUrl(url)

    @Slot(int, str, str)
    def exportMessageToDocument(self, message_id: int, target_format: str, output_url: str):
        row = self.db.execute("SELECT content FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row or not row["content"]:
            self.notice.emit("Dışa aktarılacak mesaj içeriği bulunamadı.")
            return
        out_path = QUrl(output_url).toLocalFile() if output_url.startswith("file://") else output_url
        if not out_path:
            return
        try:
            saved = export_document(row["content"], target_format, out_path, title="LifeOS Chat Yanıtı")
            saved_p = Path(saved)
            self.db.execute(
                "INSERT OR REPLACE INTO artifacts(id,chat,category,kind,title,badge,path,media,content) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    f"saved-{uuid.uuid4().hex[:8]}",
                    self._chat,
                    "document",
                    "saved_document",
                    saved_p.name,
                    f"✅ Kaydedildi · {target_format.upper()} · {round(saved_p.stat().st_size / 1024.0, 1)} KB",
                    str(saved_p),
                    QUrl.fromLocalFile(str(saved_p)).toString(),
                    row["content"],
                ),
            )
            self.db.commit()
            self.artifactsChanged.emit()
            self.notice.emit(f"{target_format.upper()} belgesi oluşturuldu: {saved_p.name}")
        except Exception as exc:
            self.notice.emit(f"Belge oluşturma hatası: {exc}")

    @Slot(str, str, str)
    def saveArtifactContent(self, title: str, content: str, output_url: str):
        out_path = QUrl(output_url).toLocalFile() if output_url.startswith("file://") else output_url
        if not out_path:
            return
        try:
            out_p = Path(out_path)
            ext = out_p.suffix.lower().lstrip(".")
            if ext in ("udf", "docx", "pdf", "rtf", "html"):
                saved = export_document(content, ext, str(out_p), title=title)
            else:
                out_p.parent.mkdir(parents=True, exist_ok=True)
                out_p.write_text(content, encoding="utf-8")
                saved = str(out_p)
            self.notice.emit(f"Dosya kaydedildi: {Path(saved).name}")
        except Exception as exc:
            self.notice.emit(f"Kaydetme hatası: {exc}")

    @Slot(str)
    def exportAllCodeToFolder(self, folder_url: str):
        """Writes all extracted code artifacts in the current chat into a project folder."""
        folder = QUrl(folder_url).toLocalFile() if folder_url.startswith("file://") else folder_url
        if not folder or not self._chat:
            return
        items = json.loads(self.artifactsJson)
        code_items = [x for x in items if x.get("category") == "code" and x.get("content")]
        if not code_items:
            self.notice.emit("Bu sohbette kaydedilecek kod bloğu bulunamadı.")
            return
        base = Path(folder)
        base.mkdir(parents=True, exist_ok=True)
        written = 0
        for item in code_items:
            rel_name = item.get("title") or f"file_{written + 1}.txt"
            safe_rel = Path(rel_name).name if ".." in rel_name else rel_name.lstrip("/")
            target = base / safe_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item["content"], encoding="utf-8")
            written += 1
        self.notice.emit(f"📦 {written} proje dosyası '{base.name}' klasörüne çıkarıldı.")

    @Slot(str, str, str)
    def convertDocument(self, source_path_or_url: str, target_format: str, output_url: str = ""):
        src = QUrl(source_path_or_url).toLocalFile() if source_path_or_url.startswith("file://") else source_path_or_url
        out = QUrl(output_url).toLocalFile() if output_url.startswith("file://") else output_url
        if not src:
            self.notice.emit("Dönüştürülecek dosya bulunamadı.")
            return
        try:
            res = convert_document_file(src, target_format, out or None)
            self.notice.emit(f"Dönüştürüldü ({res['source_format']} → {res['target_format']}): {Path(res['output_path']).name}")
        except Exception as exc:
            self.notice.emit(f"Dönüştürme hatası: {exc}")

    @Slot(int, result=str)
    def getMessageAttachmentText(self, message_id: int) -> str:
        row = self.db.execute("SELECT attachment_text FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            return ""
        return row["attachment_text"] or ""

    def _token(self, account: str) -> str:
        if account in self._tokens:
            return self._tokens[account]
        proc = subprocess.run(
            ["secret-tool", "lookup", "application", "lifeos-chat", "account", account],
            capture_output=True, text=True, timeout=8,
        )
        token = proc.stdout.strip()
        if not token:
            raise ValueError("API anahtarı bulunamadı. Hesabı yeniden ekleyin; KDE KWallet açık olmalı.")
        self._tokens[account] = token
        return token

    def _account(self, account_id: str = "") -> dict:
        account = next((a for a in self._accounts if a["id"] == (account_id or self._active_account)), None)
        if account is None:
            if self._accounts:
                return self._accounts[0]
            raise ValueError("Önce bir API hesabı ekleyin.")
        return account

    def _record_usage_and_headers(
        self,
        account: dict,
        model: str,
        usage: dict,
        resp_headers: dict[str, str],
    ) -> None:
        p_tok = int(usage.get("prompt_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or 0)
        t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
        neurons = float(usage.get("neurons") or 0.0)
        if model != "models-list":
            self.db.execute(
                "INSERT INTO usage_logs(day,account,provider,model,prompt_tokens,completion_tokens,total_tokens,neurons) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (date.today().isoformat(), account["id"], account["provider"], model, p_tok, c_tok, t_tok, neurons),
            )
            self.db.commit()

        rl: dict[str, int | str] = self._rate_limits.get(account["id"], {}).copy()
        if model != "models-list":
            rl["model"] = model
        for key, h_name in (
            ("limit_requests", "x-ratelimit-limit-requests"),
            ("remaining_requests", "x-ratelimit-remaining-requests"),
            ("limit_tokens", "x-ratelimit-limit-tokens"),
            ("remaining_tokens", "x-ratelimit-remaining-tokens"),
        ):
            if h_name in resp_headers:
                try:
                    rl[key] = int(resp_headers[h_name])
                except ValueError:
                    pass
        for key, h_name in (
            ("reset_requests", "x-ratelimit-reset-requests"),
            ("reset_tokens", "x-ratelimit-reset-tokens"),
        ):
            if h_name in resp_headers:
                rl[key] = resp_headers[h_name]
        if rl:
            self._rate_limits[account["id"]] = rl
        self.quotaChanged.emit()

    @Slot(str, str, str, str, result=bool)
    def addAccount(self, provider, title, account_id, token):
        provider = provider.strip().lower()
        if provider not in {"cloudflare", "groq"}:
            self.notice.emit("Bilinmeyen sağlayıcı.")
            return False
        if not token.strip() or (provider == "cloudflare" and not account_id.strip()):
            self.notice.emit("API anahtarı ve gerekli hesap bilgilerini girin.")
            return False
        uid = str(uuid.uuid4())
        try:
            subprocess.run(
                ["secret-tool", "store", "--label=LifeOS Chat API", "application", "lifeos-chat", "account", uid],
                input=token.strip(), text=True, check=True, timeout=15,
            )
        except Exception as exc:
            self.notice.emit(f"Anahtar KWallet'a kaydedilemedi: {exc}")
            return False
        account = {
            "id": uid,
            "provider": provider,
            "title": title.strip() or provider.title(),
            "accountId": account_id.strip(),
        }
        self._accounts.append(account)
        self._tokens[uid] = token.strip()
        self.settings.setValue("accounts", json.dumps(self._accounts, ensure_ascii=False))
        self.setActiveAccount(uid)
        self.accountsChanged.emit()
        self.quotaChanged.emit()
        self.notice.emit("Hesap eklendi. Modeller yükleniyor.")
        return True

    @Slot(str)
    def setActiveAccount(self, account_id):
        if not any(a["id"] == account_id for a in self._accounts):
            return
        self._active_account = account_id
        self.settings.setValue("activeAccount", account_id)
        self.accountsChanged.emit()
        self.quotaChanged.emit()
        self.loadModels()

    @Slot(str)
    def removeAccount(self, account_id):
        self._accounts = [a for a in self._accounts if a["id"] != account_id]
        subprocess.run(
            ["secret-tool", "clear", "application", "lifeos-chat", "account", account_id],
            capture_output=True, timeout=8,
        )
        self._tokens.pop(account_id, None)
        self._account_models.pop(account_id, None)
        self.settings.setValue("accounts", json.dumps(self._accounts, ensure_ascii=False))
        if self._active_account == account_id:
            self._active_account = self._accounts[0]["id"] if self._accounts else ""
            self.settings.setValue("activeAccount", self._active_account)
        self.accountsChanged.emit()
        self.quotaChanged.emit()
        self.loadModels()

    def _fetch_models_for_account(self, account: dict) -> list[dict]:
        acc_id = account["id"]
        if acc_id in self._account_models and self._account_models[acc_id]:
            return self._account_models[acc_id]
        token = self._token(acc_id)
        if account["provider"] == "groq":
            data, _, headers = request_with_headers(GROQ + "/models", token)
            self._record_usage_and_headers(account, "models-list", {}, headers)
            items = json.loads(data).get("data", [])
            models = [normalize_groq_model(x) for x in items if x.get("active", True)]
        else:
            models = []
            for page in range(1, 21):
                query = urllib.parse.urlencode({"page": page, "per_page": 100})
                data, _ = request(f"{API}/accounts/{account['accountId']}/ai/models/search?{query}", token)
                body = json.loads(data)
                if body.get("success") is False:
                    raise ValueError(str(body.get("errors")))
                items = body.get("result") or []
                models.extend(normalize_model(x) for x in items)
                info = body.get("result_info") or {}
                if not items or (info.get("total_pages") and page >= info["total_pages"]) or len(items) < 100:
                    break
        for m in models:
            m["provider"] = account["provider"]
            m["accountId"] = acc_id
            m["accountTitle"] = account["title"]
        sorted_models = sorted(models, key=model_sort_key)
        self._account_models[acc_id] = sorted_models
        return sorted_models

    def get_all_accounts_models_sync(self, provider_filter: str = "all") -> list[dict]:
        """Loads and aggregates models from ALL registered accounts (Groq + Cloudflare) for MCP & cross-routing."""
        all_models: list[dict] = []
        for acc in self._accounts:
            if provider_filter not in ("all", "auto", "") and acc["provider"] != provider_filter:
                continue
            try:
                all_models.extend(self._fetch_models_for_account(acc))
            except Exception:
                continue
        return all_models

    def _find_best_account_and_model(
        self,
        preferred_task: str,
        prompt: str = "",
        attachment: dict | None = None,
        preferred_account: dict | None = None,
        provider_filter: str = "auto",
    ) -> tuple[dict, str]:
        """Finds the best account + model across all accounts when a task requires a capability (e.g., Image Generation on CF or Whisper on Groq)."""
        if preferred_account and provider_filter in ("auto", "", preferred_account["provider"]):
            models = self._fetch_models_for_account(preferred_account)
            m_id, _ = select_best_model(models, prompt, attachment, preferred_task=preferred_task)
            if m_id:
                chosen = next((m for m in models if m["id"] == m_id), None)
                if chosen and (not preferred_task or chosen["task"] == preferred_task or (preferred_task == "vision" and chosen.get("hasVision"))):
                    return preferred_account, m_id

        for acc in self._accounts:
            if provider_filter not in ("auto", "all", "") and acc["provider"] != provider_filter:
                continue
            try:
                models = self._fetch_models_for_account(acc)
                m_id, _ = select_best_model(models, prompt, attachment, preferred_task=preferred_task)
                if m_id:
                    chosen = next((m for m in models if m["id"] == m_id), None)
                    if chosen and (not preferred_task or chosen["task"] == preferred_task or (preferred_task == "vision" and chosen.get("hasVision"))):
                        return acc, m_id
            except Exception:
                continue

        fallback_acc = preferred_account or self._account()
        models = self._fetch_models_for_account(fallback_acc)
        m_id, _ = select_best_model(models, prompt, attachment, preferred_task=preferred_task)
        return fallback_acc, m_id

    def ensure_models_loaded_sync(self) -> list[dict]:
        if self._models:
            return self._models
        if not self._active_account and self._accounts:
            self._active_account = self._accounts[0]["id"]
        if not self._active_account:
            return []
        account = self._account()
        self._models = self._fetch_models_for_account(account)
        return self._models

    @Slot()
    def loadModels(self):
        if not self._active_account:
            self._models = []
            self.modelsChanged.emit()
            return
        account = self._account()

        def work():
            self._account_models.pop(account["id"], None)
            self._models = self._fetch_models_for_account(account)
            self.modelsChanged.emit()
            self.notice.emit(f"{len(self._models)} canlı model yetenekleriyle yüklendi.")

        self._run(work, stage="Modeller yükleniyor…")

    @Slot(str, str)
    def newChat(self, account_id, model):
        account = self._account(account_id)
        chosen_model = model or AUTO_MODEL_ID
        self._chat = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO chats(id,title,account,model) VALUES(?,?,?,?)",
            (self._chat, "Yeni sohbet", account["id"], chosen_model),
        )
        self.db.commit()
        if account["id"] != self._active_account:
            self.setActiveAccount(account["id"])
        self.chatsChanged.emit()
        self.messagesChanged.emit()
        self.artifactsChanged.emit()
        self.quotaChanged.emit()

    @Slot(str, str)
    def renameChat(self, chat_id: str, new_title: str):
        cleaned = new_title.strip()
        if not chat_id or not cleaned:
            return
        self.db.execute("UPDATE chats SET title=?, updated=CURRENT_TIMESTAMP WHERE id=?", (cleaned[:80], chat_id))
        self.db.commit()
        self.chatsChanged.emit()
        self.notice.emit(f"Sohbet adı '{cleaned[:40]}' olarak değiştirildi.")

    @Slot(str)
    def openChat(self, chat_id):
        row = self.db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row is None:
            return
        self._chat = chat_id
        if row["account"] != self._active_account:
            self.setActiveAccount(row["account"])
        self.messagesChanged.emit()
        self.artifactsChanged.emit()
        self.quotaChanged.emit()

    @Slot(str)
    def deleteChat(self, chat_id):
        self.db.execute("DELETE FROM messages WHERE chat=?", (chat_id,))
        self.db.execute("DELETE FROM artifacts WHERE chat=?", (chat_id,))
        self.db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        self.db.commit()
        if self._chat == chat_id:
            self._chat = ""
            self.messagesChanged.emit()
            self.artifactsChanged.emit()
        self.chatsChanged.emit()
        self.quotaChanged.emit()

    @Slot(str, str)
    def chooseModel(self, account_id, model):
        if not self._chat:
            self.newChat(account_id, model or AUTO_MODEL_ID)
            return
        self.db.execute(
            "UPDATE chats SET account=?, model=?, updated=CURRENT_TIMESTAMP WHERE id=?",
            (account_id, model or AUTO_MODEL_ID, self._chat),
        )
        self.db.commit()
        if account_id != self._active_account:
            self.setActiveAccount(account_id)
        self.chatsChanged.emit()

    def _prepare_attachment(self, path: str) -> dict | None:
        if not path:
            return None
        file = Path(path)
        if not file.exists():
            raise ValueError(f"Dosya bulunamadı: {path}")
        suffix = file.suffix.lower()
        size_kb = round(file.stat().st_size / 1024.0, 1)
        if suffix in SUPPORTED_IMAGE_EXTENSIONS and suffix not in (".tif", ".tiff"):
            mime, data = file_data(path)
            return {
                "kind": "image",
                "name": file.name,
                "path": str(file),
                "data_url": f"data:{mime};base64,{data}",
                "meta": {
                    "name": file.name,
                    "path": str(file),
                    "format": suffix.lstrip(".").upper(),
                    "badge": f"🖼️ Görsel · {suffix.lstrip('.').upper()} · {size_kb} KB",
                },
            }
        if suffix in SUPPORTED_AUDIO_EXTENSIONS:
            return {
                "kind": "audio",
                "name": file.name,
                "path": str(file),
                "meta": {
                    "name": file.name,
                    "path": str(file),
                    "format": suffix.lstrip(".").upper(),
                    "badge": f"🎙️ Ses Kaydı · {suffix.lstrip('.').upper()} · {size_kb} KB",
                },
            }
        extracted = extract_document_full(path, limit=250000)
        return {
            "kind": "document",
            "name": extracted.name,
            "path": extracted.path,
            "text": extracted.text,
            "badge": extracted.summary_badge,
            "rendered_pages": extracted.rendered_pages,
            "meta": extracted.to_meta_dict(),
        }

    def _find_high_context_128k_model(self, required_tokens: int, prompt: str = "", attachment: dict | None = None) -> tuple[dict | None, str, str]:
        """Finds a model across all accounts that can process the ENTIRE document (128K context) without any truncation."""
        # 1. Check if any Groq account has a model with TPM >= required_tokens + 2000
        for acc in self._accounts:
            if acc["provider"] == "groq":
                try:
                    ms = self._fetch_models_for_account(acc)
                    for m in ms:
                        if m["task"] in CHAT_TASKS:
                            tpm = (m.get("limits") or get_groq_model_limits(m["id"])).get("tpm") or 8000
                            if tpm >= required_tokens + 2000:
                                return acc, m["id"], f"Yüksek bağlam ({tpm:,} TPM) için {m['title']} seçildi"
                except Exception:
                    continue

        # 2. Switch to Cloudflare 128K context models (which have NO 8,000 TPM per-minute burst cap and support full 128K tokens)
        for acc in self._accounts:
            if acc["provider"] == "cloudflare":
                try:
                    ms = self._fetch_models_for_account(acc)
                    for pref in (
                        "@cf/openai/gpt-oss-120b",
                        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                        "@cf/qwen/qwen3-30b-a3b-fp8",
                        "@cf/qwen/qwq-32b",
                        "@cf/qwen/qwen3.8-27b",
                        "@cf/meta/llama-4-scout-17b-16e-instruct",
                    ):
                        hit = next((m for m in ms if m["id"].lower() == pref), None)
                        if hit:
                            return (
                                acc,
                                hit["id"],
                                f"Tam metin ({required_tokens:,} token) için 128K bağlamlı {hit['title']} ({acc['title']}) modeline otomatik geçildi",
                            )
                    m_id, _ = select_best_model(ms, prompt, attachment)
                    if m_id:
                        return acc, m_id, f"Tam metin ({required_tokens:,} token) için 128K bağlamlı {m_id.split('/')[-1]} modeline otomatik geçildi"
                except Exception:
                    continue
        return None, "", ""

    def _execute_model_call(
        self,
        account: dict,
        model: str,
        task: str,
        history: list[dict],
        prompt: str,
        attachment: dict | None,
        custom_json: str = "",
        voice: str = "",
        system_prompt: str = "",
    ) -> tuple[dict, float]:
        t0 = time.monotonic()
        token = self._token(account["id"])
        max_tok = int(self.settings.value("maxTokens", 4096))
        temp = float(self.settings.value("temperature", 0.6))
        sys_prompt = system_prompt.strip() or str(self.settings.value("systemPrompt", "")).strip()
        switched_reason = ""

        if attachment and attachment["kind"] == "audio" and task != "automatic-speech-recognition" and not custom_json.strip():
            asr_acc, asr_model = self._find_best_account_and_model("automatic-speech-recognition", prompt, attachment, preferred_account=account)
            if not asr_model:
                raise ValueError("Bu hesapta Ses → Metin (Whisper) modeli bulunamadı.")
            asr_tok = self._token(asr_acc["id"])
            if asr_acc["provider"] == "groq":
                body, content_type = multipart_audio(attachment["path"], asr_model)
                asr_data, asr_mime, asr_headers = request_with_headers(
                    GROQ + "/audio/transcriptions", asr_tok, raw=body, content_type=content_type
                )
            else:
                asr_payload = {"audio": file_data(attachment["path"])[1]}
                asr_data, asr_mime, asr_headers = request_with_headers(
                    f"{API}/accounts/{asr_acc['accountId']}/ai/run/{asr_model}", asr_tok, asr_payload
                )
            asr_res = parse_response(asr_data, asr_mime, prompt_text=attachment["name"], task="automatic-speech-recognition", model_id=asr_model)
            self._record_usage_and_headers(asr_acc, asr_model, asr_res.get("usage", {}), asr_headers)
            transcript = (asr_res.get("text") or "").strip()
            if not prompt.strip():
                elapsed = (time.monotonic() - t0) * 1000.0
                asr_res["text"] = f"### 🎙️ Ses Deşifresi\n\n{transcript}"
                return asr_res, elapsed
            prompt = f"{prompt.strip()}\n\n[Ses Kaydı Deşifresi ({attachment['name']})]:\n{transcript}"
            attachment = None

        # Pre-check: if the full uncut document + conversation exceeds Groq's per-minute TPM cap (8,000 tokens),
        # automatically route to a 128K full-context model BEFORE sending so zero text is ever cut!
        if account["provider"] == "groq" and task in CHAT_TASKS and not custom_json.strip():
            doc_str = attachment.get("text", "") if (attachment and attachment.get("kind") == "document") else ""
            hist_str = " ".join((m.get("content") or "") + " " + (m.get("attachment_text") or "") for m in history[-10:])
            est_input_tok = estimate_tokens(prompt + " " + doc_str + " " + hist_str)
            limits = get_groq_model_limits(model)
            tpm_cap = limits.get("tpm") or 8000
            if est_input_tok + 1200 > tpm_cap:
                hi_acc, hi_model, hi_reason = self._find_high_context_128k_model(est_input_tok, prompt, attachment)
                if hi_acc and hi_model:
                    account = hi_acc
                    model = hi_model
                    token = self._token(account["id"])
                    switched_reason = hi_reason

        if custom_json.strip():
            payload = json.loads(custom_json)
        elif account["provider"] == "groq":
            payload = conversation_payload(
                history,
                prompt,
                attachment,
                max_tokens=max_tok,
                temperature=temp,
                system_prompt=sys_prompt,
                max_doc_chars=250000,
                max_history_chars=120000,
            )
            payload["model"] = model
        elif task in CHAT_TASKS:
            payload = conversation_payload(
                history,
                prompt,
                attachment,
                max_tokens=max_tok,
                temperature=temp,
                system_prompt=sys_prompt,
                max_doc_chars=250000,
                max_history_chars=120000,
            )
            if task == "image-to-text" and attachment and attachment["kind"] == "image":
                payload = {
                    "image": attachment["data_url"].split(",", 1)[1],
                    "prompt": prompt or "Görseldeki metni oku ve açıkla.",
                    "max_tokens": max_tok,
                }
        elif task == "text-to-image":
            payload = {"prompt": prompt}
        elif task == "text-to-speech":
            payload = {"text": prompt}
        elif task == "automatic-speech-recognition" and attachment and attachment["kind"] == "audio":
            payload = {"audio": file_data(attachment["path"])[1]}
        elif task == "text-embeddings":
            payload = {"text": [prompt]}
        else:
            payload = {"text": prompt}

        if account["provider"] == "groq" and task == "automatic-speech-recognition":
            if not attachment or attachment["kind"] != "audio":
                raise ValueError("Ses yazıya çevirme için bir ses dosyası ekleyin.")
            body, content_type = multipart_audio(attachment["path"], model)
            data, mime, resp_headers = request_with_headers(
                GROQ + "/audio/transcriptions", token, raw=body, content_type=content_type
            )
        elif account["provider"] == "groq" and task == "text-to-speech":
            speech = (
                payload
                if custom_json.strip()
                else {"model": model, "input": prompt, "voice": voice or "troy", "response_format": "wav"}
            )
            data, mime, resp_headers = request_with_headers(GROQ + "/audio/speech", token, speech)
        else:
            url = (
                GROQ + "/chat/completions"
                if account["provider"] == "groq"
                else f"{API}/accounts/{account['accountId']}/ai/run/{model}"
            )
            try:
                data, mime, resp_headers = request_with_headers(url, token, payload)
            except ValueError as err:
                err_str = str(err)
                if account["provider"] == "groq" and ("HTTP 413" in err_str or "HTTP 429" in err_str or "too large" in err_str.lower()):
                    # Immediately switch to a 128K full-context Cloudflare model with the FULL uncut document
                    hi_acc, hi_model, hi_reason = self._find_high_context_128k_model(12000, prompt, attachment)
                    if hi_acc and hi_model and hi_acc["provider"] == "cloudflare":
                        cf_tok = self._token(hi_acc["id"])
                        cf_payload = conversation_payload(
                            history,
                            prompt,
                            attachment,
                            max_tokens=max_tok,
                            temperature=temp,
                            system_prompt=sys_prompt,
                            max_doc_chars=250000,
                            max_history_chars=120000,
                        )
                        cf_url = f"{API}/accounts/{hi_acc['accountId']}/ai/run/{hi_model}"
                        data, mime, resp_headers = request_with_headers(cf_url, cf_tok, cf_payload)
                        account = hi_acc
                        model = hi_model
                        switched_reason = hi_reason
                    else:
                        raise
                else:
                    raise

        prompt_est_str = prompt + (attachment.get("text", "") if attachment else "")
        if mime == "application/octet-stream" and task in {"text-to-image", "text-to-speech"}:
            result = parse_response(data, "image/png" if task == "text-to-image" else "audio/wav", prompt_est_str, task, model_id=model)
        else:
            result = parse_response(data, mime, prompt_est_str, task, model_id=model)

        result["model_used"] = model.split("/")[-1]
        if switched_reason:
            result["switched_reason"] = switched_reason

        self._record_usage_and_headers(account, model, result.get("usage", {}), resp_headers)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        return result, elapsed_ms

    @Slot(str, str, str, str)
    def send(self, prompt, file_url, custom_json, voice):
        if self._busy:
            return
        if not self._chat:
            if not self._active_account:
                self.notice.emit("Önce bir API hesabı ekleyin.")
                return
            self.newChat(self._active_account, AUTO_MODEL_ID)
        chat = self.db.execute("SELECT * FROM chats WHERE id=?", (self._chat,)).fetchone()
        account = self._account(chat["account"])
        selected_model = chat["model"] or AUTO_MODEL_ID
        path = QUrl(file_url).toLocalFile() if file_url and file_url.startswith("file://") else (file_url or "")

        try:
            attachment = self._prepare_attachment(path) if path else None
        except Exception as exc:
            self.notice.emit(str(exc))
            return

        if not prompt.strip() and not attachment and not custom_json.strip():
            return

        rows = self.db.execute(
            "SELECT role, content, attachment, attachment_text, kind, media FROM messages WHERE chat=? ORDER BY id",
            (self._chat,),
        ).fetchall()
        history = [dict(r) for r in rows]
        current_chat = self._chat

        self._preview_attachment = {}
        self.previewChanged.emit()

        def work():
            if not self._models:
                self._models = self._fetch_models_for_account(account)
                self.modelsChanged.emit()

            auto_reason = ""
            actual_model = selected_model
            exec_account = account

            if selected_model in (AUTO_MODEL_ID, "auto", "") or not any(m["id"] == selected_model for m in self._models):
                actual_model, auto_reason = select_best_model(self._models, prompt, attachment)
                # Cross-account smart fallback: e.g., user asked for image generation on Groq -> use Cloudflare account if available
                p_low = (prompt or "").lower()
                wants_img = not attachment and any(t in p_low for t in ("resim çiz", "görsel üret", "görsel oluştur", "resim oluştur", "fotoğraf üret"))
                if wants_img and not any(m.get("hasImageOut") for m in self._models):
                    cf_acc, cf_img_model = self._find_best_account_and_model("text-to-image", prompt)
                    if cf_img_model:
                        exec_account = cf_acc
                        actual_model = cf_img_model
                        auto_reason = f"Görsel üretimi için {cf_acc['title']} ({cf_img_model.split('/')[-1]}) seçildi"
            else:
                chosen_info = next((m for m in self._models if m["id"] == selected_model), None)
                if attachment and attachment["kind"] == "image" and chosen_info and not chosen_info.get("hasVision"):
                    actual_model, auto_reason = select_best_model(self._models, prompt, attachment, preferred_task="vision")
                    auto_reason = f"Görsel eklendiği için otomatik geçiş: {auto_reason}"

            all_known = self._models + self._account_models.get(exec_account["id"], [])
            task = next((m["task"] for m in all_known if m["id"] == actual_model), "text-generation")
            model_title = next((m["title"] for m in all_known if m["id"] == actual_model), actual_model.split("/")[-1])
            self._busy_stage = f"{model_title} yanıt üretiyor…"
            self.busyChanged.emit()

            result, elapsed_ms = self._execute_model_call(
                account=exec_account,
                model=actual_model,
                task=task,
                history=history,
                prompt=prompt,
                attachment=attachment,
                custom_json=custom_json,
                voice=voice,
            )

            if result["kind"] in {"image", "audio"} and result.get("data"):
                media_dir = APP_DIR / "media"
                media_dir.mkdir(exist_ok=True)
                suffix = ".png" if result["kind"] == "image" else ".wav" if result.get("mime") == "audio/wav" else ".mp3"
                media_path = media_dir / (str(uuid.uuid4()) + suffix)
                media_path.write_bytes(base64.b64decode(result["data"]))
                result["url"] = QUrl.fromLocalFile(str(media_path)).toString()

            user_visible_text = prompt.strip()
            if not user_visible_text:
                if attachment:
                    if attachment["kind"] == "document":
                        user_visible_text = "Belgeyi incele ve özetle."
                    elif attachment["kind"] == "image":
                        user_visible_text = "Görseli incele ve açıkla."
                    elif attachment["kind"] == "audio":
                        user_visible_text = "Ses kaydını yazıya çevir."
                else:
                    user_visible_text = "JSON testi"

            attached_media = ""
            if attachment and attachment["kind"] in {"image", "audio"}:
                media_dir = APP_DIR / "attachments"
                media_dir.mkdir(exist_ok=True)
                copied = media_dir / (str(uuid.uuid4()) + Path(path).suffix.lower())
                shutil.copy2(path, copied)
                attached_media = QUrl.fromLocalFile(str(copied)).toString() if attachment["kind"] == "image" else str(copied)

            att_name = attachment["name"] if attachment else ""
            att_text = attachment.get("text", "") if attachment and attachment["kind"] == "document" else ""
            att_meta = json.dumps(attachment.get("meta") or {}, ensure_ascii=False) if attachment else ""
            user_kind = "image" if (attachment and attachment["kind"] == "image") else "text"

            usage = result.get("usage") or {}
            self.db.execute(
                "INSERT INTO messages(chat,role,content,attachment,attachment_text,attachment_meta,kind,media) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (current_chat, "user", user_visible_text, att_name, att_text, att_meta, user_kind, attached_media),
            )
            self.db.execute(
                "INSERT INTO messages(chat,role,content,thinking,kind,media,model_used,auto_reason,"
                "prompt_tokens,completion_tokens,total_tokens,neurons,elapsed_ms,truncated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    current_chat,
                    "assistant",
                    result.get("text", ""),
                    result.get("thinking", ""),
                    result["kind"],
                    result.get("url", ""),
                    result.get("model_used") or model_title,
                    result.get("switched_reason") or auto_reason,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("neurons") or 0.0),
                    int(round(elapsed_ms)),
                    1 if result.get("truncated") else 0,
                ),
            )
            title_candidate = (prompt.strip() or att_name or "Yeni sohbet")[:55]
            self.db.execute(
                "UPDATE chats SET title=CASE WHEN title='Yeni sohbet' THEN ? ELSE title END, updated=CURRENT_TIMESTAMP WHERE id=?",
                (title_candidate, current_chat),
            )
            self.db.commit()
            self.resultReady.emit(json.dumps(result, ensure_ascii=False))
            self.chatsChanged.emit()
            self.messagesChanged.emit()
            self.artifactsChanged.emit()
            self.quotaChanged.emit()

        self._run(work, stage="İstek hazırlanıyor…")

    @Slot(int)
    def continueMessage(self, message_id: int):
        if self._busy or not self._chat:
            return
        row = self.db.execute("SELECT * FROM messages WHERE id=? AND chat=?", (message_id, self._chat)).fetchone()
        if not row or row["role"] != "assistant":
            return
        chat = self.db.execute("SELECT * FROM chats WHERE id=?", (self._chat,)).fetchone()
        account = self._account(chat["account"])
        rows = self.db.execute(
            "SELECT role, content, attachment, attachment_text, kind, media FROM messages WHERE chat=? AND id<=? ORDER BY id",
            (self._chat, message_id),
        ).fetchall()
        history = [dict(r) for r in rows]
        selected_model = chat["model"] or AUTO_MODEL_ID
        existing_content = row["content"] or ""

        def work():
            if not self._models:
                self._models = self._fetch_models_for_account(account)
            actual_model = selected_model
            if selected_model in (AUTO_MODEL_ID, "auto", "") or not any(m["id"] == selected_model for m in self._models):
                actual_model, _ = select_best_model(self._models, existing_content[-400:])
            task = next((m["task"] for m in self._models if m["id"] == actual_model), "text-generation")
            continue_prompt = (
                "Önceki yanıtın yarıda kesildi. Lütfen son kaldığın cümleden itibaren tekrar etmeden "
                "doğrudan devam et ve yanıtı eksiksiz tamamla."
            )
            result, elapsed_ms = self._execute_model_call(
                account=account,
                model=actual_model,
                task=task,
                history=history,
                prompt=continue_prompt,
                attachment=None,
            )
            addition = (result.get("text") or "").strip()
            merged = (existing_content.rstrip() + "\n" + addition) if addition else existing_content
            usage = result.get("usage") or {}
            self.db.execute(
                "UPDATE messages SET content=?, prompt_tokens=prompt_tokens+?, completion_tokens=completion_tokens+?, "
                "total_tokens=total_tokens+?, neurons=neurons+?, elapsed_ms=elapsed_ms+?, truncated=? WHERE id=?",
                (
                    merged,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("neurons") or 0.0),
                    int(round(elapsed_ms)),
                    1 if result.get("truncated") else 0,
                    message_id,
                ),
            )
            self.db.commit()
            self.messagesChanged.emit()
            self.artifactsChanged.emit()
            self.quotaChanged.emit()
            self.notice.emit("Yanıt kaldığı yerden tamamlandı.")

        self._run(work, stage="Yanıt devam ettiriliyor…")

    @Slot(int)
    def regenerateMessage(self, message_id: int):
        if self._busy or not self._chat:
            return
        row = self.db.execute("SELECT * FROM messages WHERE id=? AND chat=?", (message_id, self._chat)).fetchone()
        if not row or row["role"] != "assistant":
            return
        prev_rows = self.db.execute(
            "SELECT * FROM messages WHERE chat=? AND id<? ORDER BY id",
            (self._chat, message_id),
        ).fetchall()
        if not prev_rows or prev_rows[-1]["role"] != "user":
            return
        user_msg = dict(prev_rows[-1])
        history = [dict(r) for r in prev_rows[:-1]]
        chat = self.db.execute("SELECT * FROM chats WHERE id=?", (self._chat,)).fetchone()
        account = self._account(chat["account"])
        selected_model = chat["model"] or AUTO_MODEL_ID

        def work():
            if not self._models:
                self._models = self._fetch_models_for_account(account)
            attachment = None
            if user_msg.get("attachment_text"):
                attachment = {
                    "kind": "document",
                    "name": user_msg.get("attachment") or "Belge",
                    "text": user_msg["attachment_text"],
                }
            auto_reason = ""
            actual_model = selected_model
            if selected_model in (AUTO_MODEL_ID, "auto", "") or not any(m["id"] == selected_model for m in self._models):
                actual_model, auto_reason = select_best_model(self._models, user_msg["content"], attachment)
            task = next((m["task"] for m in self._models if m["id"] == actual_model), "text-generation")
            model_title = next((m["title"] for m in self._models if m["id"] == actual_model), actual_model)

            result, elapsed_ms = self._execute_model_call(
                account=account,
                model=actual_model,
                task=task,
                history=history,
                prompt=user_msg["content"],
                attachment=attachment,
            )
            usage = result.get("usage") or {}
            self.db.execute(
                "UPDATE messages SET content=?, thinking=?, model_used=?, auto_reason=?, "
                "prompt_tokens=?, completion_tokens=?, total_tokens=?, neurons=?, elapsed_ms=?, truncated=? WHERE id=?",
                (
                    result.get("text", ""),
                    result.get("thinking", ""),
                    model_title,
                    auto_reason,
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    float(usage.get("neurons") or 0.0),
                    int(round(elapsed_ms)),
                    1 if result.get("truncated") else 0,
                    message_id,
                ),
            )
            self.db.commit()
            self.messagesChanged.emit()
            self.artifactsChanged.emit()
            self.quotaChanged.emit()
            self.notice.emit(f"Yanıt {model_title} ile yeniden üretildi.")

        self._run(work, stage="Yanıt yeniden üretiliyor…")

    # ======================== SYNCHRONOUS MCP & CLI METHODS ========================
    def run_sync_completion(
        self,
        prompt: str,
        model_id: str = AUTO_MODEL_ID,
        file_path: str = "",
        task_hint: str = "auto",
        system_prompt: str = "",
        provider: str = "auto",
    ) -> dict:
        """Runs completion across any requested provider/account or auto-selects across all accounts."""
        attachment = self._prepare_attachment(file_path) if file_path else None
        all_models = self.get_all_accounts_models_sync(provider_filter=provider)
        if not all_models:
            account = self._account()
            all_models = self._fetch_models_for_account(account)

        auto_reason = ""
        actual_model = model_id
        chosen_entry = next((m for m in all_models if m["id"] == model_id or m["title"] == model_id), None)
        if chosen_entry:
            actual_model = chosen_entry["id"]
            account = self._account(chosen_entry.get("accountId", ""))
        else:
            pref = "" if task_hint == "auto" else task_hint
            actual_model, auto_reason = select_best_model(all_models, prompt, attachment, preferred_task=pref)
            chosen_entry = next((m for m in all_models if m["id"] == actual_model), None)
            account = self._account(chosen_entry.get("accountId", "") if chosen_entry else "")

        task = chosen_entry["task"] if chosen_entry else "text-generation"
        result, elapsed_ms = self._execute_model_call(
            account=account,
            model=actual_model,
            task=task,
            history=[],
            prompt=prompt,
            attachment=attachment,
            system_prompt=system_prompt,
        )
        return {
            "provider": account["provider"],
            "account": account["title"],
            "model": actual_model,
            "auto_selection_reason": auto_reason,
            "response": result.get("text", ""),
            "thinking": result.get("thinking", ""),
            "truncated": bool(result.get("truncated")),
            "elapsed_ms": int(round(elapsed_ms)),
            "usage": result.get("usage", {}),
            "quota": json.loads(self.quotaSummaryJson),
        }

    def run_sync_image_generation(self, prompt: str, output_path: str = "", model_id: str = "") -> dict:
        acc, img_model = self._find_best_account_and_model("text-to-image", prompt, provider_filter="cloudflare")
        chosen_model = model_id or img_model or "@cf/black-forest-labs/flux-1-schnell"
        result, elapsed_ms = self._execute_model_call(
            account=acc,
            model=chosen_model,
            task="text-to-image",
            history=[],
            prompt=prompt,
            attachment=None,
        )
        if not result.get("data"):
            raise ValueError("Görsel verisi üretilemedi.")
        out_p = Path(output_path) if output_path else (APP_DIR / "media" / f"img_{uuid.uuid4().hex[:8]}.png")
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(base64.b64decode(result["data"]))
        return {
            "success": True,
            "provider": acc["provider"],
            "model": chosen_model,
            "output_path": str(out_p),
            "elapsed_ms": int(round(elapsed_ms)),
            "usage": result.get("usage", {}),
        }

    def run_sync_tts(self, text: str, output_path: str = "") -> dict:
        audio_path = synthesize_natural_tts(text, lang="tr")
        if output_path:
            out_p = Path(output_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(audio_path, out_p)
            audio_path = str(out_p)
        return {
            "success": True,
            "output_path": audio_path,
            "size_bytes": Path(audio_path).stat().st_size,
        }

    def run_sync_project_generation(self, prompt: str, target_dir: str, provider: str = "auto") -> dict:
        sys_p = (
            "Sen uzman bir yazılım mimarısın. Kullanıcının istediği projeyi eksiksiz ve çalışır şekilde üret. "
            "Her dosyayı mutlaka ```dil dosya_yolu (örnek: ```python main.py veya ```html index.html) başlığıyla "
            "ayrı bir kod bloğu içinde yaz."
        )
        res = self.run_sync_completion(
            prompt=prompt,
            model_id=AUTO_MODEL_ID,
            task_hint="coding",
            system_prompt=sys_p,
            provider=provider,
        )
        fake_msg = [{"id": 1, "role": "assistant", "content": res.get("response", ""), "kind": "text", "media": ""}]
        extracted = extract_artifacts_from_messages(fake_msg)
        code_files = [x for x in extracted if x.get("category") == "code" and x.get("content")]
        base = Path(target_dir)
        base.mkdir(parents=True, exist_ok=True)
        written_files: list[str] = []
        for idx, item in enumerate(code_files):
            rel = item.get("title") or f"file_{idx + 1}.txt"
            safe_rel = Path(rel).name if ".." in rel else rel.lstrip("/")
            dest = base / safe_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(item["content"], encoding="utf-8")
            written_files.append(str(dest))
        return {
            "success": True,
            "model": res.get("model"),
            "target_dir": str(base),
            "written_files": written_files,
            "response_summary": (res.get("response") or "")[:600],
            "usage": res.get("usage", {}),
        }


def main():
    parser = argparse.ArgumentParser(description="LifeOS Chat — Masaüstü AI Çalışma Alanı & MCP Sunucusu")
    parser.add_argument("--mcp", action="store_true", help="MCP (Model Context Protocol) stdio sunucusu olarak çalıştır")
    parser.add_argument("--ask", type=str, help="Komut satırından hızlı soru/görev çalıştır")
    parser.add_argument("--file", type=str, default="", help="--ask ile birlikte gönderilecek UDF/PDF/DOCX/görsel dosyası")
    parser.add_argument("--provider", type=str, default="auto", help="Sağlayıcı ('auto', 'groq', 'cloudflare')")
    parser.add_argument("--model", type=str, default=AUTO_MODEL_ID, help="Model ID veya 'auto'")
    parser.add_argument("--read-doc", type=str, help="Belgeyi (UDF/DOCX/PDF/RTF/XLSX) oku ve metnini yazdır")
    parser.add_argument("--convert", nargs=2, metavar=("SOURCE", "FORMAT"), help="Belgeyi hedef formata dönüştür (udf, docx, pdf, rtf)")
    args, qt_args = parser.parse_known_args()

    if args.read_doc:
        doc = extract_document_full(args.read_doc)
        print(json.dumps({"meta": doc.to_meta_dict(), "text": doc.text}, ensure_ascii=False, indent=2))
        return 0

    if args.convert:
        res = convert_document_file(args.convert[0], args.convert[1])
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.mcp:
        from mcp_server import run_mcp_stdio
        bridge = Bridge()
        return run_mcp_stdio(bridge)

    if args.ask:
        bridge = Bridge()
        res = bridge.run_sync_completion(
            prompt=args.ask, model_id=args.model, file_path=args.file, provider=args.provider
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    app = QGuiApplication([sys.argv[0], *qt_args])
    app.setApplicationName("LifeOS Chat")
    bridge = Bridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "Main.qml")))
    if not engine.rootObjects():
        return 1
    if bridge.activeAccount:
        bridge.loadModels()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
