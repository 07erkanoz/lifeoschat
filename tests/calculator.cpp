#include "calculator.h"
#include <QFontDatabase>
#include <QMessageBox>
#include <cmath>

Calculator::Calculator(QWidget *parent)
    : QMainWindow(parent)
    , displayLabel(nullptr)
    , buttonGrid(nullptr)
    , currentNumber(0.0)
    , previousNumber(0.0)
    , pendingOperation('\0')
    , waitingForOperand(true)
    , justCalculated(false)
{
    setupUI();
    setupStyles();
    
    setWindowTitle("Hesap Makinesi");
    setFixedSize(320, 500); // iOS tarzı dikey oran
}

Calculator::~Calculator()
{
}

void Calculator::setupUI()
{
    // Ana Widget
    QWidget *centralWidget = new QWidget(this);
    setCentralWidget(centralWidget);

    // Ana Düzey
    QVBoxLayout *mainLayout = new QVBoxLayout(centralWidget);
    mainLayout->setContentsMargins(20, 20, 20, 20);
    mainLayout->setSpacing(10);

    // Display (Gösterge)
    displayLabel = new QLabel("0");
    displayLabel->setObjectName("displayLabel");
    displayLabel->setAlignment(Qt::AlignRight | Qt::AlignBottom);
    displayLabel->setWordWrap(false);
    
    // Font ayarları
    QFont displayFont = QFont("Helvetica Neue", 40);
    if (displayFont.family() == "Helvetica Neue") {
        displayFont.setFamily("Helvetica Neue");
    } else {
        displayFont.setFamily("Arial"); // Fallback
    }
    displayFont.setBold(false);
    displayLabel->setFont(displayFont);
    
    mainLayout->addWidget(displayLabel);

    // Buton Izgarası
    buttonGrid = new QGridLayout();
    buttonGrid->setSpacing(10);
    buttonGrid->setContentsMargins(0, 0, 0, 0);
    mainLayout->addLayout(buttonGrid);

    // Butonları Oluştur ve Yerleştir (iOS Düzeni)
    // Satır 0: AC, +/-, %, ÷
    QPushButton *btnAC = createButton("AC", "AC", "function");
    QPushButton *btnPlusMinus = createButton("±", "PM", "function");
    QPushButton *btnPercent = createButton("%", "PCT", "function");
    QPushButton *btnDivide = createButton("÷", "DIV", "operator");

    buttonGrid->addWidget(btnAC, 0, 0);
    buttonGrid->addWidget(btnPlusMinus, 0, 1);
    buttonGrid->addWidget(btnPercent, 0, 2);
    buttonGrid->addWidget(btnDivide, 0, 3);

    // Satır 1: 7, 8, 9, x
    QPushButton *btn7 = createButton("7", "7", "number");
    QPushButton *btn8 = createButton("8", "8", "number");
    QPushButton *btn9 = createButton("9", "9", "number");
    QPushButton *btnMultiply = createButton("×", "MUL", "operator");

    buttonGrid->addWidget(btn7, 1, 0);
    buttonGrid->addWidget(btn8, 1, 1);
    buttonGrid->addWidget(btn9, 1, 2);
    buttonGrid->addWidget(btnMultiply, 1, 3);

    // Satır 2: 4, 5, 6, -
    QPushButton *btn4 = createButton("4", "4", "number");
    QPushButton *btn5 = createButton("5", "5", "number");
    QPushButton *btn6 = createButton("6", "6", "number");
    QPushButton *btnSubtract = createButton("-", "SUB", "operator");

    buttonGrid->addWidget(btn4, 2, 0);
    buttonGrid->addWidget(btn5, 2, 1);
    buttonGrid->addWidget(btn6, 2, 2);
    buttonGrid->addWidget(btnSubtract, 2, 3);

    // Satır 3: 1, 2, 3, +
    QPushButton *btn1 = createButton("1", "1", "number");
    QPushButton *btn2 = createButton("2", "2", "number");
    QPushButton *btn3 = createButton("3", "3", "number");
    QPushButton *btnAdd = createButton("+", "ADD", "operator");

    buttonGrid->addWidget(btn1, 3, 0);
    buttonGrid->addWidget(btn2, 3, 1);
    buttonGrid->addWidget(btn3, 3, 2);
    buttonGrid->addWidget(btnAdd, 3, 3);

    // Satır 4: 0, ., =
    // 0 iki sütunu kaplayacak
    QPushButton *btn0 = createButton("0", "0", "number");
    QPushButton *btnDot = createButton(".", "DOT", "number");
    QPushButton *btnEquals = createButton("=", "EQ", "equals");

    buttonGrid->addWidget(btn0, 4, 0, 1, 2); // Row 4, Col 0, Span 1 Row, Span 2 Cols
    buttonGrid->addWidget(btnDot, 4, 2);
    buttonGrid->addWidget(btnEquals, 4, 3);

    // Buton boyutlarını ayarla (Kare veya geniş)
    // QGridLayout otomatik boyutlandırır, ancak minimum boyut belirleyelim
    for (int i = 0; i < buttonGrid->count(); ++i) {
        QWidget *w = buttonGrid->itemAt(i)->widget();
        if (w) {
            w->setMinimumHeight(60);
            w->setMinimumWidth(60);
        }
    }
}

QPushButton* Calculator::createButton(const QString &text, const QString &objectName, const QString &type)
{
    QPushButton *btn = new QPushButton(text);
    btn->setObjectName(objectName);
    btn->setProperty("type", type); // Stil ayırt etmek için
    
    // Font Ayarları
    QFont btnFont = QFont("Helvetica Neue", 22);
    if (btnFont.family() == "Helvetica Neue") {
        btnFont.setFamily("Helvetica Neue");
    } else {
        btnFont.setFamily("Arial");
    }
    btnFont.setBold(true);
    btn->setFont(btnFont);
    
    // Tıklama sinyali bağla
    connect(btn, &QPushButton::clicked, this, &Calculator::onButtonClicked);

    return btn;
}

void Calculator::setupStyles()
{
    // iOS Tarzı QSS (Qt Style Sheets)
    QString qss = R"(
        QMainWindow {
            background-color: #000000; /* Arka plan siyah */
        }

        #displayLabel {
            color: #FFFFFF;
            background-color: transparent;
            font-size: 48px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: 300; /* Light weight */
            padding-right: 10px;
        }

        QPushButton {
            border-radius: 30px; /* Yuvarlak köşeler */
            font-size: 24px;
            font-family: 'Helvetica Neue', Arial, sans-serif;
            font-weight: bold;
            border: none;
            padding: 0px;
            margin: 0px;
        }

        /* Sayı Butonları: Koyu Gri */
        QPushButton[type="number"] {
            background-color: #333333;
            color: #FFFFFF;
        }

        /* Fonksiyon Butonları: Açık Gri (AC, ±, %) */
        QPushButton[type="function"] {
            background-color: #A5A5A5;
            color: #000000;
        }

        /* Operatör Butonları: Turuncu (÷, ×, -, +) */
        QPushButton[type="operator"] {
            background-color: #FF9500; /* iOS Turuncusu */
            color: #FFFFFF;
        }

        /* Eşittir Butonu: Turuncu */
        QPushButton[type="equals"] {
            background-color: #FF9500;
            color: #FFFFFF;
        }

        /* Hover Efektleri */
        QPushButton[type="number"]:hover {
            background-color: #444444;
        }

        QPushButton[type="function"]:hover {
            background-color: #B5B5B5;
        }

        QPushButton[type="operator"]:hover,
        QPushButton[type="equals"]:hover {
            background-color: #FFA520;
        }

        /* Tıklama (Pressed) Efektleri */
        QPushButton[type="number"]:pressed {
            background-color: #555555;
        }

        QPushButton[type="function"]:pressed {
            background-color: #C5C5C5;
        }

        QPushButton[type="operator"]:pressed,
        QPushButton[type="equals"]:pressed {
            background-color: #FFB540;
        }
    )";

    this->setStyleSheet(qss);
}

void Calculator::onButtonClicked()
{
    QPushButton *btn = qobject_cast<QPushButton*>(sender());
    if (!btn) return;

    QString text = btn->text();
    QString type = btn->property("type").toString();

    if (type == "number") {
        handleNumberInput(text);
    } else if (type == "operator") {
        handleOperatorInput(text);
    } else if (type == "function") {
        handleFunctionInput(text);
    } else if (type == "equals") {
        calculateResult();
    }
}

void Calculator::handleNumberInput(const QString &text)
{
    if (waitingForOperand) {
        // Yeni sayı girişi
        displayLabel->setText(text);
        currentNumber = text.toDouble();
        waitingForOperand = false;
    } else {
        // Mevcut sayıya ekle
        QString currentText = displayLabel->text();
        
        // Nokta kontrolü (çift nokta olmasın)
        if (text == "." && currentText.contains(".")) {
            return;
        }

        // Sıfır durumu kontrolü (0 basılıyken rakam gelirse 0'ı sil)
        if (currentText == "0" && text != ".") {
            currentText = text;
        } else {
            currentText += text;
        }
        
        displayLabel->setText(currentText);
        currentNumber = currentText.toDouble();
    }
    
    if (justCalculated) {
        justCalculated = false;
    }
}

void Calculator::handleOperatorInput(const QString &op)
{
    if (!waitingForOperand) {
        // Eğer bir operatör bekleniyorsa ve kullanıcı başka bir operatör seçtiyse, sadece operatörü değiştir
        if (!pendingOperation) {
            previousNumber = currentNumber;
        } else {
            // Zincirleme işlem: Önce mevcut işlemi yap, sonucu previousNumber yap
            calculateResult();
            previousNumber = currentNumber;
        }
    } else {
        // Display'daki değeri previousNumber olarak al (Eğer daha önce bir sayı girilmediyse)
        previousNumber = currentNumber;
    }

    pendingOperation = op.toLatin1()[0];
    waitingForOperand = true;
    justCalculated = false;
}

void Calculator::handleFunctionInput(const QString &func)
{
    if (func == "AC") {
        clearDisplay();
    } else if (func == "±") {
        // İşareti değiştir
        QString text = displayLabel->text();
        if (!text.isEmpty() && text != "0") {
            if (text.startsWith("-")) {
                text.remove(0, 1);
            } else {
                text = "-" + text;
            }
            displayLabel->setText(text);
            currentNumber = text.toDouble();
        }
    } else if (func == "%") {
        // Yüzde
        currentNumber = currentNumber / 100.0;
        displayLabel->setText(QString::number(currentNumber, 'g', 10));
        waitingForOperand = true;
    }
}

void Calculator::calculateResult()
{
    if (pendingOperation && !waitingForOperand) {
        double result = 0;

        switch (pendingOperation) {
            case '+':
                result = previousNumber + currentNumber;
                break;
            case '-':
                result = previousNumber - currentNumber;
                break;
            case 'x': // '×' karakterinin ASCII değeri veya Latin1 karşılığı
            case '×':
                result = previousNumber * currentNumber;
                break;
            case '/':
            case '÷':
                if (currentNumber != 0) {
                    result = previousNumber / currentNumber;
                } else {
                    displayLabel->setText("Hata");
                    QMessageBox::warning(this, "Hata", "Sıfıra bölme yapılamaz.");
                    clearDisplay();
                    return;
                }
                break;
            default:
                break;
        }

        currentNumber = result;
        
        // Göstergeyi güncelle (gereksiz ondalık sıfırları temizle)
        QString resultStr = QString::number(result, 'g', 10);
        // Eğer sonuç tam sayıysa .0 ekleme
        if (resultStr.contains(".") && resultStr.right(1) == "0") {
             // Basit bir temizleme, daha karmaşık formatlama yapılabilir
             // Burada sadece görsel olarak düzgün olsun
        }
        
        displayLabel->setText(resultStr);
        pendingOperation = '\0';
        waitingForOperand = true;
        justCalculated = true;
    }
}

void Calculator::clearDisplay()
{
    displayLabel->setText("0");
    currentNumber = 0.0;
    previousNumber = 0.0;
    pendingOperation = '\0';
    waitingForOperand = true;
    justCalculated = false;
}