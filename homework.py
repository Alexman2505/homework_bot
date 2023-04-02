import logging
import os
import sys
import time
from http import HTTPStatus

import requests
import telegram
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


class UnsuccessAnswerException(Exception):
    """Исключение, возникающее при отсутствии успешного ответа от API Домашка.

    Будет "перехвачено", если статус ответа от сервера API ЯП Домашка
    пришел, но код ответа не равен 200. Это проблема на стороне ЯП Домашки.
    """

    pass


class TotallyUnsuccessAnswerException(Exception):
    """Исключение, возникающее при отсутствии какой-либо связи с серверами.

    Будет "перехвачено", если статус ответа от сервера API ЯП Домашка
    вообще не пришел. Т.е. сервер API ЯП домашка не отвечает на запросы.
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
    """Исключение, возникающее при отсутствии новых домашних работ.

    Будет "перехвачено", если в ответе от API ЯП Домашка не будет информации
    о новых домашках. Не является "ошибкой", т.к. информация о новых домашках
    в ответе от сервера зависит от временной метки,
    начиная с которой идет запрос.
    """

    pass


def check_tokens() -> None:
    """Проверяет доступность переменных окружения.

    Ничего не возвращает, если все переменные окружения доступны.
    Но если какой-то переменной в окружении нет -
    выбрасывает исключение.
    """
    TOKENS = {
        'PRACTICUM_TOKEN': PRACTICUM_TOKEN,
        'TELEGRAM_TOKEN': TELEGRAM_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }
    FORGET_TOKENS = []
    for token_key in TOKENS.keys():
        if TOKENS[token_key] is None:
            FORGET_TOKENS.append(token_key)
    if len(FORGET_TOKENS) != 0:
        raise TokenUnexistingException(
            f'Потеряны переменные окружения из '
            f'этого списка {FORGET_TOKENS}'
        )


def get_api_answer(timestamp) -> dict:
    """Делает запрос к API ЯП Домашка с временной меткой timestamp.

    Возвращает ответ от API ЯП Домашка,
    который мы приводим методом ".json()" к словарю.
    При этом может быть 2 исключения. Прогнозируемое со статусом
    ответа != 200, и общее, если API поломался.
    """
    params = {'from_date': timestamp}
    try:
        response = requests.get(ENDPOINT, headers=HEADERS, params=params)
    except Exception:
        raise TotallyUnsuccessAnswerException(
            'Вообще не удалось получить ответ от API ЯП Домашка.'
            'Нет доступа к серверам ЯП, чтобы получить подробную ошибку'
        )
    else:
        if response.status_code != HTTPStatus.OK:
            raise UnsuccessAnswerException(
                'Ответ от API ЯП получен,но статус ответа не "успешный"',
                'Это внутренняя ошибка на стороне сервера ЯП Домашка.',
            )
    return response.json()


def check_response(response: dict) -> None:
    """Проверяет ответ API ЯП Домашка.

    Проверяет содержимое словаря "response" на соответствие некоторым тестам.
    Ничего не возвращает. Но если API ЯП Домашка вернул словарь с
    невалидными данными - выбрасываются некоторые "встроенные"
    исключения и одно собственное.
    """
    if not isinstance(response, dict):
        raise TypeError('Ответ API ЯП не является словарем, а должен')
    if ('homeworks' or 'current_date') not in response:
        raise KeyError(
            'В ответе API ЯП нет ключа homeworks или current_date. '
            'А это ключи под которыми лежит время запроса и список домашек'
        )
    if not isinstance(response.get('homeworks'), list):
        raise TypeError('Ответ API ЯП не в виде структуры данных "список".')
    if not isinstance(response.get('current_date'), int):
        raise TypeError('API ЯП вернул время запроса в неправильном формате')
    homeworks = response['homeworks']
    if len(homeworks) == 0:
        raise HomeworksAreAbsentException(
            'Ответ API ЯП пришел без данных о новых домашках'
        )


def parse_status(homework: dict) -> str:
    """Извлекает статусы конкретной домашней работы.

    Проверяет название и статус домашней работы на "валидность".
    И отправляет строку с информацией об изменения статуса домашней работы.
    Если данные не валидны - перехватываются исключения.
    """
    if ('homework_name' or 'status') not in homework:
        raise KeyError(
            'Ответ от API ЯП домашка не содержит ключа "homework_name". '
            'или ключа "status".'
        )
    if homework.get('status') not in HOMEWORK_VERDICTS:
        raise HomeworkStatusIsUncorrectException(
            'У домашней работы некорректный статус.'
        )
    homework_name = homework['homework_name']
    verdict = HOMEWORK_VERDICTS[homework['status']]
    return f'Изменился статус проверки работы "{homework_name}". {verdict}'


def send_message(bot: telegram.Bot, message: str) -> None:
    """Отправляет сообщение в Telegram чат."""
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logger.debug('Сообщение отправлено')
    except Exception as err:
        logger.error(err)


def main():
    """Основная логика работы бота."""
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    timestamp = int(time.time())
    try:
        check_tokens()
    except TokenUnexistingException as token:
        logger.critical(f'{token}')
        sys.exit()
    mistake_info_send_to_bot = None
    while True:
        try:
            response = get_api_answer(timestamp)
            check_response(response)
            homework = response.get('homeworks')[0]
            message = parse_status(homework)
            logger.info(f'Есть обновление {message}')
            send_message(bot, message)
            timestamp = response.get('current_date')
        except HomeworksAreAbsentException as deb:
            logger.debug(deb)
        except (
            TypeError,
            KeyError,
            HomeworkStatusIsUncorrectException,
            UnsuccessAnswerException,
            TotallyUnsuccessAnswerException,
            Exception,
        ) as err:
            logger.error(err, exc_info=True)
            message = f'Сбой в работе программы: {err}'
            if message != mistake_info_send_to_bot:
                send_message(bot, message)
                mistake_info_send_to_bot = message
        finally:
            time.sleep(RETRY_PERIOD)


if __name__ == '__main__':
    main()
