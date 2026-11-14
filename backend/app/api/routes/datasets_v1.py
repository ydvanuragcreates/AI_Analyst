"""
Phase 9 v1 dataset endpoints: the same upload/storage/DuckDB pipeline as
Phase 2/3 (DatasetService, unchanged), now with owner tracking, basic
versioning, and schema caching layered on top in Postgres/Redis.

The underlying file + DuckDB table are managed exactly as before --
`Dataset.id` here IS the `dataset_id` DatasetService/DuckDBService already
use, so both systems agree on one identifier without either needing to
know about the other.
"""

import hashlib
import io
import logging
import uuid

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.client_ip import client_key
from app.core.errors import DailyLimitReachedError, DatasetNotFoundError, ValidationError
from app.db.models.user import User
from app.db.repositories.analysis_repository import AnalysisRepository
from app.db.repositories.dataset_repository import DatasetRepository
from app.db.session import get_db_session
from app.middleware.rate_limit import rate_limit
from app.schemas.dataset import DatasetMetadata
from app.schemas.dataset_v1 import DatasetV1ListResponse, DatasetV1Response
from app.schemas.deep_analysis import DataQualityReport, DatasetProfile
from app.services.data_quality_service import DataQualityService, get_data_quality_service
from app.services.dataset_service import DatasetService, get_dataset_service
from app.services.profiling_service import ProfilingService, get_profiling_service
from app.services.redis_service import RedisService, get_redis_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _cache_key(dataset_id: uuid.UUID, version: int) -> str:
    return f"dataset_schema:{dataset_id}:{version}"


def _profile_cache_key(dataset_id: uuid.UUID, version: int) -> str:
    return f"dataset_profile:{dataset_id}:{version}"


def _quality_cache_key(dataset_id: uuid.UUID, version: int) -> str:
    return f"dataset_quality:{dataset_id}:{version}"


@router.post("", response_model=DatasetV1Response, status_code=201, dependencies=[Depends(rate_limit("upload", "upload_rate_limit"))])
async def upload_dataset_v1(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetV1Response:
    """Upload a dataset, owned by the caller.

    File validation (extension allowlist, size limit, content sniffing,
    filename sanitization -- see utils/file_utils.py) is entirely
    DatasetService's job, unchanged from Phase 2/3; this route only adds
    the Postgres ownership/versioning row around it.
    """

    settings = get_settings()
    caller_key = client_key(request, settings)
    dataset_repo = DatasetRepository(session)
    per_client = settings.daily_upload_limit_per_client
    if per_client > 0:
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        if await dataset_repo.count_created_since(since, client_key=caller_key) >= per_client:
            raise DailyLimitReachedError(
                f"You've used your {per_client} uploads for today on this demo. "
                "Please try again tomorrow (limits reset at 00:00 UTC)."
            )

    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    # DatasetService.upload_dataset() reads (and thereby consumes) the
    # UploadFile itself -- rebuild one from the bytes already read above
    # rather than touching DatasetService's own signature.
    rebuilt = UploadFile(
        file=io.BytesIO(content), filename=file.filename, headers=Headers({"content-type": file.content_type or ""})
    )
    metadata: DatasetMetadata = await dataset_service.upload_dataset(rebuilt)

    previous = await dataset_repo.get_latest_version_by_filename_owned(metadata.filename, current_user.id)
    version = previous.version + 1 if previous else 1

    dataset = await dataset_repo.create(
        id=uuid.UUID(metadata.dataset_id),
        user_id=current_user.id,
        name=metadata.filename,
        file_name=metadata.filename,
        file_type=metadata.file_type,
        file_size=metadata.file_size,
        file_hash=file_hash,
        version=version,
        client_key=caller_key,
    )
    await dataset_repo.save_file(dataset.id, content)
    await session.commit()

    logger.info(
        "Dataset uploaded", extra={"user_id": str(current_user.id), "dataset_id": str(dataset.id), "version": version}
    )
    return DatasetV1Response(dataset_id=dataset.id, version=dataset.version, metadata=metadata)


@router.get("", response_model=DatasetV1ListResponse)
async def list_datasets_v1(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> DatasetV1ListResponse:
    dataset_repo = DatasetRepository(session)
    owned = await dataset_repo.list_for_user(current_user.id)

    responses = []
    for dataset in owned:
        try:
            metadata = dataset_service.get_dataset(str(dataset.id))
        except Exception:  # noqa: BLE001 -- file-layer row missing/corrupted; skip rather than 500 the whole list
            logger.warning("Dataset %s has a Postgres row but no readable file metadata", dataset.id)
            continue
        responses.append(DatasetV1Response(dataset_id=dataset.id, version=dataset.version, metadata=metadata))

    return DatasetV1ListResponse(datasets=responses, count=len(responses))


@router.get("/{dataset_id}", response_model=DatasetV1Response)
async def get_dataset_v1(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    dataset_service: DatasetService = Depends(get_dataset_service),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> DatasetV1Response:
    """Return one dataset's metadata (including its cached-when-possible
    schema/columns) -- 404, never leaking whether a dataset owned by
    someone else exists (see DatasetRepository.get_owned's docstring)."""

    dataset_repo = DatasetRepository(session)
    dataset = await dataset_repo.get_owned(dataset_id, current_user.id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")

    cache_key = _cache_key(dataset.id, dataset.version)
    if redis_service:
        cached = await redis_service.get(cache_key)
        if cached:
            return DatasetV1Response(dataset_id=dataset.id, version=dataset.version, metadata=DatasetMetadata.model_validate_json(cached))

    metadata = dataset_service.get_dataset(str(dataset.id))
    if redis_service:
        settings = get_settings()
        await redis_service.set(cache_key, metadata.model_dump_json(), ttl_seconds=settings.cache_ttl_seconds)

    return DatasetV1Response(dataset_id=dataset.id, version=dataset.version, metadata=metadata)


@router.get("/{dataset_id}/quality", response_model=DataQualityReport)
async def get_dataset_quality_v1(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    profiling_service: ProfilingService = Depends(get_profiling_service),
    data_quality_service: DataQualityService = Depends(get_data_quality_service),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> DataQualityReport:
    """Section 2's deterministic data-quality checks (services/data_quality_service.py),
    exposed standalone -- added for the frontend's dataset detail page,
    which needs a quality snapshot (missing values, duplicates, outliers)
    without requiring a full Phase 10 deep-analysis run first. Reuses
    DataQualityService.assess() exactly as the deep-analysis graph's
    data_quality_check node does; no quality logic is duplicated here.
    Cached the same way GET /{dataset_id}/profile is."""

    dataset_repo = DatasetRepository(session)
    dataset = await dataset_repo.get_owned(dataset_id, current_user.id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")

    cache_key = _quality_cache_key(dataset.id, dataset.version)
    if redis_service:
        cached = await redis_service.get(cache_key)
        if cached:
            return DataQualityReport.model_validate_json(cached)

    profile = await run_in_threadpool(profiling_service.profile_dataset, str(dataset.id))
    report = await run_in_threadpool(data_quality_service.assess, str(dataset.id), profile)
    if redis_service:
        settings = get_settings()
        await redis_service.set(cache_key, report.model_dump_json(), ttl_seconds=settings.cache_ttl_seconds)

    return report


@router.get("/{dataset_id}/profile", response_model=DatasetProfile)
async def get_dataset_profile_v1(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    profiling_service: ProfilingService = Depends(get_profiling_service),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> DatasetProfile:
    """Section 33/40: the autonomous profiling stage (services/profiling_service.py),
    exposed directly -- callers that just want the profile (without
    running a full deep analysis) don't need to create an Analysis job
    for it. Cached in Redis the same way GET /{dataset_id} caches
    DatasetMetadata: keyed by dataset id + version, so a re-upload
    (a new version) never serves a stale profile."""

    dataset_repo = DatasetRepository(session)
    dataset = await dataset_repo.get_owned(dataset_id, current_user.id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")

    cache_key = _profile_cache_key(dataset.id, dataset.version)
    if redis_service:
        cached = await redis_service.get(cache_key)
        if cached:
            return DatasetProfile.model_validate_json(cached)

    profile = await run_in_threadpool(profiling_service.profile_dataset, str(dataset.id))
    if redis_service:
        settings = get_settings()
        await redis_service.set(cache_key, profile.model_dump_json(), ttl_seconds=settings.cache_ttl_seconds)

    return profile


@router.delete("/{dataset_id}", status_code=204)
async def delete_dataset_v1(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    dataset_service: DatasetService = Depends(get_dataset_service),
    redis_service: RedisService | None = Depends(get_redis_service),
) -> None:
    dataset_repo = DatasetRepository(session)
    dataset = await dataset_repo.get_owned(dataset_id, current_user.id)
    if dataset is None:
        raise DatasetNotFoundError(f"Dataset '{dataset_id}' was not found")

    # Checked BEFORE touching the file/DuckDB table -- deleting those
    # first and only then discovering the Postgres row can't be deleted
    # (existing Analysis rows reference it) would leave a Dataset row
    # pointing at a file that no longer exists. Also keeps historical
    # analyses reproducible (Analysis.dataset_version + dataset_id is
    # exactly what that's for -- see db/models/analysis.py).
    if await AnalysisRepository(session).exists_for_dataset(dataset.id):
        raise ValidationError(
            "This dataset has existing analyses and cannot be deleted -- historical analyses must "
            "remain reproducible. Delete is only allowed for datasets with no analyses."
        )

    dataset_service.delete_dataset(str(dataset.id))
    await dataset_repo.delete(dataset)
    await session.commit()

    if redis_service:
        await redis_service.delete(_cache_key(dataset.id, dataset.version))

    logger.info("Dataset deleted", extra={"user_id": str(current_user.id), "dataset_id": str(dataset_id)})
