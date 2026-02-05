"""
Исключения WB Redistribution Bot.
"""


class WBBotError(Exception):
    """Базовое исключение бота"""
    pass


# ==================== WB API Errors ====================

class WBApiError(WBBotError):
    """Общая ошибка WB API"""

    def __init__(self, message: str, status_code: int = None, response: dict = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class WBAuthError(WBApiError):
    """Ошибка авторизации (401, 403)"""
    pass


class WBRateLimitError(WBApiError):
    """Превышен лимит запросов (429)"""

    def __init__(self, message: str, retry_after: int = None):
        self.retry_after = retry_after
        super().__init__(message)


class WBNotFoundError(WBApiError):
    """Ресурс не найден (404)"""
    pass


class WBValidationError(WBApiError):
    """Ошибка валидации данных (400)"""
    pass


class WBServerError(WBApiError):
    """Ошибка сервера WB (5xx)"""
    pass


# ==================== Token Errors ====================

class TokenError(WBBotError):
    """Ошибки работы с токенами"""
    pass


class TokenNotFoundError(TokenError):
    """Токен не найден"""
    pass


class TokenExpiredError(TokenError):
    """Токен истёк"""
    pass


class TokenInvalidError(TokenError):
    """Токен невалидный"""
    pass


class EncryptionError(TokenError):
    """Ошибка шифрования/дешифрования"""
    pass


# ==================== User Errors ====================

class UserError(WBBotError):
    """Ошибки пользователя"""
    pass


class UserNotFoundError(UserError):
    """Пользователь не найден"""
    pass


class AccessDeniedError(UserError):
    """Доступ запрещён"""
    pass


class RateLimitUserError(UserError):
    """Превышен лимит запросов пользователя"""

    def __init__(self, message: str, remaining_seconds: int = None):
        self.remaining_seconds = remaining_seconds
        super().__init__(message)
