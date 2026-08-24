"""
REAL PostgreSQL integration tests -- Phase 9 production-readiness audit.

Every other test in this suite that touches "the database" runs against
SQLite (see tests/conftest.py's `sqlite_db` fixture and its own docstring
on exactly why: no Postgres server is available in this development
environment). That proves the SQLAlchemy models/migrations/GUID type are
*portable*, but it does NOT prove PostgreSQL-specific behavior --
native UUID/JSONB columns, real concurrent-transaction isolation, and
(most importantly) the actual `AsyncPostgresSaver` LangGraph checkpointer,
which has no SQLite equivalent at all in this codebase.

These tests are SKIPPED (not faked, not run against SQLite instead)
unless `TEST_DATABASE_URL` is set to a reachable `postgresql://` server,
e.g.:

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test_db \
        pytest -m postgres tests/test_postgres_integration.py

Run automatically by nothing in this environment -- there is no
PostgreSQL server here. Every one of these tests is REQUIRES REAL
POSTGRES in the final audit report; none of their assertions are backed
by a local run in this session.
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
        reason=(
            "TEST_DATABASE_URL is not set to a reachable postgresql:// URL -- "
            "these tests require a REAL PostgreSQL server and are skipped, never "
            "run against SQLite as a substitute. Set TEST_DATABASE_URL and re-run "
            "with `pytest -m postgres` to actually exercise them."
        ),
    ),
]


@pytest.fixture
async def postgres_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.db.models import Base

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


class TestPostgresSpecificBehavior:
    """UUID / JSON / indexes / constraints / transactions / isolation --
    everything SQLite either fakes (GUID -> CHAR(32)) or doesn't enforce
    the same way (SQLite's default transaction isolation is coarser)."""

    async def test_guid_column_is_a_real_postgres_uuid_type(self, postgres_engine) -> None:
        from sqlalchemy import text

        async with postgres_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT data_type FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'id'")
            )
            data_type = result.scalar_one()
        assert data_type == "uuid", f"expected a native Postgres uuid column, got {data_type!r}"

    async def test_token_usage_json_column_round_trips(self, postgres_engine) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models.analysis import Analysis, AnalysisStatus
        from app.db.models.dataset import Dataset
        from app.db.models.user import User

        session_factory = async_sessionmaker(bind=postgres_engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x")
            session.add(user)
            await session.flush()
            dataset = Dataset(user_id=user.id, name="n", file_name="n.csv", file_type="csv", file_size=1, file_hash="h")
            session.add(dataset)
            await session.flush()
            analysis = Analysis(
                user_id=user.id, dataset_id=dataset.id, dataset_version=1, thread_id="t1", question="q",
                status=AnalysisStatus.COMPLETED, token_usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
            session.add(analysis)
            await session.commit()
            analysis_id = analysis.id

        async with session_factory() as session:
            reloaded = await session.get(Analysis, analysis_id)
            assert reloaded.token_usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    async def test_unique_constraint_on_idempotency_key_is_enforced(self, postgres_engine) -> None:
        from sqlalchemy.exc import IntegrityError
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models.analysis import Analysis, AnalysisStatus
        from app.db.models.dataset import Dataset
        from app.db.models.user import User

        session_factory = async_sessionmaker(bind=postgres_engine, expire_on_commit=False)
        async with session_factory() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x")
            session.add(user)
            await session.flush()
            dataset = Dataset(user_id=user.id, name="n", file_name="n.csv", file_type="csv", file_size=1, file_hash="h")
            session.add(dataset)
            await session.flush()
            session.add(Analysis(
                user_id=user.id, dataset_id=dataset.id, dataset_version=1, thread_id="t1", question="q",
                status=AnalysisStatus.QUEUED, idempotency_key="dup-key",
            ))
            await session.commit()

            session.add(Analysis(
                user_id=user.id, dataset_id=dataset.id, dataset_version=1, thread_id="t2", question="q2",
                status=AnalysisStatus.QUEUED, idempotency_key="dup-key",
            ))
            with pytest.raises(IntegrityError):
                await session.commit()

    async def test_concurrent_transactions_do_not_see_each_others_uncommitted_writes(self, postgres_engine) -> None:
        """Real transaction isolation -- two separate sessions, one with
        an uncommitted write, the other must not see it (READ COMMITTED,
        Postgres's default)."""

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models.user import User

        session_factory = async_sessionmaker(bind=postgres_engine, expire_on_commit=False)
        email = f"{uuid.uuid4().hex}@example.com"

        session_a = session_factory()
        session_b = session_factory()
        try:
            session_a.add(User(email=email, password_hash="x"))
            await session_a.flush()  # written to the transaction, NOT committed

            from sqlalchemy import select

            result = await session_b.execute(select(User).where(User.email == email))
            assert result.scalar_one_or_none() is None, "session_b should not see session_a's uncommitted write"

            await session_a.commit()

            result = await session_b.execute(select(User).where(User.email == email))
            assert result.scalar_one_or_none() is not None, "session_b should see it once committed"
        finally:
            await session_a.close()
            await session_b.close()


class TestPostgresCheckpointPersistence:
    """The test explicitly required by the Phase 9 audit: create a
    thread, run the graph, persist a checkpoint, tear down the
    checkpointer entirely, build a BRAND NEW AsyncPostgresSaver instance
    against the same database, resume the SAME thread_id, and verify the
    state survived -- proving persistence actually works, not just that
    the wiring compiles (see agent/graph.py::checkpointer_context, which
    every other test in this codebase exercises only with InMemorySaver
    or a shared in-process fake -- see tests/test_multi_agent_graph.py
    and workers/analysis_worker.py's own test doubles)."""

    async def test_checkpoint_survives_a_new_checkpointer_instance(self) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        from app.agent.graph import build_multi_agent_graph
        from app.schemas.analysis import GeneratedSQL, ResultValidation, SupervisorDecision
        from tests.conftest import FakeStructuredModel

        conn_string = TEST_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        supervisor_model = FakeStructuredModel(
            SupervisorDecision(complexity="simple", requires_planning=False, requires_data_analysis=True, is_answerable=False, reasoning="not answerable, to reach a terminal state in one step")
        )

        # 1: create thread, 2: execute graph, 3: persist checkpoint.
        async with AsyncPostgresSaver.from_conn_string(conn_string) as checkpointer_a:
            await checkpointer_a.setup()
            graph_a = build_multi_agent_graph(checkpointer=checkpointer_a, supervisor_model=supervisor_model)
            await graph_a.ainvoke(
                {
                    "dataset_id": "irrelevant", "user_question": "anything", "conversation_context": None,
                    "current_agent": None, "supervisor_decision": None, "dataset_schema": None,
                    "analysis_plan": None, "current_step_index": 0, "completed_steps": [],
                    "generated_sql": None, "validated_sql": None, "executed_queries": [], "step_result_validation": None,
                    "retry_reason": None, "retry_count": 0, "findings": None, "review": None, "review_retry_count": 0,
                    "human_approval_required": False, "human_approval_reason": None, "human_approved": None,
                    "human_feedback": None, "synthesis_context": None, "chart": None, "final_response": None,
                },
                config=config,
            )
        # 4: stop execution -- checkpointer_a's connection is fully closed here.

        # 5: create a NEW checkpointer instance (a different connection,
        # simulating a restarted process) and 6: resume the SAME thread_id.
        async with AsyncPostgresSaver.from_conn_string(conn_string) as checkpointer_b:
            graph_b = build_multi_agent_graph(checkpointer=checkpointer_b, supervisor_model=supervisor_model)
            state = await graph_b.aget_state(config)

        # 7: verify state is preserved.
        assert state.values.get("final_response") is not None
        assert "not answerable" in state.values["final_response"].answer.lower() or "can't answer" in state.values["final_response"].answer.lower()
