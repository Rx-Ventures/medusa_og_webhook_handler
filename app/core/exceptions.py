class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: dict | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class WebhookProcessingError(AppException):

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(
            status_code=500,
            error_code="WEBHOOK_PROCESSING_ERROR",
            message=message,
            details=details,
        )

class ExternalServiceError(AppException):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(
            status_code=502,
            error_code="EXTERNAL_SERVICE_ERROR",
            message=message,
            details=details,
        )
