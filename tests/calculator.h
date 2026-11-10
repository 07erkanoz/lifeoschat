#ifndef CALCULATOR_H
#define CALCULATOR_H

#include <QMainWindow>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>
#include <QGridLayout>
#include <QPainter>
#include <QPainterPath>
#include <QString>

class Calculator : public QMainWindow
{
    Q_OBJECT

public:
    explicit Calculator(QWidget *parent = nullptr);
    ~Calculator();

private slots:
    void onButtonClicked();
    void clearDisplay();
    void calculateResult();

private:
    void setupUI();
    void setupStyles();
    QPushButton* createButton(const QString &text, const QString &objectName, const QString &styleSheet);

    // UI Elemanları
    QLabel *displayLabel;
    QGridLayout *buttonGrid;
    
    // Mantık Değişkenleri
    double currentNumber;
    double previousNumber;
    char pendingOperation;
    bool waitingForOperand;
    bool justCalculated;

    // Buton Tipleri (Stil vermek için)
    enum ButtonType {
        Number,
        Operator,
        Function,
        Equals
    };
};

#endif // CALCULATOR_H