import QtQuick
import QtQuick.Controls as C
import QtQuick.Layouts
import QtQuick.Dialogs
import QtMultimedia
import org.kde.kirigami as Kirigami

C.ApplicationWindow {
    id: root
    visible: true
    width: 1560; height: 950
    minimumWidth: 1120; minimumHeight: 680
    title: "LifeOS Chat — Yapay Zekâ, UYAP Belge & Canlı Ses Stüdyosu"
    font.family: "Inter, Noto Sans"

    readonly property bool dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5
    readonly property color bg: dark ? "#0b0b10" : "#f7f6fb"
    readonly property color side: dark ? "#100f17" : "#efedf6"
    readonly property color card: dark ? "#161521" : "#ffffff"
    readonly property color card2: dark ? "#1f1d2d" : "#f2effa"
    readonly property color card3: dark ? "#28253a" : "#e8e3f5"
    readonly property color line: dark ? "#2b283d" : "#dfdbe9"
    readonly property color ink: dark ? "#f4f2fc" : "#1d1928"
    readonly property color muted: dark ? "#9a94b0" : "#6d6680"
    readonly property color purple: dark ? "#a888f8" : "#6b46c1"
    readonly property color purpleSoft: dark ? "#281f42" : "#ece5fc"
    readonly property color cyanAccent: dark ? "#4fd1c5" : "#2b6cb0"
    readonly property color cyanSoft: dark ? "#163036" : "#e6fffa"
    readonly property color greenAccent: dark ? "#68d391" : "#2f855a"
    readonly property color greenSoft: dark ? "#183328" : "#f0fff4"
    readonly property color amberAccent: dark ? "#f6ad55" : "#c05621"
    readonly property color amberSoft: dark ? "#382819" : "#fffaf0"
    color: bg

    property var accounts: JSON.parse(bridge.accountsJson)
    property var chats: JSON.parse(bridge.chatsJson)
    property var messages: JSON.parse(bridge.messagesJson)
    property var models: JSON.parse(bridge.modelsJson)
    property var quota: JSON.parse(bridge.quotaSummaryJson)
    property var attachmentPreview: JSON.parse(bridge.attachmentPreviewJson)
    property var artifacts: JSON.parse(bridge.artifactsJson)
    property var appSettings: JSON.parse(bridge.settingsJson)

    property string selectedModel: "__auto__"
    property string selectedFile: ""
    property string filterText: ""
    property string filterModality: "Tümü"
    property string filterTier: "Tümü"
    property string artifactTab: "Tümü"
    property bool libraryOpen: false
    property bool rightPanelOpen: true
    property bool rawOpen: false
    property string statusText: ""
    property string previewModalTitle: ""
    property string previewModalText: ""
    property int pendingExportMessageId: -1
    property string pendingExportFormat: "udf"
    property string pendingArtifactTitle: ""
    property string pendingArtifactContent: ""
    property string renameTargetChatId: ""
    property string renameTargetCurrentTitle: ""

    property var composerInput: null
    property var rawInput: null
    property var voiceInput: null
    property var messageList: null

    property var activeChat: chats.find(c => c.id === bridge.chatId) || null
    property var activeAccount: accounts.find(a => a.id === bridge.activeAccount) || null
    property var selectedModelInfo: models.find(m => m.id === selectedModel) || null

    property var filteredArtifacts: artifacts.filter(a => {
        if (artifactTab === "Tümü") return true
        if (artifactTab === "Evraklar") return a.category === "document"
        if (artifactTab === "Kod/Proje") return a.category === "code"
        if (artifactTab === "Medya") return a.category === "media"
        return true
    })

    property var modelComboItems: {
        const list = [{
            id: "__auto__",
            label: "⚡ Otomatik Seçim (İşe Uygun Model — Ses / Görsel / Belge / Kod / Metin)",
            purpose: "Gönderdiğiniz dosya (UDF, PDF, DOCX, Görsel, Ses) ve soruya göre en uygun modeli otomatik seçer.",
            tierReason: "Akıllı Yönlendirici (Smart Router) aktif"
        }]
        for (let i = 0; i < models.length; i++) {
            const m = models[i]
            list.push({
                id: m.id,
                label: m.modalityBadge + "  ·  " + m.title + "  (" + m.tier + (m.contextWindow ? " · " + m.contextWindow : "") + ")",
                purpose: m.purpose,
                tierReason: m.tierReason
            })
        }
        return list
    }

    function matchesModalityFilter(m, modFilter) {
        if (modFilter === "Tümü") return true
        if (modFilter === "Metin & Sohbet") return m.task === "text-generation" && !m.hasReasoning && !m.hasCoding
        if (modFilter === "Derin Düşünme") return !!m.hasReasoning
        if (modFilter === "Kodlama") return !!m.hasCoding
        if (modFilter === "Görsel & OCR") return !!m.hasVision
        if (modFilter === "Resim Üret") return !!m.hasImageOut
        if (modFilter === "Ses → Metin") return !!m.hasAudioIn
        if (modFilter === "Metin → Ses") return !!m.hasAudioOut
        if (modFilter === "Embedding") return m.task === "text-embeddings"
        return true
    }

    property var filteredModels: models.filter(m =>
        matchesModalityFilter(m, filterModality) &&
        (filterTier === "Tümü" || m.tier === filterTier) &&
        (!filterText || (m.id + " " + m.description + " " + m.category + " " + m.group + " " + m.modalityBadge + " " + m.badges.join(" ")).toLowerCase().includes(filterText.toLowerCase()))
    )
    property var visibleGroups: filteredModels.map(m => m.group).filter((name, index, all) => all.indexOf(name) === index)

    Component.onCompleted: bridge.setDarkMode(root.dark)
    onDarkChanged: bridge.setDarkMode(root.dark)

    Connections {
        target: bridge
        function onAccountsChanged() { root.accounts = JSON.parse(bridge.accountsJson) }
        function onChatsChanged() { root.chats = JSON.parse(bridge.chatsJson) }
        function onQuotaChanged() { root.quota = JSON.parse(bridge.quotaSummaryJson) }
        function onPreviewChanged() { root.attachmentPreview = JSON.parse(bridge.attachmentPreviewJson) }
        function onSettingsChanged() { root.appSettings = JSON.parse(bridge.settingsJson) }
        function onArtifactsChanged() {
            const prevCount = root.artifacts ? root.artifacts.length : 0
            root.artifacts = JSON.parse(bridge.artifactsJson)
            if (root.artifacts.length > prevCount && root.appSettings.autoOpenRightPanel) {
                root.rightPanelOpen = true
            }
        }
        function onBusyChanged() {
            if (bridge.busy) {
                Qt.callLater(() => { if (root.messageList) root.messageList.positionViewAtEnd() })
            }
        }
        function onMessagesChanged() {
            root.messages = JSON.parse(bridge.messagesJson)
            if (root.activeChat && root.activeChat.model) root.selectedModel = root.activeChat.model
            Qt.callLater(() => {
                if (!root.messageList || root.messages.length === 0) return
                const lastMsg = root.messages[root.messages.length - 1]
                if (lastMsg && lastMsg.role === "assistant") {
                    root.messageList.positionViewAtIndex(root.messages.length - 1, ListView.Beginning)
                } else {
                    root.messageList.positionViewAtEnd()
                }
            })
        }
        function onModelsChanged() {
            root.models = JSON.parse(bridge.modelsJson)
            if (root.selectedModel !== "__auto__" && !root.models.some(m => m.id === root.selectedModel)) {
                root.selectedModel = (root.activeChat && (root.activeChat.model === "__auto__" || root.models.some(m => m.id === root.activeChat.model)))
                    ? root.activeChat.model : "__auto__"
            }
        }
        function onNotice(message) { root.statusText = message; toastTimer.restart() }
        function onResultReady(result) {
            if (root.composerInput) root.composerInput.text = ""
            root.selectedFile = ""
            bridge.clearAttachment()
        }
        function onRecorded(url) {
            root.selectedFile = url
            root.statusText = "🎙️ Ses kaydı mesaja eklendi (Otomatik Whisper ile çözümlenecek)."
            toastTimer.restart()
        }
        function onLiveAudioReady(audioUrl, replyText) {
            liveAudioPlayer.source = audioUrl
            liveAudioPlayer.play()
        }
    }

    MediaPlayer {
        id: liveAudioPlayer
        audioOutput: AudioOutput {}
        onPlaybackStateChanged: {
            if (playbackState === MediaPlayer.StoppedState && bridge.liveVoiceActive && bridge.liveVoiceState === "speaking") {
                bridge.onLiveSpeechFinished()
            }
        }
    }

    Timer { id: toastTimer; interval: 5500; onTriggered: root.statusText = "" }
    Shortcut { sequence: "Ctrl+N"; onActivated: root.startChat("__auto__") }
    Shortcut { sequence: "Ctrl+K"; onActivated: libraryOpen = !libraryOpen }

    function startChat(preferredModel) {
        if (!bridge.activeAccount) { accountDialog.open(); return }
        selectedModel = preferredModel || "__auto__"
        bridge.newChat(bridge.activeAccount, selectedModel)
        libraryOpen = false
        Qt.callLater(() => { if (root.composerInput) root.composerInput.forceActiveFocus() })
    }

    function sendNow() {
        if (bridge.busy) return
        if (!bridge.activeAccount) { accountDialog.open(); return }
        const textToSend = composerInput ? composerInput.text : ""
        const fileToSend = selectedFile
        const rawToSend = rawOpen && rawInput ? rawInput.text : ""
        const voiceToSend = voiceInput ? voiceInput.text : ""
        if (!textToSend.trim() && !fileToSend && !rawToSend.trim()) return

        if (!bridge.chatId) startChat(selectedModel)
        bridge.chooseModel(bridge.activeAccount, selectedModel || "__auto__")

        // Immediately clear input box and attachment bar so user sees instant feedback
        if (composerInput) composerInput.text = ""
        selectedFile = ""
        bridge.send(textToSend, fileToSend, rawToSend, voiceToSend)
        Qt.callLater(() => { if (root.messageList) root.messageList.positionViewAtEnd() })
    }

    function requestExport(messageId, fmt) {
        root.pendingExportMessageId = messageId
        root.pendingExportFormat = fmt
        exportSaveDialog.nameFilters = [fmt.toUpperCase() + " Belgesi (*." + fmt + ")"]
        exportSaveDialog.currentFile = "file:///home/erkanoz/belge_" + messageId + "." + fmt
        exportSaveDialog.open()
    }

    function openRenameModal(chatId, currentTitle) {
        root.renameTargetChatId = chatId
        root.renameTargetCurrentTitle = currentTitle
        renameField.text = currentTitle
        renameChatDialog.open()
        Qt.callLater(() => { renameField.forceActiveFocus(); renameField.selectAll() })
    }

    RowLayout {
        anchors.fill: parent; spacing: 0

        // ======================== LEFT SIDEBAR ========================
        Rectangle {
            Layout.preferredWidth: 274; Layout.fillHeight: true; color: side
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: line }

            ColumnLayout {
                anchors.fill: parent; anchors.margins: 16; spacing: 7

                // Brand Header
                RowLayout {
                    Layout.fillWidth: true; Layout.bottomMargin: 8; spacing: 12
                    Rectangle {
                        width: 40; height: 40; radius: 12
                        gradient: Gradient {
                            GradientStop { position: 0.0; color: purple }
                            GradientStop { position: 1.0; color: "#553c9a" }
                        }
                        C.Label { anchors.centerIn: parent; text: "✦"; color: "white"; font.pixelSize: 22 }
                    }
                    ColumnLayout {
                        spacing: 1
                        C.Label { text: "LifeOS Chat"; color: ink; font.pixelSize: 17; font.bold: true }
                        C.Label { text: "AI · UYAP UDF · Live · MCP"; color: muted; font.pixelSize: 11 }
                    }
                }

                ActionButton {
                    Layout.fillWidth: true; implicitHeight: 38
                    accentStyle: true
                    text: "＋   Yeni Sohbet Başlat"
                    onClicked: root.startChat("__auto__")
                }

                C.Label {
                    text: "ÇALIŞMA ALANI"; color: muted; font.pixelSize: 10; font.bold: true
                    font.letterSpacing: 1.1; Layout.leftMargin: 8; Layout.topMargin: 4
                }
                NavButton { title: "Sohbet & Belge Stüdyosu"; symbol: "◉"; active: !libraryOpen; onClicked: libraryOpen = false }
                NavButton { title: "Model Kütüphanesi (" + models.length + ")"; symbol: "▦"; active: libraryOpen; onClicked: libraryOpen = true }
                NavButton { title: "🎙️ Canlı Sesli Sohbet (Live)"; symbol: "◎"; active: bridge.liveVoiceActive; onClicked: bridge.startLiveVoiceSession() }
                NavButton { title: "UDF / DOCX / PDF Dönüştür"; symbol: "⇄"; active: false; onClicked: converterDialog.open() }
                NavButton { title: "Ayarlar & MCP (" + (appSettings.mcpEnabled ? "Aktif" : "Kapalı") + ")"; symbol: "⚙"; active: false; onClicked: settingsDialog.open() }

                RowLayout {
                    Layout.fillWidth: true; Layout.leftMargin: 8; Layout.rightMargin: 4; Layout.topMargin: 4
                    C.Label { text: "SOHBET GEÇMİŞİ"; color: muted; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.1 }
                    Item { Layout.fillWidth: true }
                    Rectangle {
                        radius: 8; color: card2; implicitWidth: 24; implicitHeight: 18
                        C.Label { anchors.centerIn: parent; text: chats.length; color: muted; font.pixelSize: 10; font.bold: true }
                    }
                }

                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                    model: chats
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width; height: 46; radius: 11
                        color: bridge.chatId === modelData.id && !libraryOpen ? purpleSoft : chatHover.hovered ? card2 : "transparent"
                        border.color: bridge.chatId === modelData.id && !libraryOpen ? purple : "transparent"
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 150 } }

                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 6; spacing: 7
                            C.Label {
                                text: modelData.model === "__auto__" ? "⚡" : "◌"
                                color: bridge.chatId === modelData.id ? purple : muted
                                font.pixelSize: 14
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 1
                                C.Label {
                                    Layout.fillWidth: true; text: modelData.title
                                    color: ink; elide: Text.ElideRight; font.pixelSize: 12
                                    font.bold: bridge.chatId === modelData.id
                                }
                                C.Label {
                                    Layout.fillWidth: true
                                    text: modelData.model === "__auto__" ? "Otomatik Model" : (modelData.model || "").split("/").pop()
                                    color: muted; elide: Text.ElideRight; font.pixelSize: 10
                                }
                            }
                            C.ToolButton {
                                visible: chatHover.hovered || bridge.chatId === modelData.id
                                text: "✎"
                                implicitWidth: 24; implicitHeight: 24
                                onClicked: root.openRenameModal(modelData.id, modelData.title)
                                C.ToolTip.text: "Sohbet adını değiştir"
                                C.ToolTip.visible: hovered
                            }
                            C.ToolButton {
                                visible: chatHover.hovered
                                text: "×"
                                implicitWidth: 24; implicitHeight: 24
                                onClicked: bridge.deleteChat(modelData.id)
                                C.ToolTip.text: "Sohbeti sil"
                                C.ToolTip.visible: hovered
                            }
                        }
                        HoverHandler { id: chatHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: { bridge.openChat(modelData.id); libraryOpen = false }
                            onDoubleTapped: root.openRenameModal(modelData.id, modelData.title)
                        }
                    }
                }

                // Live Quota & Token Summary Card in Sidebar (Accurate Daily vs Minute Limits!)
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: quotaCol.implicitHeight + 18
                    radius: 12; color: card; border.color: line
                    ColumnLayout {
                        id: quotaCol
                        anchors.fill: parent; anchors.margins: 9; spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                            C.Label { text: "📊 ÜCRETSİZ KOTA & TOKEN"; color: purple; font.pixelSize: 10; font.bold: true }
                            Item { Layout.fillWidth: true }
                            C.Label {
                                text: "Bugün: " + (quota.todayTotalTokens || 0) + " tok"
                                color: ink; font.pixelSize: 10; font.bold: true
                            }
                        }
                        Rectangle {
                            Layout.fillWidth: true; height: 6; radius: 3; color: card2
                            Rectangle {
                                height: parent.height; radius: 3
                                width: Math.max(4, parent.width * Math.min(1, (quota.quotaPercent || 0) / 100.0))
                                color: (quota.quotaPercent || 0) > 80 ? amberAccent : greenAccent
                                Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                            }
                        }
                        C.Label {
                            Layout.fillWidth: true
                            text: quota.quotaBadge || "Kota bilgisi hazır"
                            color: ink; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight
                        }
                        C.Label {
                            visible: !!quota.minuteBadge
                            Layout.fillWidth: true
                            text: quota.minuteBadge || ""
                            color: muted; font.pixelSize: 10; elide: Text.ElideRight
                        }
                    }
                    TapHandler { onTapped: quotaDialog.open() }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }

                ActionButton {
                    Layout.fillWidth: true
                    text: activeAccount ? (activeAccount.provider === "groq" ? "🟢  Groq · " : "🟠  Cloudflare · ") + activeAccount.title : "＋  API Hesabı Ekle"
                    onClicked: accountDialog.open()
                }
            }
        }

        // ======================== MAIN CONTENT AREA ========================
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0

            // Top Header Bar
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 66; color: bg
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: line }

                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22; spacing: 10
                    ColumnLayout {
                        spacing: 2
                        RowLayout {
                            spacing: 6
                            C.Label {
                                text: libraryOpen ? "Model Kütüphanesi & Yetenek Rehberi" : (activeChat ? activeChat.title : "Yeni Bir Görevle Başlayın")
                                color: ink; font.pixelSize: 17; font.bold: true; elide: Text.ElideRight; Layout.maximumWidth: 380
                            }
                            ActionButton {
                                visible: !libraryOpen && !!activeChat
                                implicitHeight: 24; leftPadding: 7; rightPadding: 7
                                text: "✎ Adı Değiştir"
                                onClicked: root.openRenameModal(activeChat.id, activeChat.title)
                            }
                        }
                        C.Label {
                            text: libraryOpen
                                ? models.length + " model · Ses, Görüntü, Metin ve Kodlama yeteneklerine göre gruplandı"
                                : (selectedModel === "__auto__"
                                    ? "⚡ Otomatik Model Seçimi aktif · UDF, DOCX, PDF, Kod/Proje, Görsel ve Canlı Ses destekli"
                                    : (selectedModelInfo ? selectedModelInfo.modalityBadge + " · " + selectedModelInfo.purpose : "Model seçin"))
                            color: muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.maximumWidth: 520
                        }
                    }

                    Item { Layout.fillWidth: true }

                    // Live Generating Pill
                    Rectangle {
                        visible: bridge.busy
                        radius: 14; color: purpleSoft; border.color: purple
                        implicitHeight: 32; implicitWidth: busyRow.implicitWidth + 22
                        RowLayout {
                            id: busyRow; anchors.centerIn: parent; spacing: 8
                            C.BusyIndicator { running: bridge.busy; implicitWidth: 18; implicitHeight: 18 }
                            C.Label { text: bridge.busyStage || "Çalışıyor…"; color: purple; font.pixelSize: 11; font.bold: true }
                        }
                    }

                    ActionButton {
                        accentStyle: bridge.liveVoiceActive
                        text: "🎙️ Canlı Ses (Live)"
                        onClicked: bridge.startLiveVoiceSession()
                    }
                    ActionButton {
                        text: "📊 Kota (" + (quota.chatTotalTokens || 0) + ")"
                        onClicked: quotaDialog.open()
                    }
                    ActionButton {
                        accentStyle: rightPanelOpen
                        text: "📂 Çıktılar & Evraklar (" + artifacts.length + ")"
                        onClicked: rightPanelOpen = !rightPanelOpen
                    }
                    ActionButton {
                        text: "⚙ Ayarlar"
                        onClicked: settingsDialog.open()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0

                Loader {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    sourceComponent: libraryOpen ? libraryPage : chatPage
                }

                // ======================== RIGHT PANEL: ARTIFACTS, DOCUMENTS, CODE & MEDIA ========================
                Rectangle {
                    visible: rightPanelOpen && !libraryOpen
                    Layout.preferredWidth: visible ? 348 : 0
                    Layout.fillHeight: true
                    color: side
                    Rectangle { anchors.left: parent.left; width: 1; height: parent.height; color: line }

                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            C.Label { text: "📂 ÜRETİLEN EVRAK, KOD & MEDYA"; color: ink; font.pixelSize: 12; font.bold: true }
                            Item { Layout.fillWidth: true }
                            C.ToolButton { text: "×"; onClicked: rightPanelOpen = false }
                        }

                        // Filter Tabs for Right Panel
                        RowLayout {
                            Layout.fillWidth: true; spacing: 5
                            Repeater {
                                model: ["Tümü", "Evraklar", "Kod/Proje", "Medya"]
                                delegate: FilterPill {
                                    required property string modelData
                                    label: modelData
                                    selected: artifactTab === modelData
                                    onClicked: artifactTab = modelData
                                }
                            }
                        }

                        // Export All Code as Project Button
                        ActionButton {
                            visible: artifacts.some(a => a.category === "code")
                            Layout.fillWidth: true; accentStyle: true
                            text: "📦 Tüm Kodları Proje Klasörüne Çıkar"
                            onClicked: projectFolderDialog.open()
                        }

                        ListView {
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 10
                            model: filteredArtifacts
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view.width
                                implicitHeight: artCol.implicitHeight + 22
                                radius: 13; color: card; border.color: line

                                ColumnLayout {
                                    id: artCol
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.margins: 11; spacing: 7

                                    RowLayout {
                                        Layout.fillWidth: true; spacing: 8
                                        Rectangle {
                                            width: 30; height: 30; radius: 8
                                            color: modelData.category === "document" ? purpleSoft : (modelData.category === "code" ? cyanSoft : greenSoft)
                                            C.Label {
                                                anchors.centerIn: parent
                                                text: modelData.category === "document" ? "⚖️" : (modelData.category === "code" ? "💻" : (modelData.kind === "image" ? "🎨" : "🔊"))
                                                font.pixelSize: 14
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 1
                                            C.Label { Layout.fillWidth: true; text: modelData.title; color: ink; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight }
                                            C.Label { Layout.fillWidth: true; text: modelData.badge; color: muted; font.pixelSize: 10; elide: Text.ElideRight }
                                        }
                                    }

                                    // Thumbnail if image artifact
                                    Image {
                                        visible: modelData.kind === "image" && !!modelData.media
                                        Layout.fillWidth: true; Layout.preferredHeight: visible ? 140 : 0
                                        fillMode: Image.PreserveAspectFit
                                        source: visible ? modelData.media : ""
                                    }

                                    // Code / Document snippet preview
                                    Rectangle {
                                        visible: !!modelData.content && modelData.category !== "media"
                                        Layout.fillWidth: true; implicitHeight: 54; radius: 8; color: card2
                                        C.Label {
                                            anchors.fill: parent; anchors.margins: 7
                                            text: (modelData.content || "").slice(0, 180).replace(/\n/g, " ")
                                            color: muted; font.pixelSize: 10; font.family: modelData.category === "code" ? "monospace" : "Inter"
                                            wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight
                                        }
                                    }

                                    // Actions per artifact type
                                    Flow {
                                        Layout.fillWidth: true; spacing: 6
                                        ActionButton {
                                            visible: !!modelData.content
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "👁️ İncele"
                                            onClicked: {
                                                root.previewModalTitle = modelData.title
                                                root.previewModalText = modelData.content
                                                docPreviewDialog.open()
                                            }
                                        }
                                        ActionButton {
                                            visible: !!modelData.content
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "📋 Kopyala"
                                            onClicked: bridge.copyText(modelData.content)
                                        }
                                        ActionButton {
                                            visible: modelData.category === "document" && !!modelData.messageId
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "⚖️ UDF"
                                            onClicked: root.requestExport(modelData.messageId, "udf")
                                        }
                                        ActionButton {
                                            visible: modelData.category === "document" && !!modelData.messageId
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "📘 DOCX"
                                            onClicked: root.requestExport(modelData.messageId, "docx")
                                        }
                                        ActionButton {
                                            visible: modelData.category === "document" && !!modelData.messageId
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "📕 PDF"
                                            onClicked: root.requestExport(modelData.messageId, "pdf")
                                        }
                                        ActionButton {
                                            visible: modelData.category === "code"
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "💾 Kaydet"
                                            onClicked: {
                                                root.pendingArtifactTitle = modelData.title
                                                root.pendingArtifactContent = modelData.content
                                                artifactSaveDialog.currentFile = "file:///home/erkanoz/" + modelData.title
                                                artifactSaveDialog.open()
                                            }
                                        }
                                        ActionButton {
                                            visible: !!(modelData.path || modelData.media)
                                            implicitHeight: 26; leftPadding: 8; rightPadding: 8
                                            text: "📂 Aç"
                                            onClicked: bridge.openFileExternally(modelData.path || modelData.media)
                                        }
                                    }
                                }
                            }

                            C.Label {
                                anchors.centerIn: parent
                                width: parent.width - 24
                                visible: filteredArtifacts.length === 0
                                text: "Modelin ürettiği dilekçeler, UDF/DOCX/PDF evraklar, kod blokları, proje dosyaları ve görseller burada listelenir."
                                color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                            }
                        }
                    }
                }
            }
        }
    }

    // ======================== GEMINI LIVE STYLE VOICE OVERLAY ========================
    Rectangle {
        id: liveVoiceOverlay
        visible: bridge.liveVoiceActive
        anchors.fill: parent
        color: "#e80b0b12"
        z: 90

        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 80, 680)
            spacing: 24

            C.Label {
                Layout.fillWidth: true
                text: "🎙️ LifeOS Canlı Sesli Sohbet (Live)"
                color: "white"; font.pixelSize: 22; font.bold: true; horizontalAlignment: Text.AlignHCenter
            }

            C.Label {
                Layout.fillWidth: true
                text: bridge.liveVoiceState === "listening"
                    ? "🟢 Sizi Dinliyor… Konuşmanız bitince 'Yanıtla' butonuna basın"
                    : bridge.liveVoiceState === "thinking"
                        ? "🧠 Sesi Çözümlüyor ve Yanıt Hazırlıyor…"
                        : bridge.liveVoiceState === "speaking"
                            ? "🔊 Asistan Konuşuyor…"
                            : "Hazır — Konuşmak için 'Dinle' butonuna basın"
                color: bridge.liveVoiceState === "listening" ? greenAccent : (bridge.liveVoiceState === "thinking" ? amberAccent : purple)
                font.pixelSize: 15; font.bold: true; horizontalAlignment: Text.AlignHCenter
            }

            // Animated Pulsing Voice Orb
            Item {
                Layout.alignment: Qt.AlignHCenter
                width: 190; height: 190

                Rectangle {
                    id: outerRing
                    anchors.centerIn: parent
                    width: 180; height: 180; radius: 90
                    color: "transparent"
                    border.color: bridge.liveVoiceState === "listening" ? greenAccent : purple
                    border.width: 2
                    opacity: 0.35
                    SequentialAnimation on scale {
                        running: bridge.liveVoiceActive
                        loops: Animation.Infinite
                        NumberAnimation { from: 0.88; to: 1.15; duration: 950; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.15; to: 0.88; duration: 950; easing.type: Easing.InOutSine }
                    }
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 128; height: 128; radius: 64
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: bridge.liveVoiceState === "listening" ? "#38a169" : (bridge.liveVoiceState === "thinking" ? "#dd6b20" : purple) }
                        GradientStop { position: 1.0; color: "#322659" }
                    }
                    C.Label {
                        anchors.centerIn: parent
                        text: bridge.liveVoiceState === "listening" ? "🎙️" : (bridge.liveVoiceState === "thinking" ? "✦" : "🔊")
                        font.pixelSize: 44
                    }
                }
            }

            // Live Transcript & Spoken Reply Subtitles
            Rectangle {
                visible: !!bridge.liveTranscript
                Layout.fillWidth: true; implicitHeight: trLbl.implicitHeight + 24; radius: 14
                color: "#1e1c2e"; border.color: line
                C.Label {
                    id: trLbl; anchors.fill: parent; anchors.margins: 12
                    text: "👤 Siz: \"" + bridge.liveTranscript + "\""
                    color: "#d6d2e8"; font.pixelSize: 14; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                }
            }

            Rectangle {
                visible: !!bridge.liveReply
                Layout.fillWidth: true; implicitHeight: Math.min(220, repLbl.implicitHeight + 28); radius: 14
                color: "#251f3d"; border.color: purple
                C.Label {
                    id: repLbl; anchors.fill: parent; anchors.margins: 14
                    text: bridge.liveReply
                    color: "white"; font.pixelSize: 15; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignHCenter; spacing: 14

                ActionButton {
                    visible: bridge.liveVoiceState === "listening"
                    accentStyle: true; implicitHeight: 44; leftPadding: 20; rightPadding: 20
                    text: "✅ Konuşmayı Bitir & Yanıtla"
                    onClicked: bridge.finishLiveListeningAndRespond()
                }

                ActionButton {
                    visible: bridge.liveVoiceState !== "listening" && !bridge.busy
                    accentStyle: true; implicitHeight: 44; leftPadding: 20; rightPadding: 20
                    text: "🎙️ Şimdi Konuş"
                    onClicked: {
                        liveAudioPlayer.stop()
                        bridge.startLiveListening()
                    }
                }

                ActionButton {
                    visible: bridge.liveVoiceState === "speaking"
                    implicitHeight: 44; leftPadding: 16; rightPadding: 16
                    text: "⏹ Sesi Sustur"
                    onClicked: {
                        liveAudioPlayer.stop()
                        bridge.onLiveSpeechFinished()
                    }
                }

                ActionButton {
                    implicitHeight: 44; leftPadding: 18; rightPadding: 18
                    text: "✕ Canlı Sohbeti Kapat"
                    onClicked: {
                        liveAudioPlayer.stop()
                        bridge.stopLiveVoiceSession()
                    }
                }
            }
        }
    }

    // ======================== REUSABLE UI COMPONENTS ========================
    component NavButton: Rectangle {
        property string title
        property string symbol
        property bool active
        signal clicked()
        Layout.fillWidth: true; height: 38; radius: 10
        color: active ? purpleSoft : navHover.hovered ? card2 : "transparent"
        border.color: active ? purple : "transparent"
        Behavior on color { ColorAnimation { duration: 150 } }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 12; spacing: 10
            C.Label { text: symbol; color: active ? purple : muted; font.pixelSize: 15 }
            C.Label { text: title; color: active ? purple : ink; font.pixelSize: 12; font.bold: active; elide: Text.ElideRight; Layout.fillWidth: true }
        }
        HoverHandler { id: navHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: parent.clicked() }
    }

    component ActionButton: C.Button {
        id: action
        property bool accentStyle: false
        implicitHeight: 34
        leftPadding: 12; rightPadding: 12
        font.pixelSize: 11
        font.bold: true
        scale: action.down ? 0.97 : (action.hovered ? 1.02 : 1.0)
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutQuad } }
        contentItem: C.Label {
            text: action.text
            color: !action.enabled ? muted : (action.accentStyle ? "white" : (action.down || action.hovered ? purple : ink))
            font: action.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: action.accentStyle
                ? (action.down ? "#553c9a" : action.hovered ? "#805ad5" : purple)
                : (action.down ? purpleSoft : action.hovered ? purpleSoft : card)
            border.color: action.accentStyle ? purple : (action.hovered ? purple : line)
            opacity: action.enabled ? 1 : 0.5
            Behavior on color { ColorAnimation { duration: 150 } }
            Behavior on border.color { ColorAnimation { duration: 150 } }
        }
    }

    component FilterPill: Rectangle {
        property string label
        property bool selected: false
        signal clicked()
        implicitHeight: 28; implicitWidth: pillLabel.implicitWidth + 18; radius: 14
        color: selected ? purple : (pillHover.hovered ? card2 : card)
        border.color: selected ? purple : line
        Behavior on color { ColorAnimation { duration: 140 } }
        C.Label {
            id: pillLabel; anchors.centerIn: parent; text: parent.label
            color: parent.selected ? "white" : ink; font.pixelSize: 11; font.bold: parent.selected
        }
        HoverHandler { id: pillHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: parent.clicked() }
    }

    // ======================== MODEL LIBRARY PAGE ========================
    Component {
        id: libraryPage
        ColumnLayout {
            spacing: 0

            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.topMargin: 18; Layout.bottomMargin: 10; spacing: 12
                C.TextField {
                    Layout.fillWidth: true; implicitHeight: 38
                    placeholderText: "🔍 Model adı, görev (ses, ocr, görsel, kod, derin düşünme) ara..."
                    text: filterText; onTextChanged: filterText = text
                }
                C.ComboBox {
                    Layout.preferredWidth: 160
                    model: ["Tümü", "Güçlü", "Dengeli", "Hafif", "Uzman"]
                    onCurrentTextChanged: filterTier = currentText
                }
            }

            Flow {
                Layout.fillWidth: true; Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.bottomMargin: 12; spacing: 8
                Repeater {
                    model: ["Tümü", "Metin & Sohbet", "Derin Düşünme", "Kodlama", "Görsel & OCR", "Resim Üret", "Ses → Metin", "Metin → Ses", "Embedding"]
                    delegate: FilterPill {
                        required property string modelData
                        label: modelData
                        selected: filterModality === modelData
                        onClicked: filterModality = modelData
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: line }

            Flickable {
                id: groupScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                contentWidth: width; contentHeight: groupColumn.implicitHeight + 36
                C.ScrollBar.vertical: C.ScrollBar {
                    policy: C.ScrollBar.AlwaysOn; width: 9
                    contentItem: Rectangle { radius: 4; color: root.purple; opacity: 0.55 }
                }

                ColumnLayout {
                    id: groupColumn; x: 24; width: groupScroll.width - 48; spacing: 8
                    Repeater {
                        model: visibleGroups
                        delegate: ColumnLayout {
                            id: groupSection
                            required property string modelData
                            Layout.fillWidth: true; spacing: 12

                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 18; spacing: 10
                                C.Label { text: groupSection.modelData; color: ink; font.pixelSize: 16; font.bold: true }
                                Rectangle {
                                    radius: 9; color: purpleSoft; implicitHeight: 22; implicitWidth: cntLbl.implicitWidth + 16
                                    C.Label { id: cntLbl; anchors.centerIn: parent; text: cards.count + " model"; color: purple; font.pixelSize: 11; font.bold: true }
                                }
                            }

                            Flow {
                                id: cardFlow; Layout.fillWidth: true; spacing: 14
                                property int columns: Math.max(1, Math.floor((width + 14) / 350))
                                property real cardWidth: (width - 14 * (columns - 1)) / columns
                                Layout.preferredHeight: Math.ceil(cards.count / columns) * 254

                                Repeater {
                                    id: cards
                                    model: filteredModels.filter(m => m.group === groupSection.modelData)
                                    delegate: Rectangle {
                                        required property var modelData
                                        width: cardFlow.cardWidth; height: 240; radius: 16
                                        color: cardHover.hovered ? card2 : card
                                        border.color: cardHover.hovered ? purple : line
                                        scale: cardHover.hovered ? 1.01 : 1.0
                                        Behavior on color { ColorAnimation { duration: 150 } }
                                        Behavior on border.color { ColorAnimation { duration: 150 } }
                                        Behavior on scale { NumberAnimation { duration: 130 } }

                                        ColumnLayout {
                                            anchors.fill: parent; anchors.margins: 16; spacing: 7

                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 9
                                                Rectangle {
                                                    width: 36; height: 36; radius: 10
                                                    color: modelData.hasAudioIn || modelData.hasAudioOut ? greenSoft : (modelData.hasVision || modelData.hasImageOut ? cyanSoft : purpleSoft)
                                                    C.Label {
                                                        anchors.centerIn: parent
                                                        text: modelData.hasAudioIn ? "🎙️" : modelData.hasAudioOut ? "🔊" : modelData.hasImageOut ? "🎨" : modelData.hasVision ? "🖼️" : modelData.hasReasoning ? "🧠" : modelData.hasCoding ? "💻" : "✦"
                                                        font.pixelSize: 17
                                                    }
                                                }
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 1
                                                    C.Label { Layout.fillWidth: true; text: modelData.title; color: ink; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight }
                                                    C.Label { text: (modelData.author || "Model") + " · " + modelData.tier; color: muted; font.pixelSize: 10 }
                                                }
                                                Rectangle {
                                                    visible: !!modelData.contextWindow
                                                    radius: 7; color: card3; implicitHeight: 22; implicitWidth: ctxLabel.implicitWidth + 12
                                                    C.Label { id: ctxLabel; anchors.centerIn: parent; text: modelData.contextWindow; color: purple; font.pixelSize: 10; font.bold: true }
                                                }
                                            }

                                            Rectangle {
                                                Layout.fillWidth: true; height: 26; radius: 7; color: purpleSoft
                                                RowLayout {
                                                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                                                    C.Label { text: modelData.modalityBadge; color: purple; font.pixelSize: 11; font.bold: true }
                                                    Item { Layout.fillWidth: true }
                                                    C.Label {
                                                        text: (modelData.inputs || []).join(" + ")
                                                        color: muted; font.pixelSize: 10
                                                    }
                                                }
                                            }

                                            C.Label {
                                                Layout.fillWidth: true; text: modelData.purpose
                                                color: ink; font.pixelSize: 12; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                                            }
                                            C.Label {
                                                Layout.fillWidth: true; Layout.fillHeight: true; text: modelData.tierReason
                                                color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true; spacing: 6
                                                C.Label {
                                                    Layout.fillWidth: true
                                                    text: modelData.badges && modelData.badges.length ? modelData.badges.join(" · ") : modelData.category
                                                    color: cyanAccent; font.pixelSize: 10; font.bold: true; elide: Text.ElideRight
                                                }
                                                ActionButton {
                                                    accentStyle: true
                                                    text: "Bu Modelle Başla →"
                                                    onClicked: root.startChat(modelData.id)
                                                }
                                            }
                                        }
                                        HoverHandler { id: cardHover }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ======================== CHAT PAGE ========================
    Component {
        id: chatPage
        ColumnLayout {
            spacing: 0

            // Provider & Smart Model Selector Bar
            Rectangle {
                Layout.fillWidth: true; implicitHeight: 52; color: side
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: line }

                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22; spacing: 10
                    C.Label { text: "HESAP"; color: muted; font.pixelSize: 10; font.bold: true }
                    C.ComboBox {
                        Layout.preferredWidth: 190
                        model: accounts.map(a => a.title + " · " + (a.provider === "groq" ? "Groq" : "Cloudflare"))
                        currentIndex: Math.max(0, accounts.findIndex(a => a.id === bridge.activeAccount))
                        onActivated: index => { bridge.setActiveAccount(accounts[index].id); selectedModel = "__auto__" }
                    }
                    ActionButton { text: "＋"; onClicked: accountDialog.open() }

                    C.Label { text: "MODEL SEÇİMİ"; color: muted; font.pixelSize: 10; font.bold: true; Layout.leftMargin: 6 }
                    C.ComboBox {
                        id: modelBox
                        Layout.fillWidth: true
                        model: modelComboItems.map(x => x.label)
                        currentIndex: Math.max(0, modelComboItems.findIndex(x => x.id === selectedModel))
                        onActivated: index => {
                            selectedModel = modelComboItems[index].id
                            if (bridge.chatId) bridge.chooseModel(bridge.activeAccount, selectedModel)
                        }
                    }
                    ActionButton {
                        text: selectedModel === "__auto__" ? "⚡ Otomatik Aktif" : "⚡ Otomatiğe Al"
                        accentStyle: selectedModel === "__auto__"
                        onClicked: {
                            selectedModel = "__auto__"
                            if (bridge.chatId) bridge.chooseModel(bridge.activeAccount, "__auto__")
                        }
                    }
                    ActionButton {
                        text: "▦ Kütüphane"
                        onClicked: libraryOpen = true
                    }
                }
            }

            // Messages ListView (Fixed: NEVER override `y` or apply FBO opacity animations on tall ListView delegates!)
            ListView {
                id: listView
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 18
                cacheBuffer: 16000
                Layout.leftMargin: 24; Layout.rightMargin: 24; Layout.topMargin: 18; Layout.bottomMargin: 10
                Component.onCompleted: root.messageList = listView
                Component.onDestruction: if (root.messageList === listView) root.messageList = null
                C.ScrollBar.vertical: C.ScrollBar {
                    policy: C.ScrollBar.AlwaysOn; width: 9
                    contentItem: Rectangle { radius: 4; color: root.purple; opacity: 0.55 }
                }

                model: messages
                delegate: Item {
                    id: msgDelegate
                    required property var modelData
                    property bool thinkingExpanded: false
                    width: ListView.view.width
                    height: bubble.implicitHeight + 6

                    Rectangle {
                        id: bubble
                        anchors.right: modelData.role === "user" ? parent.right : undefined
                        anchors.left: modelData.role === "assistant" ? parent.left : undefined
                        width: modelData.role === "assistant"
                            ? Math.min(parent.width * 0.88, 900)
                            : Math.min(parent.width * 0.72, 640)
                        implicitHeight: bubbleColumn.implicitHeight + 28
                        radius: 18
                        color: modelData.role === "user" ? purpleSoft : card
                        border.color: modelData.role === "user" ? purple : line
                        border.width: 1

                        ColumnLayout {
                            id: bubbleColumn
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 14; spacing: 10

                            // Bubble Header Row
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Rectangle {
                                    width: 24; height: 24; radius: 7
                                    color: modelData.role === "user" ? purple : card2
                                    C.Label {
                                        anchors.centerIn: parent
                                        text: modelData.role === "user" ? "👤" : "✦"
                                        color: modelData.role === "user" ? "white" : purple
                                        font.pixelSize: 12
                                    }
                                }
                                C.Label {
                                    text: modelData.role === "user" ? "SİZ" : (modelData.model_used ? modelData.model_used : "ASİSTAN")
                                    color: modelData.role === "user" ? purple : ink
                                    font.pixelSize: 12; font.bold: true
                                }
                                Rectangle {
                                    visible: !!modelData.auto_reason
                                    radius: 7; color: cyanSoft; implicitHeight: 20; implicitWidth: autoLbl.implicitWidth + 14
                                    C.Label {
                                        id: autoLbl; anchors.centerIn: parent
                                        text: "⚡ " + modelData.auto_reason
                                        color: cyanAccent; font.pixelSize: 10; font.bold: true
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                ActionButton {
                                    visible: modelData.role === "assistant" && !!modelData.showThinkingAllowed
                                    implicitHeight: 22
                                    text: msgDelegate.thinkingExpanded ? "🧠 Düşünce Sürecini Gizle ▴" : "🧠 Düşünce Sürecini Gör ▾"
                                    onClicked: msgDelegate.thinkingExpanded = !msgDelegate.thinkingExpanded
                                }
                                C.Label {
                                    visible: !!modelData.created
                                    text: (modelData.created || "").slice(11, 16)
                                    color: muted; font.pixelSize: 10
                                }
                            }

                            // Collapsible Reasoning / Thinking Box (collapsed by default so main Turkish response starts with zero gap!)
                            Rectangle {
                                visible: modelData.role === "assistant" && !!modelData.showThinkingAllowed && msgDelegate.thinkingExpanded
                                Layout.fillWidth: true
                                implicitHeight: thinkText.implicitHeight + 16
                                radius: 10; color: purpleSoft; border.color: purple
                                C.Label {
                                    id: thinkText
                                    anchors.fill: parent; anchors.margins: 8
                                    text: modelData.thinking || ""
                                    color: muted; font.pixelSize: 12; wrapMode: Text.WordWrap
                                }
                            }

                            // COMPACT DOCUMENT / FILE ATTACHMENT CARD
                            Rectangle {
                                visible: !!modelData.attachment
                                Layout.fillWidth: true; implicitHeight: attCardRow.implicitHeight + 18
                                radius: 12; color: card2; border.color: purple
                                RowLayout {
                                    id: attCardRow
                                    anchors.fill: parent; anchors.margins: 10; spacing: 10
                                    Rectangle {
                                        width: 36; height: 36; radius: 9; color: purpleSoft
                                        C.Label {
                                            anchors.centerIn: parent
                                            text: (modelData.attachment || "").toLowerCase().endsWith(".udf") ? "⚖️" : "📄"
                                            font.pixelSize: 18
                                        }
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 2
                                        C.Label {
                                            Layout.fillWidth: true; text: modelData.attachment
                                            color: ink; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight
                                        }
                                        C.Label {
                                            Layout.fillWidth: true
                                            text: (modelData.meta && modelData.meta.badge)
                                                ? modelData.meta.badge
                                                : "Eklenen belge arka planda modele iletildi"
                                            color: cyanAccent; font.pixelSize: 10; elide: Text.ElideRight
                                        }
                                    }
                                    ActionButton {
                                        visible: modelData.role === "user"
                                        text: "👁️ Metni Gör"
                                        onClicked: {
                                            root.previewModalTitle = modelData.attachment
                                            root.previewModalText = bridge.getMessageAttachmentText(modelData.id) || "Belge metni."
                                            docPreviewDialog.open()
                                        }
                                    }
                                }
                            }

                            // Rich Selectable Markdown Message Content (Explicit preferredHeight: contentHeight prevents overlap!)
                            TextEdit {
                                id: messageText
                                Layout.fillWidth: true
                                Layout.preferredHeight: contentHeight
                                visible: !!modelData.content
                                text: modelData.richHtml || modelData.content
                                textFormat: TextEdit.RichText
                                readOnly: true
                                selectByMouse: true
                                selectedTextColor: "white"
                                selectionColor: root.purple
                                color: ink
                                font.family: "Inter, Noto Sans"
                                font.pixelSize: appSettings.fontSize || 14
                                wrapMode: TextEdit.Wrap
                                onLinkActivated: link => Qt.openUrlExternally(link)
                            }

                            // Image output or attachment preview
                            Image {
                                visible: modelData.kind === "image" && !!modelData.media
                                Layout.preferredWidth: 380
                                Layout.preferredHeight: visible ? 260 : 0
                                fillMode: Image.PreserveAspectFit
                                source: visible ? modelData.media : ""
                            }

                            // Audio player control
                            ActionButton {
                                visible: modelData.kind === "audio" && !!modelData.media
                                text: audioPlayer.playbackState === MediaPlayer.PlayingState ? "❚❚  Sesi Duraklat" : "▶  Ses Yanıtını Oynat"
                                onClicked: {
                                    if (audioPlayer.playbackState === MediaPlayer.PlayingState) audioPlayer.pause()
                                    else { audioPlayer.source = modelData.media; audioPlayer.play() }
                                }
                            }

                            // Truncated Response Warning & Seamless Continue Button
                            Rectangle {
                                visible: modelData.role === "assistant" && modelData.truncated === 1
                                Layout.fillWidth: true; implicitHeight: 42; radius: 10
                                color: amberSoft; border.color: amberAccent
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                                    C.Label {
                                        Layout.fillWidth: true
                                        text: "⚠️ Yanıt uzunluk sınırına ulaştığı için yarıda kaldı."
                                        color: amberAccent; font.pixelSize: 11; font.bold: true
                                    }
                                    ActionButton {
                                        accentStyle: true
                                        enabled: !bridge.busy
                                        text: "⚡ Kaldığı Yerden Devam Ettir →"
                                        onClicked: bridge.continueMessage(modelData.id)
                                    }
                                }
                            }

                            // Assistant Action & Token Telemetry Footer
                            Rectangle {
                                visible: modelData.role === "assistant"
                                Layout.fillWidth: true; height: 1; color: line
                            }

                            Flow {
                                visible: modelData.role === "assistant"
                                Layout.fillWidth: true; spacing: 8

                                Rectangle {
                                    visible: (modelData.total_tokens || 0) > 0
                                    radius: 8; color: card2; implicitHeight: 28; implicitWidth: tokLbl.implicitWidth + 16
                                    C.Label {
                                        id: tokLbl; anchors.centerIn: parent
                                        text: "📊 " + (modelData.prompt_tokens || 0) + " giriş + " + (modelData.completion_tokens || 0) + " çıkış = " + (modelData.total_tokens || 0) + " tok" +
                                              (modelData.elapsed_ms ? " · " + (modelData.elapsed_ms / 1000.0).toFixed(1) + " sn" : "")
                                        color: muted; font.pixelSize: 10
                                    }
                                }

                                ActionButton {
                                    implicitHeight: 28
                                    text: "📋 Kopyala"
                                    onClicked: bridge.copyText(modelData.content)
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    enabled: !bridge.busy
                                    text: "⚡ Devam Ettir"
                                    onClicked: bridge.continueMessage(modelData.id)
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    enabled: !bridge.busy
                                    text: "↻ Yeniden Üret"
                                    onClicked: bridge.regenerateMessage(modelData.id)
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    text: "⚖️ UDF İndir"
                                    onClicked: root.requestExport(modelData.id, "udf")
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    text: "📘 DOCX"
                                    onClicked: root.requestExport(modelData.id, "docx")
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    text: "📕 PDF"
                                    onClicked: root.requestExport(modelData.id, "pdf")
                                }
                                ActionButton {
                                    implicitHeight: 28
                                    text: "📝 RTF"
                                    onClicked: root.requestExport(modelData.id, "rtf")
                                }
                            }
                        }
                    }
                }

                // Live Assistant Thinking / Processing Bubble in Chat Stream while bridge.busy is true
                footer: Item {
                    width: listView.width
                    height: bridge.busy ? (thinkingBubble.implicitHeight + 24) : 12
                    visible: bridge.busy

                    Rectangle {
                        id: thinkingBubble
                        anchors.left: parent.left
                        anchors.top: parent.top; anchors.topMargin: 12
                        width: Math.min(parent.width * 0.78, 680)
                        implicitHeight: thinkingCol.implicitHeight + 28
                        radius: 18; color: card; border.color: purple; border.width: 2

                        ColumnLayout {
                            id: thinkingCol
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 14; spacing: 8

                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                Rectangle {
                                    width: 28; height: 28; radius: 8; color: purpleSoft
                                    C.Label {
                                        anchors.centerIn: parent; text: "✦"; color: purple; font.pixelSize: 15
                                        SequentialAnimation on opacity {
                                            running: bridge.busy; loops: Animation.Infinite
                                            NumberAnimation { from: 1.0; to: 0.3; duration: 600 }
                                            NumberAnimation { from: 0.3; to: 1.0; duration: 600 }
                                        }
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    C.Label {
                                        Layout.fillWidth: true
                                        text: bridge.busyStage || "Model yanıt üretiyor…"
                                        color: ink; font.pixelSize: 13; font.bold: true; wrapMode: Text.WordWrap
                                    }
                                    C.Label {
                                        Layout.fillWidth: true
                                        text: "İstek modele iletildi · Belge ve bağlam tam metin olarak işleniyor, lütfen bekleyin…"
                                        color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap
                                    }
                                }
                            }
                        }
                    }
                }

                // Empty State Dashboard with Interactive Quick-Start Cards
                Item {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 40, 740)
                    height: emptyCol.implicitHeight
                    visible: messages.length === 0 && !bridge.busy

                    ColumnLayout {
                        id: emptyCol; anchors.fill: parent; spacing: 18
                        C.Label {
                            Layout.fillWidth: true
                            text: accounts.length === 0
                                ? "LifeOS Chat'e Hoş Geldiniz"
                                : "Bugün Ne Üzerinde Çalışıyoruz?"
                            color: ink; font.pixelSize: 24; font.bold: true; horizontalAlignment: Text.AlignHCenter
                        }
                        C.Label {
                            Layout.fillWidth: true
                            text: accounts.length === 0
                                ? "Başlamak için Cloudflare Workers AI veya Groq hesabınızı ekleyin."
                                : "⚡ Otomatik Model Seçimi aktif. UYAP (.udf), Word (.docx), PDF, Excel, Görsel veya Canlı Ses kullanabilirsiniz."
                            color: muted; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        }

                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 14; rowSpacing: 14
                            Repeater {
                                model: [
                                    {
                                        icon: "⚖️",
                                        title: "UYAP (.udf) / PDF Evrak Analizi",
                                        desc: "Dava dilekçesi, bilirkişi raporu veya mahkeme kararını ekleyip özetletin, e-imzasını doğrulayın.",
                                        prompt: "Eklediğim hukuki evrakı (UDF/PDF/DOCX) detaylıca incele; tarafları, talepleri, önemli tarihleri ve hukuki sonuçları maddeler halinde özetle.",
                                        pickFile: true,
                                        liveVoice: false
                                    },
                                    {
                                        icon: "✍️",
                                        title: "Dilekçe Yaz & UYAP (.udf) Oluştur",
                                        desc: "Yanıtı hazırlatıp sağ panelden tek tıkla doğrudan UYAP Editör uyumlu .udf, .docx veya .pdf olarak kaydedin.",
                                        prompt: "Aşağıdaki konu hakkında resmi mahkeme formatına uygun, başlıkları ve sonuç-istem bölümü tam bir dilekçe taslağı hazırla: ",
                                        pickFile: false,
                                        liveVoice: false
                                    },
                                    {
                                        icon: "🎙️",
                                        title: "Canlı Sesli Sohbet (Gemini Live)",
                                        desc: "Yazmadan doğrudan mikrofona konuşun; asistan sizi dinlesin ve sesli olarak yanıtlasın.",
                                        prompt: "",
                                        pickFile: false,
                                        liveVoice: true
                                    },
                                    {
                                        icon: "💻",
                                        title: "Kod & Çok Dosyalı Proje Üret",
                                        desc: "Üretilen tüm kod dosyaları sağ panelde listelenir ve tek tıkla proje klasörüne çıkarılır.",
                                        prompt: "Bana şu özelliklere sahip tam çalışan bir proje üret (her dosyayı ```dil dosya_adi şeklinde yaz): ",
                                        pickFile: false,
                                        liveVoice: false
                                    }
                                ]
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true; implicitHeight: 98; radius: 14
                                    color: qHover.hovered ? card2 : card
                                    border.color: qHover.hovered ? purple : line
                                    Behavior on color { ColorAnimation { duration: 150 } }
                                    RowLayout {
                                        anchors.fill: parent; anchors.margins: 14; spacing: 12
                                        Rectangle {
                                            width: 42; height: 42; radius: 11; color: purpleSoft
                                            C.Label { anchors.centerIn: parent; text: modelData.icon; font.pixelSize: 20 }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true; spacing: 3
                                            C.Label { Layout.fillWidth: true; text: modelData.title; color: ink; font.pixelSize: 13; font.bold: true }
                                            C.Label { Layout.fillWidth: true; text: modelData.desc; color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 2 }
                                        }
                                    }
                                    HoverHandler { id: qHover; cursorShape: Qt.PointingHandCursor }
                                    TapHandler {
                                        onTapped: {
                                            if (modelData.liveVoice) {
                                                bridge.startLiveVoiceSession()
                                                return
                                            }
                                            if (root.composerInput) {
                                                root.composerInput.text = modelData.prompt
                                                root.composerInput.forceActiveFocus()
                                            }
                                            if (modelData.pickFile) filePicker.open()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            MediaPlayer { id: audioPlayer; audioOutput: AudioOutput {} }

            // ======================== COMPOSER AREA ========================
            Rectangle {
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.bottomMargin: 16
                implicitHeight: composer.implicitHeight + 24
                color: card; radius: 18
                border.color: promptBox.activeFocus ? purple : line
                border.width: promptBox.activeFocus ? 2 : 1
                Behavior on border.color { ColorAnimation { duration: 160 } }

                ColumnLayout {
                    id: composer
                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                    anchors.margins: 12; spacing: 8

                    Rectangle {
                        Layout.fillWidth: true; visible: !!selectedFile
                        implicitHeight: 46; radius: 11; color: purpleSoft; border.color: purple
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 10; spacing: 10
                            C.Label {
                                text: attachmentPreview && attachmentPreview.badge
                                    ? attachmentPreview.badge
                                    : ("📎 " + decodeURIComponent(selectedFile.split("/").pop()))
                                color: ink; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            ActionButton {
                                visible: !!(attachmentPreview && attachmentPreview.previewText)
                                implicitHeight: 28
                                text: "👁️ Önizle"
                                onClicked: {
                                    root.previewModalTitle = attachmentPreview.name || "Belge Önizlemesi"
                                    root.previewModalText = attachmentPreview.previewText || ""
                                    docPreviewDialog.open()
                                }
                            }
                            C.ToolButton {
                                text: "×"
                                onClicked: { selectedFile = ""; bridge.clearAttachment() }
                            }
                        }
                    }

                    C.TextArea {
                        id: promptBox
                        Layout.fillWidth: true; Layout.preferredHeight: 78
                        placeholderText: selectedModelInfo && selectedModelInfo.category.includes("Embedding")
                            ? "Vektöre dönüştürülecek metni yazın…"
                            : "Mesajınızı yazın...  UYAP (.udf), PDF, Word (.docx/.doc), RTF, Excel (.xlsx), Görsel ekleyebilir veya Canlı Ses başlatabilirsiniz"
                        color: ink; font.pixelSize: appSettings.fontSize || 14; wrapMode: TextEdit.Wrap
                        background: Rectangle { color: "transparent" }
                        Component.onCompleted: root.composerInput = promptBox
                        Component.onDestruction: if (root.composerInput === promptBox) root.composerInput = null
                        Keys.onReturnPressed: event => {
                            if (!(event.modifiers & Qt.ShiftModifier)) {
                                root.sendNow()
                                event.accepted = true
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        ActionButton {
                            text: "📎  Dosya / UDF Ekle"
                            onClicked: filePicker.open()
                        }
                        ActionButton {
                            text: bridge.recording ? "■  Kaydı Bitir" : "🎙️  Ses Kaydet"
                            accentStyle: bridge.recording
                            onClicked: bridge.toggleRecording()
                        }
                        ActionButton {
                            text: "◎  Canlı Sesli Konuşma"
                            onClicked: bridge.startLiveVoiceSession()
                        }
                        ActionButton {
                            text: rawOpen ? "{} JSON Kapat" : "{} JSON Testi"
                            onClicked: rawOpen = !rawOpen
                        }
                        Item { Layout.fillWidth: true }
                        C.Label {
                            text: bridge.busy
                                ? (bridge.busyStage || "Yanıt üretiliyor…")
                                : (selectedModel === "__auto__" ? "⚡ İşe uygun model otomatik seçilecek · Enter gönder" : "Enter gönder · Shift+Enter yeni satır")
                            color: bridge.busy ? purple : muted; font.pixelSize: 11
                        }
                        ActionButton {
                            accentStyle: true
                            implicitHeight: 36
                            text: selectedModelInfo && selectedModelInfo.category.includes("Embedding") ? "Vektör Üret  ↗" : "Gönder  ↗"
                            enabled: !bridge.busy
                            onClicked: root.sendNow()
                        }
                    }

                    C.TextField {
                        id: voiceBox
                        visible: !!(selectedModelInfo && selectedModelInfo.hasAudioOut)
                        Layout.fillWidth: true; text: "troy"
                        placeholderText: "Ses adı (ör. troy)"
                        Component.onCompleted: root.voiceInput = voiceBox
                        Component.onDestruction: if (root.voiceInput === voiceBox) root.voiceInput = null
                    }

                    C.TextArea {
                        id: rawBox
                        visible: rawOpen; Layout.fillWidth: true; Layout.preferredHeight: 96
                        placeholderText: "Modelin giriş şemasına uygun özel JSON yazın. Örnek: {\"prompt\":\"Merhaba\"}"
                        font.family: "monospace"; wrapMode: TextEdit.Wrap
                        Component.onCompleted: root.rawInput = rawBox
                        Component.onDestruction: if (root.rawInput === rawBox) root.rawInput = null
                    }
                }
            }
        }
    }

    // ======================== FILE & FOLDER DIALOGS ========================
    FileDialog {
        id: filePicker
        title: "Sohbete Belge (UDF/PDF/DOCX/XLSX), Görsel veya Ses Ekle"
        nameFilters: [
            "Tüm Desteklenenler (*.udf *.pdf *.docx *.doc *.rtf *.odt *.xlsx *.csv *.txt *.md *.html *.json *.png *.jpg *.jpeg *.webp *.gif *.tif *.tiff *.wav *.mp3 *.m4a *.ogg *.flac *.webm)",
            "UYAP ve Ofis Belgeleri (*.udf *.pdf *.docx *.doc *.rtf *.odt *.xlsx *.csv)",
            "Görseller (*.png *.jpg *.jpeg *.webp *.gif *.tif *.tiff)",
            "Ses Dosyaları (*.wav *.mp3 *.m4a *.ogg *.flac *.webm)",
            "Tüm Dosyalar (*)"
        ]
        onAccepted: {
            root.selectedFile = selectedFile.toString()
            bridge.inspectAttachment(root.selectedFile)
        }
    }

    FileDialog {
        id: exportSaveDialog
        title: "Yanıtı Belge Olarak Kaydet"
        fileMode: FileDialog.SaveFile
        onAccepted: {
            bridge.exportMessageToDocument(root.pendingExportMessageId, root.pendingExportFormat, selectedFile.toString())
        }
    }

    FileDialog {
        id: artifactSaveDialog
        title: "Kod veya Belge Dosyasını Kaydet"
        fileMode: FileDialog.SaveFile
        onAccepted: {
            bridge.saveArtifactContent(root.pendingArtifactTitle, root.pendingArtifactContent, selectedFile.toString())
        }
    }

    FolderDialog {
        id: projectFolderDialog
        title: "Proje Dosyalarının Çıkarılacağı Klasörü Seçin"
        onAccepted: {
            bridge.exportAllCodeToFolder(selectedFolder.toString())
        }
    }

    FileDialog {
        id: convertSourcePicker
        title: "Dönüştürülecek Belgeyi Seçin (UDF, DOCX, DOC, RTF, ODT, PDF, XLSX)"
        nameFilters: [
            "Belgeler (*.udf *.pdf *.docx *.doc *.rtf *.odt *.xlsx *.csv *.txt *.md *.html)",
            "Tüm Dosyalar (*)"
        ]
        onAccepted: convertSourceField.text = selectedFile.toString()
    }

    // ======================== RENAME CHAT DIALOG ========================
    C.Dialog {
        id: renameChatDialog
        title: "✎ Sohbet Adını Değiştir"
        modal: true; anchors.centerIn: parent; width: 440
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 12
            C.Label { text: "Sohbet için yeni bir başlık girin:"; color: muted; font.pixelSize: 12 }
            C.TextField {
                id: renameField
                Layout.fillWidth: true
                placeholderText: "Sohbet başlığı"
                onAccepted: {
                    bridge.renameChat(root.renameTargetChatId, renameField.text)
                    renameChatDialog.close()
                }
            }
            ActionButton {
                Layout.fillWidth: true; accentStyle: true; implicitHeight: 38
                text: "Kaydet"
                onClicked: {
                    bridge.renameChat(root.renameTargetChatId, renameField.text)
                    renameChatDialog.close()
                }
            }
        }
    }

    // ======================== SETTINGS & MCP DIALOG ========================
    C.Dialog {
        id: settingsDialog
        title: "⚙ Uygulama Ayarları & MCP Sunucusu"
        modal: true; anchors.centerIn: parent; width: 640
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 14

            // MCP Server Card
            Rectangle {
                Layout.fillWidth: true; implicitHeight: mcpCol.implicitHeight + 24; radius: 12
                color: card2; border.color: mcpSwitch.checked ? greenAccent : line
                ColumnLayout {
                    id: mcpCol; anchors.fill: parent; anchors.margins: 12; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        C.Label {
                            text: "🔌 'lifeoschat' MCP Sunucusu (Antigravity agy CLI / Claude / Codex)"
                            color: ink; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true
                        }
                        C.Switch {
                            id: mcpSwitch
                            checked: !!appSettings.mcpEnabled
                            onToggled: bridge.setMcpEnabled(checked)
                        }
                    }
                    C.Label {
                        Layout.fillWidth: true
                        text: "Aktif edildiğinde diğer CLI ajanları tüm Groq ve Cloudflare modellerine, görsel üretime, Whisper ses deşifresine ve UDF/DOCX/PDF belge motoruna doğrudan erişebilir."
                        color: muted; font.pixelSize: 11; wrapMode: Text.WordWrap
                    }
                    C.Label {
                        Layout.fillWidth: true
                        text: "Yapılandırma: " + (appSettings.mcpConfigPath || "~/.gemini/config/mcp_config.json")
                        color: purple; font.pixelSize: 10; font.family: "monospace"
                    }
                }
            }

            // Font Size Setting
            Rectangle {
                Layout.fillWidth: true; implicitHeight: fontCol.implicitHeight + 22; radius: 12; color: card2; border.color: line
                ColumnLayout {
                    id: fontCol; anchors.fill: parent; anchors.margins: 12; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        C.Label { text: "🔤 Sohbet Yazı Tipi Boyutu: " + Math.round(fontSizeSlider.value) + " px"; color: ink; font.pixelSize: 13; font.bold: true }
                        Item { Layout.fillWidth: true }
                        ActionButton { implicitHeight: 26; text: "Küçük (12)"; onClicked: fontSizeSlider.value = 12 }
                        ActionButton { implicitHeight: 26; text: "Normal (14)"; onClicked: fontSizeSlider.value = 14 }
                        ActionButton { implicitHeight: 26; text: "Büyük (16)"; onClicked: fontSizeSlider.value = 16 }
                        ActionButton { implicitHeight: 26; text: "Çok Büyük (18)"; onClicked: fontSizeSlider.value = 18 }
                    }
                    C.Slider {
                        id: fontSizeSlider
                        Layout.fillWidth: true
                        from: 11; to: 20; stepSize: 1
                        value: appSettings.fontSize || 14
                    }
                }
            }

            // Generation & UI toggles
            GridLayout {
                Layout.fillWidth: true; columns: 2; columnSpacing: 14; rowSpacing: 10
                C.Label { text: "Maksimum Yanıt Uzunluğu (Max Tokens):"; color: ink; font.pixelSize: 12 }
                C.ComboBox {
                    id: maxTokensCombo; Layout.fillWidth: true
                    model: ["2048", "4096", "8192"]
                    currentIndex: model.indexOf(String(appSettings.maxTokens || 4096)) >= 0 ? model.indexOf(String(appSettings.maxTokens || 4096)) : 1
                }

                C.CheckBox {
                    id: showThinkCheck
                    text: "🧠 Düşünce Süreci (Reasoning) Kutusunu Göster"
                    checked: appSettings.showThinking !== false
                }
                C.CheckBox {
                    id: autoRightPanelCheck
                    text: "📂 Yeni Evrak/Kod Üretildiğinde Sağ Paneli Aç"
                    checked: appSettings.autoOpenRightPanel !== false
                }
                C.CheckBox {
                    id: handsFreeCheck
                    text: "🎙️ Canlı Seste Otomatik Karşılıklı Konuşma Döngüsü"
                    checked: appSettings.liveVoiceHandsFree !== false
                }
            }

            C.Label { text: "Özel Sistem Talimatı (İsteğe Bağlı):"; color: ink; font.pixelSize: 12; font.bold: true }
            C.TextArea {
                id: sysPromptField
                Layout.fillWidth: true; Layout.preferredHeight: 68
                text: appSettings.systemPrompt || ""
                placeholderText: "Boş bırakılırsa varsayılan Türkçe hukuk/belge/kod uzman talimatı kullanılır."
                color: ink; font.pixelSize: 12; wrapMode: TextEdit.Wrap
                background: Rectangle { color: card2; radius: 10; border.color: line }
            }

            ActionButton {
                Layout.fillWidth: true; accentStyle: true; implicitHeight: 40
                text: "💾  Ayarları Kaydet ve Uygula"
                onClicked: {
                    bridge.updateSettings(JSON.stringify({
                        fontSize: Math.round(fontSizeSlider.value),
                        maxTokens: parseInt(maxTokensCombo.currentText),
                        showThinking: showThinkCheck.checked,
                        autoOpenRightPanel: autoRightPanelCheck.checked,
                        liveVoiceHandsFree: handsFreeCheck.checked,
                        mcpEnabled: mcpSwitch.checked,
                        systemPrompt: sysPromptField.text
                    }))
                    settingsDialog.close()
                }
            }
        }
    }

    // ======================== DOCUMENT PREVIEW DIALOG ========================
    C.Dialog {
        id: docPreviewDialog
        title: "📄 İçerik Önizleme — " + root.previewModalTitle
        modal: true; anchors.centerIn: parent; width: 780; height: 580
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 10
            C.ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                C.TextArea {
                    text: root.previewModalText
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                    color: ink; font.family: "monospace"; font.pixelSize: 12
                    background: Rectangle { color: card2; radius: 10 }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                ActionButton {
                    text: "📋 Tümünü Kopyala"
                    onClicked: bridge.copyText(root.previewModalText)
                }
                Item { Layout.fillWidth: true }
            }
        }
    }

    // ======================== DOCUMENT CONVERTER DIALOG (Evrak Convert) ========================
    C.Dialog {
        id: converterDialog
        title: "⇄ Evrak Dönüştürücü (UDF ↔ DOCX ↔ PDF ↔ RTF ↔ MD)"
        modal: true; anchors.centerIn: parent; width: 580
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 12
            C.Label {
                Layout.fillWidth: true
                text: "UYAP (.udf), Word (.docx, .doc), RTF, ODT, PDF veya Excel (.xlsx) belgelerini yerel olarak başka bir formata dönüştürün."
                color: muted; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                C.TextField {
                    id: convertSourceField; Layout.fillWidth: true
                    placeholderText: "Kaynak dosya seçin (.udf, .docx, .pdf, .rtf...)"
                }
                ActionButton { text: "Dosya Seç…"; onClicked: convertSourcePicker.open() }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                C.Label { text: "Hedef Format:"; color: ink; font.pixelSize: 12; font.bold: true }
                C.ComboBox {
                    id: convertTargetCombo; Layout.fillWidth: true
                    model: ["udf (UYAP Doküman Formatı)", "docx (Microsoft Word)", "pdf (Taşınabilir Belge)", "rtf (Zengin Metin)", "md (Markdown)", "txt (Düz Metin)", "html (Web Sayfası)"]
                }
            }
            ActionButton {
                Layout.fillWidth: true; accentStyle: true; implicitHeight: 38
                text: "⇄  Şimdi Dönüştür ve Kaydet"
                onClicked: {
                    const fmt = convertTargetCombo.currentText.split(" ")[0]
                    bridge.convertDocument(convertSourceField.text, fmt, "")
                    converterDialog.close()
                }
            }
        }
    }

    // ======================== QUOTA & OFFICIAL LIMITS DIALOG ========================
    C.Dialog {
        id: quotaDialog
        title: "📊 Gerçek Ücretsiz API Kota & Token Limitleri (Groq & Cloudflare)"
        modal: true; anchors.centerIn: parent; width: 720; height: 540
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 12

            Rectangle {
                Layout.fillWidth: true; implicitHeight: 96; radius: 12; color: card2; border.color: line
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 12; spacing: 4
                    C.Label { text: quota.quotaBadge || "Kota Durumu"; color: purple; font.pixelSize: 14; font.bold: true }
                    C.Label { visible: !!quota.minuteBadge; text: quota.minuteBadge || ""; color: cyanAccent; font.pixelSize: 12; font.bold: true }
                    C.Label {
                        text: "Bugün Toplam Harcanan: " + (quota.todayPromptTokens || 0) + " giriş + " +
                              (quota.todayCompletionTokens || 0) + " çıkış = " + (quota.todayTotalTokens || 0) + " token (" + (quota.todayRequests || 0) + " istek)"
                        color: ink; font.pixelSize: 12
                    }
                    C.Label {
                        text: "Cloudflare Workers AI Günlük Ücretsiz Kota: 10.000 Neuron / Gün (Bugün harcanan: " + (quota.todayNeurons || 0) + " Neuron · Her gün 00:00 UTC'de sıfırlanır)"
                        color: muted; font.pixelSize: 11
                    }
                }
            }

            C.Label {
                text: "RESMÎ GROQ ÜCRETSİZ PLAN MODEL LİMİTLERİ (GÜNLÜK TPD/RPD & DAKİKALIK TPM)"
                color: muted; font.pixelSize: 10; font.bold: true
            }

            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
                model: quota.limitsTable || []
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 42; radius: 9; color: card2; border.color: line
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 10
                        C.Label { Layout.preferredWidth: 230; text: modelData.model; color: ink; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight }
                        C.Label {
                            Layout.fillWidth: true
                            text: modelData.dailyTokensLimit > 0
                                ? ("Günlük: " + modelData.dailyTokensLimit.toLocaleString() + " tok/gün · " + modelData.dailyRequestsLimit.toLocaleString() + " istek/gün · Anlık: " + modelData.minuteTokensLimit.toLocaleString() + " tok/dk")
                                : ("Günlük: " + modelData.dailyRequestsLimit.toLocaleString() + " ses isteği/gün")
                            color: muted; font.pixelSize: 11
                        }
                        C.Label {
                            text: "Bugün: " + modelData.todayTokensUsed + " tok"
                            color: purple; font.pixelSize: 11; font.bold: true
                        }
                    }
                }
            }
        }
    }

    // ======================== ACCOUNT DIALOG ========================
    C.Dialog {
        id: accountDialog; title: "API Hesabı Yönetimi"; modal: true; anchors.centerIn: parent; width: 520
        standardButtons: C.Dialog.Close
        background: Rectangle { color: card; radius: 16; border.color: line }
        contentItem: ColumnLayout {
            spacing: 12
            C.Label { text: "Cloudflare Workers AI veya Groq hesabı ekleyin; modeller ve ücretsiz kota bilgisi canlı yüklensin."; color: muted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            C.ComboBox { id: providerInput; Layout.fillWidth: true; model: ["Cloudflare Workers AI", "Groq"] }
            C.TextField { id: accountTitle; Layout.fillWidth: true; placeholderText: "Hesap adı (ör. Kişisel)" }
            C.TextField { id: accountIdInput; Layout.fillWidth: true; visible: providerInput.currentIndex === 0; placeholderText: "Cloudflare Account ID" }
            C.TextField { id: tokenInput; Layout.fillWidth: true; placeholderText: providerInput.currentIndex === 0 ? "Cloudflare API token" : "Groq API key"; echoMode: TextInput.Password }
            ActionButton {
                Layout.fillWidth: true; accentStyle: true; implicitHeight: 38
                text: "Hesabı Kaydet ve Modelleri Getir"
                onClicked: {
                    if (bridge.addAccount(providerInput.currentIndex === 0 ? "cloudflare" : "groq", accountTitle.text, accountIdInput.text, tokenInput.text)) {
                        tokenInput.text = ""; accountDialog.close()
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: line; visible: accounts.length > 0 }
            C.Label { text: "KAYITLI HESAPLAR"; color: muted; font.pixelSize: 10; font.bold: true; visible: accounts.length > 0 }
            Repeater {
                model: accounts
                delegate: RowLayout {
                    required property var modelData; Layout.fillWidth: true
                    C.Label { Layout.fillWidth: true; text: modelData.title + " · " + modelData.provider; color: ink; font.pixelSize: 12 }
                    ActionButton { text: "Aktif Et"; onClicked: { bridge.setActiveAccount(modelData.id); accountDialog.close() } }
                    ActionButton { text: "Sil"; onClicked: bridge.removeAccount(modelData.id) }
                }
            }
        }
    }

    // Toast Notification
    Rectangle {
        visible: !!statusText
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 24
        width: Math.min(720, statusLabel.implicitWidth + 44); height: 44; radius: 13
        color: card3; border.color: purple; border.width: 2; z: 100
        C.Label {
            id: statusLabel; anchors.centerIn: parent; text: statusText
            color: ink; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight
            width: parent.width - 28; horizontalAlignment: Text.AlignHCenter
        }
    }
}
