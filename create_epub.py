import os
import json
import logging
import io
from pathlib import Path
from ebooklib import epub
from collections import defaultdict
from bs4 import BeautifulSoup  # для поиска <img src="imgs/...">
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

class EpubCreator:
    def __init__(self, ranobe_json_path, image_quality=85):
        """
        :param ranobe_json_path: путь к ranobe.json
        :param image_quality: качество JPEG, по умолч. 85
        """
        self.ranobe_path = Path(ranobe_json_path)
        if not self.ranobe_path.exists():
            raise FileNotFoundError(f"Нет файла {ranobe_json_path}")

        self.base_dir = self.ranobe_path.parent
        self.image_quality = image_quality
        self._image_cache = {}  # кэш сжатых изображений

        with open(self.ranobe_path, "r", encoding="utf-8") as f:
            self.ranobe_data = json.load(f)

        self.book = epub.EpubBook()

    def create_epub(self):
        """
        Создаём EPUB:
          1) Обложка (при наличии),
          2) Титульная страница,
          3) Главы (группируем по томам),
          4) Сохраняем.
        """
        # Метаданные
        self.book.set_identifier(f"ranobe_{self.ranobe_data['id']}")
        self.book.set_title(self.ranobe_data['title'])
        self.book.set_language("ru")

        # CSS
        style_item = self._create_style()
        self.book.add_item(style_item)

        spine = ["nav"]
        toc = []

        # Обложка
        if self.ranobe_data.get("cover_image"):
            cover_fullpath = self.base_dir / self.ranobe_data["cover_image"]
            if cover_fullpath.exists():
                try:
                    # Создаём страницу cover.xhtml
                    cover_page = epub.EpubHtml(
                        title="Cover",
                        file_name="cover.xhtml",
                        content='<div style="text-align:center;"><img src="images/cover.jpg" alt="cover" /></div>'
                    )
                    cover_page.add_item(style_item)
                    self.book.add_item(cover_page)

                    # Добавляем в spine
                    spine.insert(0, cover_page)

                    # Сжимаем и делаем set_cover
                    cov_data = self._compress_image(cover_fullpath)
                    self.book.set_cover("images/cover.jpg", cov_data)
                except Exception as e:
                    logging.warning(f"Не удалось обработать обложку: {e}")

        # Титульная страница
        title_page = self._make_title_page(style_item)
        self.book.add_item(title_page)
        spine.append(title_page)
        toc.append(title_page)

        # Группируем главы по томам
        volumes_map = defaultdict(list)
        for ch in self.ranobe_data["chapters"]:
            volumes_map[ch["volume"]].append(ch)

        # Сортируем ключи "томов" как числа, но если вдруг не число - как строку
        def _vol_key(v):
            try:
                return float(v)
            except:
                return v

        sorted_vols = sorted(volumes_map.keys(), key=_vol_key)

        volumes_for_toc = []

        for vol in sorted_vols:
            vol_title = f"Том {vol}"
            vol_filename = f"volume_{vol}.xhtml"
            vol_content_parts = [f'<h2 id="volume_{vol}">{vol_title}</h2>']
            chapters_toc = []

            # Сортируем главы по номеру
            chapters = sorted(volumes_map[vol], key=lambda c: float(c["chapter"]))
            for cinfo in chapters:
                ch_id = cinfo["id"]
                ch_title = f"Глава {cinfo['chapter']} - {cinfo['name']}"
                anchor = f"chapter_{ch_id}"

                vol_content_parts.append(f'<h3 id="{anchor}">{ch_title}</h3>')

                # Обрабатываем контент 
                chapter_html = self._process_chapter_content(
                    cinfo["content"],
                    cinfo.get("attachments", [])
                )
                vol_content_parts.append(chapter_html)

                chapters_toc.append((anchor, ch_title))

            # Создаём EpubHtml для всего тома
            vol_html = epub.EpubHtml(
                title=vol_title,
                file_name=vol_filename,
                content="\n".join(vol_content_parts)
            )
            vol_html.add_item(style_item)
            self.book.add_item(vol_html)
            spine.append(vol_html)

            volumes_for_toc.append((vol_title, vol_filename, chapters_toc))

        # Формируем многоуровневое TOC
        for (v_title, v_fname, chap_list) in volumes_for_toc:
            vol_section = epub.Section(v_title, v_fname)
            subitems = []
            for (anchor, ch_title) in chap_list:
                href = f"{v_fname}#{anchor}"
                link_item = epub.Link(href, ch_title, f"chap_{anchor}")
                subitems.append(link_item)
            toc.append((vol_section, subitems))

        self.book.toc = toc
        self.book.spine = spine
        self.book.add_item(epub.EpubNav())
        self.book.add_item(epub.EpubNcx())

        # Сохраняем
        out_name = f"{self.ranobe_data['title']}.epub"
        out_path = self.base_dir / out_name
        epub.write_epub(str(out_path), self.book, {})
        logging.info(f"EPUB создан: {out_path}")
        return str(out_path)

    def _process_chapter_content(self, content, attachments):
        """
        content может быть:
          - строка HTML
          - объект типа {"type": "doc", ...} (ProseMirror-формат)
          - что-то ещё (None и т.д.)
        Если doc-формат, конвертируем в HTML через _doc_to_html.
        Далее создаём BeautifulSoup, ищем <img src="imgs/...">,
        сжимаем и добавляем в EPUB, подменяя src="images/...".
        """
        # 1) Если контент - строка (HTML)
        if isinstance(content, str):
            raw_html = content
        # 2) Если контент - dict (doc-формат)
        elif isinstance(content, dict) and content.get("type") == "doc":
            raw_html = self._doc_to_html(content, attachments)
        else:
            # не знаем, что это, вернём пустую строку
            return ""

        # Теперь обрабатываем получившийся HTML
        soup = BeautifulSoup(raw_html, "html.parser")
        all_imgs = soup.find_all("img")
        for tag in all_imgs:
            old_src = tag.get("src")
            if not old_src:
                continue
            if old_src.startswith("imgs/"):
                local_file = self.base_dir / old_src  # "output/imgs/filename.jpg" и т.п.
                if local_file.exists():
                    # Сжать + добавить
                    new_data = self._compress_image(local_file)
                    new_filename = "images/" + os.path.basename(local_file)
                    # Добавляем в книгу
                    item = epub.EpubItem(
                        uid=f"img_{os.path.basename(old_src)}",
                        file_name=new_filename,
                        media_type="image/jpeg",
                        content=new_data
                    )
                    self.book.add_item(item)

                    # Меняем src
                    tag["src"] = new_filename
                else:
                    logging.warning(f"Файл {local_file} не найден, пропускаем.")
        return str(soup)

    def _doc_to_html(self, doc_content, attachments):
        """
        Конвертация ProseMirror-формата (doc) в простой HTML.
        attachments - список вложений, где filename соответствует "image".
        
        Пример структуры:
          {
            "type": "doc",
            "content": [
              {"type": "paragraph", "content": [{"type": "text","text":"..."}]},
              {"type": "image", "attrs": {"images": [{"image":"xxxx"}]}},
              ...
            ]
          }
        
        Нужно:
         - paragraph -> <p>текст</p>
         - image -> <img src="imgs/файл-из-attachments" />
         - если встречаются другие типы, игнорируем или обрабатываем как абзац.
        """
        if doc_content.get("type") != "doc":
            return ""

        content_arr = doc_content.get("content", [])
        html_parts = []

        # Для быстрого доступа: "имяБезРасширения" -> attachment["filename"]
        #   или просто сделаем словарь   image_name -> filename
        name_map = {}
        for att in attachments:
            # Обычно att["filename"] = "8a57f2de.jpg"
            # а в doc-е:   "image": "8a57f2de-df06-4a20-93af-a6e721fedfb2"
            # Нужно сопоставить, часто это совпадает с `att["filename"]` без расширения,
            # но бывает точное совпадение. Подгоняем логику под вашу структуру.
            
            # Если "images":[{"image":"17b9f599-efc3-4bee-8d15-9ad24da9dfac"}]
            # тогда ищем attachment, у которого filename = "17b9f599-efc3-4bee-8d15-9ad24da9dfac.jpg"
            base_name = os.path.splitext(att["filename"])[0]  # "17b9f599-efc3-4bee-8d15-9ad24da9dfac"
            name_map[base_name] = att["filename"]

        for node in content_arr:
            ntype = node.get("type")

            # 1) Абзац
            if ntype == "paragraph":
                paragraph_text = ""
                if "content" in node:
                    for inline in node["content"]:
                        if inline.get("type") == "text":
                            paragraph_text += inline.get("text", "")
                if paragraph_text.strip():
                    html_parts.append(f"<p>{paragraph_text}</p>")

            # 2) Изображение
            elif ntype == "image":
                # атрибуты лежат в node["attrs"]["images"]
                # это массив вида [{"image":"8a57f2de-df06-4a20-93af-a6e721fedfb2"}]
                images_list = node.get("attrs", {}).get("images", [])
                for img_obj in images_list:
                    img_name = img_obj.get("image")  # "8a57f2de-df06-4a20-93af-a6e721fedfb2"
                    if not img_name:
                        continue
                    # Сопоставляем с attachments
                    filename = name_map.get(img_name)
                    if filename:
                        html_parts.append(f'<img src="imgs/{filename}"/>')
                    else:
                        # Если нет в attachments, пропустим
                        logging.warning(f"Не нашли attachment для {img_name}")
            
            # 3) Любой другой тип (table, heading, list и пр.) - можно дописать по надобности
            else:
                # пока просто игнорируем или можно сделать ещё один <p>?
                pass

        return "\n".join(html_parts)

    def _compress_image(self, img_path):
        """
        Сжимаем (конвертируем) в JPEG, используем кэш, чтобы не обрабатывать повторно.
        """
        if img_path in self._image_cache:
            return self._image_cache[img_path]

        try:
            with Image.open(img_path) as im:
                if im.mode != "RGB":
                    im = im.convert("RGB")
                buf = io.BytesIO()
                im.save(buf, format="JPEG", optimize=True, quality=self.image_quality)
                buf.seek(0)
                data = buf.read()
                self._image_cache[img_path] = data
                return data
        except Exception as e:
            logging.warning(f"Ошибка сжатия {img_path}: {e}")
            return img_path.read_bytes()

    def _make_title_page(self, style_item):
        title = self.ranobe_data.get("title", "Без названия")
        orig = self.ranobe_data.get("original_title", "")
        desc = self.ranobe_data.get("description", "")

        # Ссылка "Далее" -> первый том
        volumes = [ch["volume"] for ch in self.ranobe_data["chapters"]]
        link = "#"
        if volumes:
            try:
                first_vol = sorted(volumes, key=lambda x: float(x))[0]
                link = f"volume_{first_vol}.xhtml#volume_{first_vol}"
            except:
                pass

        html = f"""
        <h1 style="text-align:center;">{title}</h1>
        <h2 style="text-align:center;">{orig}</h2>
        <h3>Описание</h3>
        <p>{desc}</p>
        <p style="text-align:center;">
          <a href="{link}" style="font-size:1.2em;">Далее &raquo;</a>
        </p>
        <h3>Содержание</h3>
        <p>Используйте оглавление или кнопку &laquo;Далее&raquo;.</p>
        """
        page = epub.EpubHtml(
            title="Титульная страница",
            file_name="title_page.xhtml",
            content=html
        )
        page.add_item(style_item)
        return page

    def _create_style(self):
        css = '''
        @namespace epub "http://www.idpf.org/2007/ops";
        body {
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0 auto;
            max-width: 800px;
        }
        h1, h2, h3 {
            text-align: center;
            margin: 1em 0;
        }
        p {
            margin: 0.5em 0;
            text-indent: 1.5em;
        }
        img {
            display: block;
            margin: 1em auto;
            max-width: 100%;
        }
        '''
        style_item = epub.EpubItem(
            uid="main_style",
            file_name="style/main.css",
            media_type="text/css",
            content=css
        )
        return style_item

def main():
    print("Введите путь к ranobe.json:")
    path = input().strip()
    if not os.path.exists(path):
        print("Файл не найден!")
        return
    try:
        creator = EpubCreator(path, image_quality=85)
        epub_file = creator.create_epub()
        print(f"Готово! EPUB: {epub_file}")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    main()
