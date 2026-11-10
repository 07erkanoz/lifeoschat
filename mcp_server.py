"""LifeOS Chat MCP Server (`lifeoschat`).

Exposes ALL registered Cloudflare Workers AI & Groq accounts and models
(chat, coding, reasoning, vision/OCR, Whisper ASR, TTS, Flux/SDXL image generation)
plus the UYAP (.udf) / DOCX / PDF / RTF document & project engine over stdio JSON-RPC 2.0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from core import AUTO_MODEL_ID
from documents import (
    convert_document_file,
    export_document,
    extract_document_full,
)


MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "lifeoschat"
SERVER_VERSION = "2.2.0"

INSTRUCTIONS = (
    "LifeOS Chat MCP sunucusu (`lifeoschat`). "
    "Kayıtlı TÜM Cloudflare Workers AI ve Groq hesaplarındaki modellere erişim sağlar:\n"
    "1) `lifeoschat_ask`: Metin/kod/analiz görevlerini `provider='auto'|'groq'|'cloudflare'` ve `model='auto'` "
    "ile en uygun ücretsiz modelde çalıştırır. İsteğe bağlı `save_output_path` verilirse yanıtı doğrudan "
    ".udf (UYAP), .docx, .pdf, .rtf veya kod dosyası olarak kaydeder.\n"
    "2) `lifeoschat_models`: Hem Groq hem Cloudflare'deki tüm aktif modelleri ve yeteneklerini listeler.\n"
    "3) `lifeoschat_generate_image`: Metin açıklamasından görsel (PNG) üretir ve diske kaydeder.\n"
    "4) `lifeoschat_tts` ve `lifeoschat_transcribe`: Metni sese (TTS) ve sesi metne (Whisper ASR) çevirir.\n"
    "5) `lifeoschat_read_document`, `lifeoschat_create_document`, `lifeoschat_convert_document`: "
    "UYAP (.udf), Word (.docx, .doc), RTF, ODT, PDF ve Excel (.xlsx) belgelerini okur, oluşturur ve dönüştürür.\n"
    "6) `lifeoschat_generate_project`: Model yardımıyla çok dosyalı kod/proje üretip hedef klasöre yazar."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lifeoschat_ask",
        "title": "Groq & Cloudflare Modelleriyle Çalıştır (Smart Router)",
        "description": (
            "Kayıtlı tüm Groq ve Cloudflare Workers AI hesaplarındaki modelleri kullanarak metin, kod, "
            "özet, çeviri veya belge/görsel analizi yapar. `save_output_path` verilirse modelin ürettiği "
            "yanıtı otomatik olarak .udf (UYAP), .docx, .pdf, .rtf veya kod dosyası olarak kaydeder."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Modele gönderilecek talimat veya soru",
                },
                "provider": {
                    "type": "string",
                    "enum": ["auto", "groq", "cloudflare"],
                    "description": "Hangi sağlayıcının kullanılacağı ('auto', 'groq' veya 'cloudflare')",
                },
                "model": {
                    "type": "string",
                    "description": "'auto' (önerilen) veya belirli bir Groq/Cloudflare model ID'si",
                },
                "task_hint": {
                    "type": "string",
                    "enum": ["auto", "fast", "coding", "reasoning", "document", "vision"],
                    "description": "Otomatik model seçimi için görev ipucu",
                },
                "file_path": {
                    "type": "string",
                    "description": "İsteğe bağlı: İncelenecek UDF, PDF, DOCX, XLSX veya görsel dosyasının tam yolu",
                },
                "save_output_path": {
                    "type": "string",
                    "description": "İsteğe bağlı: Model yanıtının kaydedileceği dosya yolu (.udf, .docx, .pdf, .rtf, .md, .py vb.)",
                },
                "system_prompt": {
                    "type": "string",
                    "description": "İsteğe bağlı sistem talimatı",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "lifeoschat_models",
        "title": "Tüm Hesaplardaki (Groq + Cloudflare) Modelleri Listele",
        "description": (
            "LifeOS Chat'te kayıtlı TÜM Cloudflare Workers AI ve Groq hesaplarındaki aktif modelleri "
            "sağlayıcıya ve girdi/çıktı yeteneklerine göre listeler."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["all", "groq", "cloudflare"],
                    "description": "Sağlayıcı filtresi ('all', 'groq', 'cloudflare')",
                },
                "modality": {
                    "type": "string",
                    "enum": ["all", "chat", "vision", "audio_in", "audio_out", "image_out", "coding", "reasoning"],
                    "description": "Yetenek filtresi",
                },
            },
        },
    },
    {
        "name": "lifeoschat_generate_image",
        "title": "Metinden Görsel / Resim Üret (Flux / SDXL)",
        "description": (
            "Cloudflare Workers AI görsel üretim modellerini (Flux-1-Schnell, SDXL vb.) kullanarak "
            "verilen açıklamadan PNG görsel üretir ve diske kaydeder."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Üretilecek görselin detaylı açıklaması",
                },
                "output_path": {
                    "type": "string",
                    "description": "Görselin kaydedileceği dosya yolu (örn. /tmp/gorsel.png)",
                },
                "model": {
                    "type": "string",
                    "description": "İsteğe bağlı model ID (varsayılan: otomatik en iyi görsel modeli)",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "lifeoschat_tts",
        "title": "Metni Sese Dönüştür (Text-to-Speech)",
        "description": "Metni Cloudflare (Aura / MeloTTS) veya Groq (Orpheus / PlayAI) ya da yerel TTS ile ses dosyasına dönüştürür.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Seslendirilecek metin",
                },
                "output_path": {
                    "type": "string",
                    "description": "İsteğe bağlı ses dosyası kayıt yolu (.wav veya .mp3)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "lifeoschat_transcribe",
        "title": "Ses Dosyasını Yazıya Çevir (Whisper ASR)",
        "description": "Ses dosyasını (.wav, .mp3, .m4a, .ogg, .flac) Groq veya Cloudflare Whisper modeliyle metne dönüştürür.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Ses dosyasının tam yolu",
                },
            },
            "required": ["audio_path"],
        },
    },
    {
        "name": "lifeoschat_read_document",
        "title": "UDF / DOCX / DOC / RTF / PDF / XLSX Belge Oku",
        "description": (
            "UYAP (.udf), Word (.docx, .doc), RTF, ODT, PDF, Excel (.xlsx, .csv) veya metin dosyasını okur; "
            "içeriğini, sayfa/kelime sayısını ve UYAP e-imza (sign.sgn) imzacı bilgilerini döndürür."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Okunacak dosyanın tam yolu",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maksimum karakter sınırı (varsayılan: 80000)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "lifeoschat_create_document",
        "title": "UDF (UYAP) / DOCX / PDF / RTF Belge Oluştur",
        "description": (
            "Verilen Markdown veya düz metin içeriğinden (başlıklar, kalın/italik, listeler, tablolar dahil) "
            "gerçek UYAP (.udf), Word (.docx), PDF (.pdf), RTF (.rtf), HTML veya MD dosyası oluşturur."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Belgeye yazılacak Markdown veya düz metin içeriği",
                },
                "output_path": {
                    "type": "string",
                    "description": "Oluşturulacak dosyanın kaydedileceği tam yol (örn. /home/.../dilekce.udf)",
                },
                "format": {
                    "type": "string",
                    "enum": ["udf", "docx", "pdf", "rtf", "md", "txt", "html"],
                    "description": "Hedef dosya biçimi (udf, docx, pdf, rtf, md, txt, html)",
                },
                "title": {
                    "type": "string",
                    "description": "Belge başlığı",
                },
            },
            "required": ["content", "output_path", "format"],
        },
    },
    {
        "name": "lifeoschat_convert_document",
        "title": "Belge Formatı Dönüştür (UDF ↔ DOCX ↔ PDF ↔ RTF)",
        "description": (
            "Mevcut bir belgeyi (.udf, .docx, .doc, .rtf, .odt, .pdf, .xlsx) başka bir biçime "
            "(udf, docx, pdf, rtf, md, txt, html) dönüştürür."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Kaynak dosyanın tam yolu",
                },
                "target_format": {
                    "type": "string",
                    "enum": ["udf", "docx", "pdf", "rtf", "md", "txt", "html"],
                    "description": "Dönüştürülecek hedef format",
                },
                "output_path": {
                    "type": "string",
                    "description": "İsteğe bağlı çıktı dosya yolu",
                },
            },
            "required": ["source_path", "target_format"],
        },
    },
    {
        "name": "lifeoschat_generate_project",
        "title": "Kod / Çok Dosyalı Proje Üret ve Klasöre Kaydet",
        "description": (
            "Verilen proje/kod talimatını güçlü kodlama modelinde çalıştırır ve yanıttaki tüm kod bloklarını "
            "(` ```dil dosya_adi `) otomatik ayrıştırarak hedef klasöre (`target_dir`) gerçek proje dosyaları olarak yazar."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Üretilecek proje veya kodun detaylı açıklaması",
                },
                "target_dir": {
                    "type": "string",
                    "description": "Proje dosyalarının yazılacağı hedef klasör yolu",
                },
                "provider": {
                    "type": "string",
                    "enum": ["auto", "groq", "cloudflare"],
                    "description": "Tercih edilen sağlayıcı",
                },
            },
            "required": ["prompt", "target_dir"],
        },
    },
    {
        "name": "lifeoschat_quota",
        "title": "Tüm Hesapların Token Tüketimi ve Ücretsiz Kota Durumu",
        "description": (
            "Cloudflare Workers AI (10.000 Neurons/gün ücretsiz kota) ve Groq (model bazlı günlük/dakikalık) "
            "ücretsiz kota durumunu ve bugünkü kullanım istatistiklerini döndürür."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _handle_tool_call(bridge: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "lifeoschat_read_document":
        doc = extract_document_full(str(args["path"]), limit=int(args.get("max_chars") or 80000))
        return {
            "meta": doc.to_meta_dict(),
            "text": doc.text,
        }

    if name == "lifeoschat_create_document":
        out = export_document(
            content=str(args["content"]),
            target_format=str(args["format"]),
            output_path=str(args["output_path"]),
            title=str(args.get("title") or "LifeOS Belgesi"),
        )
        return {
            "success": True,
            "output_path": out,
            "format": str(args["format"]).upper(),
            "size_bytes": Path(out).stat().st_size,
        }

    if name == "lifeoschat_convert_document":
        return convert_document_file(
            source_path=str(args["source_path"]),
            target_format=str(args["target_format"]),
            output_path=args.get("output_path"),
        )

    if name == "lifeoschat_quota":
        return json.loads(bridge.quotaSummaryJson)

    if name == "lifeoschat_models":
        prov_filter = str(args.get("provider") or "all").lower()
        mod_filter = str(args.get("modality") or args.get("Modality") or "all").lower()
        all_models = bridge.get_all_accounts_models_sync(provider_filter=prov_filter)
        filtered = []
        for m in all_models:
            if mod_filter == "vision" and not m.get("hasVision"):
                continue
            if mod_filter == "audio_in" and not m.get("hasAudioIn"):
                continue
            if mod_filter == "audio_out" and not m.get("hasAudioOut"):
                continue
            if mod_filter == "image_out" and not m.get("hasImageOut"):
                continue
            if mod_filter == "coding" and not m.get("hasCoding"):
                continue
            if mod_filter == "reasoning" and not m.get("hasReasoning"):
                continue
            if mod_filter == "chat" and m.get("task") != "text-generation":
                continue
            filtered.append({
                "id": m["id"],
                "title": m["title"],
                "provider": m.get("provider", ""),
                "account_title": m.get("accountTitle", ""),
                "group": m["group"],
                "modality": m["modalityBadge"],
                "tier": m["tier"],
                "context": m.get("contextWindow", ""),
                "limits": m.get("limits", {}),
                "purpose": m["purpose"],
            })
        return {
            "accounts": json.loads(bridge.accountsJson),
            "total_count": len(filtered),
            "models": filtered[:120],
        }

    if name == "lifeoschat_generate_image":
        return bridge.run_sync_image_generation(
            prompt=str(args["prompt"]),
            output_path=str(args.get("output_path") or ""),
            model_id=str(args.get("model") or ""),
        )

    if name == "lifeoschat_tts":
        return bridge.run_sync_tts(
            text=str(args["text"]),
            output_path=str(args.get("output_path") or ""),
        )

    if name == "lifeoschat_transcribe":
        return bridge.run_sync_completion(
            prompt="Bu ses dosyasını eksiksiz yazıya dök.",
            model_id=AUTO_MODEL_ID,
            file_path=str(args["audio_path"]),
            task_hint="automatic-speech-recognition",
        )

    if name == "lifeoschat_generate_project":
        return bridge.run_sync_project_generation(
            prompt=str(args["prompt"]),
            target_dir=str(args["target_dir"]),
            provider=str(args.get("provider") or "auto"),
        )

    if name == "lifeoschat_ask":
        res = bridge.run_sync_completion(
            prompt=str(args.get("prompt") or ""),
            model_id=str(args.get("model") or AUTO_MODEL_ID),
            file_path=str(args.get("file_path") or ""),
            task_hint=str(args.get("task_hint") or "auto"),
            system_prompt=str(args.get("system_prompt") or ""),
            provider=str(args.get("provider") or "auto"),
        )
        save_out = str(args.get("save_output_path") or "").strip()
        if save_out and res.get("response"):
            out_p = Path(save_out)
            ext = out_p.suffix.lower().lstrip(".")
            if ext in ("udf", "docx", "pdf", "rtf", "html", "md", "txt"):
                saved = export_document(res["response"], ext, str(out_p))
            else:
                out_p.parent.mkdir(parents=True, exist_ok=True)
                out_p.write_text(res["response"], encoding="utf-8")
                saved = str(out_p)
            res["saved_output_path"] = saved
        return res

    raise ValueError(f"Bilinmeyen araç: {name}")


def run_mcp_stdio(bridge: Any) -> int:
    """Runs the JSON-RPC 2.0 MCP server loop over stdin/stdout."""
    out_stream = sys.stdout
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue

        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if msg_id is None:
            continue

        def reply(result: Any) -> None:
            payload = {"jsonrpc": "2.0", "id": msg_id, "result": result}
            out_stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            out_stream.flush()

        def reply_error(code: int, message: str) -> None:
            payload = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
            out_stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
            out_stream.flush()

        try:
            if method == "initialize":
                reply({
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": INSTRUCTIONS,
                })
            elif method == "ping":
                reply({})
            elif method == "tools/list":
                reply({"tools": TOOLS})
            elif method == "tools/call":
                tool_name = str(params.get("name") or "")
                tool_args = params.get("arguments") or {}
                result_obj = _handle_tool_call(bridge, tool_name, tool_args)
                reply({
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result_obj, ensure_ascii=False, indent=2),
                        }
                    ],
                    "isError": False,
                })
            else:
                reply_error(-32601, f"Desteklenmeyen metot: {method}")
        except Exception as exc:
            if method == "tools/call":
                reply({
                    "content": [{"type": "text", "text": f"Hata: {exc}"}],
                    "isError": True,
                })
            else:
                reply_error(-32603, str(exc))
    return 0
