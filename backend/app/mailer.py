"""Outgoing mail for email + password sign-in: confirmation, reset and notice
emails, in the user's interface language.

Mail goes over plain SMTP to SMTP_HOST:SMTP_PORT, by default the send-only
Postfix on this host (see SETUP.md), which signs it with DKIM and delivers it.
Pointing the SMTP_* settings at a relay needs no code change. Sending is
synchronous; routes call it through asyncio.to_thread.
"""

import html
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from app.config import (
    APP_ORIGIN,
    MAIL_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_STARTTLS,
    SMTP_USER,
)

TIMEOUT = 15


class MailError(RuntimeError):
    """The SMTP server could not be reached or refused the message."""


def send(to: str, subject: str, text: str, html_body: str) -> None:
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    domain = parseaddr(MAIL_FROM)[1].rpartition("@")[2] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    # Automated mail: no out-of-office replies back to no-reply@.
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
            if SMTP_STARTTLS:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(str(exc)) from exc


# ---------- templates ----------
# Each kind has a subject, the paragraphs before the button, the button label
# and the paragraphs after it. {name} is the display name. A notice with no
# link sets "button" to "". Languages match src/i18n.

_TEMPLATES: dict[str, dict[str, dict]] = {
    "en": {
        "confirm": {
            "subject": "Confirm your email for Cappuccina",
            "before": ["Hi {name}!", "Thanks for signing up. Confirm your email address to start chatting:"],
            "button": "Confirm my email",
            "after": ["This link works for 24 hours. If you didn't sign up, you can ignore this email."],
        },
        "reset": {
            "subject": "Reset your Cappuccina password",
            "before": ["Hi {name}!", "Someone asked to reset the password for your Cappuccina account. To choose a new password, open this link:"],
            "button": "Choose a new password",
            "after": ["This link works for 1 hour. If you didn't ask for this, you can ignore this email and your password stays the same."],
        },
        "exists_google": {
            "subject": "You already have a Cappuccina account",
            "before": ["Hi {name}!", "Someone tried to sign up with this email, but you already have an account that signs in with Google.", "To sign in, use “Continue with Google”. To also sign in with a password, use “Forgot password?” on the sign-in page to set one."],
            "button": "Open Cappuccina",
            "after": ["If this wasn't you, you can ignore this email."],
        },
        "exists_password": {
            "subject": "You already have a Cappuccina account",
            "before": ["Hi {name}!", "Someone tried to sign up with this email, but you already have an account. You can sign in with your password, or use “Forgot password?” to reset it."],
            "button": "Open Cappuccina",
            "after": ["If this wasn't you, you can ignore this email."],
        },
        "password_changed": {
            "subject": "Your Cappuccina password was changed",
            "before": ["Hi {name}!", "The password for your Cappuccina account was just changed, and your other devices were signed out."],
            "button": "",
            "after": ["If this wasn't you, use “Forgot password?” on the sign-in page right away to choose a new one."],
        },
    },
    "it": {
        "confirm": {
            "subject": "Conferma la tua email per Cappuccina",
            "before": ["Ciao {name}!", "Grazie per la registrazione. Conferma il tuo indirizzo email per iniziare a chattare:"],
            "button": "Conferma la mia email",
            "after": ["Questo link funziona per 24 ore. Se non ti sei registrato, puoi ignorare questa email."],
        },
        "reset": {
            "subject": "Reimposta la password di Cappuccina",
            "before": ["Ciao {name}!", "Qualcuno ha chiesto di reimpostare la password del tuo account Cappuccina. Per sceglierne una nuova, apri questo link:"],
            "button": "Scegli una nuova password",
            "after": ["Questo link funziona per 1 ora. Se non l'hai chiesto tu, puoi ignorare questa email: la password resta la stessa."],
        },
        "exists_google": {
            "subject": "Hai già un account Cappuccina",
            "before": ["Ciao {name}!", "Qualcuno ha provato a registrarsi con questa email, ma hai già un account che accede con Google.", "Per accedere, usa “Continua con Google”. Per accedere anche con una password, usa “Password dimenticata?” nella pagina di accesso per impostarne una."],
            "button": "Apri Cappuccina",
            "after": ["Se non sei stato tu, puoi ignorare questa email."],
        },
        "exists_password": {
            "subject": "Hai già un account Cappuccina",
            "before": ["Ciao {name}!", "Qualcuno ha provato a registrarsi con questa email, ma hai già un account. Puoi accedere con la tua password, oppure usare “Password dimenticata?” per reimpostarla."],
            "button": "Apri Cappuccina",
            "after": ["Se non sei stato tu, puoi ignorare questa email."],
        },
        "password_changed": {
            "subject": "La password di Cappuccina è stata cambiata",
            "before": ["Ciao {name}!", "La password del tuo account Cappuccina è appena stata cambiata e gli altri dispositivi sono stati disconnessi."],
            "button": "",
            "after": ["Se non sei stato tu, usa subito “Password dimenticata?” nella pagina di accesso per sceglierne una nuova."],
        },
    },
    "id": {
        "confirm": {
            "subject": "Konfirmasi email kamu untuk Cappuccina",
            "before": ["Hai {name}!", "Terima kasih sudah mendaftar. Konfirmasi alamat email kamu untuk mulai mengobrol:"],
            "button": "Konfirmasi email saya",
            "after": ["Tautan ini berlaku selama 24 jam. Kalau kamu tidak mendaftar, abaikan saja email ini."],
        },
        "reset": {
            "subject": "Atur ulang kata sandi Cappuccina",
            "before": ["Hai {name}!", "Seseorang meminta untuk mengatur ulang kata sandi akun Cappuccina kamu. Untuk memilih kata sandi baru, buka tautan ini:"],
            "button": "Pilih kata sandi baru",
            "after": ["Tautan ini berlaku selama 1 jam. Kalau kamu tidak memintanya, abaikan saja email ini dan kata sandimu tidak berubah."],
        },
        "exists_google": {
            "subject": "Kamu sudah punya akun Cappuccina",
            "before": ["Hai {name}!", "Seseorang mencoba mendaftar dengan email ini, tapi kamu sudah punya akun yang masuk dengan Google.", "Untuk masuk, gunakan “Lanjutkan dengan Google”. Supaya bisa masuk dengan kata sandi juga, gunakan “Lupa kata sandi?” di halaman masuk untuk membuatnya."],
            "button": "Buka Cappuccina",
            "after": ["Kalau ini bukan kamu, abaikan saja email ini."],
        },
        "exists_password": {
            "subject": "Kamu sudah punya akun Cappuccina",
            "before": ["Hai {name}!", "Seseorang mencoba mendaftar dengan email ini, tapi kamu sudah punya akun. Kamu bisa masuk dengan kata sandimu, atau gunakan “Lupa kata sandi?” untuk mengatur ulang."],
            "button": "Buka Cappuccina",
            "after": ["Kalau ini bukan kamu, abaikan saja email ini."],
        },
        "password_changed": {
            "subject": "Kata sandi Cappuccina kamu telah diubah",
            "before": ["Hai {name}!", "Kata sandi akun Cappuccina kamu baru saja diubah, dan perangkat lain telah dikeluarkan."],
            "button": "",
            "after": ["Kalau ini bukan kamu, segera gunakan “Lupa kata sandi?” di halaman masuk untuk memilih yang baru."],
        },
    },
    "ur": {
        "confirm": {
            "subject": "Cappuccina کے لیے اپنی ای میل کی تصدیق کریں",
            "before": ["سلام {name}!", "سائن اپ کرنے کا شکریہ۔ چیٹ شروع کرنے کے لیے اپنے ای میل ایڈریس کی تصدیق کریں:"],
            "button": "میری ای میل کی تصدیق کریں",
            "after": ["یہ لنک 24 گھنٹے تک کام کرے گا۔ اگر آپ نے سائن اپ نہیں کیا تو اس ای میل کو نظر انداز کر دیں۔"],
        },
        "reset": {
            "subject": "اپنا Cappuccina پاس ورڈ ری سیٹ کریں",
            "before": ["سلام {name}!", "کسی نے آپ کے Cappuccina اکاؤنٹ کا پاس ورڈ ری سیٹ کرنے کو کہا ہے۔ نیا پاس ورڈ چننے کے لیے یہ لنک کھولیں:"],
            "button": "نیا پاس ورڈ چنیں",
            "after": ["یہ لنک 1 گھنٹے تک کام کرے گا۔ اگر آپ نے یہ نہیں کہا تو اس ای میل کو نظر انداز کر دیں، آپ کا پاس ورڈ وہی رہے گا۔"],
        },
        "exists_google": {
            "subject": "آپ کا Cappuccina اکاؤنٹ پہلے سے موجود ہے",
            "before": ["سلام {name}!", "کسی نے اس ای میل سے سائن اپ کرنے کی کوشش کی، لیکن آپ کا اکاؤنٹ پہلے سے موجود ہے جو گوگل سے سائن اِن ہوتا ہے۔", "سائن اِن کرنے کے لیے “گوگل کے ساتھ جاری رکھیں” استعمال کریں۔ پاس ورڈ سے بھی سائن اِن کرنے کے لیے، سائن اِن صفحے پر “پاس ورڈ بھول گئے؟” سے پاس ورڈ بنائیں۔"],
            "button": "Cappuccina کھولیں",
            "after": ["اگر یہ آپ نہیں تھے تو اس ای میل کو نظر انداز کر دیں۔"],
        },
        "exists_password": {
            "subject": "آپ کا Cappuccina اکاؤنٹ پہلے سے موجود ہے",
            "before": ["سلام {name}!", "کسی نے اس ای میل سے سائن اپ کرنے کی کوشش کی، لیکن آپ کا اکاؤنٹ پہلے سے موجود ہے۔ آپ اپنے پاس ورڈ سے سائن اِن کر سکتے ہیں، یا “پاس ورڈ بھول گئے؟” سے اسے ری سیٹ کر سکتے ہیں۔"],
            "button": "Cappuccina کھولیں",
            "after": ["اگر یہ آپ نہیں تھے تو اس ای میل کو نظر انداز کر دیں۔"],
        },
        "password_changed": {
            "subject": "آپ کا Cappuccina پاس ورڈ بدل دیا گیا",
            "before": ["سلام {name}!", "آپ کے Cappuccina اکاؤنٹ کا پاس ورڈ ابھی بدلا گیا ہے، اور دوسرے آلات سے سائن آؤٹ کر دیا گیا ہے۔"],
            "button": "",
            "after": ["اگر یہ آپ نہیں تھے تو فوراً سائن اِن صفحے پر “پاس ورڈ بھول گئے؟” سے نیا پاس ورڈ چنیں۔"],
        },
    },
    "ur-Latn": {
        "confirm": {
            "subject": "Cappuccina ke liye apni email confirm karein",
            "before": ["Salaam {name}!", "Sign up karne ka shukriya. Chat shuru karne ke liye apna email address confirm karein:"],
            "button": "Meri email confirm karein",
            "after": ["Yeh link 24 ghante tak kaam karega. Agar aap ne sign up nahin kiya to is email ko nazar andaaz kar dein."],
        },
        "reset": {
            "subject": "Apna Cappuccina password reset karein",
            "before": ["Salaam {name}!", "Kisi ne aap ke Cappuccina account ka password reset karne ko kaha hai. Naya password chunne ke liye yeh link kholein:"],
            "button": "Naya password chunein",
            "after": ["Yeh link 1 ghante tak kaam karega. Agar aap ne yeh nahin kaha to is email ko nazar andaaz kar dein, aap ka password wahi rahega."],
        },
        "exists_google": {
            "subject": "Aap ka Cappuccina account pehle se maujood hai",
            "before": ["Salaam {name}!", "Kisi ne is email se sign up karne ki koshish ki, lekin aap ka account pehle se maujood hai jo Google se sign in hota hai.", "Sign in karne ke liye “Google ke saath jari rakhein” istemaal karein. Password se bhi sign in karne ke liye, sign-in page par “Password bhool gaye?” se password banayein."],
            "button": "Cappuccina kholein",
            "after": ["Agar yeh aap nahin the to is email ko nazar andaaz kar dein."],
        },
        "exists_password": {
            "subject": "Aap ka Cappuccina account pehle se maujood hai",
            "before": ["Salaam {name}!", "Kisi ne is email se sign up karne ki koshish ki, lekin aap ka account pehle se maujood hai. Aap apne password se sign in kar sakte hain, ya “Password bhool gaye?” se usay reset kar sakte hain."],
            "button": "Cappuccina kholein",
            "after": ["Agar yeh aap nahin the to is email ko nazar andaaz kar dein."],
        },
        "password_changed": {
            "subject": "Aap ka Cappuccina password badal diya gaya",
            "before": ["Salaam {name}!", "Aap ke Cappuccina account ka password abhi badla gaya hai, aur doosre devices se sign out kar diya gaya hai."],
            "button": "",
            "after": ["Agar yeh aap nahin the to foran sign-in page par “Password bhool gaye?” se naya password chunein."],
        },
    },
}

_RTL = {"ur"}


def render(kind: str, lang: str, name: str, link: str = "") -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one email kind in ``lang`` (English if unknown)."""
    lang = lang if lang in _TEMPLATES else "en"
    tpl = _TEMPLATES[lang][kind]
    before = [p.format(name=name) for p in tpl["before"]]
    after = [p.format(name=name) for p in tpl["after"]]
    link = link or f"{APP_ORIGIN}/"

    text_parts = [*before, link, *after] if tpl["button"] else [*before, *after]
    text = "\n\n".join(text_parts) + "\n\n— Cappuccina\n"

    def para(p: str) -> str:
        return f'<p style="margin:0 0 16px">{html.escape(p)}</p>'

    button = ""
    if tpl["button"]:
        href = html.escape(link, quote=True)
        button = (
            f'<p style="margin:24px 0"><a href="{href}" style="background:#7a4b2a;'
            "color:#fff;padding:12px 20px;border-radius:999px;text-decoration:none;"
            f'display:inline-block">{html.escape(tpl["button"])}</a></p>'
            f'<p style="margin:0 0 16px;font-size:13px;color:#666;word-break:break-all">{href}</p>'
        )
    direction = "rtl" if lang in _RTL else "ltr"
    html_body = (
        f'<!doctype html><html lang="{lang}" dir="{direction}"><body style="margin:0;'
        "padding:24px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;"
        'font-size:15px;line-height:1.5;color:#222">'
        '<div style="max-width:480px;margin:0 auto">'
        + "".join(para(p) for p in before)
        + button
        + "".join(para(p) for p in after)
        + '<p style="margin:24px 0 0;color:#666">— Cappuccina</p></div></body></html>'
    )
    return tpl["subject"], text, html_body


def send_kind(kind: str, to: str, lang: str, name: str, link: str = "") -> None:
    subject, text, html_body = render(kind, lang, name, link)
    send(to, subject, text, html_body)
