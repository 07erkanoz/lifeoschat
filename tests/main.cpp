#include <QApplication>
#include "calculator.h"

int main(int argc, char *argv[])
{
    // Yüksek çözünürlük desteği için (Retina ekranlar için önemli)
    QCoreApplication::setAttribute(Qt::AA_EnableHighDpiScaling);
    QCoreApplication::setAttribute(Qt::AA_UseHighDpiPixmaps);

    QApplication app(argc, argv);
    
    // Uygulama adını ve stilini ayarla
    app.setApplicationName("iOS Calculator");
    app.setStyle("Fusion"); // Temel olarak Fusion stilini kullanıp üzerine QSS uygulayacağız

    Calculator window;
    window.show();

    return app.exec();
}