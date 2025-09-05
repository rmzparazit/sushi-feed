import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from xml.dom import minidom
import time
import random
import os
import re
import urllib.parse

# --- НАСТРОЙКИ ---
BASE_URL = "https://суши-стрит.рф/nabory/"
BASE_DOMAIN = "https://суши-стрит.рф"
OUTPUT_DIR = "."
YML_FILE = "sushi_street_catalog.xml"

os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# --- КОЛЛЕКЦИИ ---
COLLECTIONS = {
    "akcii": {"id": "akcii", "name": "Акции", "url": "https://суши-стрит.рф/akcii/"},
    "promonabory": {"id": "promonabory", "name": "Промонаборы", "url": "https://суши-стрит.рф/nabory/"},
    "kilogrammovye": {"id": "kilogrammovye", "name": "Килограммовые наборы", "url": "https://суши-стрит.рф/nabory/?item[item_id_parent]=3"},
    "na_dvoih": {"id": "na_dvoih", "name": "На двоих", "url": "https://суши-стрит.рф/nabory/?item[item_id_parent]=27"},
    "hity": {"id": "hity", "name": "Хиты продаж", "url": "https://суши-стрит.рф/nabory/?item[item_id_parent]=29"},
    "s_filedelfiej": {"id": "s_filedelfiej", "name": "С филадельфией", "url": "https://суши-стрит.рф/nabory/?item[item_id_parent]=28"},
}

# --- ФУНКЦИИ ---

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def make_full_url(path):
    if path.startswith('http'):
        return path
    return BASE_DOMAIN + (path if path.startswith('/') else '/' + path)


def clean_url(url):
    return url.replace('&amp;', '&')


def get_collections(original_name, url, short_description=""):
    colls = []
    name_lower = original_name.lower()
    url_lower = url.lower()
    desc_lower = short_description.lower()

    if any(kw in url_lower for kw in ['akcii', 'ogromnyj', 'vip%20kilogramm']):
        colls.append('akcii')
    if 'на-двоих' in url_lower or 'na-dvoih' in url_lower or 'на двоих' in name_lower:
        colls.append('na_dvoih')
    if 'килограмм' in name_lower or 'кило' in name_lower:
        colls.append('kilogrammovye')
    if 'хит' in name_lower or 'hit' in name_lower:
        colls.append('hity')
    if 'промонабор' in name_lower:
        colls.append('promonabory')
    if 'филадельфия' in name_lower or 'филадельфия' in desc_lower:
        colls.append('s_filedelfiej')
    
    return list(set(colls))


def format_name(original_name):
    clean_name = original_name.strip().strip('"\'')
    return f"Сет \"{clean_name}\""


def extract_weight_and_pieces(description):
    weight = None
    pieces = None
    match = re.search(r'(\d+)\s*гр[^\d]*(\d+)\s*шт', description, re.IGNORECASE)
    if match:
        weight = match.group(1)
        pieces = match.group(2)
    else:
        weight_match = re.search(r'(\d+)\s*гр', description, re.IGNORECASE)
        pieces_match = re.search(r'(\d+)\s*шт', description, re.IGNORECASE)
        if weight_match:
            weight = weight_match.group(1)
        if pieces_match:
            pieces = pieces_match.group(1)
    return weight, pieces


def extract_roll_types_from_description(description):
    cleaned = re.sub(r'Вес\s*[\d\w/]+\s*шт\.*', '', description, flags=re.IGNORECASE)
    cleaned = re.sub(r'\*.*', '', cleaned)
    cleaned = re.sub(r'[^\w\s,.-]', '', cleaned)

    match = re.search(r'Состав\s*[:\-]?\s*(.+)', cleaned, re.IGNORECASE)
    if not match:
        return []

    items_str = match.group(1).strip()
    if not items_str:
        return []

    raw_items = [item.strip() for item in items_str.split(',') if item.strip()]
    roll_types = []
    for item in raw_items:
        item = re.sub(r'^и\s+|^а\s+также\s+|^также\s+', '', item, flags=re.IGNORECASE).strip()
        if item and len(item) > 3:
            roll_types.append(item)

    return roll_types


def parse_product_page(session, url):
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        desc_el = soup.find('div', class_='description') or soup.find('div', itemprop='description')
        if desc_el:
            full_desc = ' '.join(desc_el.stripped_strings)
            return full_desc.strip()
        return "Состав не указан."
    except Exception as e:
        log(f"⚠️ Не удалось получить описание с {url}: {e}")
        return "Состав не указан."


def parse_catalog_page(session, base_url):
    log(f"🔍 Начинаем парсинг каталога: {base_url}")
    products = []
    seen_ids = set()  # Для отслеживания уникальности
    page_num = 1

    while True:
        if page_num == 1:
            current_url = base_url
        else:
            current_url = f"{base_url}?page={page_num}"

        log(f"➡️ Парсинг страницы {page_num}: {current_url}")

        try:
            response = session.get(current_url, headers=HEADERS, timeout=10)
            if response.status_code == 404:
                log(f"🔚 Страница {current_url} не найдена (404). Остановка.")
                break

            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            items = soup.find_all('form', class_='js_catalog-item')
            new_items_count = 0

            for item in items:
                try:
                    # 🔍 Извлекаем ссылку
                    link_tag = item.find('a', href=True)
                    link = link_tag['href'] if link_tag else ''
                    full_link = make_full_url(link)
                    full_link = clean_url(full_link)

                    # 🔍 Название (нужно ДО проверки дублей)
                    meta_name = item.find('meta', {'name': 'name'})
                    original_name = meta_name['content'].strip() if meta_name else 'Без названия'

                    # 🔎 1. Пытаемся извлечь product_id из URL
                    product_id = None
                    if 'product_id=' in full_link:
                        try:
                            parsed = urllib.parse.urlparse(full_link)
                            query = urllib.parse.parse_qs(parsed.query)
                            product_id = query.get('product_id', [None])[0]
                        except Exception as e:
                            log(f"⚠️ Ошибка при извлечении product_id из URL: {e}")

                    # 🔎 2. Резерв: item_id из input
                    item_id_input = item.find('input', {'name': 'item_id'})
                    item_id = item_id_input['value'].strip() if item_id_input else None

                    # ✅ Приоритет: product_id > item_id
                    base_vendor_code = product_id or item_id
                    if not base_vendor_code:
                        log(f"⚠️ Пропущен товар: нет vendorCode (ссылка: {full_link})")
                        continue

                    # 🔁 Генерируем уникальный vendor_code
                    vendor_code = base_vendor_code
                    counter = 1
                    original_vendor_code = vendor_code

                    while vendor_code in seen_ids:
                        log(f"🔁 Дубль: vendorCode={original_vendor_code}, Название='{original_name}', URL={full_link}")
                        vendor_code = f"{original_vendor_code}_{counter}"
                        counter += 1
                        if counter > 10:  # Защита от зацикливания
                            log(f"⚠️ Слишком много дублей для {original_vendor_code}, пропускаем")
                            continue

                    # ✅ Теперь vendor_code уникален

                    # 🔍 Цена
                    price_tag = item.find('span', class_='price-fixed')
                    price = price_tag.text.strip() if price_tag else ''
                    price = re.sub(r'\D', '', price)

                    # 🔍 Изображение
                    img_tag = item.find('img', itemprop='image')
                    image = img_tag['src'] if img_tag else ''
                    full_image = make_full_url(image)

                    # 🔍 Описание
                    desc_el = item.find('div', class_='description')
                    short_desc = ' '.join(desc_el.stripped_strings).strip() if desc_el else ''

                    # 🔍 Коллекции
                    collections = get_collections(original_name, full_link, short_desc)

                    # 🔍 Вес и штуки
                    weight, pieces = extract_weight_and_pieces(short_desc)

                    # 🔍 Типы роллов
                    roll_types = extract_roll_types_from_description(short_desc)

                    product = {
                        'id': vendor_code,
                        'vendorCode': vendor_code,
                        'name': format_name(original_name),
                        'original_name': original_name,
                        'price': price,
                        'url': full_link,
                        'image': full_image,
                        'short_description': short_desc,
                        'collections': collections,
                        'weight': weight,
                        'pieces': pieces,
                        'roll_types': roll_types
                    }

                    products.append(product)
                    seen_ids.add(vendor_code)
                    new_items_count += 1

                except Exception as e:
                    log(f"❌ Ошибка при обработке товара: {e}")
                    continue

            log(f"✅ Найдено {new_items_count} товаров на странице {page_num}")

            if new_items_count == 0:
                log("🔚 Больше нет товаров. Остановка.")
                break

            page_num += 1
            time.sleep(random.uniform(1.5, 3.0))

        except Exception as e:
            log(f"❌ Ошибка при парсинге {current_url}: {e}")
            break

    log(f"📦 Всего найдено {len(products)} товаров по всем страницам")
    return products


def generate_yml(products):
    log("📝 Генерация YML-фида...")

    # --- Собираем картинки для коллекций ---
    collection_images = {}
    for prod in products:
        for coll_id in prod['collections']:
            if coll_id in COLLECTIONS and coll_id not in collection_images:
                collection_images[coll_id] = prod['image']

    # --- Создаём XML ---
    yml = ET.Element('yml_catalog', date=time.strftime("%Y-%m-%dT%H:%M:%S"))
    shop = ET.SubElement(yml, 'shop')
    ET.SubElement(shop, 'name').text = 'Суши Стрит'
    ET.SubElement(shop, 'company').text = 'Суши Стрит Рязань'
    ET.SubElement(shop, 'url').text = BASE_DOMAIN
    ET.SubElement(shop, 'platform').text = 'OpenCart'

    currencies = ET.SubElement(shop, 'currencies')
    ET.SubElement(currencies, 'currency', id='RUB', rate='1')

    categories = ET.SubElement(shop, 'categories')
    cat = ET.SubElement(categories, 'category', id='1')
    cat.text = 'Суши и роллы'

    offers = ET.SubElement(shop, 'offers')

    session = requests.Session()
    session.headers.update(HEADERS)

    for i, prod in enumerate(products, 1):
        log(f"➡️ ({i}/{len(products)}) Парсинг детальной страницы: {prod['name']}")

        full_description = parse_product_page(session, prod['url'])
        if not full_description or full_description == "Состав не указан.":
            full_description = prod['short_description'] or f"Состав: {prod['original_name']}. Подробности на сайте."

        if len(full_description) < 20:
            full_description = f"Состав: {prod['original_name']}. Подробности на сайте."

        offer = ET.SubElement(offers, 'offer', id=prod['id'], available='true')
        ET.SubElement(offer, 'name').text = prod['name']
        ET.SubElement(offer, 'vendorCode').text = prod['vendorCode']

        # 🔥 URL с CDATA через заглушку
        url_elem = ET.SubElement(offer, 'url')
        url_elem.text = f"__CDATA_START__{prod['url']}__CDATA_END__"

        ET.SubElement(offer, 'price').text = prod['price'] or '0'
        ET.SubElement(offer, 'currencyId').text = 'RUB'
        ET.SubElement(offer, 'categoryId').text = '1'
        if prod['image']:
            ET.SubElement(offer, 'picture').text = prod['image']
        if full_description:
            desc_elem = ET.SubElement(offer, 'description')
            desc_elem.text = full_description
        sales_notes = f"Артикул: {prod['vendorCode']}. Доставка по Рязани."
        ET.SubElement(offer, 'sales_notes').text = sales_notes

        # 🔥 collectionId
        for coll_id in prod['collections']:
            if coll_id in COLLECTIONS:
                ET.SubElement(offer, 'collectionId').text = coll_id

        if prod['weight']:
            ET.SubElement(offer, 'param', name='weight').text = prod['weight']
        if prod['pieces']:
            ET.SubElement(offer, 'param', name='pieces').text = prod['pieces']

        for roll_type in prod['roll_types']:
            ET.SubElement(offer, 'param', name='roll_type').text = roll_type

        time.sleep(random.uniform(0.5, 1.0))

    # --- Блок коллекций ---
    collections_elem = ET.SubElement(shop, 'collections')
    for coll_id, coll_data in COLLECTIONS.items():
        coll = ET.SubElement(collections_elem, 'collection', id=coll_id)
        ET.SubElement(coll, 'name').text = coll_data['name']

        url_elem = ET.SubElement(coll, 'url')
        url_elem.text = f"__CDATA_START__{coll_data['url']}__CDATA_END__"

        ET.SubElement(coll, 'description').text = f"Наборы категории: {coll_data['name']}"
        if coll_id in collection_images:
            ET.SubElement(coll, 'picture').text = collection_images[coll_id]

    # --- Генерация XML ---
    rough_string = ET.tostring(yml, 'utf-8')
    reparsed = minidom.parseString(rough_string)

    # pretty xml
    pretty_xml = reparsed.toprettyxml(indent="  ", newl="\n")
    lines = [line for line in pretty_xml.split('\n') if line.strip()]
    xml_content = '\n'.join(lines)

    # 🔥 Заменяем заглушки на CDATA
    xml_content = xml_content.replace('__CDATA_START__', '<![CDATA[')
    xml_content = xml_content.replace('__CDATA_END__', ']]>')

    # 🔥 Исправляем разбитые CDATA
    import re
    xml_content = re.sub(
        r'<url><!\[CDATA\[(.*?)\]\]></url>',
        r'<url><![CDATA[\1]]></url>',
        xml_content
    )

    # ✅ Убедимся, что XML начинается с <?xml ...>
    if not xml_content.startswith('<?xml'):
        xml_content = '<?xml version="1.0" encoding="utf-8"?>\n' + xml_content

    with open(YML_FILE, 'w', encoding='utf-8') as f:
        f.write(xml_content)

    log(f"✅ Фид сохранён: {YML_FILE}")


# --- ОСНОВНОЙ КОД ---
if __name__ == "__main__":
    log("🚀 Запуск парсера суши-стрит.рф")

    session = requests.Session()
    session.headers.update(HEADERS)

    # Парсим все страницы
    all_products = parse_catalog_page(session, BASE_URL)

    if all_products:
        generate_yml(all_products)
        log(f"🎉 Готово! Всего товаров: {len(all_products)}")
    else:
        log("❌ Не удалось собрать ни одного товара.")

    session.close()
