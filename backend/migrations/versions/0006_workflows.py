"""Immutable measurement/evaluation/model evidence and atomic version pointers."""
from alembic import op
import sqlalchemy as sa
revision='0006_workflows'
down_revision='0005_datasets'
branch_labels=depends_on=None

def upgrade():
    op.create_table('work_artifacts',sa.Column('id',sa.String(),primary_key=True),sa.Column('project_id',sa.String(),sa.ForeignKey('projects.id')),sa.Column('kind',sa.String(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('content_hash',sa.String(),nullable=False),sa.Column('created_at',sa.String(),nullable=False))
    op.create_index('ix_work_artifacts_project_id','work_artifacts',['project_id'])
    op.create_index('ix_work_artifacts_kind','work_artifacts',['kind'])
    op.create_table('work_pointers',sa.Column('key',sa.String(),primary_key=True),sa.Column('artifact_id',sa.String(),sa.ForeignKey('work_artifacts.id'),nullable=False),sa.Column('version',sa.Integer(),nullable=False))

def downgrade():
    op.drop_table('work_pointers');op.drop_table('work_artifacts')
