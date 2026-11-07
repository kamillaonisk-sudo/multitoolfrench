class AppError(Exception):
    """Ошибка с понятным пользователю сообщением на русском."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
