import json
import logging
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

import vk_api
from dotenv import load_dotenv
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor


BASE_DIR = Path(__file__).resolve().parent
PHONE_RE = re.compile(r"^[+()\-\s\d]{7,25}$")

QUESTIONS = [
    (
        "status",
        "Какая у вас цель по статусу?",
        ["ВНЖ", "ПМЖ", "Гражданство", "Золотая виза"],
    ),
    (
        "budget",
        "Какой у вас бюджет?",
        ["5 000–50 000 $", "90 000–150 000 $", "250 000–500 000 $", "Свыше 500 000 $"],
    ),
    (
        "motivation",
        "Что является вашей основной мотивацией?",
        ["План Б", "Карты / счета / разблокировка счетов", "Открытие бизнеса", "Миграция"],
    ),
    (
        "financing",
        "Какой способ финансирования вам подходит?",
        ["Инвестиционный взнос", "Покупка недвижимости", "Финансовая независимость", "Без подтверждения"],
    ),
]

SESSIONS = {}


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_countries() -> list[dict]:
    with (BASE_DIR / "countries.json").open(encoding="utf-8") as file:
        countries = json.load(file)

    if not isinstance(countries, list) or not countries:
        raise RuntimeError("countries.json должен содержать непустой список стран")

    required_keys = {"name", "status", "budget", "motivation", "financing"}
    for index, country in enumerate(countries, start=1):
        missing = required_keys - set(country)
        if missing:
            raise RuntimeError(f"countries.json: страна #{index} без полей: {', '.join(sorted(missing))}")

    return countries


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} не задан")
    return value


def build_question_keyboard(question: tuple[str, str, list[str]]) -> str:
    keyboard = VkKeyboard()
    for option in question[2]:
        keyboard.add_button(option, color=VkKeyboardColor.PRIMARY)
        keyboard.add_line()
    keyboard.add_button("Готово", color=VkKeyboardColor.POSITIVE)
    keyboard.add_line()
    keyboard.add_button("Начать заново", color=VkKeyboardColor.SECONDARY)
    return keyboard.get_keyboard()


def start_keyboard() -> str:
    keyboard = VkKeyboard()
    keyboard.add_button("Начать подбор", color=VkKeyboardColor.PRIMARY)
    return keyboard.get_keyboard()


def restart_keyboard() -> str:
    keyboard = VkKeyboard()
    keyboard.add_button("Пройти заново", color=VkKeyboardColor.PRIMARY)
    return keyboard.get_keyboard()


def start_session(user_id: int) -> None:
    SESSIONS[user_id] = {
        "step": 0,
        "answers": {question[0]: [] for question in QUESTIONS},
        "name": None,
        "result": None,
        "countries": [],
        "awaiting_name": False,
        "awaiting_phone": False,
    }


def status_match(country: dict, selected: list[str]) -> bool:
    statuses = set(selected)
    if "ПМЖ" in statuses:
        statuses.add("Золотая виза")
    return any(status in country["status"] for status in statuses)


def motivation_match(country: dict, selected: list[str]) -> bool:
    selected_without_plan_b = [item for item in selected if item != "План Б"]
    return not selected_without_plan_b or any(item in country["motivation"] for item in selected_without_plan_b)


def country_score(country: dict, answers: dict[str, list[str]]) -> int:
    checks = [
        status_match(country, answers["status"]),
        any(item in country["budget"] for item in answers["budget"]),
        motivation_match(country, answers["motivation"]),
        any(item in country["financing"] for item in answers["financing"]),
    ]
    return sum(checks)


def calculate_result(countries: list[dict], answers: dict[str, list[str]]) -> tuple[str, list[dict]]:
    full_matches = [country for country in countries if country_score(country, answers) == 4]
    if full_matches:
        return "full", full_matches

    near_matches = [country for country in countries if country_score(country, answers) == 3]
    if near_matches:
        return "near", near_matches

    return "none", []


def send_mail(
    *,
    user_id: int,
    answers: dict[str, list[str]],
    name: str,
    phone: str,
    result_type: str,
    countries: list[dict],
) -> None:
    smtp_password = get_required_env("SMTP_PASSWORD")
    admin_email = os.getenv("ADMIN_EMAIL", "pinned-mir@yandex.ru")
    smtp_host = os.getenv("SMTP_HOST", "smtp.yandex.ru")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER", admin_email)

    labels = {
        "full": "Полное совпадение — 4 из 4",
        "near": "Наиболее близкое совпадение — 3 из 4",
        "none": "Индивидуальный подбор",
    }

    message = EmailMessage()
    message["Subject"] = "Новая анкета подбора страны — Точка на карте"
    message["From"] = smtp_user
    message["To"] = admin_email
    message.set_content(
        "\n".join(
            [
                "НОВАЯ АНКЕТА ПОЛЬЗОВАТЕЛЯ",
                "",
                f"Имя: {name}",
                f"Телефон: {phone}",
                f"VK user ID: {user_id}",
                "",
                "ОТВЕТЫ:",
                f"Цель: {', '.join(answers['status'])}",
                f"Бюджет: {', '.join(answers['budget'])}",
                f"Мотивация: {', '.join(answers['motivation'])}",
                f"Финансирование: {', '.join(answers['financing'])}",
                "",
                "РЕЗУЛЬТАТ:",
                labels[result_type],
                f"Страны: {', '.join(country['name'] for country in countries) or 'Не определены'}",
                "",
            ]
        )
    )

    with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def send_message(vk, user_id: int, message: str, keyboard: str | None = None) -> None:
    payload = {
        "user_id": user_id,
        "random_id": secrets.randbits(31),
        "message": message,
    }
    if keyboard:
        payload["keyboard"] = keyboard
    vk.messages.send(**payload)


def handle_event(vk, countries: list[dict], event) -> None:
    message = event.object.message
    user_id = message["from_id"]
    text = message.get("text", "").strip()

    if text.lower() in {"начать подбор", "пройти заново", "начать заново", "старт", "/start"}:
        start_session(user_id)
        send_message(
            vk,
            user_id,
            "Начинаем подбор. Выберите до двух вариантов и нажмите «Готово».",
            build_question_keyboard(QUESTIONS[0]),
        )
        return

    if user_id not in SESSIONS:
        send_message(user_id=user_id, vk=vk, message="Чтобы начать подбор страны, нажмите кнопку ниже.", keyboard=start_keyboard())
        return

    session = SESSIONS[user_id]
    answers = session["answers"]

    if session["awaiting_name"]:
        if len(text) < 2:
            send_message(vk, user_id, "Введите имя, чтобы специалист понимал, как к вам обращаться:")
            return

        session["name"] = text
        session["awaiting_name"] = False
        session["awaiting_phone"] = True
        send_message(vk, user_id, "Введите ваш номер телефона:")
        return

    if session["awaiting_phone"]:
        if not PHONE_RE.match(text):
            send_message(vk, user_id, "Проверьте номер телефона и отправьте его еще раз:")
            return

        session["awaiting_phone"] = False
        try:
            send_mail(
                user_id=user_id,
                answers=answers,
                name=session["name"],
                phone=text,
                result_type=session["result"],
                countries=session["countries"],
            )
            response = "Спасибо. Анкета отправлена специалисту. Мы свяжемся с вами для консультации."
        except Exception:
            logging.exception("Не удалось отправить email с анкетой")
            response = "Анкета заполнена, но отправка не выполнена. Проверьте SMTP-настройки на сервере."

        send_message(vk, user_id, response, restart_keyboard())
        return

    question = QUESTIONS[session["step"]]
    key = question[0]

    if text == "Готово":
        if not answers[key]:
            send_message(vk, user_id, "Выберите хотя бы один вариант.", build_question_keyboard(question))
            return

        if session["step"] < len(QUESTIONS) - 1:
            session["step"] += 1
            next_question = QUESTIONS[session["step"]]
            send_message(
                vk,
                user_id,
                next_question[1] + "\nМожно выбрать до двух вариантов.",
                build_question_keyboard(next_question),
            )
            return

        result_type, matched_countries = calculate_result(countries, answers)
        session["result"] = result_type
        session["countries"] = matched_countries
        session["awaiting_name"] = True

        country_names = ", ".join(country["name"] for country in matched_countries)
        if result_type == "full":
            response = (
                "По вашим параметрам рекомендуем рассмотреть такие страны, как: "
                f"{country_names}.\n\nНапишите ваше имя — мы перезвоним и проконсультируем по конкретной стране."
            )
        elif result_type == "near":
            response = (
                "Идеального совпадения по всем выбранным параметрам не найдено.\n\n"
                f"Ближе всего к вашему запросу подходят: {country_names}.\n\n"
                "Напишите ваше имя — мы перезвоним и проконсультируем."
            )
        else:
            response = (
                "По выбранным параметрам не удалось автоматически определить подходящую страну.\n\n"
                "Оставьте ваше имя — специалист проведет индивидуальный подбор."
            )

        send_message(vk, user_id, response)
        send_message(vk, user_id, "Введите ваше имя:")
        return

    if text not in question[2]:
        send_message(vk, user_id, "Выберите вариант из кнопок. Можно выбрать до двух вариантов.", build_question_keyboard(question))
        return

    selected = answers[key]
    if text in selected:
        selected.remove(text)
    elif len(selected) < 2:
        selected.append(text)
    else:
        send_message(vk, user_id, "Можно выбрать максимум 2 варианта. Нажмите «Готово».", build_question_keyboard(question))
        return

    selected_text = ", ".join(selected) or "ничего"
    send_message(
        vk,
        user_id,
        f"Выбрано: {selected_text}\nКогда закончите выбор, нажмите «Готово».",
        build_question_keyboard(question),
    )


def run_bot() -> None:
    token = get_required_env("VK_TOKEN")
    countries = load_countries()
    session = vk_api.VkApi(token=token)
    vk = session.get_api()
    group_id = int(os.getenv("VK_GROUP_ID") or vk.groups.getById()[0]["id"])
    longpoll = VkBotLongPoll(session, group_id)

    logging.info("VK-бот запущен. Группа: %s", group_id)
    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue

        try:
            handle_event(vk, countries, event)
        except Exception:
            logging.exception("Ошибка обработки события VK")


def main() -> None:
    load_dotenv()
    setup_logging()

    while True:
        try:
            run_bot()
        except KeyboardInterrupt:
            logging.info("Остановка бота")
            raise
        except Exception:
            logging.exception("Бот упал, повторный запуск через 10 секунд")
            time.sleep(10)


if __name__ == "__main__":
    main()
