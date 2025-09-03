import os
from pathlib import Path

# --- НАСТРОЙКИ ---
# Папка, в которой находится ваш проект
ROOT_DIRECTORY = Path(__file__).parent

# Имя файла, в который будет сохранен весь код
OUTPUT_FILE = "full_project_code.txt"

# Папки, которые нужно исключить из сборки (очень важно для venv!)
EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    ".idea",
    "bot_database.sqlite-journal"  # Исключаем временные файлы БД
}

# Расширения файлов, которые нужно включить
INCLUDE_EXTENSIONS = {
    ".py",
    ".txt",
    ".env",
    ".json"
}


# ------------------

def package_project():
    """Собирает все текстовые файлы проекта в один большой текстовый файл."""
    full_code = []

    print(f"Начинаю сборку проекта из папки: {ROOT_DIRECTORY}")

    # os.walk рекурсивно обойдет все папки и файлы
    for dirpath, dirnames, filenames in os.walk(ROOT_DIRECTORY):

        # Удаляем из списка папок те, которые нужно исключить
        # Это предотвращает обход этих папок os.walk
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        # Преобразуем путь в объект Path для удобства
        current_path = Path(dirpath)

        for filename in filenames:
            # Собираем полный путь к файлу
            file_path = current_path / filename

            # Проверяем расширение файла
            if file_path.suffix in INCLUDE_EXTENSIONS:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                        # Получаем относительный путь для заголовка
                        relative_path = file_path.relative_to(ROOT_DIRECTORY)

                        # Формируем красивый заголовок
                        header = f"--- FILE: {str(relative_path).replace(os.sep, '/')} ---\n"

                        # Добавляем заголовок и содержимое в наш список
                        full_code.append(header)
                        full_code.append(content)
                        full_code.append("\n\n")  # Добавляем отступ между файлами

                        print(f"  [+] Добавлен файл: {relative_path}")

                except Exception as e:
                    print(f"  [!] Ошибка чтения файла {file_path}: {e}")

    # Записываем все собранное в один файл
    try:
        with open(ROOT_DIRECTORY / OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.writelines(full_code)
        print(f"\n✅ Проект успешно собран в файл: {OUTPUT_FILE}")
    except Exception as e:
        print(f"\n❌ Ошибка записи в итоговый файл: {e}")


if __name__ == "__main__":
    package_project()