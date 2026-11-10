"""Cloudflare Workers AI & Groq catalog, smart router, accurate quota tracker, artifact extractor, and rich formatter."""
from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from pathlib import Path

from documents import (
    EXPORT_FORMATS,
    convert_document_file,
    export_document,
    extract_document_full,
)


TASKS = {
    "text-generation": ("Metin & Sohbet", "Metin üretir, belge/kod analiz eder ve sohbet geçmişini kullanır."),
    "image-to-text": ("Görsel Anlama / OCR", "Görseli yorumlar, resim ve taranmış belgedeki yazıyı okur."),
    "image-text-to-text": ("Görsel + Metin (Vision)", "Görsel ve metni birlikte analiz eder, OCR yapar."),
    "text-to-image": ("Görsel Üretimi", "Yazdığınız metin açıklamasından yeni görsel/resim oluşturur."),
    "automatic-speech-recognition": ("Ses → Metin (Deşifre)", "Ses dosyasını veya mikrofon kaydını yazıya çevirir."),
    "text-to-speech": ("Metin → Ses (TTS)", "Yazılan metni doğal konuşma sesine dönüştürür."),
    "text-embeddings": ("Embedding (Vektör)", "Metni arama/benzerlik için sayısal vektöre dönüştürür."),
    "summarization": ("Özetleme", "Uzun metin ve belgeleri özetler."),
    "translation": ("Çeviri", "Metni diller arasında çevirir."),
    "text-classification": ("Sınıflandırma", "Metni güvenlik veya duygu kategorilerine ayırır."),
    "image-classification": ("Görsel Sınıflandırma", "Görseli nesne/kategori sınıflarına ayırır."),
    "object-detection": ("Nesne Tespiti", "Görseldeki nesnelerin konumunu bulur."),
    "voice-activity-detection": ("Ses Algılama", "Ses içindeki konuşma bölümlerini saptar."),
    "structured-extraction": ("Veri Çıkarma", "Belgeden yapılandırılmış JSON alanları çıkarır."),
    "decision-model": ("Karar Modeli", "Tanımlanmış seçenekler arasında seçim yapar."),
}

CHAT_TASKS = {"text-generation", "image-text-to-text", "image-to-text"}

GROUP_ORDER = {
    "🧠 Derin Düşünme & Analiz (Metin)": 0,
    "⚡ Güçlü Sohbet & Belge (Metin)": 1,
    "💻 Kodlama & Teknik (Metin)": 2,
    "💬 Dengeli & Hızlı Sohbet (Metin)": 3,
    "🖼️ Görsel Anlama & OCR (Görüntü → Metin)": 4,
    "🎨 Görsel Üretimi (Metin → Görüntü)": 5,
    "🎙️ Ses Deşifre (Ses → Metin)": 6,
    "🔊 Seslendirme (Metin → Ses)": 7,
    "🔢 Vektör / Embedding": 8,
    "🛡️ Uzman & Sınıflandırma": 9,
}

AUTO_MODEL_ID = "__auto__"

# Official Groq Free Tier Limits (RPD = Requests/Day, TPM = Tokens/Min, TPD = Tokens/Day)
GROQ_FREE_LIMITS: dict[str, dict[str, int]] = {
    "llama-3.1-8b-instant": {"rpm": 30, "rpd": 14400, "tpm": 6000, "tpd": 500000},
    "llama-3.3-70b-versatile": {"rpm": 30, "rpd": 1000, "tpm": 12000, "tpd": 100000},
    "meta-llama/llama-4-scout-17b-16e-instruct": {"rpm": 30, "rpd": 1000, "tpm": 30000, "tpd": 500000},
    "meta-llama/llama-4-maverick-17b-128e-instruct": {"rpm": 30, "rpd": 1000, "tpm": 6000, "tpd": 500000},
    "openai/gpt-oss-120b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
    "openai/gpt-oss-20b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
    "qwen/qwen3-32b": {"rpm": 60, "rpd": 1000, "tpm": 6000, "tpd": 100000},
    "qwen-qwq-32b": {"rpm": 30, "rpd": 1000, "tpm": 6000, "tpd": 100000},
    "deepseek-r1-distill-llama-70b": {"rpm": 30, "rpd": 1000, "tpm": 6000, "tpd": 100000},
    "whisper-large-v3": {"rpm": 20, "rpd": 2000, "tpm": 0, "tpd": 0},
    "whisper-large-v3-turbo": {"rpm": 20, "rpd": 2000, "tpm": 0, "tpd": 0},
}

# Official Cloudflare Workers AI Neuron weights per 1M input / 1M output tokens
# Free tier = 10,000 Neurons / day (resets 00:00 UTC)
CF_NEURON_WEIGHTS: list[tuple[str, float, float]] = [
    ("70b", 26668.0, 204805.0),     # ~48,800 output tokens or ~375,000 input tokens / day
    ("120b", 35000.0, 220000.0),
    ("32b", 18000.0, 95000.0),
    ("27b", 16000.0, 85000.0),
    ("20b", 14000.0, 65000.0),
    ("12b", 12000.0, 50000.0),
    ("8b-instruct-awq", 11161.0, 24215.0),  # ~412,000 output tokens / day
    ("8b-instruct-fp8", 13778.0, 38000.0),  # ~263,000 output tokens / day
    ("8b", 25608.0, 75147.0),
    ("3b", 4500.0, 22000.0),
    ("1b", 2457.0, 18252.0),        # ~547,000 output tokens / day
]


def get_groq_model_limits(model_id: str) -> dict[str, int]:
    low = (model_id or "").lower()
    if low in GROQ_FREE_LIMITS:
        return GROQ_FREE_LIMITS[low]
    for k, v in GROQ_FREE_LIMITS.items():
        if k in low or low in k:
            return v
    if "8b" in low or "instant" in low:
        return {"rpm": 30, "rpd": 14400, "tpm": 6000, "tpd": 500000}
    if "whisper" in low:
        return {"rpm": 20, "rpd": 2000, "tpm": 0, "tpd": 0}
    return {"rpm": 30, "rpd": 1000, "tpm": 10000, "tpd": 200000}


def estimate_cloudflare_neurons(task: str, prompt_tokens: int, completion_tokens: int, model_id: str = "") -> float:
    """Calculates Cloudflare Workers AI Neurons based on published per-1M token weights."""
    if task == "text-to-image":
        return 45.0
    if task in {"automatic-speech-recognition", "text-to-speech"}:
        return 12.0
    if task == "text-embeddings":
        return round(max(0.2, (prompt_tokens / 1_000_000.0) * 1200.0), 3)

    low = (model_id or "").lower()
    in_per_m, out_per_m = 13778.0, 38000.0
    for pattern, inp, outp in CF_NEURON_WEIGHTS:
        if pattern in low:
            in_per_m, out_per_m = inp, outp
            break
    neurons = (prompt_tokens / 1_000_000.0) * in_per_m + (completion_tokens / 1_000_000.0) * out_per_m
    return round(max(0.05, neurons), 2)


def model_tier(name: str, task: str) -> tuple[str, str]:
    if task not in CHAT_TASKS:
        return "Uzman", "Bu model özel bir görev (ses/görsel/vektör) için uzmanlaşmıştır."
    lowered = name.lower()
    sizes = [float(x) for x in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)b(?![a-z])", lowered)]
    if re.search(r"(?<![\d.])\d+(?:\.\d+)?t(?![a-z])", lowered):
        return "Güçlü", "Trilyon ölçekli büyük model kapasitesi."
    if sizes:
        size = max(sizes)
        tier = "Hafif" if size < 7 else "Dengeli" if size < 30 else "Güçlü"
        return tier, f"{size:g}B parametre ölçeği · {'Hızlı ve ekonomik' if tier == 'Hafif' else 'Hız/kalite dengeli' if tier == 'Dengeli' else 'Yüksek muhakeme ve belge kapasitesi'}."
    if any(k in lowered for k in ("large", "70b", "120b", "pro", "versatile", "maverick", "scout", "qwen3", "deepseek", "kimi")):
        return "Güçlü", "Yüksek kapasiteli amiral gemisi model."
    if any(k in lowered for k in ("mini", "nano", "small", "instant", "lite", "flash")):
        return "Hafif", "Düşük gecikmeli hızlı yanıt modeli."
    return "Dengeli", "Genel amaçlı sohbet ve metin modeli."


def model_modalities(task: str, badges: list[str], name: str) -> dict:
    """Returns explicit input/output modalities and visual tags for clear UI grouping."""
    lower = name.lower()
    has_vision = (
        task in {"image-to-text", "image-text-to-text", "image-classification", "object-detection"}
        or "Görsel" in badges
        or "OCR / Görsel" in badges
        or any(k in lower for k in ("vision", "llava", "pixtral", "vl", "scout", "maverick", "gemma-3-12b", "gemma-3-27b", "llama-4"))
    )
    has_audio_in = task in {"automatic-speech-recognition", "voice-activity-detection"}
    has_audio_out = task == "text-to-speech" or any(k in lower for k in ("aura", "melotts", "orpheus", "playai", "tts"))
    has_image_out = task == "text-to-image"
    has_reasoning = "Derin düşünme" in badges or any(k in lower for k in ("qwq", "r1", "reason", "thinking", "gpt-oss"))
    is_paid_only_cf = any(p in lower for p in ("deepseek-v4", "kimi-k2", "gemma-3-12b"))
    is_dedicated_coder = (task in CHAT_TASKS) and not is_paid_only_cf and any(k in lower for k in ("coder", "codestral", "code", "starcoder"))
    has_coding = (task in CHAT_TASKS) and not is_paid_only_cf and (
        is_dedicated_coder
        or any(
            k in lower
            for k in ("deepseek", "gpt-oss", "qwen3", "qwq", "llama-3.3-70b", "llama-4-scout", "gemma-4-26b", "mistral-small-3.1", "nemotron")
        )
    )
    has_tools = "Araç çağırma" in badges or any(k in lower for k in ("llama-3.3", "llama-3.1", "qwen", "gpt-oss", "mistral", "gemma-3"))

    inputs: list[str] = []
    outputs: list[str] = []

    if has_audio_in:
        inputs = ["🎙️ Ses"]
        outputs = ["📝 Metin"]
        modality_badge = "🎙️ Ses → 📝 Metin"
    elif has_audio_out:
        inputs = ["📝 Metin"]
        outputs = ["🔊 Ses"]
        modality_badge = "📝 Metin → 🔊 Ses"
    elif has_image_out:
        inputs = ["📝 Metin"]
        outputs = ["🎨 Görsel"]
        modality_badge = "📝 Metin → 🎨 Görsel"
    elif task == "text-embeddings":
        inputs = ["📝 Metin"]
        outputs = ["🔢 Vektör"]
        modality_badge = "📝 Metin → 🔢 Vektör"
    elif has_vision:
        inputs = ["📝 Metin", "🖼️ Görsel", "📄 Belge"]
        outputs = ["📝 Metin"]
        modality_badge = "🖼️ Görsel + 📝 Metin → 📝 Metin"
    elif has_coding:
        inputs = ["💻 Kod", "📝 Metin", "📄 Dosya"]
        outputs = ["💻 Kod", "📝 Metin"]
        modality_badge = "💻 Kod + 📝 Metin → 💻 Çözüm"
    else:
        inputs = ["📝 Metin", "📄 Belge"]
        outputs = ["📝 Metin"]
        modality_badge = "📝 Metin + 📄 Belge → 📝 Metin"

    if any(k in lower for k in ("128k", "llama-3.1", "llama-3.2", "llama-3.3", "llama-4", "qwen", "gpt-oss", "gemma-3", "deepseek")):
        context_window = "128K"
    elif any(k in lower for k in ("32k", "mistral", "mixtral", "qwq")):
        context_window = "32K"
    elif task in CHAT_TASKS:
        context_window = "16K+"
    else:
        context_window = "Görev"

    return {
        "inputs": inputs,
        "outputs": outputs,
        "modalityBadge": modality_badge,
        "hasVision": has_vision,
        "hasAudioIn": has_audio_in,
        "hasAudioOut": has_audio_out,
        "hasImageOut": has_image_out,
        "hasReasoning": has_reasoning,
        "hasCoding": has_coding,
        "isDedicatedCoder": is_dedicated_coder,
        "hasTools": has_tools,
        "contextWindow": context_window,
    }


def model_group(task: str, tier: str, mod: dict) -> str:
    if mod["hasAudioIn"]:
        return "🎙️ Ses Deşifre (Ses → Metin)"
    if mod["hasAudioOut"]:
        return "🔊 Seslendirme (Metin → Ses)"
    if mod["hasImageOut"]:
        return "🎨 Görsel Üretimi (Metin → Görüntü)"
    if task == "text-embeddings":
        return "🔢 Vektör / Embedding"
    if task in {"image-to-text", "image-text-to-text"} or (mod["hasVision"] and task in CHAT_TASKS):
        return "🖼️ Görsel Anlama & OCR (Görüntü → Metin)"
    if task == "text-generation":
        if mod.get("isDedicatedCoder"):
            return "💻 Kodlama & Teknik (Metin)"
        if mod["hasReasoning"]:
            return "🧠 Derin Düşünme & Analiz (Metin)"
        if mod["hasCoding"] and tier == "Güçlü":
            return "⚡ Güçlü Sohbet & Belge (Metin)"
        if tier == "Güçlü":
            return "⚡ Güçlü Sohbet & Belge (Metin)"
        return "💬 Dengeli & Hızlı Sohbet (Metin)"
    return "🛡️ Uzman & Sınıflandırma"


def model_purpose(task: str, badges: list[str], name: str, mod: dict | None = None) -> str:
    if task == "text-embeddings":
        return "Arama ve benzerlik için sayı vektörü üretir; sohbet yanıtı vermez."
    if task == "text-to-image":
        return "Yazdığınız açıklamadan yüksek kaliteli görsel/resim üretir."
    if task == "automatic-speech-recognition":
        return "Ses kaydını ve ses dosyalarını yüksek doğrulukla yazıya çevirir."
    if task == "text-to-speech" or (mod and mod.get("hasAudioOut")):
        return "Yazıyı doğal sesli konuşmaya dönüştürür (Canlı Sesli Sohbet uyumlu)."
    if mod and mod.get("hasVision"):
        return "Resim, ekran görüntüsü ve taranmış belgeleri okur ve yorumlar."
    if task in {"image-to-text", "image-text-to-text"}:
        return "Resim ve ekran görüntüsü yorumlar; destekliyorsa OCR yapar."
    if task == "text-generation":
        if "coder" in name.lower() or "code" in name.lower():
            return "Kod yazma, proje üretimi, hata ayıklama (debug) ve yazılım mimarisi için uzman model."
        if "Derin düşünme" in badges:
            return "Adım adım mantık yürütme, kod/algoritma geliştirme ve kapsamlı veri/belge analizi."
        return "Genel sohbet, araştırma, kodlama, içerik üretimi ve belge inceleme."
    return TASKS.get(task, ("", "Modelin özel giriş biçimiyle test edin."))[1]


def model_sort_key(model: dict) -> tuple:
    return (GROUP_ORDER.get(model["group"], 20), 0 if model.get("tier") == "Güçlü" else 1, model["title"].lower())


def default_chat_model(models: list[dict]) -> str:
    candidates = [m for m in models if m["task"] == "text-generation"]
    preferred = (
        "llama-3.3-70b-versatile",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "openai/gpt-oss-120b",
        "@cf/openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "@cf/openai/gpt-oss-20b",
        "@cf/meta/llama-3.1-8b-instruct-fp8",
        "llama-3.1-8b-instant",
    )
    for model_id in preferred:
        if any(m["id"] == model_id for m in candidates):
            return model_id
    for tier in ("Güçlü", "Dengeli", "Hafif", "Belirsiz"):
        match = next(
            (
                m for m in candidates
                if m["tier"] == tier
                and not any(x in m["id"].lower() for x in ("-lora", "-guard", "apertus", "clef", "clairic"))
            ),
            None,
        )
        if match:
            return match["id"]
    return next((m["id"] for m in models if m["task"] in CHAT_TASKS), "")


def select_best_model(
    models: list[dict],
    prompt: str = "",
    attachment: dict | None = None,
    preferred_task: str = "",
) -> tuple[str, str]:
    """Smart Router: Automatically picks the best model for the user's task & attachment."""
    if not models:
        return "", "Model listesi boş"

    p_low = (prompt or "").lower().strip()

    # 1. Explicit audio transcription
    if (attachment and attachment.get("kind") == "audio") or preferred_task == "automatic-speech-recognition":
        asr_models = [m for m in models if m["task"] == "automatic-speech-recognition" or m.get("hasAudioIn")]
        for pref in ("whisper-large-v3-turbo", "whisper-large-v3", "@cf/openai/whisper-large-v3-turbo", "@cf/openai/whisper"):
            hit = next((m for m in asr_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Ses deşifresi için {hit['title']} seçildi"
        if asr_models:
            return asr_models[0]["id"], f"Ses dosyası için {asr_models[0]['title']} seçildi"

    # 2. Image / Vision / Scanned PDF OCR
    needs_vision = (
        (attachment and attachment.get("kind") == "image")
        or (attachment and attachment.get("kind") == "document" and attachment.get("rendered_pages"))
        or preferred_task in {"image-to-text", "image-text-to-text", "vision"}
    )
    if needs_vision:
        vision_models = [m for m in models if m.get("hasVision") or m["task"] in {"image-text-to-text", "image-to-text"}]
        for pref in ("llama-4-scout", "llama-4-maverick", "llama-3.2-90b-vision", "llama-3.2-11b-vision", "gemma-3-27b", "gemma-3-12b", "pixtral", "llava"):
            hit = next((m for m in vision_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Görsel/OCR analizi için {hit['title']} seçildi"
        if vision_models:
            return vision_models[0]["id"], f"Görsel analizi için {vision_models[0]['title']} seçildi"

    # 3. Image generation request
    img_triggers = (
        "resim çiz", "görsel üret", "görsel oluştur", "resim oluştur", "fotoğraf üret",
        "logo tasarla", "illüstrasyon", "draw a", "generate an image", "create an image",
    )
    if preferred_task == "text-to-image" or (not attachment and any(t in p_low for t in img_triggers)):
        img_models = [m for m in models if m["task"] == "text-to-image" or m.get("hasImageOut")]
        for pref in ("flux-1-schnell", "flux", "stable-diffusion-xl", "dreamshaper", "phoenix"):
            hit = next((m for m in img_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Görsel üretimi için {hit['title']} seçildi"
        if img_models:
            return img_models[0]["id"], f"Görsel üretimi için {img_models[0]['title']} seçildi"

    # 4. Text-to-Speech request
    tts_triggers = ("seslendir", "sesli oku", "sese çevir", "konuşma üret")
    if preferred_task == "text-to-speech" or (not attachment and any(t in p_low for t in tts_triggers) and len(p_low) < 400):
        tts_models = [m for m in models if m["task"] == "text-to-speech" or m.get("hasAudioOut")]
        for pref in ("aura-1", "orpheus", "melotts", "playai"):
            hit = next((m for m in tts_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Seslendirme (TTS) için {hit['title']} seçildi"
        if tts_models:
            return tts_models[0]["id"], f"Seslendirme (TTS) için {tts_models[0]['title']} seçildi"

    chat_models = [
        m for m in models
        if (m["task"] == "text-generation" or "llama-4-scout" in m["id"].lower())
        and not any(
            x in m["id"].lower()
            for x in ("-lora", "-guard", "apertus", "clef", "clairic", "moderation", "safeguard", "deepseek-v4", "kimi-k2")
        )
    ]
    if not chat_models:
        fallback = default_chat_model(models)
        return fallback, "Varsayılan sohbet modeli seçildi"

    # 5. Fast mode (used by Live Voice or quick questions)
    if preferred_task == "fast":
        for pref in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "@cf/meta/llama-3.1-8b-instruct-fp8", "gpt-oss-20b"):
            hit = next((m for m in chat_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Hızlı yanıt için {hit['title']} seçildi"

    # 6. Coding / debugging / software architecture / project generation request
    code_triggers = (
        "kod ", "proje ", "uygulama yaz", "python", "javascript", "typescript", "flutter", "dart", "html",
        "css", "sql", "regex", "fonksiyon", "bug", "hata", "debug", "refactor", "script", "```", "class ", "def ",
    )
    if preferred_task == "coding" or any(t in p_low for t in code_triggers):
        coders = [m for m in chat_models if m.get("hasCoding")]
        for pref in (
            "qwen-2.5-coder-32b", "qwen2.5-coder", "qwen3-32b", "qwen3.8-27b", "qwen3-30b",
            "qwq-32b", "deepseek", "llama-3.3-70b", "gpt-oss-120b", "gpt-oss-20b",
        ):
            hit = next((m for m in chat_models if pref in m["id"].lower()), None)
            if hit:
                return hit["id"], f"Kodlama ve hata ayıklama için {hit['title']} seçildi"
        if coders:
            return coders[0]["id"], f"Kodlama görevi için {coders[0]['title']} seçildi"

    # 7. Document / Legal / Deep Reasoning (UDF, PDF, DOCX or long/analytical prompt)
    deep_triggers = (
        "dilekçe", "sözleşme", "mahkeme", "kanun", "madde", "yargıtay", "analiz", "evrak", "udf",
        "karşılaştır", "incele", "özetle", "detaylı", "adım adım", "neden", "savunma", "ihtarname", "rapor",
    )
    if (
        (attachment and attachment.get("kind") == "document")
        or preferred_task in {"reasoning", "document"}
        or len(p_low) > 220
        or any(t in p_low for t in deep_triggers)
    ):
        for pref in (
            "llama-3.3-70b-versatile",
            "gpt-oss-120b",
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "deepseek-r1",
            "qwen-qwq",
            "qwen3-32b",
            "@cf/qwen/qwen3-30b",
            "gpt-oss-20b",
            "gemma-3-27b",
        ):
            hit = next((m for m in chat_models if pref in m["id"].lower()), None)
            if hit:
                reason_tag = "Belge ve kapsamlı analiz" if attachment else "Derin analiz ve belge üretimi"
                return hit["id"], f"{reason_tag} için {hit['title']} seçildi"
        strong = [m for m in chat_models if m["tier"] == "Güçlü"]
        if strong:
            return strong[0]["id"], f"Kapsamlı yanıt için {strong[0]['title']} seçildi"

    # 8. Balanced everyday query (prefer non-reasoning fast versatile models first so simple "merhaba" doesn't waste reasoning tokens)
    for pref in (
        "llama-3.3-70b-versatile",
        "llama-4-scout",
        "llama-3.1-8b-instant",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "@cf/meta/llama-3.1-8b-instruct-fp8",
        "gpt-oss-120b",
        "gpt-oss-20b",
    ):
        hit = next((m for m in chat_models if pref in m["id"].lower()), None)
        if hit:
            return hit["id"], f"Hızlı ve dengeli yanıt için {hit['title']} seçildi"

    best = default_chat_model(models)
    best_title = next((m["title"] for m in models if m["id"] == best), best)
    return best, f"Otomatik olarak {best_title} seçildi"


def embedding_summary(result: object) -> str | None:
    if not isinstance(result, dict):
        return None
    vectors = result.get("data")
    if not isinstance(vectors, list) or not vectors:
        return None
    if all(isinstance(x, (int, float)) for x in vectors):
        vectors = [vectors]
    if not all(isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v) for v in vectors):
        return None
    dims = len(vectors[0])
    preview = ", ".join(f"{x:.4f}" for x in vectors[0][:6])
    return (
        f"Embedding üretildi · {len(vectors)} metin · {dims} boyut\n"
        f"İlk değerler: {preview}…\n"
        "Bu model sohbet yanıtı üretmez; metin benzerliği ve arama için kullanılır."
    )


def normalize_model(item: dict) -> dict:
    name = str(item.get("name") or item.get("id") or item.get("model") or "")
    task_raw = item.get("task") or item.get("task_name") or item.get("type") or ""
    if isinstance(task_raw, dict):
        task_raw = task_raw.get("name") or task_raw.get("id") or ""
    task = str(task_raw).lower().replace("_", "-").replace(" ", "-")
    lowered_name = name.lower()
    if lowered_name.endswith("/clairic"):
        task = "structured-extraction"
    elif "/clef" in lowered_name:
        task = "decision-model"
    elif "guard" in lowered_name and task == "text-generation":
        task = "text-classification"
    elif any(k in lowered_name for k in ("aura-1", "melotts")) and not task:
        task = "text-to-speech"

    description = str(item.get("description") or item.get("summary") or "").strip()
    tags_raw = item.get("tags") or item.get("capabilities") or []
    if isinstance(tags_raw, dict):
        tags_raw = list(tags_raw.keys())
    tags = [str(x.get("name") if isinstance(x, dict) else x) for x in tags_raw]
    blob = " ".join([name, description, *tags]).lower()

    badges: list[str] = []
    if any(k in blob for k in ("reasoning", "reason", "thinking", "akıl yürüt", "qwq", "deepseek-r1", "gpt-oss")):
        badges.append("Derin düşünme")
    if any(k in blob for k in ("ocr", "document", "image-to-text", "image-text-to-text", "pixtral", "llava", "scout", "maverick", "vl")):
        badges.append("OCR / Görsel")
    if "vision" in blob and "OCR / Görsel" not in badges:
        badges.append("Görsel")
    if any(k in blob for k in ("function calling", "tool calling", "tools")):
        badges.append("Araç çağırma")
    if any(k in lowered_name for k in ("coder", "code", "codestral", "starcoder")):
        badges.append("Kodlama")
    if "premium" in " ".join(tags).lower() or re.search(r"(?<![a-z])pro(?![a-z])", lowered_name):
        badges.append("Pro")
    if "realtime" in blob or "real-time" in blob or "fast" in lowered_name or "instant" in lowered_name or "turbo" in lowered_name:
        badges.append("Hızlı")

    label, explanation = TASKS.get(task, (task.replace("-", " ").title() or "Diğer", "Modelin giriş şemasıyla test edin."))
    tier, tier_reason = model_tier(name, task)
    mod = model_modalities(task, badges, name)
    group = model_group(task, tier, mod)

    return {
        "id": name,
        "title": name.split("/")[-1],
        "task": task,
        "category": label,
        "description": description or explanation,
        "purpose": model_purpose(task, badges, name, mod),
        "tier": tier,
        "tierReason": tier_reason,
        "group": group,
        "badges": badges,
        "author": str(item.get("author") or (name.split("/")[1] if name.startswith("@cf/") and len(name.split("/")) > 2 else "")),
        "inputs": mod["inputs"],
        "outputs": mod["outputs"],
        "modalityBadge": mod["modalityBadge"],
        "hasVision": mod["hasVision"],
        "hasAudioIn": mod["hasAudioIn"],
        "hasAudioOut": mod["hasAudioOut"],
        "hasImageOut": mod["hasImageOut"],
        "hasReasoning": mod["hasReasoning"],
        "hasCoding": mod["hasCoding"],
        "hasTools": mod["hasTools"],
        "contextWindow": mod["contextWindow"],
        "raw": item,
    }


def normalize_groq_model(item: dict) -> dict:
    name = str(item.get("id") or "")
    lower = name.lower()
    if "whisper" in lower:
        task = "automatic-speech-recognition"
    elif "guard" in lower or "moderation" in lower or "safeguard" in lower:
        task = "text-classification"
    elif "tts" in lower or "speech" in lower or "playai" in lower or "orpheus" in lower:
        task = "text-to-speech"
    elif "vision" in lower or "llava" in lower or "scout" in lower or "maverick" in lower:
        task = "image-text-to-text"
    else:
        task = "text-generation"

    ctx_win = item.get("context_window")
    limits = get_groq_model_limits(name)
    model = normalize_model({
        "name": name,
        "task": task,
        "author": item.get("owned_by", "Groq"),
        "description": item.get("description", ""),
    })
    if ctx_win and isinstance(ctx_win, int):
        model["contextWindow"] = f"{ctx_win // 1024}K" if ctx_win >= 1024 else str(ctx_win)
    if limits.get("tpd"):
        model["tierReason"] += f" · Ücretsiz Limit: {limits['tpd']:,} tok/gün ({limits['rpd']:,} istek/gün, {limits['tpm']:,} tok/dk)"
    elif limits.get("rpd"):
        model["tierReason"] += f" · Ücretsiz Limit: {limits['rpd']:,} istek/gün"
    model["limits"] = limits
    model["raw"] = item
    return model


def extract_document(path: str, limit: int = 250000) -> str:
    return extract_document_full(path, limit=limit).text


def smart_compress_document(text: str, max_chars: int = 15000, query: str = "") -> str:
    """Preserves Head (Künye/Dava/İddia), Middle (Gerekçe/Deliller/Query matches), and Tail (Hüküm/Sonuç) of large legal/office documents so they fit within strict per-minute token (TPM) budgets without losing the verdict."""
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars or max_chars <= 0:
        return cleaned

    head_budget = int(max_chars * 0.42)
    mid_budget = int(max_chars * 0.23)
    tail_budget = max_chars - head_budget - mid_budget

    head_part = cleaned[:head_budget].rstrip()
    tail_part = cleaned[-tail_budget:].lstrip()
    middle_pool = cleaned[head_budget : len(cleaned) - tail_budget]

    # Pick high-value middle paragraphs (matching query keywords or legal reasoning headings)
    q_words = [w.lower() for w in re.findall(r"\w{3,}", query or "") if w.lower() not in {"inceler", "misin", "özetle", "nedir", "bu", "belgeyi", "dosyayı"}]
    legal_anchors = ("gerekçe", "delil", "bilirkişi", "tespit", "değerlendirme", "hüküm", "sonuç", "kanaat", "madde", "fıkra", "itiraz", "tazminat", "alacak")

    paragraphs = [p.strip() for p in re.split(r"\n{2,}|\n(?=[A-ZÇĞİÖŞÜ0-9]{2,})", middle_pool) if p.strip()]
    scored_paras: list[tuple[int, int, str]] = []
    for idx, p in enumerate(paragraphs):
        p_low = p.lower()
        score = sum(3 for w in q_words if w in p_low) + sum(2 for a in legal_anchors if a in p_low)
        scored_paras.append((score, idx, p))

    scored_paras.sort(key=lambda x: (-x[0], x[1]))
    chosen_mid: list[tuple[int, str]] = []
    used_mid = 0
    for score, idx, p in scored_paras:
        if used_mid + len(p) + 2 > mid_budget:
            if used_mid == 0:
                chosen_mid.append((idx, p[:mid_budget]))
                used_mid += mid_budget
            continue
        chosen_mid.append((idx, p))
        used_mid += len(p) + 2
        if used_mid >= mid_budget * 0.9:
            break

    chosen_mid.sort(key=lambda x: x[0])
    mid_part = "\n\n".join(p for _, p in chosen_mid) if chosen_mid else middle_pool[:mid_budget]

    return (
        f"{head_part}\n\n"
        f"[... Belge Akıllı Sıkıştırma ({len(cleaned):,} karakter → {max_chars:,} karakter): "
        f"Başlık/İddia, Gerekçe Özetleri ve Hüküm/Sonuç korundu ...]\n\n"
        f"{mid_part}\n\n"
        f"[... Belgenin Son Bölümü / Hüküm & Sonuç ...]\n\n"
        f"{tail_part}"
    )


def file_data(path: str) -> tuple[str, str]:
    file = Path(path)
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    return mime, base64.b64encode(file.read_bytes()).decode("ascii")


def conversation_payload(
    messages: list[dict],
    prompt: str,
    attachment: dict | None = None,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.6,
    system_prompt: str = "",
    max_doc_chars: int = 250000,
    max_history_chars: int = 120000,
    task_mode: str = "all",
) -> dict:
    """Builds the chat completion payload without polluting UI bubbles with raw document text, and fits within model token budgets."""
    history: list[dict] = []
    if system_prompt.strip():
        default_sys = system_prompt.strip()
    elif task_mode == "coding":
        default_sys = (
            "Sen LifeOS Chat Kıdemli Yazılım Mühendisi ve Hata Ayıklama (Debugging) uzmanısın. "
            "Yanıtlarını açık, temiz ve eksiksiz olarak Türkçe ver.\n"
            "- Kod yazarken veya hata ayıklarken kök nedeni net biçimde açıkla, temiz ve üretime hazır (production-ready) kod üret.\n"
            "- Her kod bloğunun başına dosya adını ```dil dosya_adi.uzanti (örneğin ```python main.py, ```javascript app.js, ```qml Main.qml) "
            "biçiminde yaz ki Sağ Çalışma Alanı panelinde doğrudan proje dosyası olarak listelensin ve indirilebilsin.\n"
            "- Kodu asla yarıda kesme veya '// geri kalanı aynı' şeklinde eksik bırakma."
        )
    else:
        default_sys = (
            "Sen LifeOS Chat çok amaçlı yapay zekâ çalışma alanı asistanısın. Yanıtlarını açık, doğru, iyi yapılandırılmış "
            "Markdown biçiminde ve Türkçe olarak ver.\n"
            "- Kullanıcı bir dosya (PDF, Word, Excel, Kod dosyası, UDF, Görsel vb.) eklediğinde içeriğini eksiksiz analiz et.\n"
            "- Kullanıcı bir kod veya çok dosyalı proje isterse her kod bloğunun başına dosya adını ```dil dosya_adi.uzanti "
            "(örneğin ```python main.py veya ```html index.html) biçiminde yaz.\n"
            "- Kullanıcı bir rapor, makale veya resmi belge oluşturmanı isterse başlıkları ve tabloları düzenli Markdown olarak hazırla.\n"
            "- Cevabını asla yarıda kesme."
        )
    history.append({"role": "system", "content": default_sys})

    recent_msgs: list[dict] = []
    used_hist_chars = 0
    per_old_doc_limit = max(2000, max_history_chars // 2)
    for m in reversed(messages[-16:]):
        if m.get("role") not in {"user", "assistant"}:
            continue
        content = m.get("content") or ""
        att_text = m.get("attachment_text") or ""
        att_name = m.get("attachment") or ""
        if att_text and m.get("role") == "user" and f"[Ek belge: {att_name}]" not in content:
            compressed_old_doc = smart_compress_document(att_text, max_chars=per_old_doc_limit, query=prompt)
            content = f"{content}\n\n[Ek belge: {att_name}]\n{compressed_old_doc}".strip()
        elif len(content) > per_old_doc_limit:
            content = smart_compress_document(content, max_chars=per_old_doc_limit, query=prompt)

        if used_hist_chars + len(content) > max_history_chars and recent_msgs:
            break
        used_hist_chars += len(content)

        media_val = m.get("media") or ""
        media_local = media_val.replace("file://", "") if media_val.startswith("file://") else media_val
        if m.get("kind") == "image" and media_local and Path(media_local).exists():
            old_mime, old_data = file_data(media_local)
            content = [
                {"type": "text", "text": content or "Bu görseli incele."},
                {"type": "image_url", "image_url": {"url": f"data:{old_mime};base64,{old_data}"}},
            ]
        if content:
            recent_msgs.append({"role": m["role"], "content": content})

    history.extend(reversed(recent_msgs))

    user_content: str | list = prompt.strip()
    if attachment:
        kind = attachment.get("kind")
        if kind == "document":
            rendered_pages = attachment.get("rendered_pages") or []
            doc_header = f"[Ek Belge: {attachment['name']} ({attachment.get('badge', 'Belge')})]"
            fitted_doc_text = smart_compress_document(attachment.get("text") or "", max_chars=max_doc_chars, query=prompt)
            full_prompt = f"{user_content or 'Bu belgeyi incele ve özetle.'}\n\n{doc_header}\n{fitted_doc_text}"
            if rendered_pages:
                user_content = [{"type": "text", "text": full_prompt}] + [
                    {"type": "image_url", "image_url": {"url": page_url}} for page_url in rendered_pages[:3]
                ]
            else:
                user_content = full_prompt
        elif kind == "image":
            user_content = [
                {"type": "text", "text": user_content or "Bu görseli ayrıntılı olarak açıkla ve varsa içindeki metinleri oku."},
                {"type": "image_url", "image_url": {"url": attachment["data_url"]}},
            ]

    history.append({"role": "user", "content": user_content})
    return {"messages": history, "max_tokens": max_tokens, "temperature": temperature}


def split_thinking(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    thinks: list[str] = []

    def _collect(m: re.Match) -> str:
        thinks.append(m.group(1).strip())
        return ""

    cleaned = re.sub(r"(?is)<think>(.*?)</think>", _collect, text).strip()
    return "\n\n".join(t for t in thinks if t), cleaned


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 3.6))


def parse_response(data: bytes, mime: str, prompt_text: str = "", task: str = "text-generation", model_id: str = "") -> dict:
    if mime.startswith("image/") or mime.startswith("audio/"):
        kind = "image" if mime.startswith("image/") else "audio"
        p_tok = estimate_tokens(prompt_text)
        return {
            "kind": kind,
            "data": base64.b64encode(data).decode(),
            "mime": mime,
            "usage": {
                "prompt_tokens": p_tok,
                "completion_tokens": 0,
                "total_tokens": p_tok,
                "neurons": estimate_cloudflare_neurons(task, p_tok, 0, model_id),
            },
            "truncated": False,
        }
    try:
        obj = json.loads(data)
    except ValueError:
        txt = data.decode("utf-8", errors="replace")
        p_tok = estimate_tokens(prompt_text)
        c_tok = estimate_tokens(txt)
        return {
            "kind": "text",
            "text": txt,
            "usage": {
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
                "neurons": estimate_cloudflare_neurons(task, p_tok, c_tok, model_id),
            },
            "truncated": False,
        }

    if isinstance(obj, dict) and obj.get("success") is False:
        errors = obj.get("errors") or []
        raise ValueError("; ".join(str(e.get("message", e)) for e in errors) or "Cloudflare isteği başarısız.")

    result = obj.get("result", obj) if isinstance(obj, dict) else obj
    usage_raw = {}
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        usage_raw = result["usage"]
    elif isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
        usage_raw = obj["usage"]

    finish_reason = ""
    if isinstance(result, dict):
        summary = embedding_summary(result)
        if summary:
            p_tok = int(usage_raw.get("prompt_tokens") or estimate_tokens(prompt_text))
            return {
                "kind": "embedding",
                "text": summary,
                "raw": result,
                "usage": {
                    "prompt_tokens": p_tok,
                    "completion_tokens": 0,
                    "total_tokens": p_tok,
                    "neurons": estimate_cloudflare_neurons("text-embeddings", p_tok, 0, model_id),
                },
                "truncated": False,
            }

        text_out = ""
        reasoning_out = ""
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_reason = str(first_choice.get("finish_reason") or "")
            message = first_choice.get("message") or {}
            text_out = str(message.get("content") or "")
            reasoning_out = str(message.get("reasoning") or message.get("reasoning_content") or "")
            if not text_out and reasoning_out:
                text_out = reasoning_out
                reasoning_out = ""
        else:
            for key in ("response", "description", "text", "summary", "translated_text"):
                if isinstance(result.get(key), str):
                    text_out = result[key]
                    break

        if text_out or reasoning_out:
            inline_think, cleaned_text = split_thinking(text_out)
            thinking = "\n\n".join(x for x in (reasoning_out.strip(), inline_think.strip()) if x)
            final_text = cleaned_text or text_out
            p_tok = int(usage_raw.get("prompt_tokens") or estimate_tokens(prompt_text))
            c_tok = int(usage_raw.get("completion_tokens") or estimate_tokens(final_text + thinking))
            truncated = finish_reason.lower() in ("length", "max_tokens")
            return {
                "kind": "text",
                "text": final_text,
                "thinking": thinking,
                "raw": result,
                "finish_reason": finish_reason,
                "truncated": truncated,
                "usage": {
                    "prompt_tokens": p_tok,
                    "completion_tokens": c_tok,
                    "total_tokens": int(usage_raw.get("total_tokens") or (p_tok + c_tok)),
                    "neurons": estimate_cloudflare_neurons(task, p_tok, c_tok, model_id),
                },
            }

        for key, kind, media_mime in (("image", "image", "image/png"), ("audio", "audio", "audio/mpeg")):
            value = result.get(key)
            if isinstance(value, str):
                p_tok = estimate_tokens(prompt_text)
                return {
                    "kind": kind,
                    "data": value,
                    "mime": media_mime,
                    "raw": result,
                    "usage": {
                        "prompt_tokens": p_tok,
                        "completion_tokens": 0,
                        "total_tokens": p_tok,
                        "neurons": estimate_cloudflare_neurons(task, p_tok, 0, model_id),
                    },
                    "truncated": False,
                }

    dumped = json.dumps(result, ensure_ascii=False, indent=2)
    p_tok = estimate_tokens(prompt_text)
    c_tok = estimate_tokens(dumped)
    return {
        "kind": "json",
        "text": dumped,
        "usage": {
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "total_tokens": p_tok + c_tok,
            "neurons": estimate_cloudflare_neurons(task, p_tok, c_tok, model_id),
        },
        "truncated": False,
    }


def extract_artifacts_from_messages(messages: list[dict], saved_files: list[dict] | None = None, saved_dir: str = "") -> list[dict]:
    """Extracts generated documents, code/project files, images, and audio from chat messages for the Right Panel."""
    artifacts: list[dict] = []

    if saved_files:
        artifacts.extend(saved_files)

    ext_by_lang = {
        "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
        "html": "html", "css": "css", "json": "json", "sql": "sql", "bash": "sh", "sh": "sh",
        "dart": "dart", "qml": "qml", "cpp": "cpp", "c": "c", "rust": "rs", "go": "go", "yaml": "yaml",
        "cmake": "txt",
    }

    code_counter = 0
    doc_counter = 0
    base_saved = Path(saved_dir) if saved_dir else None

    for m in messages:
        msg_id = m.get("id", 0)
        role = m.get("role", "")
        content = m.get("content") or ""
        kind = m.get("kind") or "text"
        media = m.get("media") or ""

        # 1. Media artifacts (generated images / audio or user attachments)
        if kind in ("image", "audio") and media:
            artifacts.append({
                "id": f"media-{msg_id}",
                "messageId": msg_id,
                "category": "media",
                "kind": kind,
                "title": ("Üretilen Görsel" if role == "assistant" else (m.get("attachment") or "Görsel"))
                if kind == "image"
                else ("Ses Çıktısı" if role == "assistant" else "Ses Kaydı"),
                "badge": "🎨 Görsel" if kind == "image" else "🔊 Ses",
                "media": media,
                "savedPath": media.replace("file://", ""),
                "content": content,
            })

        if role != "assistant" or not content:
            continue

        msg_code_count = 0
        # 2. Extract code blocks: ```lang [optional_filename] ... ```
        for match in re.finditer(r"```([^\n`]*)\n(.*?)```", content, re.DOTALL):
            header = match.group(1).strip()
            code_body = match.group(2).strip()
            if not code_body:
                continue
            code_counter += 1
            msg_code_count += 1
            tokens = header.split()
            lang = tokens[0].lower() if tokens else "txt"
            filename = ""
            if len(tokens) > 1 and ("." in tokens[1] or "/" in tokens[1] or tokens[1].lower() in ("cmakelists.txt", "makefile", "dockerfile")):
                filename = tokens[1].strip("`*_():")
            elif "." in lang:
                filename = lang
                lang = Path(filename).suffix.lstrip(".") or "txt"
            else:
                first_code_line = code_body.splitlines()[0].strip() if code_body else ""
                m_file = re.match(r"^(?:#|//|--|/\*)\s*(?:dosya|file|path)?\s*:?\s*([A-Za-z0-9_./-]+\.[A-Za-z0-9]+|CMakeLists\.txt|Makefile|Dockerfile)", first_code_line, re.IGNORECASE)
                if m_file:
                    filename = m_file.group(1)
                else:
                    pre_text = content[max(0, match.start() - 160) : match.start()]
                    pre_matches = re.findall(r"([A-Za-z0-9_./-]+\.(?:cpp|c|h|hpp|py|js|ts|tsx|jsx|qml|html|css|json|yaml|yml|toml|rs|go|java|kt|dart|sh|sql|md|txt)|CMakeLists\.txt|Makefile|Dockerfile)", pre_text)
                    valid_pre = ""
                    if pre_matches:
                        cand = pre_matches[-1]
                        cand_ext = Path(cand).suffix.lstrip(".").lower()
                        if lang in ("cmake", "txt", "") and cand.lower() == "cmakelists.txt":
                            valid_pre = "CMakeLists.txt"
                        elif lang in ("cpp", "c", "cxx", "cc", "h", "hpp") and cand_ext in ("cpp", "c", "h", "hpp"):
                            valid_pre = cand
                        elif ext_by_lang.get(lang) == cand_ext:
                            valid_pre = cand
                    if valid_pre:
                        filename = valid_pre
                    elif lang == "cmake":
                        filename = "CMakeLists.txt"
                    else:
                        ext = ext_by_lang.get(lang, "txt")
                        filename = f"kod_{code_counter}.{ext}"

            lines_cnt = len(code_body.splitlines())
            saved_file_path = ""
            if base_saved:
                safe_rel = Path(filename).name if ".." in filename else filename.lstrip("/")
                candidate = base_saved / safe_rel
                if candidate.exists():
                    saved_file_path = str(candidate)

            artifacts.append({
                "id": f"code-{msg_id}-{code_counter}",
                "messageId": msg_id,
                "category": "code",
                "kind": "code",
                "title": filename,
                "lang": lang.upper(),
                "badge": f"💻 {lang.upper()} · {lines_cnt} satır" + (" · 💾 Diskte" if saved_file_path else ""),
                "savedPath": saved_file_path,
                "content": code_body,
                "media": "",
            })

        # 3. Detect structured documents (explicit formal documents, or non-code structured reports)
        explicit_doc = any(k in content.upper() for k in (
            "MAHKEMESİ", "MAHKEMESI", "MÜDÜRLÜĞÜ", "BAŞKANLIĞI", "DAVACI", "DAVALI",
            "KONU:", "AÇIKLAMALAR", "SONUÇ VE İSTEM", "SÖZLEŞME", "TUTANAK", "RAPOR", "İHTARNAME",
        ))
        is_legal_or_doc = (
            len(content) >= 280
            and (
                explicit_doc
                or (msg_code_count == 0 and (content.count("#") >= 2 or content.count("| ---") >= 1))
            )
        )
        if is_legal_or_doc:
            doc_counter += 1
            first_line = next((ln.lstrip("#* ").strip() for ln in content.splitlines() if ln.strip()), f"Taslak Belge #{doc_counter}")
            words_cnt = len(content.split())
            artifacts.append({
                "id": f"doc-{msg_id}",
                "messageId": msg_id,
                "category": "document",
                "kind": "document",
                "title": first_line[:48],
                "badge": f"📄 Belge Taslağı · {words_cnt} kelime · DOCX/PDF/UDF Hazır",
                "savedPath": "",
                "content": content,
                "media": "",
            })

    return artifacts


def render_message_html(
    content: str,
    dark: bool = True,
    thinking: str = "",
    font_size: int = 14,
    show_thinking: bool = True,
) -> str:
    """Renders Markdown message into rich, styled HTML for QML TextEdit (selectable & visually polished)."""
    if not content and not thinking:
        return ""

    ink = "#f3f1fa" if dark else "#1f1b2c"
    muted = "#9f99b5" if dark else "#635c75"
    accent = "#b598f6" if dark else "#6b46c1"
    code_bg = "#12111a" if dark else "#f1eef9"
    code_fg = "#e2dcf5" if dark else "#2d2640"
    tbl_border = "#353148" if dark else "#d8d2e7"
    tbl_head = "#252136" if dark else "#ebe6f7"
    think_bg = "#1b1828" if dark else "#f4f0fc"
    base_px = max(11, min(22, int(font_size)))
    code_px = max(10, base_px - 1)

    parts: list[str] = []

    if show_thinking and thinking.strip():
        escaped_think = html.escape(thinking.strip()).replace("\n", "<br>")
        parts.append(
            f'<div style="background:{think_bg};border-left:3px solid {accent};padding:8px 12px;margin-bottom:10px;border-radius:6px;">'
            f'<div style="color:{accent};font-size:{base_px - 2}px;font-weight:bold;margin-bottom:4px;">🧠 DÜŞÜNCE SÜRECİ (REASONING)</div>'
            f'<div style="color:{muted};font-size:{base_px - 1}px;line-height:1.4;">{escaped_think}</div>'
            f"</div>"
        )

    def format_inline(line_str: str) -> str:
        s = html.escape(line_str)
        s = re.sub(r"&lt;br\s*/?&gt;", "<br>", s, flags=re.IGNORECASE)
        s = re.sub(
            r"`([^`\n]+)`",
            rf'<span style="background:{code_bg};color:{accent};font-family:monospace;padding:1px 5px;border-radius:4px;">\1</span>',
            s,
        )
        s = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^\)]+)\)",
            rf'<a href="\2" style="color:{accent};text-decoration:underline;">\1</a>',
            s,
        )
        return s

    lines = content.replace("\r\n", "\n").split("\n")
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "kod"
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(html.escape(lines[i]))
                i += 1
            if i < n:
                i += 1
            code_html = "<br>".join(code_lines)
            parts.append(
                f'<div style="background:{code_bg};border:1px solid {tbl_border};border-radius:8px;padding:10px 12px;margin:8px 0;">'
                f'<div style="color:{muted};font-size:{base_px - 3}px;font-family:monospace;font-weight:bold;margin-bottom:5px;text-transform:uppercase;">💻 {html.escape(lang)}</div>'
                f'<div style="color:{code_fg};font-family:\'JetBrains Mono\',\'Fira Code\',monospace;font-size:{code_px}px;line-height:1.45;white-space:pre-wrap;">{code_html}</div>'
                f"</div>"
            )
            continue

        if stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]:
            t_rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                r_str = lines[i].strip()[1:-1]
                cells = [c.strip() for c in r_str.split("|")]
                if not all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    t_rows.append(cells)
                i += 1
            if t_rows:
                cols = max(len(r) for r in t_rows)
                tr_list: list[str] = []
                for r_idx, r in enumerate(t_rows):
                    padded = r + [""] * (cols - len(r))
                    if r_idx == 0:
                        tds = "".join(
                            f'<th style="background:{tbl_head};color:{ink};padding:6px 10px;border:1px solid {tbl_border};text-align:left;font-weight:bold;">{format_inline(c)}</th>'
                            for c in padded
                        )
                    else:
                        tds = "".join(
                            f'<td style="color:{ink};padding:6px 10px;border:1px solid {tbl_border};">{format_inline(c)}</td>'
                            for c in padded
                        )
                    tr_list.append(f"<tr>{tds}</tr>")
                parts.append(
                    f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;margin:8px 0;font-size:{base_px - 1}px;">{"".join(tr_list)}</table>'
                )
            continue

        if re.match(r"^([-*_])\1{2,}$", stripped):
            parts.append(f'<hr style="border:none;border-top:1px solid {tbl_border};margin:10px 0;"/>')
            i += 1
            continue

        hm = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if hm:
            lvl = len(hm.group(1))
            sizes = {1: f"{base_px + 4}px", 2: f"{base_px + 2}px", 3: f"{base_px + 1}px", 4: f"{base_px}px"}
            parts.append(
                f'<div style="color:{ink};font-size:{sizes.get(lvl, f"{base_px}px")};font-weight:bold;margin:10px 0 4px 0;">{format_inline(hm.group(2))}</div>'
            )
            i += 1
            continue

        if stripped.startswith(">"):
            q_text = format_inline(stripped.lstrip(">").strip())
            parts.append(
                f'<div style="border-left:3px solid {accent};padding-left:10px;margin:4px 0;color:{muted};font-style:italic;">{q_text}</div>'
            )
            i += 1
            continue

        bm = re.match(r"^(\s*)([-*•])\s+(.*)$", line)
        om = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if bm or om:
            indent_px = min(48, len((bm or om).group(1)) * 8) + 8
            marker = f'<span style="color:{accent};font-weight:bold;">•</span>' if bm else f'<span style="color:{accent};font-weight:bold;">{html.escape(om.group(2))}</span>'
            item_body = format_inline((bm or om).group(3))
            parts.append(
                f'<div style="margin:3px 0 3px {indent_px}px;color:{ink};line-height:1.48;">{marker} {item_body}</div>'
            )
            i += 1
            continue

        if not stripped:
            parts.append('<div style="height:6px;"></div>')
            i += 1
            continue

        parts.append(f'<div style="color:{ink};margin:2px 0;line-height:1.52;">{format_inline(line)}</div>')
        i += 1

    return f'<div style="font-size:{base_px}px;color:{ink};">{"".join(parts)}</div>'
