"""Generate six slides from real Ditto-driven captures, without network access.

Install dependencies with pip install -r team/presentation-requirements.txt.
The required captures and their corresponding API evidence live in presentation/.
"""
import argparse
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parents[1]
W, H = 960, 540
BG, CARD, TEXT, MUTED, GREEN, RED, BLUE = map(HexColor, ['#101722', '#182435', '#eef4fa', '#a8bad0', '#69cbb6', '#ed9aa5', '#91b4e7'])
NAVIGATOR = ("Из корня выполните make up, затем make team-smoke. "
             "Откройте http://localhost:3000, логин admin. "
             "Перейдите на /stepnoe/?view=mine. "
             "В карточке 'Фабрика остановлена' нажмите Запуск. "
             "Дождитесь Ditto. Наблюдайте заполнение буфера и простой. "
             "Нажмите Сброс. Grafana: Dashboards → Stepnoe → Mass Balance")


def fonts():
    candidates = [(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')),
                  (Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf'))]
    pair = next((p for p in candidates if all(f.is_file() for f in p)), None)
    if pair is None:
        raise SystemExit('Install fonts-dejavu-core (Ubuntu) or provide Arial on Windows.')
    for name, path in zip(('Body', 'BodyBold'), pair):
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily('Body', normal='Body', bold='BodyBold')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'presentation.pdf')
    args = parser.parse_args()
    captures = ROOT / 'team' / 'presentation'
    evidence = json.loads((captures / 'evidence.json').read_text(encoding='utf-8'))
    before, during, after = (evidence[name]['audit'] for name in ('before', 'during', 'after'))
    if not (before['scenario_plant_erp'] == 0 and during['scenario_plant_erp'] == 1
            and during['truck_queue_length'] == 2 and during['buffer_fill_pct'] >= 99.99
            and during['excavator_interlock'] == 1 and after['scenario_plant_erp'] == 0
            and after['anomaly'] == 0):
        raise ValueError('Screenshots must correspond to a verified real stop/reset sequence.')
    fonts()
    c = canvas.Canvas(str(args.output), pagesize=(W, H), pageCompression=1, invariant=1)
    c.setTitle('Степное | Контроль массы и независимые измерения')
    c.setAuthor('Team 051 · OpenEgiz')
    c.setSubject('Digital Mine: 18 twins, independent instruments, cascade evidence and judge navigation')

    def text(value, x, y, size=14, color=TEXT, bold=False, width=850):
        style = ParagraphStyle('body', fontName='BodyBold' if bold else 'Body', fontSize=size,
                               leading=size * 1.42, textColor=color)
        p = Paragraph(value, style)
        _, height = p.wrap(width, H)
        if y - height < 36:
            raise ValueError(f'Text crosses slide footer: {value[:60]}')
        p.drawOn(c, x, y - height)
        return height

    def card(x, y, w, h, fill=CARD):
        c.setFillColor(fill)
        c.roundRect(x, y, w, h, 12, stroke=0, fill=1)

    def capture(name, x, y, width, height):
        path = captures / name
        if not path.is_file():
            raise FileNotFoundError(f'Required actual UI capture: {path}')
        image = ImageReader(str(path))
        iw, ih = image.getSize()
        scale = min(width / iw, height / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(image, x + (width-dw)/2, y + (height-dh)/2,
                    width=dw, height=dh, mask='auto')

    def arrow(x1, y1, x2, y2, color=GREEN):
        c.setStrokeColor(color)
        c.setLineWidth(2)
        c.line(x1, y1, x2, y2)
        import math
        angle = math.atan2(y2-y1, x2-x1)
        for offset in (-.45, .45):
            c.line(x2, y2, x2-8*math.cos(angle+offset), y2-8*math.sin(angle+offset))

    def page(n, eyebrow, title, subtitle):
        c.setFillColor(BG)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        text(eyebrow, 42, 509, 11, GREEN, True)
        text(title, 42, 480, 30, bold=True)
        text(subtitle, 42, 428, 13, MUTED)
        c.setStrokeColor(HexColor('#2b3d52'))
        c.line(42, 33, W-42, 33)
        c.setFillColor(MUTED)
        c.setFont('Body', 8)
        c.drawString(42, 16, 'TEAM 051 / OPENEGIZ / СИНТЕТИЧЕСКИЕ ДАННЫЕ')
        c.drawRightString(W-42, 16, f'{n:02} / 06')

    page(1, 'ЗАМЫСЕЛ', 'Каждая тонна имеет подтверждение',
         'ГОК «Степное» - вымышленный объект. Пользователь: финансовый контролер предприятия.')
    for x, value, label in [(42, '432 000 т/мес', 'сырье: 600 т/ч × 24 × 30'), (340, '3% → 7%', 'дождь: влажность по сухой основе'), (638, '> 1 т', 'порог остатка сверки')]:
        card(x, 285, 280, 98)
        text(value, x+18, 365, 27, GREEN, True, 245)
        text(label, x+18, 319, 12, MUTED, width=245)
    text('Проблема', 42, 251, 18, bold=True)
    text('Бумажные остатки могут расти при остановке производства. Ошибка датчика или влажности может выглядеть как утрата руды.', 42, 219, 16, width=850)
    text('Решение, которое помогает принять двойник', 42, 151, 18, bold=True)
    text('Остановить недостоверное начисление ERP или отправить на проверку измерительный канал. Сначала установить причину, затем корректировать учет.', 42, 121, 15, MUTED, width=830)
    c.showPage()

    page(2, 'АРХИТЕКТУРА', 'Измерения отдельно от истины',
         'UI читает Eclipse Ditto. Решение аудита строится по показаниям и учетному регистру.')
    for x, title, body in [(42, 'Модель + приборы', 'Весы / LiDAR<br/>Влажность / ERP<br/>Свои часы и статусы'),
                           (273, 'MQTT', 'Ditto protocol<br/>QoS 1<br/>Полные features'),
                           (504, 'Eclipse Ditto', '18 двойников<br/>Иерархия + связи<br/>Единицы и timestamps'),
                           (735, 'UI / Grafana', '3D + 2D<br/>Сверка и причины<br/>Запуск / сброс')]:
        card(x, 237, 183, 143)
        text(title, x+14, 363, 15, GREEN, True, 160)
        text(body, x+14, 327, 12, MUTED, width=160)
    for x in (225, 456, 687):
        arrow(x+2, 306, x+44, 306)
    text('MQTT → Telegraf → InfluxDB → графики Grafana', 273, 211, 15, BLUE, width=620)
    card(42, 65, 876, 107)
    text('Управление: кнопка → HTTP API /stepnoe/api/scenarios → MQTT → модель → Ditto', 60, 153, 14, GREEN, True, 835)
    text('Аудит: ERP на времени измерения + весы + LiDAR + влажность и допуски. В 3D показан измеренный объем LiDAR; эталон симуляции доступен через «Отладочный режим».', 60, 118, 13, MUTED, width=830)
    c.showPage()

    page(3, 'ПРОСТРАНСТВЕННАЯ МОДЕЛЬ', 'Каскадная остановка на стенде',
         'Реальный снимок интерфейса после запуска «Фабрика остановлена». Источник состояния: Eclipse Ditto.')
    capture('cascade-scene.png', 42, 65, 610, 324)
    card(675, 65, 243, 324)
    text('Каскад подтвержден', 693, 369, 17, RED, True, 205)
    text('Переработка: 0 т/ч<br/><br/>Буфер: 360 т / 100%<br/><br/>Очередь: 2 самосвала<br/><br/>Экскаватор: IDLE<br/><br/>Красная сетка: выпуск ERP без подтверждения LiDAR', 693, 329, 13, MUTED, width=205)
    c.showPage()

    page(4, 'НАВИГАТОР ДЛЯ СУДЕЙ', 'От запуска до безопасного сброса',
         'Пароль admin выводится командой make up. Требования и подготовка Ubuntu / WSL - в README.md.')
    card(42, 151, 876, 238)
    text(NAVIGATOR, 66, 368, 19, width=826)
    text('Ожидаемый результат', 42, 123, 16, GREEN, True)
    text('После запуска буфер заполняется, самосвалы ждут, экскаватор простаивает. После сброса фабрика возобновляет переработку, очередь убывает, учет снова подтвержден.', 42, 94, 13, MUTED, width=865)
    c.showPage()

    page(5, 'ПРИЧИНА И ПОСЛЕДСТВИЯ', 'До → во время → после сброса',
         'Три снимка одной проверки: кнопка → MQTT → модель → Eclipse Ditto → интерфейс. Без подмены данных.')
    for x, name, title, color in [(42, 'before', '01 / До', GREEN),
                                  (340, 'during', '02 / Во время', RED),
                                  (638, 'after', '03 / После сброса', BLUE)]:
        text(title, x, 391, 16, color, True, 280)
        capture(f'{name}.png', x, 92, 280, 274)
    text('ERP начисляет 400.8 т/ч при остановке. Буфер заполняется, очередь блокирует добычу.', 42, 76, 12, MUTED, width=870)
    text('Сброс снимает ложное начисление и возобновляет переработку; вещество и журнал сохраняются.', 42, 56, 11, MUTED, width=870)
    c.showPage()

    page(6, 'ОГРАНИЧЕНИЯ МОДЕЛИ', 'Проверяем причины, знаем границы',
         'Это демонстрационная модель вымышленного предприятия, а не промышленная система измерений.')
    for x, title, body, color in [
        (42, 'Синтетические приборы', 'Нет реальных весов, облаков точек и ERP. Шум, задержки и отказы заданы моделью.', BLUE),
        (340, 'Постоянное извлечение', 'Fe: 43-45% → 55%. Извлечение 83.5% - допущение. Минералогия и экономика не моделируются.', GREEN),
        (638, 'Упрощенная погода', 'Дождь: 3% → 7% по сухой основе. Нет испарения, дренажа и пространственного профиля влажности.', RED)]:
        card(x, 217, 280, 166)
        text(title, x+17, 362, 16, color, True, 246)
        text(body, x+17, 322, 13, MUTED, width=246)
    text('Дождь: корректная рекомендация вместо обвинения', 42, 187, 19, GREEN, True)
    text('«Требуется пересчет влажности / ошибка ERP»: 3000 т сухого вещества → 3210 т влажной массы. Старый коэффициент 1.03 дает лишние 116.5 т в учете.', 42, 152, 14, width=870)
    text('Источники и допущения', 42, 90, 14, bold=True)
    text('Транспортный ориентир: OpenMines (зафиксированная версия). Fe, плотность 2.7 т/м³ и производительность - допущения команды. Таблица источников, значений и влияния: SUBMISSION.md.', 42, 66, 10, MUTED, width=870)
    c.showPage()
    c.save()
    print(args.output.resolve())


if __name__ == '__main__':
    main()
