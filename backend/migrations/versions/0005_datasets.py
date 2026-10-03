"""Task-scoped training review, dataset releases and permanent split protection."""
from alembic import op
import sqlalchemy as sa
revision='0005_datasets'
down_revision='0004_gt_pilot'
branch_labels=depends_on=None


def upgrade():
    op.create_table('training_reviews',sa.Column('id',sa.String(),primary_key=True),sa.Column('revision_id',sa.String(),sa.ForeignKey('annotation_revisions.id'),nullable=False),sa.Column('review_id',sa.String(),sa.ForeignKey('review_records.id'),nullable=False),sa.Column('scope_key',sa.String(),nullable=False),sa.Column('reviewer',sa.String(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.String(),nullable=False))
    op.create_index('ix_training_reviews_revision_id','training_reviews',['revision_id'])
    op.create_table('media_partitions',sa.Column('media_id',sa.String(),sa.ForeignKey('media.id'),primary_key=True),sa.Column('payload',sa.JSON(),nullable=False))
    op.create_table('dataset_snapshots',sa.Column('id',sa.String(),primary_key=True),sa.Column('project_id',sa.String(),sa.ForeignKey('projects.id'),nullable=False),sa.Column('manifest',sa.JSON(),nullable=False),sa.Column('manifest_hash',sa.String(),nullable=False),sa.Column('created_at',sa.String(),nullable=False))
    op.create_index('ix_dataset_snapshots_project_id','dataset_snapshots',['project_id'])
    op.create_table('split_locks',sa.Column('token',sa.String(),primary_key=True),sa.Column('split',sa.String(),nullable=False),sa.Column('dataset_id',sa.String(),sa.ForeignKey('dataset_snapshots.id'),nullable=False))
    op.create_table('dataset_notices',sa.Column('id',sa.String(),primary_key=True),sa.Column('dataset_id',sa.String(),sa.ForeignKey('dataset_snapshots.id'),nullable=False),sa.Column('actor',sa.String(),nullable=False),sa.Column('reason',sa.Text(),nullable=False),sa.Column('created_at',sa.String(),nullable=False))
    op.create_index('ix_dataset_notices_dataset_id','dataset_notices',['dataset_id'])


def downgrade():
    for name in ('dataset_notices','split_locks','dataset_snapshots','media_partitions','training_reviews'):op.drop_table(name)
