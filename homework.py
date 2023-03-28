import os
import logging
import telegram
import time
import requests
import sys

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

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    '%(asctime)s, %(levelname)s, %(message)s, %(name)s'
)
handler = logging.StreamHandler(stream=sys.stdout)
handler.setFormatter(formatter)
logger.addHandler(handler)


class UnsuccessfullAnswerException(Exception):
    """Исключение, возникающее при отсутствии успешного ответа от API Домашка.

    Будет "перехвачено", если статус ответа от сервера API ЯП Домашка
    не будет равен 200, либо если вообще не придет ответ от этого сервера.
    """

    pass


class TokenUnexistingException(Exception):
    """Исключение, возникающее при отсутствии обязательного токена.

    Будет "перехвачено", если в переменых окружения нет одного
    из нужных токенов.
    """

    pass


class HomeworkStatusIsUncorrectException(Exception):
    """Исключение, возникающее при некорректном статусе домашней работы.

    Будет "перехвачено", если API ЯП Домашки возвратит недокументированный
    статус домашней работы, либо домашку без статуса.
    """

    pass


class HomeworksAreAbsentException(Exception):
    """Исключение, возникающее при отсутствии домашних работ.

    Будет "перехвачено", если в ответе от API ЯП Домашка не будет информации
    о новых домашках. Не является "ошибкой", т.к. информация о новых домашках
    в ответе от сервера зависит от временной метки,
    начиная с которой идет запрос.
    """

    pass


def check_tokens() -> bool:
    """Проверяет доступность переменных окружения.

    Возвращает "истину", если все переменные окружения доступны.
    Если какой-то переменной в окружении нет -
    выбрасывает исключение.
    """
    TOKENS = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }
    for value in TOKENS.values():
        if value is None:
            raise TokenUnexistingException()
    return True


def send_message(bot, message):
    """Отправляет сообщение в Telegram чат."""
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Сообщение отправлено')
    except Exception as error:
        logger.error(error)


def get_api_answer(timestamp: int) -> dict:
    """Делает запрос к API ЯП Домашка с временной меткой timestamp.

    Возвращает ответ от API ЯП Домашка,
    который мы приводим методом ".json()" к словарю.
    При этом может быть 2 исключения. Прогнозируемое со статусом
    ответа != 200, и общее, если API поломался.
    """
    params = {'from_date': timestamp}
    try:
        response = requests.get(ENDPOINT, headers=HEADERS, params=params)
        if response.status_code != HTTPStatus.OK:
            raise UnsuccessfullAnswerException(
                'Ответ от API ЯП получен,но статус ответа не "успешный"',
                'Это внутренняя ошибка на стороне сервера ЯП Домашка.',
            )
        return response.json()
    except Exception:
        raise UnsuccessfullAnswerException(
            'Вообще не удалось получить ответ от API ЯП Домашка.'
            'Нет доступа к серверам ЯП, чтобы получить подробную ошибку'
        )


def check_response(response: dict) -> bool:
    """Проверяет ответ API ЯП Домашка.

    Проверяет содержимое словаря "response" на соответствие некоторым тестам.
    Возвращает "истину", если данные в словаре валидны.
    А если API ЯП Домашка вернул словарь с невалидными данными -
    выбрасываются некоторые "встроенные" исключения и одно собственное.
    """
    message = {
        'not_dict': 'Ответ API ЯП не является словарем',
        'not_in_homework': 'В ответе API ЯП нет ключа homeworks',
        'not_in_current_date': 'В ответе API ЯП нет ключа current_date',
        'not_list': 'Ответ API ЯП не в виде списка',
        'not_time': 'API ЯП вернул некорректное время',
        'len_zero': 'Ответ API ЯП пришел без данных о новых домашках',
    }
    if not isinstance(response, dict):
        raise TypeError(message.get('not_dict'))
    if 'homeworks' not in response:
        raise KeyError(message.get('not_in_homework'))
    if 'current_date' not in response:
        raise KeyError(message.get('not_in_current_date'))
    if not isinstance(response.get('homeworks'), list):
        raise TypeError(message.get('not_list'))
    if not isinstance(response.get('current_date'), int):
        raise TypeError(message.get('not_time'))
    if len(response.get('homeworks')) == 0:
        raise HomeworksAreAbsentException(message.get('len_zero'))
    return True


def parse_status(homework: dict) -> str:
    """Извлекает статусы конкретной домашней работы.

    Проверяет название и статус домашней работы на "валидность".
    И отправляет строку с информацией об изменения статуса домашней работы.
    Если данные не валидны - перехватываются исключения.
    """
    if 'homework_name' not in homework:
        raise KeyError(
            'Ответ от API ЯП домашка не содержит ключа "homework_name".'
        )
    if homework.get('status') not in HOMEWORK_VERDICTS:
        raise HomeworkStatusIsUncorrectException(
            'У домашней работы некорректный статус.'
        )
    homework_name = homework.get('homework_name')
    verdict = HOMEWORK_VERDICTS.get(homework.get('status'))
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def main():
    """Основная логика работы бота."""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    try:
        check_tokens()
    except TokenUnexistingException:
        logger.critical('Отсутствует обязательная переменная окружения')
        sys.exit()
    mistake_info_send_to_bot = None
    while True:
        try:
            response = get_api_answer(timestamp)
            if check_response(response):
                homework = response.get('homeworks')[0]
                message = parse_status(homework)
                logger.info(f'Есть обновление {message}')
                send_message(bot, message)
            timestamp = response.get('current_date', timestamp)
        except HomeworksAreAbsentException as deb:
            logger.debug(deb)
        except (
            TypeError,
            KeyError,
            HomeworkStatusIsUncorrectException,
            UnsuccessfullAnswerException,
            Exception,
        ) as error:
            logger.error(error, exc_info=True)
            message = f'Сбой в работе программы: {error}'
            if message != mistake_info_send_to_bot:
                send_message(bot, message)
                mistake_info_send_to_bot = message
        finally:
            time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
