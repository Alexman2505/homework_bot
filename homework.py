import os
import logging
import telegram
import time
import requests

from http import HTTPStatus
from dotenv import load_dotenv

load_dotenv()

PRACTICUM_TOKEN = os.getenv('PRACTICUM_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

RETRY_PERIOD = 600
ENDPOINT = 'https://practicum.yandex.ru/api/user_api/homework_statuses/'
HEADERS = {'Authorization': f'OAuth {PRACTICUM_TOKEN}'}

HOMEWORK_VERDICTS = {
    'approved': 'Работа проверена: ревьюеру всё понравилось. Ура!',
    'reviewing': 'Работа взята на проверку ревьюером.',
    'rejected': 'Работа проверена: у ревьюера есть замечания.',
}

logging.basicConfig(
    level=logging.DEBUG,
    filename='main.log',
    filemode='a',
    format='%(asctime)s, %(levelname)s, %(message)s, %(name)s',
)
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())


class NegativeValueException(Exception):
    """Класс исключений."""

    pass


def check_tokens():
    """Проверяет доступность переменных окружения."""
    general_info = 'Отсутствует обязательная переменная окружения: '
    if PRACTICUM_TOKEN is None:
        logger.critical(f'{general_info} PRACTICUM_TOKEN')
        return False
    elif TELEGRAM_TOKEN is None:
        logger.critical(f'{general_info} TELEGRAM_TOKEN')
        return False
    elif TELEGRAM_CHAT_ID is None:
        logger.critical(f'{general_info} TELEGRAM_CHAT_ID')
        return False
    return True


def send_message(bot, message):
    """Отправляем сообщение в Telegram чат."""
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Сообщение отправлено')
    except Exception as error:
        logger.error(error)


def get_api_answer(timestamp):
    """Делает запрос к единственному эндпоинту API-сервиса."""
    params = {'from_date': timestamp or int(time.time())}
    try:
        response = requests.get(ENDPOINT, headers=HEADERS, params=params)
        if response.status_code != HTTPStatus.OK:
            message = 'Сервер не отвечает'
            logger.error(message)
            raise Exception(message)
        return response.json()
    except Exception:
        message = 'Ошибка API'
        logger.error(message)
        raise NegativeValueException(message)


def check_response(response):
    """Проверяет ответ API на соответствие документации."""
    message = {
        'not_dict': 'Ответ API не является словарем',
        'not_in': 'В ответе API нет домашней работы',
        'len_zero': 'Ответ API пришел без новых данных',
        'not_list': 'Ответ API не в виде списка',
    }
    if type(response) is not dict:
        logger.error(message.get('not_dict'))
        raise TypeError(message.get('not_dict'))
    elif ['homeworks'][0] not in response:
        logger.error(message.get('not_in'))
        raise IndexError(message.get('not_in'))
    elif len(response['homeworks']) == 0:
        logger.error(message.get('len_zero'))
        raise NegativeValueException(message.get('len_zero'))
    elif type(response['homeworks']) is not list:
        logger.error(message.get('not_list'))
        raise TypeError(message.get('not_list'))
    homework = response['homeworks']
    return homework


def parse_status(homework):
    """Извлекает статусы конкретной домашней работы."""
    if 'homework_name' not in homework:
        raise KeyError('Ответ от API не содержит ключа "homework_name".')
    homework_name = homework['homework_name']
    if 'status' not in homework:
        logger.warning('Ответ от API не содержит ключа "status".')
        raise NegativeValueException(
            'Ответ от API не содержит ключа "status".'
        )
    homework_status = homework['status']
    if homework_status not in HOMEWORK_VERDICTS:
        logger.debug('Отсутствует в ответе новые статусы')
        raise NegativeValueException('Отсутствует в ответе новые статусы')
    verdict = HOMEWORK_VERDICTS[homework_status]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def main():
    """Основная логика работы бота."""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    if not check_tokens():
        logger.critical('Ошибка Аутентификации')
        exit()

    while True:
        try:
            response = get_api_answer(timestamp)
            homework = check_response(response)[0]
            if homework:
                message = parse_status(homework)
                logger.info(f'Есть обновление{message}')
                if message:
                    send_message(bot, message)
            timestamp = response.get('current_date', timestamp)
            time.sleep(RETRY_PERIOD)

        except Exception as error:
            message = f'Сбой в работе программы: {error}'
            send_message(bot, message)
            time.sleep(RETRY_PERIOD)
        else:
            time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
