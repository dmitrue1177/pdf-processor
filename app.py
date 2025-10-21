import os
import io
import ast
from flask import Flask, request, render_template, send_file, jsonify
import google.generativeai as genai
from PIL import Image

# --- Библиотека для PDF ---
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

# --- Конфигурация Flask и Gemini ---
app = Flask(__name__)

# Загружаем API ключ из переменных окружения
# (Это безопасный способ хранения ключей)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    raise ValueError("Необходимо установить переменную окружения GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

# --- Регистрация шрифтов для ReportLab ---
try:
    pdfmetrics.registerFont(TTFont('DejaVuSans', 'DejaVuSans.ttf'))
    FONT_NAME = 'DejaVuSans'
    pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', 'DejaVuSans-Bold.ttf'))
    FONT_NAME_BOLD = 'DejaVuSans-Bold'
    print("Шрифты DejaVuSans успешно зарегистрированы.")
except Exception as e:
    FONT_NAME = 'Helvetica'
    FONT_NAME_BOLD = 'Helvetica-Bold'
    print(f"Предупреждение: Шрифты DejaVuSans не найдены. Ошибка: {e}")


# --- Функция для генерации PDF (взята из предыдущего решения) ---
def create_pdf_in_memory(all_pages_data):
    """Создает PDF в памяти и возвращает его как байтовый объект."""
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.5*cm, leftMargin=0.5*cm,
        topMargin=1.5*cm, bottomMargin=0.5*cm
    )

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle('CellStyle', parent=styles['Normal'], fontName=FONT_NAME, fontSize=6.5, leading=8)
    header_style = ParagraphStyle('HeaderStyle', parent=cell_style, fontName=FONT_NAME_BOLD, alignment=TA_CENTER)
    section_header_style = ParagraphStyle('SectionHeaderStyle', parent=cell_style, fontName=FONT_NAME_BOLD, alignment=TA_LEFT)
    right_align_style = ParagraphStyle('RightCellStyle', parent=cell_style, alignment=TA_RIGHT)
    page_header_style = ParagraphStyle('PageHeaderStyle', parent=styles['Normal'], fontName=FONT_NAME_BOLD, fontSize=10, alignment=TA_LEFT)

    story = []
    page_width, _ = A4
    available_width = page_width - 1*cm
    
    col_widths = [
        available_width * 0.73, available_width * 0.09,
        available_width * 0.09, available_width * 0.09
    ]
    headers = ['Номенклатура', 'Цена продажи\n(из фото)', 'Полка регулярная\n(из файла)', 'Полка промо\n(из файла)']
    
    for i, page_data in enumerate(all_pages_data):
        header_text = f'ПОСТАВЩИК: ООО "АРИС ТРЕЙД" Страница {i + 1} из {len(all_pages_data)}'
        story.append(Paragraph(header_text, page_header_style))
        story.append(Spacer(1, 0.3*cm))

        table_data = []
        table_data.append([Paragraph(h.replace('\n', '<br/>'), header_style) for h in headers])

        dynamic_styles = []
        row_index = 1
        for row in page_data:
            if len(row) == 1:
                p = Paragraph(row[0], section_header_style)
                table_data.append([p, '', '', ''])
                dynamic_styles.append(('SPAN', (0, row_index), (-1, row_index)))
                dynamic_styles.append(('BACKGROUND', (0, row_index), (-1, row_index), colors.Color(220/255, 220/255, 220/255)))
                dynamic_styles.append(('TOPPADDING', (0, row_index), (-1, row_index), 0.5))
                dynamic_styles.append(('BOTTOMPADDING', (0, row_index), (-1, row_index), 0.5))
            else:
                while len(row) < 4:
                    row.append('—')
                
                wrapped_row = [
                    Paragraph(str(row[0]), cell_style),
                    Paragraph(str(row[1]), right_align_style),
                    Paragraph(str(row[2]), right_align_style),
                    Paragraph(str(row[3]), right_align_style),
                ]
                table_data.append(wrapped_row)
            row_index += 1

        tbl = Table(table_data, colWidths=col_widths)
        
        style = TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(200/255, 200/255, 200/255)),
        ])
        for s in dynamic_styles:
            style.add(*s)
        tbl.setStyle(style)
        
        story.append(tbl)
        story.append(PageBreak())

    if story:
        story.pop()

    doc.build(story)
    
    buffer.seek(0)
    return buffer


# --- Основной маршрут приложения ---
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # --- 1. Получение файлов из формы ---
        price_file = request.files.get('price_file')
        image_files = request.files.getlist('image_files')

        if not price_file or not image_files:
            return jsonify({"error": "Необходимо загрузить и прайс-лист, и изображения."}), 400

        try:
            # --- 2. Подготовка данных для Gemini ---
            price_text = price_file.read().decode('utf-8')
            
            image_parts = []
            for image_file in image_files:
                # Убедимся, что файл - изображение
                if image_file.mimetype.startswith('image/'):
                    img = Image.open(image_file.stream)
                    image_parts.append(img)

            # --- 3. Формирование промпта для Gemini ---
            # Используем Gemini 2.5 Pro как лучший вариант
            model = genai.GenerativeModel('gemini-2.5-pro')

            # Тот самый стартовый промпт
            initial_prompt = """
            ### **Задача:**
            Обработать данные из набора изображений и одного текстового файла. Для каждого изображения создать отдельную таблицу с сопоставленными ценами.

            ### **Входные данные:**
            1.  **Набор исходных изображений:** Фотографии документов с номенклатурой товаров и их ценами продажи.
            2.  **Файл с ценами:** Единый текстовый файл (`.txt`), содержащий мастер-прайс-лист с регулярными и промо-ценами для всех товаров.

            ### **Пошаговая инструкция:**
            **Шаг 1: Последовательная обработка изображений**
            *   Для **каждого изображения** из предоставленного набора выполни следующие действия:
                *   Аккуратно извлеки всю информацию и преобразуй её в структурированную таблицу.
                *   Сохрани исходную структуру, включая заголовки разделов (например, "Вина АРМЕНИИ") и названия производителей.
                *   Игнорируй любые рукописные пометки.
                *   Назови колонки этой промежуточной таблицы: `Номенклатура`, `Цена продажи`.
                *   Из колонки `Цена продажи` удали текстовое обозначение "руб.".

            **Шаг 2: Сопоставление данных и поиск цен**
            *   Для каждой товарной позиции из таблиц, полученных на Шаге 1, найди соответствующую ей запись в едином **Файле с ценами**.
            *   **Важно:** Наименования товаров в источниках могут незначительно отличаться. Выполняй сопоставление по смысловому соответствию.

            **Шаг 3: Формирование итоговых таблиц**
            *   Для каждого обработанного изображения создай свою **финальную таблицу**. Она должна содержать все данные из промежуточной таблицы (Шаг 1) с добавлением двух новых колонок из **Файла с ценами**.
            *   Структура колонок для каждой таблицы должна быть следующей:
                1.  `Номенклатура` (из изображения)
                2.  `Цена продажи (из фото)` (из изображения)
                3.  `Полка регулярная (из файла)` (найденная в файле, если нет - ставь '—')
                4.  `Полка промо (из файла)` (найденная в файле, если нет - ставь '—')

            **Шаг 4: Формат вывода**
            *   Твоя задача - вернуть ТОЛЬКО Python-код, содержащий один-единственный список `all_pages_data`.
            *   `all_pages_data` - это список, где каждый элемент - это данные для ОДНОЙ страницы (т.е. одного изображения).
            *   Каждый элемент `all_pages_data` - это, в свою очередь, список строк для таблицы.
            *   Пример структуры: `all_pages_data = [ [ ["Заголовок раздела"], ["Номенклатура 1", "100,00", "110,00", "99,00"] ], [ ["Другой заголовок"], ["Номенклатура 2", "200,00", "220,00", "199,00"] ] ]`
            *   Не добавляй никаких объяснений, комментариев или другого текста. Только `all_pages_data = [...]`.
            """

            # Собираем все части в один запрос
            prompt_parts = [
                initial_prompt,
                "\n\n--- СОДЕРЖИМОЕ ФАЙЛА С ЦЕНАМИ ---\n",
                price_text,
                "\n\n--- ИЗОБРАЖЕНИЯ ДЛЯ ОБРАБОТКИ ---\n"
            ] + image_parts

            # --- 4. Отправка запроса и получение ответа ---
            response = model.generate_content(prompt_parts)
            
            # --- 5. Парсинг ответа от Gemini ---
            # Убираем "обертку" кода и возможные ```python ```
            response_text = response.text.strip()
            if response_text.startswith("```python"):
                response_text = response_text[9:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            # Находим начало списка
            list_start_index = response_text.find('[')
            # Выделяем только сам список
            list_string = response_text[list_start_index:]
            
            # Безопасно преобразуем строку в объект Python
            all_pages_data = ast.literal_eval(list_string)

            # --- 6. Генерация PDF и отправка пользователю ---
            pdf_buffer = create_pdf_in_memory(all_pages_data)
            
            return send_file(
                pdf_buffer,
                as_attachment=True,
                download_name='compact_invoice_multipage.pdf',
                mimetype='application/pdf'
            )

        except Exception as e:
            print(f"Произошла ошибка: {e}")
            return jsonify({"error": f"Произошла внутренняя ошибка: {str(e)}"}), 500

    # Для GET запроса просто отображаем страницу
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
