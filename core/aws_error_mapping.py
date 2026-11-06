from __future__ import annotations

from typing import NoReturn

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)
from fastapi import HTTPException

from core.errors import ErrorCode, build_error

_EXPIRED_TOKEN_CODES = {
    "ExpiredTokenException",
    "ExpiredToken",
    "RequestExpired",
    "InvalidClientTokenId",
    "UnrecognizedClientException",
}


def raise_aws_http_exception(exc: Exception, default_code: str) -> NoReturn:
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        raise HTTPException(
            status_code=503,
            detail=build_error(
                ErrorCode.AWS_CREDENTIALS_UNAVAILABLE,
                "AWS credentials are missing or expired. Please refresh credentials and retry.",
            ),
        )

    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        aws_code = (err.get("Code") or "").strip()
        aws_message = err.get("Message") or str(exc)

        if aws_code in _EXPIRED_TOKEN_CODES:
            raise HTTPException(
                status_code=503,
                detail=build_error(
                    ErrorCode.AWS_CREDENTIALS_EXPIRED,
                    "AWS session token expired. Run credential refresh and retry.",
                    aws_code=aws_code,
                ),
            )

        raise HTTPException(
            status_code=503,
            detail=build_error(
                ErrorCode.AWS_SERVICE_UNAVAILABLE,
                f"AWS service error: {aws_message}",
                aws_code=aws_code,
            ),
        )

    if isinstance(exc, BotoCoreError):
        raise HTTPException(
            status_code=503,
            detail=build_error(
                ErrorCode.AWS_SERVICE_UNAVAILABLE,
                f"AWS client error: {exc}",
            ),
        )

    raise HTTPException(status_code=500, detail=build_error(default_code, str(exc)))
