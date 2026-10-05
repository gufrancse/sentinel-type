"""Add email/phone, mouse dynamics, ML model path, login history

Revision ID: a7f3c9d2e1b4
Revises: c2012ddd13a9
Create Date: 2026-09-26 00:00:00.000000

NOTE: if you're running this on a fresh/throwaway dev database (typical
for a college project), it is much simpler to just delete
`sentinel_type.db` and run `flask db upgrade` from scratch instead of
migrating existing rows — email is NOT NULL + UNIQUE in the model and
existing rows won't have one.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7f3c9d2e1b4'
down_revision = 'c2012ddd13a9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('phone_number', sa.String(length=20), nullable=True))
        batch_op.create_unique_constraint('uq_users_email', ['email'])

    with op.batch_alter_table('behavioral_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('average_mouse_speed', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('average_click_interval', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('ml_model_path', sa.String(length=255), nullable=True))

    with op.batch_alter_table('behavioral_samples', schema=None) as batch_op:
        batch_op.add_column(sa.Column('average_mouse_speed', sa.Float(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('average_click_interval', sa.Float(), nullable=False, server_default='0'))

    op.create_table(
        'login_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('region', sa.String(length=100), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('match_score', sa.Float(), nullable=True),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('email_alert_sent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sms_alert_sent', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('marked_trusted', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_login_history_user_id', 'login_history', ['user_id'])


def downgrade():
    op.drop_index('ix_login_history_user_id', table_name='login_history')
    op.drop_table('login_history')

    with op.batch_alter_table('behavioral_samples', schema=None) as batch_op:
        batch_op.drop_column('average_click_interval')
        batch_op.drop_column('average_mouse_speed')

    with op.batch_alter_table('behavioral_profiles', schema=None) as batch_op:
        batch_op.drop_column('ml_model_path')
        batch_op.drop_column('average_click_interval')
        batch_op.drop_column('average_mouse_speed')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_email', type_='unique')
        batch_op.drop_column('phone_number')
        batch_op.drop_column('email')
