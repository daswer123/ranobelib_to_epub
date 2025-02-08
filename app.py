import gradio as gr
from pipeline import run_pipeline
import os
import urllib3
from uuid import uuid4

def process_url(url):
    try:
        # Проверяем что это ranobelib.me
        if not url.startswith("https://ranobelib.me"):
            return None, "Ошибка: Принимаются только ссылки с ranobelib.me"
            
        random_folder = str(uuid4())
        # create output folder
        output_dir = f'output/{random_folder}'
        os.makedirs(output_dir, exist_ok=True)

        output_path = run_pipeline(url, output_dir=output_dir,progress=gr.Progress())
        
        # Если файл создан успешно, возвращаем его и сообщение в статус
        if os.path.exists(output_path):
            return output_path, f"EPUB создан успешно: {output_path}"
        else:
            return None, "Ошибка: Файл не был создан"
            
    except Exception as e:
        raise e
        return None, f"Ошибка: {str(e)}"

# Создаем интерфейс
with gr.Blocks() as demo:
    gr.Markdown("""
    # Конвертер ранобэ с сайта ranobelib.me в EPUB
    
    Удобный инструмент для создания электронных книг из любимых ранобэ. Программа автоматически соберет все тома и главы в единый EPUB-файл.
    
    ### Инструкция:
    1. Скопируйте ссылку на ранобэ с сайта **ranobelib.me**
    2. Вставьте её в поле ввода ниже
    3. Нажмите кнопку "Получить Epub" и дождитесь завершения конвертации
    
    ### Пример ссылки:
    ```
    https://ranobelib.me/ru/book/88265--kurasu-no-daikiraina-joshi-to-kekkon-suru-koto-ni-natta
    ```
    
    ### Особенности:
    - Работает только с сайтом **ranobelib.me**
    - Время конвертации зависит от размера произведения
    - В готовый файл включаются:
      - Структурированное оглавление
      - Все иллюстрации в высоком качестве
      - Текст в удобном для чтения формате
    - EPUB-файл совместим со всеми современными читалками
    """)
    status_bar = gr.Label(label="Статус")

    with gr.Row():
        with gr.Column():
            url_input = gr.Textbox(
                label="URL ранобэ",
                placeholder="Вставьте ссылку на ранобэ с Ranobelib.me"
            )
        with gr.Column():
            output_files = gr.Files(label="Выходные файлы")
            convert_btn = gr.Button("Получить Epub")
    
    
    convert_btn.click(
        fn=process_url,
        inputs=url_input,
        outputs=[output_files,status_bar]
    )

if __name__ == "__main__":
    demo.launch()
