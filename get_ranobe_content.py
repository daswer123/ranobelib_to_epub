import logging
import os
import json
import time
from pathlib import Path
import requests
from tqdm import tqdm
from bs4 import BeautifulSoup  # чтобы заменить ссылки прямо в HTML

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

def extract_book_id(url):
    """
    Извлекаем ID книги из URL (например /ru/book/1234--kniga, /ru/1234--kniga).
    Возвращаем '1234--kniga' или None, если не получилось.
    """
    import re
    patterns = [
        r'/ru/book/(\d+--[\w-]+)',
        r'/ru/(\d+--[\w-]+)/',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def get_book_info(book_id):
    """
    Получаем инфо о книге (название, описание).
    """
    api_url = f"https://api2.mangalib.me/api/manga/{book_id}?fields[]=summary"
    r = requests.get(api_url)
    if r.status_code == 200:
        return r.json().get('data', {})
    return None

def get_cover_url(book_id):
    """
    Получаем URL обложки.
    """
    api_url = f"https://api2.mangalib.me/api/manga/{book_id}"
    r = requests.get(api_url)
    if r.status_code == 200:
        data = r.json().get('data', {})
        cover_data = data.get('cover', {})
        return cover_data.get('default')
    return None

def get_chapters_list(book_id):
    """
    Получаем список глав: [ {"tom": int, "chapter": float, "name": str, "id": int}, ... ]
    """
    api_url = f"https://api2.mangalib.me/api/manga/{book_id}/chapters"
    r = requests.get(api_url)
    if r.status_code == 200:
        data = r.json().get('data', [])
        chapters = []
        for ch in data:
            chapters.append({
                "tom": int(ch['volume']),
                "chapter": float(ch['number']),
                "name": ch['name'],
                "id": ch['id']
            })
        chapters.sort(key=lambda x: (x['tom'], x['chapter']))
        return chapters
    return []

def get_chapter_data(book_id, volume, chapter, max_retries=5, sleep_time=1):
    """
    Получаем контент и вложения главы. Возвращаем словарь или None.
    Повторяем до max_retries раз с паузой в sleep_time секунд,
    если сервер не вернул код 200.
    """
    if chapter.endswith('.0'):
        chapter = chapter.split('.')[0]
    api_url = f"https://api2.mangalib.me/api/manga/{book_id}/chapter?number={chapter}&volume={volume}"

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(api_url)
            if r.status_code == 200:
                return r.json().get('data')
            else:
                logging.warning(
                    f"Не удалось загрузить главу (статус {r.status_code}), попытка {attempt}/{max_retries}"
                )
        except Exception as e:
            logging.error(f"Ошибка при запросе главы: {e}")

        if attempt < max_retries:
            time.sleep(sleep_time)
    # Если все попытки провалились, возвращаем None
    return None

def download_image(url, save_path, max_retries=5, sleep_time=1):
    """
    Скачиваем картинку, сохраняем в save_path.
    Повторяем до max_retries раз с паузой в sleep_time секунд,
    если сервер не вернул код 200 или возникла ошибка.
    """
    for attempt in range(1, max_retries + 1):
        try:
            if url.startswith("https://"):
                resp = requests.get(url)
            else:
                # если url типа "/uploads/...":
                resp = requests.get(f"https://ranobelib.me{url}")

            if resp.status_code == 200:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                return True
            else:
                logging.warning(
                    f"Не удалось скачать {url}, код {resp.status_code}, попытка {attempt}/{max_retries}"
                )
        except Exception as e:
            logging.error(f"Ошибка скачивания {url}: {e}")

        if attempt < max_retries:
            time.sleep(sleep_time)
    return False

def fix_img_links_in_html(html_str, output_folder):
    """
    На вход: исходный HTML (как строка), где могут быть <img loading="lazy" src="https://ranobelib.me/...">
    Задача:
      - Найти все <img src="..."> (используем BeautifulSoup).
      - Для каждого img, скачать локально (imgs/filename.jpg).
      - Заменить src="..." на "imgs/filename.jpg".
    Возвращаем новый HTML со всеми локальными ссылками.
    """
    soup = BeautifulSoup(html_str, "html.parser")
    imgs = soup.find_all("img")
    for tag in imgs:
        # Удаляем loading="lazy", если не нужно
        if 'loading' in tag.attrs:
            del tag.attrs['loading']

        src_val = tag.get("src")
        if not src_val:
            continue

        if src_val.startswith("http") or src_val.startswith("/uploads/"):
            # Извлекаем имя файла
            from urllib.parse import urlparse, unquote
            parsed = urlparse(src_val)
            filename = os.path.basename(parsed.path)  # извлечём имя файла
            if not filename:
                filename = "img_unknown.jpg"

            local_path = Path(output_folder) / "imgs" / filename
            if download_image(src_val, local_path):
                tag["src"] = f"imgs/{filename}"
        # Иначе, если уже локальная, не трогаем.
    return str(soup)

def fix_img_links_in_doc(doc_data, output_folder, attachments):
    """
    Обработка контента в doc-формате (ProseMirror).
    Зависит от структуры doc_data и attachments.
    Здесь пример, где мы скачиваем файлы из attachments,
    но не меняем напрямую сам doc (если ссылки на изображения
    формируются автоматикой по имени).
    """
    for att in attachments:
        url = att['url']
        filename = att['filename']
        local_path = Path(output_folder) / "imgs" / filename
        download_image(url, local_path)
    return doc_data

def get_ranobe_content(book_url, output_dir="output",progress=None):
    """
    Основная функция:
      1) Извлекаем book_id
      2) Скачиваем инфо о книге, обложку
      3) Получаем список глав
      4) Для каждой главы: получаем контент, скачиваем картинки (прямо в тексте <img>),
         attachments (тоже скачиваем), подменяем src="..." на локальное (imgs/...)
      5) Сохраняем ranobe.json (все ссылки уже локальные).
    """
    # if progress:
    progress(0, desc="Подготовка директорий")
        
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    imgs_path = out_path / "imgs"
    imgs_path.mkdir(exist_ok=True)

    # if progress:
    progress(0.05, desc="Получение информации о книге")
        

    book_id = extract_book_id(book_url)
    if not book_id:
        raise ValueError("Не удалось извлечь ID книги")

    info = get_book_info(book_id)
    if not info:
        raise ValueError("Не удалось получить инфо о книге")

    # if progress:
    progress(0.1, desc="Загрузка обложки")
        

    # Скачиваем обложку
    cover_local = None
    cover_url = get_cover_url(book_id)
    if cover_url:
        cover_filename = "cover" + Path(cover_url).suffix
        cover_full_path = imgs_path / cover_filename
        if download_image(cover_url, cover_full_path):
            cover_local = f"imgs/{cover_filename}"

    # if progress:
    progress(0.15, desc="Получение списка глав")
        

    # Получаем список глав
    chapters_list = get_chapters_list(book_id)
    logging.info(f"Найдено глав: {len(chapters_list)}")

    all_chapters = []

    # Используем progress.tqdm для отслеживания прогресса
    if True:
        chapters_iter = progress.tqdm(chapters_list, desc="Загрузка глав")
    # else:
    #     chapters_iter = tqdm(chapters_list, desc="Загрузка глав")
        
    for ch in chapters_iter:
        tom = str(ch['tom'])
        chap_str = str(ch['chapter'])
        ch_data = get_chapter_data(book_id, tom, chap_str)
        if not ch_data:
            logging.warning(f"Пропускаем главу {tom} {chap_str} (не удалось загрузить).")
            continue

        attachments = ch_data.get("attachments", [])
        content = ch_data.get("content", "")

        # Если контент строковый (HTML)
        if isinstance(content, str):
            new_html = fix_img_links_in_html(content, out_path)
            content = new_html
        # Если контент doc-формат
        elif isinstance(content, dict) and content.get("type") == "doc":
            content = fix_img_links_in_doc(content, out_path, attachments)

        # Скачиваем все attachments (часто совпадают с изображениями в тексте)
        for att in attachments:
            url = att["url"]
            fname = att["filename"]
            local_file = imgs_path / fname
            download_image(url, local_file)

        # Формируем запись о главе
        chapter_rec = {
            "id": ch_data["id"],
            "volume": ch_data["volume"],
            "chapter": ch_data["number"],
            "name": ch_data["name"],
            "attachments": attachments,
            "content": content
        }
        all_chapters.append(chapter_rec)

    # if progress:
    progress(0.95, desc="Сохранение результатов")
        

    # Формируем итоговую структуру и сохраняем
    ranobe_data = {
        "id": book_id,
        "title": info.get("rus_name", "Без названия"),
        "original_title": info.get("name", ""),
        "description": info.get("summary", ""),
        "cover_image": cover_local,  # "imgs/cover.jpg" или None
        "chapters": all_chapters
    }

    ranobe_json_path = out_path / "ranobe.json"
    with open(ranobe_json_path, "w", encoding="utf-8") as f:
        json.dump(ranobe_data, f, ensure_ascii=False, indent=2)
    logging.info(f"Сохранён ranobe.json: {ranobe_json_path}")
    
    # if progress:
    progress(1.0, desc="Готово")

    return str(ranobe_json_path)

def main():
    url = input("Введите URL книги: ").strip()
    try:
        rjson = get_ranobe_content(url, output_dir="output")
        print(f"Готово! Данные о книге в: {rjson}")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    main()
