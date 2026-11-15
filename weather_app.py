"""Погода в консоли — OpenWeather API.

Ключ хранится только в .env (переменная API_KEY) и в коде не встречается.
Запуск:
    python weather_app.py

Что внутри:
    get_coordinates(city)                  — город → (широта, долгота), Geocoding API;
    get_weather_by_coordinates(lat, lon)   — текущая погода, Current Weather API;
    get_weather_by_city(city)              — обёртка: координаты + погода;
    CLI                                    — меню 1/2/0, кэш на 3 часа,
                                             повторы при 429 и сетевых сбоях.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# --- Настройки -------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

# .env ищем рядом с модулем, а не в текущей папке: программу можно запускать
# из любого каталога, ключ всё равно найдётся.
load_dotenv(BASE_DIR / ".env", encoding="utf-8")
API_KEY = os.getenv("API_KEY")

GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/direct"
WEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

REQUEST_TIMEOUT = 10          # секунд на один запрос
RETRY_DELAYS = (1, 2, 4)      # паузы между повторами, секунд
TEMPORARY_STATUSES = {429, 500, 502, 503, 504}

DEFAULT_CACHE_PATH = BASE_DIR / "weather_cache.json"
CACHE_MAX_AGE_HOURS = 3

MENU = """
1 — погода по названию города
2 — погода по координатам
0 — выход
"""


# --- Исключения ------------------------------------------------------------


class WeatherError(Exception):
    """Ошибка, которую можно показать пользователю текстом, без трейсбека."""


class WeatherUnavailableError(WeatherError):
    """Сервис временно недоступен (сеть, 429, 5xx) — можно предложить кэш."""


# --- Ключ API --------------------------------------------------------------


def _api_key() -> str:
    """Вернуть ключ из окружения или объяснить, как его добавить."""
    key = (API_KEY or os.getenv("API_KEY") or "").strip()
    if not key:
        raise WeatherError(
            "Не найден API-ключ OpenWeather.\n"
            "  1. Войдите на openweathermap.org → My API keys и сгенерируйте ключ.\n"
            "  2. В папке проекта создайте файл .env со строкой:\n"
            "     API_KEY=ваш_ключ\n"
            "  3. Запустите программу снова."
        )
    return key


# --- HTTP-слой -------------------------------------------------------------


def _wait(attempt: int, reason: str) -> None:
    """Подождать перед повтором и честно сказать об этом пользователю."""
    delay = RETRY_DELAYS[attempt]
    print(f"  Повтор {attempt + 1} из {len(RETRY_DELAYS)}: {reason}, ждём {delay} с…")
    time.sleep(delay)


def _request(url: str, params: dict, action: str) -> requests.Response:
    """GET с повторами при 429, 5xx и сетевых сбоях (паузы 1, 2, 4 с).

    Остальные ответы (200, 401, 404, …) возвращаются сразу: повторять
    запрос с неверным ключом или несуществующим городом бессмысленно.
    """
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if attempt < len(RETRY_DELAYS):
                _wait(attempt, "нет связи с OpenWeather")
                continue
            raise WeatherUnavailableError(
                "Не удалось связаться с OpenWeather после "
                f"{len(RETRY_DELAYS) + 1} попыток. Проверьте интернет или VPN."
            ) from exc

        if response.status_code in TEMPORARY_STATUSES and attempt < len(RETRY_DELAYS):
            _wait(attempt, f"OpenWeather ответил HTTP {response.status_code}")
            continue
        return response

    # Сюда попасть нельзя: цикл либо вернёт ответ, либо выбросит исключение.
    raise WeatherUnavailableError("OpenWeather временно недоступен.")


def _server_message(response: requests.Response) -> str:
    """Текст ошибки из тела ответа, если API его прислал."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return ""


def _check_status(response: requests.Response, action: str) -> None:
    """Превратить неуспешный HTTP-статус в понятное сообщение."""
    status = response.status_code
    if status == 200:
        return

    if status == 401:
        raise WeatherError(
            "OpenWeather не принял API-ключ (HTTP 401).\n"
            "  Проверьте строку API_KEY в .env. Свежесозданный ключ иногда "
            "активируется не сразу — от нескольких минут до пары часов."
        )
    if status == 429:
        raise WeatherUnavailableError(
            "OpenWeather ограничил частоту запросов (HTTP 429), повторы не помогли.\n"
            "  Подождите минуту и попробуйте снова."
        )
    if status >= 500:
        raise WeatherUnavailableError(
            f"OpenWeather временно недоступен (HTTP {status}). Попробуйте позже."
        )
    if status == 404:
        raise WeatherError(
            "OpenWeather не нашёл данные по этому запросу (HTTP 404). "
            "Проверьте название города или координаты."
        )

    detail = _server_message(response)
    hint = f"\n  Ответ сервера: {detail}" if detail else ""
    raise WeatherError(f"OpenWeather вернул ошибку HTTP {status} ({action}).{hint}")


# --- Основные функции ------------------------------------------------------


def get_coordinates(city: str) -> tuple[float, float]:
    """Найти координаты города через Geocoding API.

    OpenWeather сортирует совпадения по популярности, поэтому берём первый
    результат. limit=1 достаточно: нам нужен один «главный» город.

    Raises:
        WeatherError: пустой город, город не найден, неверный ключ, ошибка сети.
    """
    city = (city or "").strip()
    if not city:
        raise WeatherError("Название города не может быть пустым.")

    params = {"q": city, "limit": 1, "appid": _api_key(), "lang": "ru"}
    response = _request(GEOCODING_URL, params, "поиск города")
    _check_status(response, "поиск города")

    try:
        data = response.json()
    except ValueError as exc:
        raise WeatherError("OpenWeather вернул не JSON при поиске города.") from exc

    if not isinstance(data, list) or not data:
        raise WeatherError(
            f"Город «{city}» не найден. Проверьте написание — "
            "например: Москва, Минск, Saint Petersburg."
        )

    place = data[0]
    lat, lon = place.get("lat"), place.get("lon")
    if lat is None or lon is None:
        raise WeatherError(f"В ответе геокодинга нет координат для «{city}».")
    return float(lat), float(lon)


def get_weather_by_coordinates(lat: float, lon: float) -> dict:
    """Текущая погода по координатам (метрическая система, русский язык).

    Returns:
        Ответ Current Weather API как dict (поля main.temp, weather[0].description
        и другие).

    Raises:
        WeatherError: некорректные координаты, неверный ключ, ошибка сети.
    """
    lat, lon = _validate_coordinates(lat, lon)

    params = {
        "lat": lat,
        "lon": lon,
        "appid": _api_key(),
        "units": "metric",
        "lang": "ru",
    }
    response = _request(WEATHER_URL, params, "получение погоды")
    _check_status(response, "получение погоды")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError("OpenWeather вернул не JSON при запросе погоды.") from exc

    if not isinstance(payload, dict) or "main" not in payload:
        raise WeatherError("Неожиданный ответ OpenWeather: нет блока main.")
    return payload


def get_weather_by_city(city: str) -> tuple[dict, float, float]:
    """Погода по названию города: сначала координаты, затем погода.

    Returns:
        (ответ API, широта, долгота) — координаты нужны для кэша.
    """
    lat, lon = get_coordinates(city)
    return get_weather_by_coordinates(lat, lon), lat, lon


def _validate_coordinates(lat, lon) -> tuple[float, float]:
    """Проверить, что координаты — числа и лежат в допустимых диапазонах."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError) as exc:
        raise WeatherError(
            "Координаты должны быть числами, например 55.7520 и 37.6156."
        ) from exc

    if not -90 <= lat <= 90:
        raise WeatherError("Широта должна быть от -90 до 90.")
    if not -180 <= lon <= 180:
        raise WeatherError("Долгота должна быть от -180 до 180.")
    return lat, lon


# --- Форматирование --------------------------------------------------------


def format_weather(weather: dict, city: str) -> str:
    """Собрать строку вида «Погода в Москва: 12.4°C, пасмурно»."""
    main = weather.get("main") or {}
    temp = main.get("temp")
    if temp is None:
        raise WeatherError("В ответе OpenWeather нет температуры.")

    try:
        temp = float(temp)
    except (TypeError, ValueError) as exc:
        raise WeatherError("В ответе OpenWeather температура не число.") from exc

    description = ""
    items = weather.get("weather") or []
    if items and isinstance(items[0], dict):
        description = str(
            items[0].get("description") or items[0].get("main") or ""
        ).strip()

    line = f"Погода в {city}: {temp:.1f}°C"
    if description:
        line += f", {description}"
    return line


def format_details(weather: dict) -> str:
    """Дополнительная строка: ощущается как, влажность, ветер."""
    main = weather.get("main") or {}
    parts: list[str] = []

    feels = main.get("feels_like")
    if isinstance(feels, (int, float)):
        parts.append(f"ощущается как {feels:.1f}°C")

    humidity = main.get("humidity")
    if isinstance(humidity, (int, float)):
        parts.append(f"влажность {humidity:.0f}%")

    wind = weather.get("wind") or {}
    speed = wind.get("speed")
    if isinstance(speed, (int, float)):
        parts.append(f"ветер {speed:.1f} м/с")

    return " · ".join(parts)


# --- Кэш -------------------------------------------------------------------


def save_cache(
    weather: dict,
    city: str,
    lat: float,
    lon: float,
    path: str | Path = DEFAULT_CACHE_PATH,
) -> None:
    """Сохранить последний успешный ответ вместе с метаданными."""
    entry = {
        "city": city,
        "lat": lat,
        "lon": lon,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "weather": weather,
    }
    with open(path, "w", encoding="utf-8") as file:
        json.dump(entry, file, ensure_ascii=False, indent=4)


def load_cache(path: str | Path = DEFAULT_CACHE_PATH) -> dict | None:
    """Прочитать кэш; None — если файла нет или он испорчен."""
    file = Path(path)
    if not file.is_file():
        return None
    try:
        with open(file, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def cache_age_hours(entry: dict | None) -> float | None:
    """Возраст записи кэша в часах по метаданным fetched_at."""
    if not entry:
        return None
    try:
        fetched = datetime.fromisoformat(str(entry.get("fetched_at")))
    except (TypeError, ValueError):
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600


# --- Интерфейс -------------------------------------------------------------


def _print_weather(
    weather: dict,
    city: str,
    lat: float,
    lon: float,
    source: str = "API",
) -> None:
    """Показать погоду: основная строка, детали, координаты и источник."""
    print()
    print(f"  {format_weather(weather, city)}")
    details = format_details(weather)
    if details:
        print(f"  {details}")
    print(f"  координаты: {lat:.4f}, {lon:.4f} · источник: {source}")


def _offer_cache(path: str | Path = DEFAULT_CACHE_PATH) -> None:
    """Предложить сохранённые данные, если им меньше трёх часов."""
    entry = load_cache(path)
    if not entry:
        print("  Сохранённых данных нет — показать нечего.")
        return

    age = cache_age_hours(entry)
    if age is None:
        print("  Файл кэша повреждён — показать нечего.")
        return

    if age >= CACHE_MAX_AGE_HOURS:
        print(
            f"  В кэше данные {age:.1f} ч назад — старше {CACHE_MAX_AGE_HOURS} ч, "
            "показывать не буду."
        )
        return

    city = str(entry.get("city") or "неизвестный город")
    answer = input(
        f"  Есть кэш: {city}, {age:.1f} ч назад. Показать? [Enter — да, н — нет]: "
    ).strip().lower()
    if answer not in ("", "д", "да", "y", "yes", "1"):
        return

    weather = entry.get("weather") or {}
    lat = entry.get("lat")
    lon = entry.get("lon")
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        lat = lon = 0.0

    print("\n  Данные из кэша — могли устареть.")
    _print_weather(weather, city, lat, lon, f"кэш, {age:.1f} ч назад")


def _remember(weather: dict, city: str, lat: float, lon: float) -> None:
    """Сохранить ответ в кэш; сбой записи не должен ломать показ погоды."""
    try:
        save_cache(weather, city, lat, lon)
        print(f"  сохранено в {Path(DEFAULT_CACHE_PATH).name}")
    except OSError as exc:
        print(f"  Не удалось сохранить кэш: {exc}")


def _ask_city() -> str | None:
    """Спросить город; пустой ввод — отмена."""
    city = input("  Город (Enter — вернуться в меню): ").strip()
    return city or None


def _parse_coordinates(text: str) -> tuple[float, float]:
    """Разобрать строку «55.7520, 37.6156» (запятая, точка с запятой, пробел)."""
    parts = re.sub(r"[;,/]+", " ", (text or "").strip()).split()
    if len(parts) != 2:
        raise WeatherError(
            "Нужны две координаты: широта и долгота. "
            "Пример: 55.7520, 37.6156"
        )
    try:
        return _validate_coordinates(float(parts[0]), float(parts[1]))
    except ValueError as exc:
        raise WeatherError(
            "Координаты должны быть числами. Пример: 55.7520, 37.6156"
        ) from exc


def _run_city_mode() -> None:
    """Запрос погоды по городу с обработкой ошибок и кэшем."""
    city = _ask_city()
    if city is None:
        return

    try:
        weather, lat, lon = get_weather_by_city(city)
    except WeatherUnavailableError as exc:
        print(f"\n  [ОШИБКА] {exc}")
        _offer_cache()
        return
    except WeatherError as exc:
        print(f"\n  [ОШИБКА] {exc}")
        return

    _print_weather(weather, city, lat, lon, "API")
    _remember(weather, city, lat, lon)


def _run_coordinates_mode() -> None:
    """Запрос погоды по координатам; название берём из ответа API."""
    raw = input("  Координаты «широта, долгота» (Enter — вернуться в меню): ").strip()
    if not raw:
        return

    try:
        lat, lon = _parse_coordinates(raw)
        weather = get_weather_by_coordinates(lat, lon)
    except WeatherUnavailableError as exc:
        print(f"\n  [ОШИБКА] {exc}")
        _offer_cache()
        return
    except WeatherError as exc:
        print(f"\n  [ОШИБКА] {exc}")
        return

    city = str(weather.get("name") or "").strip() or "указанная точка"
    _print_weather(weather, city, lat, lon, "API")
    _remember(weather, city, lat, lon)


def main() -> int:
    """Меню: погода по городу, по координатам, выход."""
    print("Погода — OpenWeather")
    print("Данные: api.openweathermap.org · единицы: °C, метры в секунду")
    if not (API_KEY or "").strip():
        print("\n[ОШИБКА] Не найден API-ключ OpenWeather.")
        print("  1. Войдите на openweathermap.org → My API keys и сгенерируйте ключ.")
        print("  2. В папке проекта создайте файл .env со строкой:")
        print("     API_KEY=ваш_ключ")
        print("  3. Запустите программу снова.")
        return 1
    print("Ключ API загружен из .env")

    while True:
        print(MENU)
        try:
            choice = input("Выбор: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nДо встречи!")
            return 0

        if choice in ("0", "q", "exit", "выход"):
            print("До встречи!")
            return 0
        if choice == "1":
            _run_city_mode()
        elif choice == "2":
            _run_coordinates_mode()
        else:
            print("Неизвестный пункт. Введите 1, 2 или 0.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nДо встречи!")
        raise SystemExit(0)
