"""Small SMTP helper for account emails."""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import logging
import smtplib

from . import settings

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def send_password_reset(email: str, reset_url: str, lang: str = "ru") -> bool:
    if not smtp_configured():
        logger.warning("Password reset email is not sent because SMTP is not configured. URL: %s", reset_url)
        return False

    if lang == "en":
        subject = "Reset your Griders password"
        body = (
            "You requested a password reset for Griders.\n\n"
            f"Open this link within {settings.PASSWORD_RESET_TTL_MINUTES} minutes:\n{reset_url}\n\n"
            "If you did not request this, ignore this email."
        )
    else:
        subject = "Восстановление пароля Griders"
        body = (
            "Вы запросили восстановление пароля в Griders.\n\n"
            f"Откройте эту ссылку в течение {settings.PASSWORD_RESET_TTL_MINUTES} минут:\n{reset_url}\n\n"
            "Если вы не запрашивали восстановление, просто проигнорируйте это письмо."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = email
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=settings.SMTP_FROM.split("@", 1)[-1])
    message.set_content(body)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_TLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)
    return True


def send_email_verification(email: str, verify_url: str, lang: str = "ru") -> bool:
    if not smtp_configured():
        logger.warning("Email verification is not sent because SMTP is not configured. URL: %s", verify_url)
        return False

    if lang == "en":
        subject = "Confirm your Griders registration"
        body = (
            "Thank you for registering with Griders.\n\n"
            f"Confirm your email address within {settings.EMAIL_VERIFICATION_TTL_MINUTES} minutes:\n{verify_url}\n\n"
            "If you did not create this account, ignore this email."
        )
    else:
        subject = "Подтверждение регистрации Griders"
        body = (
            "Спасибо за регистрацию в Griders.\n\n"
            f"Подтвердите адрес электронной почты в течение {settings.EMAIL_VERIFICATION_TTL_MINUTES} минут:\n{verify_url}\n\n"
            "Если вы не создавали аккаунт, просто проигнорируйте это письмо."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = email
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=settings.SMTP_FROM.split("@", 1)[-1])
    message.set_content(body)

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_TLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(message)
    return True
