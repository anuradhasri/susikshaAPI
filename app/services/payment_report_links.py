import base64
import hashlib
import json
from datetime import date
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import get_settings


def cipher():
    key = hashlib.sha256(('payment-receipts:v1:' + get_settings().SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def report_token(region_id, start_date, end_date):
    return cipher().encrypt(json.dumps({'region_id': region_id, 'start_date': str(start_date),
                                       'end_date': str(end_date)}).encode()).decode()


def decode_report_token(token):
    try:
        raw = base64.b64decode(token.encode('ascii'), altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(raw).decode('ascii') != token:
            raise ValueError()
        data = json.loads(cipher().decrypt(token.encode()))
        region_id = int(data['region_id'])
        start, end = date.fromisoformat(data['start_date']), date.fromisoformat(data['end_date'])
        if region_id <= 0 or end < start:
            raise ValueError()
        return region_id, start, end
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError('Invalid payment report link.') from exc
