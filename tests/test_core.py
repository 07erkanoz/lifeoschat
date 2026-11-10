import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import (
    conversation_payload,
    default_chat_model,
    embedding_summary,
    estimate_cloudflare_neurons,
    extract_artifacts_from_messages,
    extract_document,
    get_groq_model_limits,
    normalize_groq_model,
    normalize_model,
    parse_response,
    render_message_html,
    select_best_model,
    smart_compress_document,
    split_thinking,
)
from documents import (
    convert_document_file,
    export_document,
    extract_document_full,
)


class CoreAndDocumentTests(unittest.TestCase):
    def test_cloudflare_capabilities_and_modalities(self):
        model = normalize_model({
            "name": "@cf/example/vision-pro",
            "task": "Image-to-Text",
            "description": "OCR and reasoning",
            "tags": ["Function calling"],
        })
        self.assertIn("Görsel", model["category"])
        self.assertIn("OCR / Görsel", model["badges"])
        self.assertIn("Derin düşünme", model["badges"])
        self.assertTrue(model["hasVision"])
        self.assertIn("🖼️ Görsel", model["modalityBadge"])

    def test_groq_speech_and_vision(self):
        asr = normalize_groq_model({"id": "whisper-large-v3"})
        self.assertTrue(asr["hasAudioIn"])
        self.assertIn("🎙️ Ses", asr["modalityBadge"])

        tts = normalize_groq_model({"id": "canopylabs/orpheus-v1-english"})
        self.assertTrue(tts["hasAudioOut"])
        self.assertIn("🔊 Ses", tts["modalityBadge"])

        guard = normalize_groq_model({"id": "llama-guard-3-8b"})
        self.assertEqual(guard["category"], "Sınıflandırma")

    def test_default_is_chat_not_embedding(self):
        embedding = normalize_model({"name": "@cf/baai/bge-base-en-v1.5", "task": "Text Embeddings"})
        chat = normalize_model({"name": "@cf/meta/llama-3.1-8b-instruct", "task": "Text Generation"})
        self.assertEqual(default_chat_model([embedding, chat]), chat["id"])
        self.assertEqual(chat["tier"], "Dengeli")
        self.assertEqual(embedding["tier"], "Uzman")
        self.assertEqual(
            normalize_model({"name": "@cf/cloudflare/clairic", "task": "Text Generation"})["category"],
            "Veri Çıkarma",
        )

    def test_smart_router_auto_model_selection(self):
        models = [
            normalize_groq_model({"id": "whisper-large-v3-turbo"}),
            normalize_groq_model({"id": "llama-3.2-90b-vision-preview"}),
            normalize_model({"name": "@cf/black-forest-labs/flux-1-schnell", "task": "Text-to-Image"}),
            normalize_model({"name": "@cf/qwen/qwen2.5-coder-32b-instruct", "task": "Text Generation"}),
            normalize_groq_model({"id": "llama-3.3-70b-versatile"}),
            normalize_groq_model({"id": "llama-3.1-8b-instant"}),
        ]
        # Audio attachment -> Whisper
        m_id, _ = select_best_model(models, "", {"kind": "audio", "name": "kayit.wav"})
        self.assertEqual(m_id, "whisper-large-v3-turbo")

        # Image attachment -> Vision
        m_id, _ = select_best_model(models, "Bu görseli oku", {"kind": "image", "name": "ekran.png"})
        self.assertEqual(m_id, "llama-3.2-90b-vision-preview")

        # Image generation prompt -> Flux
        m_id, _ = select_best_model(models, "Bana fütüristik bir adliye binası görsel üret")
        self.assertEqual(m_id, "@cf/black-forest-labs/flux-1-schnell")

        # Coding prompt -> Qwen Coder
        m_id, _ = select_best_model(models, "Python ile hızlı bir SQL sorgu fonksiyonu yaz")
        self.assertEqual(m_id, "@cf/qwen/qwen2.5-coder-32b-instruct")

        # Document / Legal analysis -> Llama 3.3 70B
        m_id, _ = select_best_model(models, "Ekli dilekçeyi özetle", {"kind": "document", "name": "dava.udf", "text": "..."})
        self.assertEqual(m_id, "llama-3.3-70b-versatile")

    def test_udf_docx_rtf_pdf_roundtrip_and_conversion(self):
        sample_md = (
            "# ANKARA NÖBETÇİ ASLİYE HUKUK MAHKEMESİNE\n\n"
            "**DAVACI:** Erkan Öz (TC: 12345678901)\n"
            "**KONU:** Fazlaya ilişkin haklarımız saklı kalmak kaydıyla alacak talebidir.\n\n"
            "| Kalem | Tutar | Açıklama |\n"
            "| --- | --- | --- |\n"
            "| Asıl Alacak | 150.000 TL | Fatura bedeli |\n"
            "| Faiz | 18.500 TL | İşlemiş ticari faiz |\n\n"
            "1. Müvekkil ile davalı arasında sözleşme imzalanmıştır.\n"
            "2. Şüpheye yer bırakmayacak şekilde ğüşıöç ĞÜŞİÖÇ Türkçe karakterler korunmalıdır."
        )
        with tempfile.TemporaryDirectory() as tmp:
            # 1. UDF write & read
            udf_path = export_document(sample_md, "udf", str(Path(tmp) / "dilekce.udf"))
            udf_doc = extract_document_full(udf_path)
            self.assertEqual(udf_doc.format_label, "UDF (UYAP)")
            self.assertIn("ANKARA NÖBETÇİ ASLİYE HUKUK MAHKEMESİNE", udf_doc.text)
            self.assertIn("150.000 TL", udf_doc.text)
            self.assertIn("ğüşıöç ĞÜŞİÖÇ", udf_doc.text)

            # 2. DOCX write & read
            docx_path = export_document(sample_md, "docx", str(Path(tmp) / "dilekce.docx"))
            docx_doc = extract_document_full(docx_path)
            self.assertEqual(docx_doc.format_label, "DOCX (Word)")
            self.assertIn("Asıl Alacak", docx_doc.text)
            self.assertIn("ğüşıöç ĞÜŞİÖÇ", docx_doc.text)

            # 3. RTF write & read
            rtf_path = export_document(sample_md, "rtf", str(Path(tmp) / "dilekce.rtf"))
            rtf_doc = extract_document_full(rtf_path)
            self.assertEqual(rtf_doc.format_label, "RTF")
            self.assertIn("ğüşıöç ĞÜŞİÖÇ", rtf_doc.text)

            # 4. PDF write & read
            pdf_path = export_document(sample_md, "pdf", str(Path(tmp) / "dilekce.pdf"))
            pdf_doc = extract_document_full(pdf_path)
            self.assertEqual(pdf_doc.format_label, "PDF")
            self.assertIn("ANKARA", pdf_doc.text)

            # 5. UDF -> DOCX conversion
            conv = convert_document_file(udf_path, "docx")
            self.assertTrue(Path(conv["output_path"]).exists())
            conv_doc = extract_document_full(conv["output_path"])
            self.assertIn("150.000 TL", conv_doc.text)

    def test_embedding_output_is_short(self):
        raw = {"data": [[0.123456] * 768], "shape": [1, 768]}
        summary = embedding_summary(raw)
        self.assertIn("768 boyut", summary)
        self.assertLess(len(summary), 250)
        self.assertEqual(parse_response(json.dumps({"result": raw}).encode(), "application/json")["kind"], "embedding")

    def test_history_document_and_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brief.txt"
            path.write_text("Önemli metin", encoding="utf-8")
            document = extract_document(str(path))
        payload = conversation_payload(
            [{"role": "user", "content": "Merhaba"}, {"role": "assistant", "content": "Selam"}],
            "Özetle",
            {"kind": "document", "name": "brief.txt", "text": document},
        )
        # system + 2 history + 1 new user = 4 messages
        self.assertEqual(len(payload["messages"]), 4)
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertIn("Önemli metin", payload["messages"][-1]["content"])

    def test_parse_response_thinking_truncation_and_tokens(self):
        raw = json.dumps({
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "<think>Önce düşünelim</think>Kesilen yanıt"},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 512, "total_tokens": 632},
        }).encode()
        parsed = parse_response(raw, "application/json")
        self.assertEqual(parsed["text"], "Kesilen yanıt")
        self.assertEqual(parsed["thinking"], "Önce düşünelim")
        self.assertTrue(parsed["truncated"])
        self.assertEqual(parsed["usage"]["total_tokens"], 632)

        html_out = render_message_html(parsed["text"], dark=True, thinking=parsed["thinking"])
        self.assertIn("DÜŞÜNCE SÜRECİ", html_out)
        self.assertIn("Kesilen yanıt", html_out)

    def test_mcp_server_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            udf_target = str(Path(tmp) / "mcp_test.udf")
            reqs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "lifeoschat_create_document",
                        "arguments": {
                            "content": "# YARGITAY DİLEKÇESİ\n\nBu bir **MCP** UDF testidir.",
                            "output_path": udf_target,
                            "format": "udf",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "lifeoschat_read_document",
                        "arguments": {"path": udf_target},
                    },
                },
            ]
            stdin_data = "\n".join(json.dumps(r) for r in reqs) + "\n"
            proc = subprocess.run(
                [sys.executable, "app.py", "--mcp"],
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [json.loads(line) for line in proc.stdout.strip().splitlines() if line.strip()]
            self.assertEqual(len(lines), 4)
            self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "lifeoschat")
            tool_names = [t["name"] for t in lines[1]["result"]["tools"]]
            self.assertIn("lifeoschat_ask", tool_names)
            self.assertIn("lifeoschat_create_document", tool_names)
            self.assertIn("lifeoschat_read_document", tool_names)
            self.assertIn("lifeoschat_generate_project", tool_names)
            self.assertIn("lifeoschat_generate_image", tool_names)
            self.assertIn("lifeoschat_tts", tool_names)
            self.assertIn("lifeoschat_transcribe", tool_names)
            read_result = json.loads(lines[3]["result"]["content"][0]["text"])
            self.assertIn("YARGITAY DİLEKÇESİ", read_result["text"])

    def test_official_quota_limits_and_artifacts_extraction(self):
        # Verify official Groq TPD/RPD/TPM limits
        limits_8b = get_groq_model_limits("llama-3.1-8b-instant")
        self.assertEqual(limits_8b["tpd"], 500000)
        self.assertEqual(limits_8b["rpd"], 14400)
        self.assertEqual(limits_8b["tpm"], 6000)

        limits_gpt = get_groq_model_limits("openai/gpt-oss-120b")
        self.assertEqual(limits_gpt["tpd"], 200000)
        self.assertEqual(limits_gpt["rpd"], 1000)
        self.assertEqual(limits_gpt["tpm"], 8000)

        # Verify Cloudflare Neuron estimation
        neurons = estimate_cloudflare_neurons("@cf/meta/llama-3.3-70b-instruct-fp8-fast", 1000, 1000)
        self.assertGreater(neurons, 50)

        # Verify Right Panel artifact extraction from assistant messages
        msgs = [
            {
                "role": "assistant",
                "model": "openai/gpt-oss-120b",
                "time": "10:15",
                "content": (
                    "# ANKARA ASLİYE HUKUK MAHKEMESİNE\n\n"
                    "**DAVACI:** Erkan Öz\n"
                    "**DAVALI:** Örnek A.Ş.\n"
                    "**KONU:** Alacak davası dilekçesi.\n\n"
                    "Açıklamalar ve hukuki deliller aşağıda sunulmuştur. " * 6
                    + "\n\n```python\n# dosya: hesapla.py\ndef faiz_hesapla(tutar):\n    return tutar * 0.24\n```\n"
                ),
            }
        ]
        artifacts = extract_artifacts_from_messages(msgs)
        kinds = [a["kind"] for a in artifacts]
        self.assertIn("code", kinds)
        self.assertIn("document", kinds)
        code_art = next(a for a in artifacts if a["kind"] == "code")
        self.assertEqual(code_art["title"], "hesapla.py")

        # Verify large legal UDF document smart compression preserves both Head (Esas/Karar) and Tail (HÜKÜM)
        huge_decision = (
            "T.C. ANKARA 4. ASLİYE TİCARET MAHKEMESİ\nESAS NO: 2025/104\nKARAR NO: 2026/88\n"
            + ("Duruşma tutanakları ve ara kararlar detayı. " * 700)
            + "\n\nGEREKÇE:\nBilirkişi kök ve ek raporları incelenmiş, davacının alacağı tespit edilmiştir.\n\n"
            + ("Sözleşme hükümleri ve ticari defter kayıtları incelemesi. " * 400)
            + "\n\nHÜKÜM:\n1- Davanın KABULÜNE, 450.000,00 TL alacağın davalıdan tahsiline karar verildi."
        )
        compressed = smart_compress_document(huge_decision, max_chars=12000, query="inceler misin")
        self.assertLessEqual(len(compressed), 13000)
        self.assertIn("ESAS NO: 2025/104", compressed)
        self.assertIn("Bilirkişi kök ve ek raporları", compressed)
        self.assertIn("Davanın KABULÜNE, 450.000,00 TL", compressed)


if __name__ == "__main__":
    unittest.main()
