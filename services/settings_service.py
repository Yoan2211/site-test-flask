# services/settings_service.py
from models.db_database import db, AppSetting

def get_setting(key: str, default: str | None = None) -> str | None:
    row = AppSetting.query.get(key)
    return row.value if row else default

def set_setting(key: str, value: str) -> None:
    row = AppSetting.query.get(key)
    if not row:
        row = AppSetting(key=key, value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()
