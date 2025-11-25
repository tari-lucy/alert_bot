#!/bin/bash

# Скрипт для запуска бота

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "Ошибка: файл .env не найден!"
    echo "Скопируйте .env.example в .env и заполните необходимые данные:"
    echo "  cp .env.example .env"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

echo "Активация виртуального окружения..."
source venv/bin/activate

echo "Установка/обновление зависимостей..."
pip install -r requirements.txt

echo "Запуск бота..."
python3 main.py
