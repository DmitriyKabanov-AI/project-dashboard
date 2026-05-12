import xml.etree.ElementTree as ET
from collections import defaultdict

def parse_project_xml(xml_path):
    """
    Парсер XML MS Project.
    Возвращает список задач с полями:
    uid, id, name, start, finish, percent_complete, text25 (содержит ID задачи), text1, resources,
    outline_level, outline_number, summary  # ИЗМЕНЕНИЕ: добавлены поля для построения дерева
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Ошибка парсинга {xml_path}: {e}")
        return []

    # Удаляем namespace для упрощения поиска
    for elem in root.iter():
        if '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]

    tasks = {}
    resources = {}
    assignments = defaultdict(list)

    # --- Задачи ---
    for task_elem in root.findall('.//Task'):
        uid = task_elem.findtext('UID')
        if not uid:
            continue

        # Берём ID задачи (числовой идентификатор)
        task_id = task_elem.findtext('ID')

        # ИЗМЕНЕНИЕ: извлекаем outline_level, outline_number, summary для построения дерева
        outline_level_raw = task_elem.findtext('OutlineLevel')
        outline_number_raw = task_elem.findtext('OutlineNumber')
        summary_raw = task_elem.findtext('Summary')

        # Поле text25 будет содержать ID задачи (как строку)
        task = {
            'uid': uid,
            'id': task_id,
            'name': task_elem.findtext('Name'),
            'start': task_elem.findtext('Start'),
            'finish': task_elem.findtext('Finish'),
            'percent_complete': task_elem.findtext('PercentComplete'),
            'text25': task_id,          # <--- здесь хранится ID задачи
            'text1': '',                # будет заполнено из ExtendedAttribute
            'resources': [],
            # ИЗМЕНЕНИЕ: новые поля для дерева
            'outline_level': int(outline_level_raw) if outline_level_raw else 0,
            'outline_number': outline_number_raw or '',
            'summary': (summary_raw == '1') if summary_raw else False,
        }

        # Поиск пользовательских полей (Text1, Text25 могут быть и в extended)
        for ext in task_elem.findall('ExtendedAttribute'):
            field_id = ext.findtext('FieldID')
            value = ext.findtext('Value')
            if not field_id or not value:
                continue
            # Text1
            if field_id == '188743707' or field_id == 'Text1':
                task['text1'] = value
            # Если вдруг Text25 определён в extended (на всякий случай)
            elif field_id == '188743731' or field_id == 'Text25':
                # Но мы уже используем ID, так что можно игнорировать или перезаписать
                # Оставляем ID как основной, extended не трогаем
                pass

        # Если Text1 не нашли в extended, пробуем прямой элемент
        if not task['text1']:
            direct_text1 = task_elem.findtext('Text1')
            if direct_text1:
                task['text1'] = direct_text1

        tasks[uid] = task

    # --- Ресурсы ---
    for res_elem in root.findall('.//Resource'):
        uid = res_elem.findtext('UID')
        if uid:
            resources[uid] = {
                'uid': uid,
                'name': res_elem.findtext('Name')
            }

    # --- Назначения (связь задач и ресурсов) ---
    for assign_elem in root.findall('.//Assignment'):
        task_uid = assign_elem.findtext('TaskUID')
        res_uid = assign_elem.findtext('ResourceUID')
        if task_uid and res_uid:
            assignments[task_uid].append(res_uid)

    # Привязываем ресурсы к задачам
    for task_uid, res_uids in assignments.items():
        if task_uid in tasks:
            tasks[task_uid]['resources'] = [
                resources[ruid]['name']
                for ruid in res_uids
                if ruid in resources and resources[ruid]['name']
            ]

    return list(tasks.values())
