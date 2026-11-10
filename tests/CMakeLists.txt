cmake_minimum_required(VERSION 3.16)

project(iOSCalculator VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Qt6 arayüzlerini bul
find_package(Qt6 REQUIRED COMPONENTS Widgets)

# Kaynak dosyalarını listele
set(SOURCES
    main.cpp
    calculator.cpp
)

set(HEADERS
    calculator.h
)

# Uygulama hedefini oluştur
add_executable(${PROJECT_NAME}
    ${SOURCES}
    ${HEADERS}
)

# Qt6 widget kütüphanesine bağlantı
target_link_libraries(${PROJECT_NAME} PRIVATE Qt6::Widgets)

# Derleme sonrası binariyi kopyalamak için (opsiyonel)
# install(TARGETS ${PROJECT_NAME} DESTINATION bin)