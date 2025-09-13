import html
from collections import defaultdict
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import re

from telegram import Update
from telegram.constants import ParseMode

from core import database, settings
from utils.helpers import send_or_edit_message
from models import AdminState

try:
    stopwords.words("russian")
except LookupError:
    nltk.download("stopwords")
    nltk.download("punkt")

STOPWORDS_RU = set(stopwords.words("russian"))


def extract_keywords(text: str, top_n=5):
    if not text:
        return []
    text = re.sub(r'[^\w\s]', '', text.lower())
    tokens = word_tokenize(text, language="russian")
    meaningful_words = [word for word in tokens if word not in STOPWORDS_RU and len(word) > 2]
    freq_dist = nltk.FreqDist(meaningful_words)
    return [word for word, freq in freq_dist.most_common(top_n)]


async def show_climate_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_or_edit_message(update, context, "⏳ Собираю данные по климату в команде...")

    results = await database.get_climate_survey_stats()
    exit_results = await database.get_exit_interview_stats()

    if not results:
        await send_or_edit_message(update, context, "Нет данных для анализа климата.")
        return

    stats = defaultdict(lambda: {
        'responses': 0,
        'promoters': 0,
        'detractors': 0,
        'questions': defaultdict(lambda: {'positive': 0, 'total': 0}),
        'reasons_text': []
    })

    question_map = {
        'expectations': 'Знаю, что ожидается',
        'best_ability': 'Возможность делать лучше всего',
        'praise': 'Похвала за работу',
        'development_care': 'Забота о развитии',
        'opinion': 'Возможность высказать мнение',
        'colleague_success': 'Коллеги настроены на успех',
        'mission': 'Миссия вдохновляет',
        'importance': 'Моя работа важна',
        'growth_opportunity': 'Возможность расти',
        'support': 'Поддержка от руководителя',
        'friends': 'Есть друзья на работе',
        'team_part': 'Чувствую себя частью команды'
    }

    positive_answers = {'Да', 'Скорее да'}

    for row in results:
        res_name = row.get('restaurant_name', 'Неизвестно')
        stats[res_name]['responses'] += 1

        recommend_answer = row.get('recommend')
        if recommend_answer == 'Да':
            stats[res_name]['promoters'] += 1
        elif recommend_answer == 'Нет':
            stats[res_name]['detractors'] += 1

        for key, question_text in question_map.items():
            answer = row.get(key)
            if answer:
                stats[res_name]['questions'][question_text]['total'] += 1
                if answer in positive_answers:
                    stats[res_name]['questions'][question_text]['positive'] += 1

        reason = row.get('recommend_reason')
        if reason:
            stats[res_name]['reasons_text'].append(reason)

    for row in exit_results:
        res_name = row.get('restaurant_name', 'Неизвестно')
        reason = row.get('reason')
        if reason:
            stats[res_name]['reasons_text'].append(reason)

    report_parts = ["🌡️ <b>Аналитика по климату в команде:</b>\n"]
    sorted_restaurants = sorted(stats.keys(), key=lambda x: (x == 'Неизвестно', x))

    for res_name in sorted_restaurants:
        data = stats[res_name]
        total_recommend_responses = data['promoters'] + data['detractors']

        eNPS = 0
        if total_recommend_responses > 0:
            promoter_percent = (data['promoters'] / total_recommend_responses) * 100
            detractor_percent = (data['detractors'] / total_recommend_responses) * 100
            eNPS = promoter_percent - detractor_percent

        total_positive = sum(q['positive'] for q in data['questions'].values())
        total_answers = sum(q['total'] for q in data['questions'].values())
        engagement_index = (total_positive / total_answers) * 100 if total_answers > 0 else 0

        report_parts.append(f"<b>📍 {html.escape(res_name)}</b> (ответов: {data['responses']})")

        eNPS_icon = "⚪️"
        if eNPS > 30:
            eNPS_icon = "🟢"
        elif eNPS > 10:
            eNPS_icon = "🟡"
        else:
            eNPS_icon = "🔴"
        report_parts.append(f"  - {eNPS_icon} Индекс лояльности (eNPS): <b>{eNPS:.1f}</b>")
        report_parts.append(f"  - 🌟 Общий индекс вовлеченности: <b>{engagement_index:.1f}%</b>")

        worst_questions = sorted(
            data['questions'].items(),
            key=lambda item: (item[1]['positive'] / item[1]['total']) if item[1]['total'] > 0 else 1
        )

        if worst_questions:
            report_parts.append("  - ⚠️ Зоны для улучшения (вопросы с низкой долей позитивных ответов):")
            for q_text, q_data in worst_questions[:3]:
                if q_data['total'] > 0:
                    percentage = (q_data['positive'] / q_data['total']) * 100
                    report_parts.append(f"    • <i>{q_text}</i>: {percentage:.1f}%")

        all_reasons_text = ' '.join(data['reasons_text'])
        keywords = extract_keywords(all_reasons_text)
        if keywords:
            report_parts.append(f"  - 🔑 Ключевые темы в отзывах: <b>{', '.join(keywords)}</b>")

        report_parts.append("")

    from utils.keyboards import get_analytics_menu_keyboard
    keyboard = get_analytics_menu_keyboard()
    await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)


async def show_funnel_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_or_edit_message(update, context, "⏳ Собираю данные по воронке...")

    stats = await database.get_candidate_funnel_stats()

    if not stats:
        await send_or_edit_message(update, context, "Нет данных для построения аналитики.")
        return

    report_parts = ["📊 <b>Аналитика по воронке кандидатов:</b>\n"]

    sorted_restaurants = sorted(stats.keys(), key=lambda x: (x == 'Неизвестно', x))

    for res_name in sorted_restaurants:
        res_stats = stats[res_name]
        total_candidates = sum(res_stats.values())
        report_parts.append(f"<b>📍 {html.escape(res_name)}</b> (всего анкет: {total_candidates})")

        status_order = ['На рассмотрении', 'Ознакомительная смена', 'Думает', 'Отказ/Не подходит']

        for status in status_order:
            if status in res_stats:
                count = res_stats[status]
                icon = "🤔"
                if status == 'Ознакомительная смена': icon = "✅"
                if status == 'Думает': icon = "⏳"
                if status == 'Отказ/Не подходит': icon = "❌"

                report_parts.append(f"  {icon} {status}: <b>{count}</b>")

        for status, count in res_stats.items():
            if status not in status_order:
                report_parts.append(f"  - {status}: <b>{count}</b>")

        report_parts.append("")

    from utils.keyboards import get_analytics_menu_keyboard
    keyboard = get_analytics_menu_keyboard()
    await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)

    async def show_salary_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        await send_or_edit_message(update, context, "⏳ Собираю данные по зарплатам...")

        results = await database.get_salary_expectations_stats()

        if not results:
            await send_or_edit_message(update, context, "Нет данных для анализа зарплатных ожиданий.")
            return

        report_parts = ["💰 <b>Аналитика по зарплатным ожиданиям кандидатов:</b>\n"]

        sorted_restaurants = sorted(results.keys(), key=lambda x: (x == 'Неизвестно', x))

        for res_name in sorted_restaurants:
            res_stats = results[res_name]
            total = sum(res_stats.values())
            report_parts.append(f"<b>📍 {html.escape(res_name)}</b> (анкет с данными: {total})")

            income_order = ["до 60 000 ₽", "до 80 000 ₽", "до 100 000 ₽", "до 120 000 ₽", "Выше 120 000 ₽",
                            "Не указано"]
            sorted_incomes = sorted(res_stats.keys(), key=lambda x: income_order.index(x) if x in income_order else 99)

            for income_level in sorted_incomes:
                count = res_stats[income_level]
                percentage = (count / total) * 100
                report_parts.append(f"  - {html.escape(income_level)}: <b>{count}</b> ({percentage:.1f}%)")

            report_parts.append("")

        from utils.keyboards import get_analytics_menu_keyboard
        keyboard = get_analytics_menu_keyboard()
        await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)

async def show_sources_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_or_edit_message(update, context, "⏳ Собираю данные по источникам...")

    results = await database.get_recruitment_sources_stats()
    if not results:
        await send_or_edit_message(update, context, "Нет данных для анализа источников.")
        return

    stats = defaultdict(lambda: defaultdict(int))
    total_sources = defaultdict(int)
    for row in results:
        res_name = row.get('restaurant_name', 'Неизвестно')
        source = row.get('source', 'Не указан')
        count = row.get('count', 0)
        stats[res_name][source] += count
        total_sources[source] += count

    report_parts = ["📈 <b>Аналитика по источникам привлечения кандидатов:</b>\n"]

    report_parts.append("<b>Общая эффективность:</b>")
    total_all = sum(total_sources.values())
    sorted_total_sources = sorted(total_sources.items(), key=lambda item: item[1], reverse=True)
    for source, count in sorted_total_sources:
        percentage = (count / total_all) * 100
        report_parts.append(f"  - {html.escape(source)}: <b>{count}</b> ({percentage:.1f}%)")

    report_parts.append("\n" + "=" * 20 + "\n")
    report_parts.append("<b>В разрезе ресторанов:</b>\n")

    sorted_restaurants = sorted(stats.keys(), key=lambda x: (x == 'Неизвестно', x))
    for res_name in sorted_restaurants:
        report_parts.append(f"<b>📍 {html.escape(res_name)}</b>")
        res_total = sum(stats[res_name].values())
        sorted_res_sources = sorted(stats[res_name].items(), key=lambda item: item[1], reverse=True)
        for source, count in sorted_res_sources:
            percentage = (count / res_total) * 100
            report_parts.append(f"  - {html.escape(source)}: <b>{count}</b> ({percentage:.1f}%)")
        report_parts.append("")

    from utils.keyboards import get_analytics_menu_keyboard
    keyboard = get_analytics_menu_keyboard()
    await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)

async def show_experience_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_or_edit_message(update, context, "⏳ Собираю данные по опыту кандидатов...")

    results = await database.get_recruitment_experience_stats()
    if not results:
        await send_or_edit_message(update, context, "Нет данных для анализа опыта.")
        return

    stats = defaultdict(lambda: defaultdict(int))
    for row in results:
        res_name = row.get('restaurant_name', 'Неизвестно')
        exp = row.get('experience', 'Не указан')
        count = row.get('count', 0)
        stats[res_name][exp] += count

    report_parts = ["🎓 <b>Аналитика по опыту кандидатов:</b>\n"]
    exp_order = ["Нет опыта", "До 3 мес.", "3-6 мес.", "6-12 мес.", "1-3 года", "Более 3 лет", "Не указан"]

    sorted_restaurants = sorted(stats.keys(), key=lambda x: (x == 'Неизвестно', x))
    for res_name in sorted_restaurants:
        report_parts.append(f"<b>📍 {html.escape(res_name)}</b>")
        res_total = sum(stats[res_name].values())
        sorted_exp = sorted(stats[res_name].keys(), key=lambda x: exp_order.index(x) if x in exp_order else 99)
        for exp in sorted_exp:
            count = stats[res_name][exp]
            percentage = (count / res_total) * 100
            report_parts.append(f"  - {html.escape(exp)}: <b>{count}</b> ({percentage:.1f}%)")
        report_parts.append("")

    from utils.keyboards import get_analytics_menu_keyboard
    keyboard = get_analytics_menu_keyboard()
    await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)

async def show_exit_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = await database.get_exit_interview_stats()

    if not results:
        await update.message.reply_text("Нет данных для анализа exit-интервью.")
        return

    stats = defaultdict(lambda: {
        'leadership_scores': [],
        'training_scores': [],
        'feedback_counts': defaultdict(int),
        'reasons': []
    })

    rating_map = {"1 (Очень плохо)": 1, "2 (Плохо)": 2, "3 (Нормально)": 3, "4 (Хорошо)": 4, "5 (Отлично!)": 5}

    for row in results:
        res_name = row.get('restaurant_name', 'Неизвестно')

        leadership_text = row.get('leadership_rating')
        if leadership_text in rating_map:
            stats[res_name]['leadership_scores'].append(rating_map[leadership_text])

        training_text = row.get('training_rating')
        if training_text:
            stats[res_name]['training_scores'].append(training_text)

        feedback_text = row.get('feedback_freq')
        if feedback_text:
            stats[res_name]['feedback_counts'][feedback_text] += 1

        reason_text = row.get('reason')
        if reason_text and reason_text.strip():
            stats[res_name]['reasons'].append(reason_text.strip())

    report_parts = ["🚶‍♂️ <b>Аналитика по причинам увольнений (Exit-интервью):</b>\n"]
    sorted_restaurants = sorted(stats.keys(), key=lambda x: (x == 'Неизвестно', x))

    for res_name in sorted_restaurants:
        data = stats[res_name]
        report_parts.append(f"<b>📍 {html.escape(res_name)}</b>")

        if data['leadership_scores']:
            avg_score = sum(data['leadership_scores']) / len(data['leadership_scores'])
            report_parts.append(f"  - 📈 Средняя оценка руководства: <b>{avg_score:.2f} / 5</b>")

        if data['feedback_counts']:
            report_parts.append("  - 🗣️ Частота ОС:")
            for freq, count in data['feedback_counts'].items():
                report_parts.append(f"    • {html.escape(freq)}: {count}")

        if data['reasons']:
            report_parts.append("  - 💬 Причины ухода (последние 5):")
            for reason in data['reasons'][-5:]:
                report_parts.append(f"    • <i>{html.escape(reason)}</i>")

        report_parts.append("")

    full_report = "\n".join(report_parts)

    if len(full_report) > 4096:
        await update.message.reply_text("Отчет слишком длинный, вывожу по частям:")
        for i in range(0, len(full_report), 4096):
            await update.message.reply_text(full_report[i:i + 4096], parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(full_report, parse_mode=ParseMode.HTML)


async def show_enps_by_position_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_or_edit_message(update, context, "⏳ Собираю данные по eNPS в разрезе должностей...")

    results = await database.get_climate_stats_by_position()
    if not results:
        await send_or_edit_message(update, context, "Нет данных для анализа eNPS по должностям.")
        return

    stats = defaultdict(lambda: {'promoters': 0, 'detractors': 0})

    for row in results:
        position = row.get('position', 'Не указана')
        recommend = row.get('recommend')
        count = row.get('count', 0)

        if recommend == 'Да':
            stats[position]['promoters'] += count
        elif recommend == 'Нет':
            stats[position]['detractors'] += count

    report_parts = ["🧑‍🍳 <b>Аналитика лояльности (eNPS) по должностям (вся сеть):</b>\n"]

    def calculate_enps(data):
        total = data['promoters'] + data['detractors']
        if total == 0: return -200
        promoter_percent = (data['promoters'] / total) * 100
        detractor_percent = (data['detractors'] / total) * 100
        return promoter_percent - detractor_percent

    sorted_positions = sorted(stats.keys(), key=lambda pos: calculate_enps(stats[pos]))

    for position in sorted_positions:
        data = stats[position]
        total_responses = data['promoters'] + data['detractors']
        eNPS = calculate_enps(data)

        eNPS_icon = "⚪️"
        if eNPS > 30:
            eNPS_icon = "🟢"
        elif eNPS > 10:
            eNPS_icon = "🟡"
        else:
            eNPS_icon = "🔴"

        report_parts.append(f"<b>{html.escape(position)}</b> (ответов: {total_responses})")
        report_parts.append(f"  - {eNPS_icon} eNPS: <b>{eNPS:.1f}</b>")
        report_parts.append("")

    from utils.keyboards import get_analytics_menu_keyboard
    keyboard = get_analytics_menu_keyboard()
    await send_or_edit_message(update, context, "\n".join(report_parts), keyboard)