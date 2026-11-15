# План проекта VPd03 — погода через OpenWeather

## Шаги

| # | Шаг | Статус |
|---|---|---|
| 1 | Прочитать документацию Geocoding и Current Weather, выписать параметры и поля | ✅ |
| 2 | `.env` / `.env.example` / `.gitignore`: ключ только локально | ✅ |
| 3 | `requirements.txt`: `requests`, `python-dotenv`; venv и установка | ✅ |
| 4 | `get_coordinates(city)` — геокодинг, `limit=1`, `lang=ru`, проверка пустого ответа | ✅ |
| 5 | `get_weather_by_coordinates(lat, lon)` — `units=metric`, `lang=ru`, проверка статуса и тела | ✅ |
| 6 | Понятные ошибки без трейсбеков: нет ключа, 401, 404, битый JSON, пустой город | ✅ |
| 7 | CLI: строка «Погода в <город>: <t>°C, <описание>» + детали | ✅ |
| 8 | Режимы ввода 1 / 2 / 0 (город, координаты, выход) | ✅ |
| 9 | Кэш `weather_cache.json` с метаданными `city`, `lat`, `lon`, `fetched_at` | ✅ |
| 10 | Предложение кэша при сетевой ошибке, если данным меньше 3 часов | ✅ |
| 11 | Повторы при `429` / `5xx` / сети: 1, 2, 4 с | ✅ |
| 12 | Тесты без сети (`unittest.mock`), 23 проверки | ✅ |
| 13 | Проверка в чистом venv: тесты + запуск без ключа | ✅ |
| 14 | Живой запуск с настоящим ключом: Москва и Санкт-Петербург по координатам, пример в README | ✅ |
| 15 | Запуск в один клик `run.cmd` (venv + зависимости) и раздел «Если что-то не работает» | ✅ |
| 16 | Сдача: опубликовать репозиторий на GitHub | ✅ [Weather_API_progect](https://github.com/IgorL0SEV/Weather_API_progect) |

## Предусловия

- Python 3.10+; проверено на 3.12.
- Аккаунт OpenWeather и ключ в `.env`.
- Доступ в интернет к `api.openweathermap.org`.
- Новый ключ может начать работать не сразу (от минут до пары часов).

## Runbook: продолжение на другой машине

```powershell
cd D:\python-projects
git clone https://github.com/IgorL0SEV/Weather_API_progect.git Weather_API_progect
cd Weather_API_progect
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
"API_KEY=ваш_ключ" | Out-File -Encoding utf8 .env
python weather_app.py
```

Проверка без сети:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Риски и заметки

- **Новый ключ и `401`.** Ключ активируется не мгновенно; в сообщении программы
  об этом сказано, чтобы не искать ошибку в коде.
- **Пустой ответ геокодинга — это `200`.** Проверять только `status_code == 200`
  недостаточно.
- **Кодировка `.env`.** Файл читается как UTF-8; в PowerShell `>` может записать
  UTF-16, поэтому в runbook используется `Out-File -Encoding utf8`.
- **Кэш — не «свежие данные», а страховка.** Показываем только с явного согласия
  пользователя и только если записи меньше 3 часов.
- **Повторяем только временное.** `401`/`404` не повторяются: лишние запросы
  и ожидание ничего не исправят.
- **Секрет в git.** `.env` в `.gitignore`, в репозитории только `.env.example`.
  Перед сдачей — `git status` / `git ls-files` и проверка, что ключа там нет.
