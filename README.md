# LifeOS Chat — Yapay Zekâ, Canlı Sesli Sohbet, UYAP (.udf) Belge Motoru & MCP Stüdyosu

KDE Plasma 6 için yerel PySide6/QML masaüstü yapay zekâ çalışma alanı, Gemini Live tarzı **Canlı Sesli Sohbet**, evrak/proje üretim stüdyosu ve çoklu hesap destekli MCP (`lifeoschat`) sunucusu. Cloudflare Workers AI ve Groq modellerini canlı olarak yeteneklerine göre gruplar, işe uygun modeli otomatik seçer (Smart Router), resmî ücretsiz API kotalarını takip eder ve UYAP (`.udf`), Word (`.docx`, `.doc`), `.pdf`, `.rtf`, `.odt`, `.xlsx` belgelerini okuyup oluşturabilir.

## Çalıştırma

```bash
# Masaüstü Arayüzü (GUI)
./run.sh

# MCP Sunucusu (Antigravity `agy` CLI, Claude Code, Codex için stdio JSON-RPC 2.0)
./run.sh --mcp

# Komut Satırından (CLI) Otomatik Modelle Hızlı Soru / Belge Analizi
./run.sh --ask "Ekli dilekçedeki talepleri özetle" --file ornek.udf

# Komut Satırından Evrak Dönüştürme (UDF ↔ DOCX ↔ PDF ↔ RTF)
./run.sh --convert dilekce.udf pdf
```

## Temel Özellikler

1. **🎙️ Canlı Sesli Sohbet (Gemini Live Tarzı Eller Serbest Konuşma)**
   - Üst veya alt çubuktaki **🎙️ Canlı Sesli Sohbet (Live)** butonuyla tam ekran animasyonlu ses küresi açılır.
   - Mikrofondan konuşmanız Groq/Cloudflare **Whisper ASR** ile anında yazıya dökülür, hızlı sohbet modeliyle yanıtlanır ve doğal Türkçe/Cloud TTS ile sesli olarak okunur.
   - **Eller Serbest (Hands-Free)** modu açıkken asistan konuşmasını bitirdiğinde otomatik olarak tekrar dinlemeye geçer.

2. **⚡ İşe Uygun Otomatik Model Seçimi (Smart Router)**
   - Varsayılan `⚡ Otomatik Seçim` modunda gönderdiğiniz dosya ve talimata göre en uygun model otomatik seçilir:
     - **Ses dosyası / ses kaydı** → Whisper (`🎙️ Ses → 📝 Metin`) ile deşifre edilir; soru varsa sohbet modeline aktarılır.
     - **Görsel / Ekran görüntüsü / Taranmış PDF** → Vision/OCR (`🖼️ Görsel + 📝 Metin → 📝 Metin`) modeli seçilir.
     - **"Resim çiz / görsel üret"** → Flux / SDXL (`📝 Metin → 🎨 Görsel`) modeli seçilir.
     - **Kodlama / Proje üretimi** → Qwen Coder / GPT-OSS / Llama 3.3 70B seçilir.
     - **UYAP (`.udf`) / PDF / Word belgesi** → Geniş bağlamlı güçlü muhakeme modeli seçilir.

3. **🗂️ Sağ Çıktı Paneli (Evraklar · Kod & Proje · Görsel & Ses)**
   - Modelin sohbet sırasında ürettiği **dilekçe/sözleşme/rapor taslakları**, **kod blokları / çok dosyalı projeler**, **üretilen görseller/sesler** ve **indirilen evraklar** sağ panelde otomatik listelenir.
   - Çok dosyalı kod bloklarını **📁 Tüm Kodları Klasöre Proje Olarak Çıkar** butonuyla tek tıkla klasöre kaydedebilirsiniz.
   - Her çıktı sağ panelden doğrudan `.udf`, `.docx`, `.pdf`, `.rtf` veya kaynak kod dosyası olarak dışa aktarılabilir.

4. **⚖️ UYAP (`.udf`) ve Ofis Belge Motoru (`documents.py`)**
   - **Okuma:** `.udf` (bozuk ZIP local header kurtarma, Windows-1254/UTF-8 Türkçe karakter çözümleme, tablo okuma ve `sign.sgn` PKCS#7 e-imza / imzacı sertifika tespiti), `.pdf` (metin katmanı + Ghostscript ile taranmış sayfa görselleştirme), `.docx`, `.doc` (Word 97-2003 CFB), `.rtf`, `.odt`, `.xlsx`, `.csv`, `.html`, `.md`, `.txt`.
   - **Temiz Sohbet Görünümü:** Eklenen belgelerin binlerce satırlık metni sohbet ekranına dökülmez; mesaj balonunda şık bir **Belge Kartı** (`📄 dilekce.udf · UDF (UYAP) · 3 sayfa · ✒️ E-İmzalı`) görünür. İstenirse **👁️ Metni Gör** ile incelenebilir.
   - **Tek Tıkla Belge Oluşturma & Dönüştürme:** Asistanın ürettiği her yanıt mesaj altındaki **⚖️ UDF İndir**, **📘 DOCX**, **📕 PDF**, **📝 RTF** butonlarıyla doğrudan UYAP Editör veya Word uyumlu belgeye dönüştürülebilir.

5. **📊 Gerçek Ücretsiz API Kota & Token Limitleri**
   - **Groq Resmî Ücretsiz Kota Tablosu:** Dakikalık burst limiti (`TPM`) ile **Günlük Token Limiti (`TPD`: 100.000 – 500.000 tok/gün)** ve **Günlük İstek Limiti (`RPD`: 1.000 – 14.400 istek/gün)** birbirinden ayrılarak model bazında gerçek zamanlı gösterilir.
   - **Cloudflare Workers AI Ücretsiz Kota:** Günlük **10.000 Neuron / gün** (00:00 UTC sıfırlanmalı) model başına resmî Neuron ağırlıklarıyla hesaplanır.

6. **⚙️ Gelişmiş Ayarlar & Sohbet Yönetimi**
   - Sohbet geçmişindeki her sohbet **✏️ Adı Değiştir** ile yeniden adlandırılabilir.
   - **⚙️ Ayarlar** penceresinden **MCP Sunucusu (`lifeoschat`) Aktif/Pasif** durumu tek tıkla değiştirilebilir, **Sohbet Yazı Boyutu (`11px`–`20px`)**, **Maksimum Yanıt Tokenı (`1024`–`8192`)**, **Düşünce Süreci Gösterimi**, **Canlı Ses Eller Serbest Modu** ve **Özel Sistem Talimatı (System Prompt)** özelleştirilebilir.

7. **🔌 Çoklu Hesap Destekli `lifeoschat` MCP Sunucusu (`mcp_server.py` v2.2)**
   - Kayıtlı tüm **Groq** ve **Cloudflare** hesaplarınızdaki bütün modellere diğer CLI araçlarından (`agy`, Claude Code, Codex) erişim sağlar:
     - `lifeoschat_ask`: İstediğiniz sağlayıcı (`groq` / `cloudflare`), hesap veya modelle (veya `auto` Smart Router ile) metin/kod/evrak üretimi ve gerekirse çıktıyı doğrudan `.udf`/`.docx`/`.pdf` dosyasına kaydetme.
     - `lifeoschat_models`: Kayıtlı tüm hesaplardaki bütün modelleri ve resmî ücretsiz kotalarını listeleme.
     - `lifeoschat_generate_project`: Tek çağrıyla çok dosyalı kod/uygulama projesi üretip hedef klasöre yazma.
     - `lifeoschat_generate_image`: Cloudflare Flux / SDXL modelleriyle görsel üretip PNG kaydetme.
     - `lifeoschat_tts`: Metni ses dosyasına (`.wav`/`.mp3`) dönüştürme.
     - `lifeoschat_transcribe`: Ses dosyasını Whisper ile yazıya çevirme.
     - `lifeoschat_read_document`, `lifeoschat_create_document`, `lifeoschat_convert_document`: UYAP `.udf`, `.docx`, `.pdf`, `.rtf` okuma, oluşturma ve dönüştürme.
     - `lifeoschat_quota`: Günlük ve dakikalık kalan ücretsiz kotaları sorgulama.
