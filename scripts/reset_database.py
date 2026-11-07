"""
Wipes all user data (users, behavioral profiles, samples, login history)
so the project can start with a clean slate. Does NOT touch the schema/
tables themselves - only deletes rows.

THIS IS IRREVERSIBLE. Double-check you want to wipe everything before running.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app, db
from models.user import User
from models.behavioral_sample import BehavioralSample
from models.behavioral_profile import BehavioralProfile
from models.login_history import LoginHistory

with app.app_context():
    deleted_login = db.session.query(LoginHistory).delete()
    deleted_samples = db.session.query(BehavioralSample).delete()
    deleted_profiles = db.session.query(BehavioralProfile).delete()
    deleted_users = db.session.query(User).delete()

    db.session.commit()

    print(f"Deleted: {deleted_login} login history rows, "
          f"{deleted_samples} behavioral samples, "
          f"{deleted_profiles} behavioral profiles, "
          f"{deleted_users} users")
    print("Database is now clean. New records will start fresh from here.")