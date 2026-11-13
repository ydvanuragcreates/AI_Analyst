"""SQLAlchemy models for Phase 9 persistence. Import from here (not the
individual submodules) so `Base.metadata` always sees every model --
this is what Alembic's autogenerate and `Base.metadata.create_all()`
(used by the test fixtures) both rely on."""

from app.db.base import Base
from app.db.models.analysis import Analysis, AnalysisMode, AnalysisStatus
from app.db.models.analysis_job import AnalysisJob, JobStatus
from app.db.models.analysis_message import AnalysisMessage
from app.db.models.dataset import Dataset
from app.db.models.dataset_file import DatasetFile
from app.db.models.deep_analysis_result import DeepAnalysisResult
from app.db.models.user import User

__all__ = [
    "Base",
    "User",
    "Dataset",
    "DatasetFile",
    "Analysis",
    "AnalysisStatus",
    "AnalysisMode",
    "AnalysisMessage",
    "AnalysisJob",
    "JobStatus",
    "DeepAnalysisResult",
]
