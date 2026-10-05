import getpass

from app import app
from extensions import db
from models.user import User
from sqlalchemy import select
from werkzeug.security import generate_password_hash


def main():
    username = input("Username: ").strip()

    if not username:
        print("Username is required.")
        return

    with app.app_context():
        user = db.session.scalar(
            select(User).where(User.username == username)
        )

        if user is None:
            print("User not found.")
            return

        if user.password_hash:
            print("Password is already configured for this user.")
            return

        password = getpass.getpass("Enter new password: ")
        confirm_password = getpass.getpass("Confirm new password: ")

        if not password:
            print("Password cannot be empty.")
            return

        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return

        if password != confirm_password:
            print("Passwords do not match.")
            return

        user.password_hash = generate_password_hash(password)

        db.session.commit()

        print(
            f"Password configured successfully for user: {user.username}"
        )


if __name__ == "__main__":
    main()