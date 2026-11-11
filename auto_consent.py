import logging
import os
import signal
import threading
from pathlib import Path

import pg8000.dbapi

# .env подхватываем, только если он есть и установлен python-dotenv
# (Docker Compose передаёт переменные напрямую).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def required_env(name):
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise SystemExit(f"Задай переменную окружения {name}.")
    return value


PG_HOST = required_env("PG_HOST")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB = required_env("PG_DB")
PG_USER = required_env("PG_USER")
PG_PASSWORD = required_env("PG_PASSWORD")
PG_SCHEMA = os.environ.get("PG_SCHEMA", "dbo")

# Интервал между запусками, в минутах (по умолчанию 120 = 2 часа)
try:
    INTERVAL_MINUTES = int(os.environ.get("AUTO_INTERVAL_MINUTES", "120"))
    if INTERVAL_MINUTES < 1:
        raise ValueError
except ValueError:
    raise SystemExit("AUTO_INTERVAL_MINUTES должен быть целым числом больше нуля.") from None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("auto-consent")

stop_event = threading.Event()


def handle_signal(signum, frame):
    log.info("Получен сигнал %s, завершаю работу...", signum)
    stop_event.set()


def apply_consent():
    sql = f'''
        UPDATE {PG_SCHEMA}."Employees"
        SET "AllowProcessPersonalData" = true,
            "DateProcessPersonalData" = CURRENT_TIMESTAMP,
            "LastModified" = CURRENT_TIMESTAMP
        WHERE ("AllowProcessPersonalData" != true OR "AllowProcessPersonalData" IS NULL)
          AND "IsHidden" = false
    '''
    conn = pg8000.dbapi.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        timeout=15,
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        count = cur.rowcount
        cur.close()
        conn.commit()
        return count
    finally:
        conn.close()


def main():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info(
        "Запуск. БД: %s:%s/%s, схема: %s, интервал: %s мин.",
        PG_HOST, PG_PORT, PG_DB, PG_SCHEMA, INTERVAL_MINUTES,
    )

    while not stop_event.is_set():
        try:
            count = apply_consent()
            if count:
                log.info("Проставлено галочек: %s", count)
            else:
                log.info("Новых сотрудников без галочки нет.")
        except Exception as e:
            # Не падаем: пробуем снова на следующем круге
            log.error("Ошибка при обновлении БД: %r", e)

        # Ждём интервал, но прерываемся сразу при остановке контейнера
        stop_event.wait(INTERVAL_MINUTES * 60)

    log.info("Остановлено.")


if __name__ == "__main__":
    main()
