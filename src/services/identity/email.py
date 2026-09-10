"""Email sending service and localized OTP templates (Batch F)."""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import NamedTuple

from src.config import get_settings

logger = logging.getLogger(__name__)


class EmailTemplate(NamedTuple):
    subject: str
    body_text: str
    body_html: str


class PasswordResetEmailCopy(NamedTuple):
    subject: str
    heading: str
    introduction: str
    button_label: str
    expiry: str
    security_notice: str


# Static, complete 14-language email catalog for registration OTPs.
# Includes 6-digit code placeholder {otp}, 5-minute expiry notice, and security warning.
EMAIL_TEMPLATES: dict[str, EmailTemplate] = {
    "en": EmailTemplate(
        subject="Your LinguaFlow Verification Code",
        body_text=(
            "Welcome to LinguaFlow!\n\n"
            "Your verification code is: {otp}\n\n"
            "This code is valid for 5 minutes. Do not share this code with anyone.\n\n"
            "— The LinguaFlow Team"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Welcome to LinguaFlow</h2>"
            "<p>Your verification code is:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>This code is valid for <strong>5 minutes</strong>. Do not share this code with anyone.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— The LinguaFlow Team</p>"
            "</div>"
        ),
    ),
    "vi": EmailTemplate(
        subject="Mã xác thực LinguaFlow của bạn",
        body_text=(
            "Chào mừng bạn đến với LinguaFlow!\n\n"
            "Mã xác thực của bạn là: {otp}\n\n"
            "Mã có hiệu lực trong 5 phút. Vui lòng không chia sẻ mã này cho bất kỳ ai.\n\n"
            "— Đội ngũ LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Chào mừng bạn đến với LinguaFlow</h2>"
            "<p>Mã xác thực của bạn là:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Mã có hiệu lực trong <strong>5 phút</strong>. Vui lòng không chia sẻ mã này cho bất kỳ ai.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— Đội ngũ LinguaFlow</p>"
            "</div>"
        ),
    ),
    "ja": EmailTemplate(
        subject="LinguaFlow 認証コードのご案内",
        body_text=(
            "LinguaFlowへようこそ！\n\n"
            "お客様の認証コードは: {otp}\n\n"
            "このコードの有効期限は5分です。第三者に共有しないでください。\n\n"
            "— LinguaFlow チーム"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>LinguaFlowへようこそ</h2>"
            "<p>お客様の認証コードは:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>このコードの有効期限は<strong>5分</strong>です。第三者に共有しないでください。</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— LinguaFlow チーム</p>"
            "</div>"
        ),
    ),
    "zh": EmailTemplate(
        subject="LinguaFlow 验证码",
        body_text=(
            "欢迎使用 LinguaFlow！\n\n"
            "您的验证码是: {otp}\n\n"
            "此验证码在 5 分钟内有效。请勿与任何人分享此代码。\n\n"
            "— LinguaFlow 团队"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>欢迎使用 LinguaFlow</h2>"
            "<p>您的验证码是:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>此验证码在 <strong>5 分钟</strong>内有效。请勿与任何人分享此代码。</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— LinguaFlow 团队</p>"
            "</div>"
        ),
    ),
    "ko": EmailTemplate(
        subject="LinguaFlow 인증 코드",
        body_text=(
            "LinguaFlow에 오신 것을 환영합니다!\n\n"
            "인증 코드는 다음과 같습니다: {otp}\n\n"
            "이 코드는 5분 동안 유효합니다. 타인과 공유하지 마세요.\n\n"
            "— LinguaFlow 팀"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>LinguaFlow에 오신 것을 환영합니다</h2>"
            "<p>인증 코드는 다음과 같습니다:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>이 코드는 <strong>5분</strong> 동안 유효합니다. 타인과 공유하지 마세요.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— LinguaFlow 팀</p>"
            "</div>"
        ),
    ),
    "fr": EmailTemplate(
        subject="Votre code de vérification LinguaFlow",
        body_text=(
            "Bienvenue sur LinguaFlow !\n\n"
            "Votre code de vérification est : {otp}\n\n"
            "Ce code est valide pendant 5 minutes. Ne le partagez avec personne.\n\n"
            "— L'équipe LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Bienvenue sur LinguaFlow</h2>"
            "<p>Votre code de vérification est :</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Ce code est valide pendant <strong>5 minutes</strong>. Ne le partagez avec personne.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— L'équipe LinguaFlow</p>"
            "</div>"
        ),
    ),
    "de": EmailTemplate(
        subject="Ihr LinguaFlow-Bestätigungscode",
        body_text=(
            "Willkommen bei LinguaFlow!\n\n"
            "Ihr Bestätigungscode lautet: {otp}\n\n"
            "Dieser Code ist 5 Minuten lang gültig. Teilen Sie diesen Code mit niemandem.\n\n"
            "— Ihr LinguaFlow-Team"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Willkommen bei LinguaFlow</h2>"
            "<p>Ihr Bestätigungscode lautet:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Dieser Code ist <strong>5 Minuten</strong> lang gültig. Teilen Sie diesen Code mit niemandem.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— Ihr LinguaFlow-Team</p>"
            "</div>"
        ),
    ),
    "es": EmailTemplate(
        subject="Tu código de verificación de LinguaFlow",
        body_text=(
            "¡Bienvenido a LinguaFlow!\n\n"
            "Tu código de verificación es: {otp}\n\n"
            "Este código es válido por 5 minutos. No compartas este código con nadie.\n\n"
            "— El equipo de LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>¡Bienvenido a LinguaFlow!</h2>"
            "<p>Tu código de verificación es:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Este código es válido por <strong>5 minutos</strong>. No compartas este código con nadie.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— El equipo de LinguaFlow</p>"
            "</div>"
        ),
    ),
    "th": EmailTemplate(
        subject="รหัสยืนยัน LinguaFlow ของคุณ",
        body_text=(
            "ยินดีต้อนรับสู่ LinguaFlow!\n\n"
            "รหัสยืนยันของคุณคือ: {otp}\n\n"
            "รหัสนี้มีอายุ 5 นาที โปรดอย่าเปิดเผยรหัสนี้แก่ผู้อื่น\n\n"
            "— ทีมงาน LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>ยินดีต้อนรับสู่ LinguaFlow</h2>"
            "<p>รหัสยืนยันของคุณคือ:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>รหัสนี้มีอายุ <strong>5 นาที</strong> โปรดอย่าเปิดเผยรหัสนี้แก่ผู้อื่น</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— ทีมงาน LinguaFlow</p>"
            "</div>"
        ),
    ),
    "id": EmailTemplate(
        subject="Kode Verifikasi LinguaFlow Anda",
        body_text=(
            "Selamat datang di LinguaFlow!\n\n"
            "Kode verifikasi Anda adalah: {otp}\n\n"
            "Kode ini berlaku selama 5 menit. Jangan bagikan kode ini kepada siapa pun.\n\n"
            "— Tim LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Selamat datang di LinguaFlow</h2>"
            "<p>Kode verifikasi Anda adalah:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Kode ini berlaku selama <strong>5 menit</strong>. Jangan bagikan kode ini kepada siapa pun.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— Tim LinguaFlow</p>"
            "</div>"
        ),
    ),
    "pt": EmailTemplate(
        subject="Seu código de verificação LinguaFlow",
        body_text=(
            "Bem-vindo ao LinguaFlow!\n\n"
            "Seu código de verificação é: {otp}\n\n"
            "Este código é válido por 5 minutos. Não compartilhe este código com ninguém.\n\n"
            "— Equipe LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Bem-vindo ao LinguaFlow</h2>"
            "<p>Seu código de verificação é:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Este código é válido por <strong>5 minutos</strong>. Não compartilhe este código com ninguém.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— Equipe LinguaFlow</p>"
            "</div>"
        ),
    ),
    "ru": EmailTemplate(
        subject="Ваш код подтверждения LinguaFlow",
        body_text=(
            "Добро пожаловать в LinguaFlow!\n\n"
            "Ваш код подтверждения: {otp}\n\n"
            "Код действителен в течение 5 минут. Никому не сообщайте этот код.\n\n"
            "— Команда LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>Добро пожаловать в LinguaFlow</h2>"
            "<p>Ваш код подтверждения:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>Код действителен в течение <strong>5 минут</strong>. Никому не сообщайте этот код.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— Команда LinguaFlow</p>"
            "</div>"
        ),
    ),
    "ar": EmailTemplate(
        subject="رمز التحقق الخاص بك في LinguaFlow",
        body_text=(
            "مرحبًا بك في LinguaFlow!\n\n"
            "رمز التحقق الخاص بك هو: {otp}\n\n"
            "هذا الرمز صالح لمدة 5 دقائق. يُرجى عدم مشاركة هذا الرمز مع أي شخص.\n\n"
            "— فريق LinguaFlow"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto; direction: rtl; text-align: right;\">"
            "<h2>مرحبًا بك في LinguaFlow</h2>"
            "<p>رمز التحقق الخاص بك هو:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>هذا الرمز صالح لمدة <strong>5 دقائق</strong>. يُرجى عدم مشاركة هذا الرمز مع أي شخص.</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— فريق LinguaFlow</p>"
            "</div>"
        ),
    ),
    "hi": EmailTemplate(
        subject="आपका LinguaFlow सत्यापन कोड",
        body_text=(
            "LinguaFlow में आपका स्वागत है!\n\n"
            "आपका सत्यापन कोड है: {otp}\n\n"
            "यह कोड 5 मिनट के लिए मान्य है। इसे किसी के साथ साझा न करें।\n\n"
            "— LinguaFlow टीम"
        ),
        body_html=(
            "<div style=\"font-family: sans-serif; max-width: 600px; margin: 0 auto;\">"
            "<h2>LinguaFlow में आपका स्वागत है</h2>"
            "<p>आपका सत्यापन कोड है:</p>"
            "<p style=\"font-size: 28px; font-weight: bold; letter-spacing: 4px; color: #4F46E5;\">{otp}</p>"
            "<p>यह कोड <strong>5 मिनट</strong> के लिए मान्य है। इसे किसी के साथ साझा न करें।</p>"
            "<hr style=\"border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;\" />"
            "<p style=\"color: #6B7280; font-size: 12px;\">— LinguaFlow टीम</p>"
            "</div>"
        ),
    ),
}


# Password-reset emails use the account's interface language when it is known.
# Keeping the copy separate from registration OTPs makes the recovery link and
# its security notice explicit instead of overloading an OTP template.
PASSWORD_RESET_EMAIL_COPY: dict[str, PasswordResetEmailCopy] = {
    "en": PasswordResetEmailCopy("Reset your LinguaFlow password", "Reset your password", "We received a request to reset the password for your LinguaFlow account.", "Reset password", "This link expires in {minutes} minutes.", "If you did not request this, you can safely ignore this email."),
    "vi": PasswordResetEmailCopy("Đặt lại mật khẩu LinguaFlow", "Đặt lại mật khẩu", "Chúng tôi đã nhận được yêu cầu đặt lại mật khẩu cho tài khoản LinguaFlow của bạn.", "Đặt lại mật khẩu", "Liên kết này hết hạn sau {minutes} phút.", "Nếu bạn không yêu cầu việc này, bạn có thể bỏ qua email một cách an toàn."),
    "ja": PasswordResetEmailCopy("LinguaFlow のパスワードをリセット", "パスワードをリセット", "LinguaFlow アカウントのパスワード再設定リクエストを受け付けました。", "パスワードをリセット", "このリンクは {minutes} 分後に期限切れになります。", "この操作を依頼していない場合は、このメールを無視してください。"),
    "zh": PasswordResetEmailCopy("重置您的 LinguaFlow 密码", "重置密码", "我们收到了重置您 LinguaFlow 帐户密码的请求。", "重置密码", "此链接将在 {minutes} 分钟后失效。", "如果不是您本人提出的请求，您可以放心忽略此邮件。"),
    "ko": PasswordResetEmailCopy("LinguaFlow 비밀번호 재설정", "비밀번호 재설정", "LinguaFlow 계정의 비밀번호 재설정 요청을 받았습니다.", "비밀번호 재설정", "이 링크는 {minutes}분 후에 만료됩니다.", "요청하지 않은 경우 이 이메일을 안전하게 무시하셔도 됩니다."),
    "fr": PasswordResetEmailCopy("Réinitialisez votre mot de passe LinguaFlow", "Réinitialisez votre mot de passe", "Nous avons reçu une demande de réinitialisation du mot de passe de votre compte LinguaFlow.", "Réinitialiser le mot de passe", "Ce lien expire dans {minutes} minutes.", "Si vous n'avez pas fait cette demande, vous pouvez ignorer cet e-mail en toute sécurité."),
    "de": PasswordResetEmailCopy("LinguaFlow-Passwort zurücksetzen", "Passwort zurücksetzen", "Wir haben eine Anfrage zum Zurücksetzen des Passworts für Ihr LinguaFlow-Konto erhalten.", "Passwort zurücksetzen", "Dieser Link läuft in {minutes} Minuten ab.", "Falls Sie dies nicht angefordert haben, können Sie diese E-Mail bedenkenlos ignorieren."),
    "es": PasswordResetEmailCopy("Restablece tu contraseña de LinguaFlow", "Restablece tu contraseña", "Hemos recibido una solicitud para restablecer la contraseña de tu cuenta de LinguaFlow.", "Restablecer contraseña", "Este enlace caduca en {minutes} minutos.", "Si no solicitaste esto, puedes ignorar este correo de forma segura."),
    "th": PasswordResetEmailCopy("รีเซ็ตรหัสผ่าน LinguaFlow ของคุณ", "รีเซ็ตรหัสผ่าน", "เราได้รับคำขอให้รีเซ็ตรหัสผ่านสำหรับบัญชี LinguaFlow ของคุณแล้ว", "รีเซ็ตรหัสผ่าน", "ลิงก์นี้จะหมดอายุภายใน {minutes} นาที", "หากคุณไม่ได้ร้องขอรายการนี้ คุณสามารถละเว้นอีเมลนี้ได้อย่างปลอดภัย"),
    "id": PasswordResetEmailCopy("Atur ulang kata sandi LinguaFlow Anda", "Atur ulang kata sandi", "Kami menerima permintaan untuk mengatur ulang kata sandi akun LinguaFlow Anda.", "Atur ulang kata sandi", "Tautan ini akan kedaluwarsa dalam {minutes} menit.", "Jika Anda tidak meminta ini, Anda dapat mengabaikan email ini dengan aman."),
    "pt": PasswordResetEmailCopy("Redefina sua senha do LinguaFlow", "Redefina sua senha", "Recebemos uma solicitação para redefinir a senha da sua conta LinguaFlow.", "Redefinir senha", "Este link expira em {minutes} minutos.", "Se você não solicitou isso, pode ignorar este e-mail com segurança."),
    "ru": PasswordResetEmailCopy("Сбросьте пароль LinguaFlow", "Сбросьте пароль", "Мы получили запрос на сброс пароля вашей учетной записи LinguaFlow.", "Сбросить пароль", "Срок действия этой ссылки истекает через {minutes} минут.", "Если вы не запрашивали это, можете безопасно проигнорировать это письмо."),
    "ar": PasswordResetEmailCopy("إعادة تعيين كلمة مرور LinguaFlow", "إعادة تعيين كلمة المرور", "تلقينا طلبًا لإعادة تعيين كلمة مرور حساب LinguaFlow الخاص بك.", "إعادة تعيين كلمة المرور", "تنتهي صلاحية هذا الرابط خلال {minutes} دقيقة.", "إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة بأمان."),
    "hi": PasswordResetEmailCopy("अपना LinguaFlow पासवर्ड रीसेट करें", "पासवर्ड रीसेट करें", "हमें आपके LinguaFlow खाते का पासवर्ड रीसेट करने का अनुरोध मिला है।", "पासवर्ड रीसेट करें", "यह लिंक {minutes} मिनट में समाप्त हो जाएगा।", "यदि आपने इसका अनुरोध नहीं किया है, तो आप इस ईमेल को सुरक्षित रूप से अनदेखा कर सकते हैं।"),
}


class EmailDeliveryError(Exception):
    """Raised when email delivery fails without leaking sensitive information."""


class SentEmail(NamedTuple):
    to_email: str
    subject: str
    body_text: str
    body_html: str | None


class BaseEmailSender:
    """Abstract email sender interface."""

    async def send(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        raise NotImplementedError


class MemoryEmailSender(BaseEmailSender):
    """In-memory email sender for tests. Captures sent emails without network calls."""

    def __init__(self) -> None:
        self.sent_emails: list[SentEmail] = []

    async def send(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        self.sent_emails.append(
            SentEmail(
                to_email=to_email,
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            )
        )

    def clear(self) -> None:
        self.sent_emails.clear()


class ConsoleEmailSender(BaseEmailSender):
    """Console email sender for local development. Never logs the plaintext OTP."""

    async def send(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        logger.info("Sent email to %s with subject: %s", to_email, subject)


class SmtpEmailSender(BaseEmailSender):
    """Deliver emails via standard SMTP server."""

    async def send(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        settings = get_settings()
        if not settings.smtp_host or not settings.smtp_host.strip():
            raise RuntimeError("SMTP host is not configured")

        def _sync_send() -> None:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
            msg["To"] = to_email

            msg.attach(MIMEText(body_text, "plain", "utf-8"))
            if body_html:
                msg.attach(MIMEText(body_html, "html", "utf-8"))

            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            ) as server:
                if settings.smtp_use_tls:
                    server.starttls()
                if settings.smtp_user and settings.smtp_password:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(msg)

        # A mail server may accept a TCP connection but stop responding while
        # DATA is being transmitted. The total async timeout keeps that remote
        # failure from tying up a password-reset request indefinitely.
        await asyncio.wait_for(
            asyncio.to_thread(_sync_send),
            timeout=settings.smtp_timeout_seconds,
        )


_memory_sender = MemoryEmailSender()
_console_sender = ConsoleEmailSender()
_smtp_sender = SmtpEmailSender()


def get_email_sender() -> BaseEmailSender:
    """Return the configured email sender singleton."""
    provider = get_settings().email_provider
    if provider == "memory":
        return _memory_sender
    elif provider == "console":
        return _console_sender
    return _smtp_sender


async def send_registration_otp_email(
    to_email: str,
    otp: str,
    language: str = "en",
    sender: BaseEmailSender | None = None,
) -> None:
    """Send localized registration OTP email.

    Never logs the plaintext OTP or leaks it in exceptions.
    """
    normalized_lang = language.lower() if language else "en"
    template = EMAIL_TEMPLATES.get(normalized_lang, EMAIL_TEMPLATES["en"])

    subject = template.subject
    body_text = template.body_text.format(otp=otp)
    body_html = template.body_html.format(otp=otp)

    email_sender = sender or get_email_sender()
    try:
        await email_sender.send(
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )
    except Exception as exc:
        # Never include OTP in logs or exceptions
        logger.error("Failed to send OTP email to %s: %s", to_email, type(exc).__name__)
        raise EmailDeliveryError("Failed to send verification email. Please try again later.") from None


async def send_password_reset_email(
    to_email: str,
    reset_url: str,
    expires_in_minutes: int,
    language: str = "en",
    sender: BaseEmailSender | None = None,
) -> None:
    """Send a single-use password-reset link without logging its token."""
    normalized_lang = language.lower() if language else "en"
    copy = PASSWORD_RESET_EMAIL_COPY.get(normalized_lang, PASSWORD_RESET_EMAIL_COPY["en"])
    escaped_url = escape(reset_url, quote=True)
    expiry = copy.expiry.format(minutes=expires_in_minutes)
    is_rtl = normalized_lang == "ar"
    direction = ' dir="rtl"' if is_rtl else ""
    alignment = " text-align: right;" if is_rtl else ""
    body_text = (
        f"{copy.heading}\n\n{copy.introduction}\n\n"
        f"{copy.button_label}: {reset_url}\n\n{expiry}\n\n{copy.security_notice}\n\n"
        "— The LinguaFlow Team"
    )
    body_html = (
        f'<div{direction} style="font-family: sans-serif; max-width: 600px; margin: 0 auto;{alignment}">'
        f"<h2>{escape(copy.heading)}</h2>"
        f"<p>{escape(copy.introduction)}</p>"
        f'<p style="margin: 28px 0;"><a href="{escaped_url}" '
        'style="display: inline-block; padding: 12px 20px; background: #4F46E5; color: #ffffff; '
        'border-radius: 8px; font-weight: 700; text-decoration: none;">'
        f"{escape(copy.button_label)}</a></p>"
        f"<p>{escape(expiry)}</p>"
        f"<p>{escape(copy.security_notice)}</p>"
        '<hr style="border: none; border-top: 1px solid #E5E7EB; margin: 20px 0;" />'
        '<p style="color: #6B7280; font-size: 12px;">— The LinguaFlow Team</p>'
        "</div>"
    )

    email_sender = sender or get_email_sender()
    try:
        await email_sender.send(
            to_email=to_email,
            subject=copy.subject,
            body_text=body_text,
            body_html=body_html,
        )
    except Exception as exc:
        # The link contains an authentication secret: never log it.
        logger.error("Failed to send password reset email to %s: %s", to_email, type(exc).__name__)
        raise EmailDeliveryError("Failed to send password reset email.") from None
