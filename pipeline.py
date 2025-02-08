import logging
from get_ranobe_content import get_ranobe_content
from create_epub import EpubCreator
import gradio as gr
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

def run_pipeline(book_url, output_dir="output", progress=None):
    """
    Запускает полный цикл:

      1) Получение контента (get_ranobe_content)
      2) Создание EPUB (create_epub)
    Возвращает путь к созданному EPUB-файлу.
    """

    progress(0, desc="Начинаем обработку")
    

    # 1. Скачиваем данные ранобэ и картинки
    progress(0.1, desc="Получение контента ранобэ")
    ranobe_json_path = get_ranobe_content(book_url, output_dir=output_dir, progress=progress)
    
    progress(0.8, desc="Создание EPUB файла")
    creator = EpubCreator(ranobe_json_path, image_quality=85)
    epub_path = creator.create_epub()

    progress(1.0, desc="Готово")
    return epub_path

def main():
    """
    Примерный вызов для полного конвейера:
    python pipeline.py
    """

    url = input("Введите URL ранобэ (любой URL с сайта с ID книги): ").strip()
    try:
        epub_path = run_pipeline(url, output_dir="output")
        print(f"Готово! EPUB создан в: {epub_path}")
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    main()
