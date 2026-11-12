"""Stage gate persistence — no transaction ownership."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentforge.storage.models import (
    CandidateSetRow,
    GateDecisionRow,
    ReleaseRecordRow,
    ReviewCycleRow,
    ReviewReportRow,
    ReviewRequestRow,
    ReworkOrderRow,
    StageRow,
    StageSubmissionRow,
    StageUserDecisionRow,
)


class StageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_stage(self, stage_id: str) -> StageRow | None:
        return self.session.get(StageRow, stage_id)

    def list_stages(self, run_id: str) -> list[StageRow]:
        rows = list(self.session.scalars(select(StageRow).where(StageRow.run_id == run_id)).all())
        if rows:
            return [r for r in rows if r.run_id == run_id]
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, StageRow) and r.run_id == run_id
        ]

    def add_stage(self, row: StageRow) -> StageRow:
        self.session.add(row)
        return row

    def add_submission(self, row: StageSubmissionRow) -> StageSubmissionRow:
        self.session.add(row)
        return row

    def get_submission(self, submission_id: str) -> StageSubmissionRow | None:
        return self.session.get(StageSubmissionRow, submission_id)

    def find_submission_by_idempotency(
        self, *, stage_id: str, idempotency_key: str
    ) -> StageSubmissionRow | None:
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, StageSubmissionRow)
                and obj.stage_id == stage_id
                and obj.idempotency_key == idempotency_key
            ):
                return obj
        return self.session.scalar(
            select(StageSubmissionRow).where(
                StageSubmissionRow.stage_id == stage_id,
                StageSubmissionRow.idempotency_key == idempotency_key,
            )
        )

    def list_submissions(self, stage_id: str) -> list[StageSubmissionRow]:
        rows = list(
            self.session.scalars(
                select(StageSubmissionRow).where(StageSubmissionRow.stage_id == stage_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, StageSubmissionRow) and r.stage_id == stage_id
        ]

    def add_candidate_set(self, row: CandidateSetRow) -> CandidateSetRow:
        self.session.add(row)
        return row

    def get_candidate_set(self, set_id: str) -> CandidateSetRow | None:
        return self.session.get(CandidateSetRow, set_id)

    def add_review_cycle(self, row: ReviewCycleRow) -> ReviewCycleRow:
        self.session.add(row)
        return row

    def get_review_cycle(self, cycle_id: str) -> ReviewCycleRow | None:
        return self.session.get(ReviewCycleRow, cycle_id)

    def cycle_by_submission(self, submission_id: str) -> ReviewCycleRow | None:
        for obj in getattr(self.session, "added", []):
            if isinstance(obj, ReviewCycleRow) and obj.submission_id == submission_id:
                return obj
        return self.session.scalar(
            select(ReviewCycleRow).where(ReviewCycleRow.submission_id == submission_id)
        )

    def list_review_cycles(self, stage_id: str) -> list[ReviewCycleRow]:
        rows = list(
            self.session.scalars(
                select(ReviewCycleRow).where(ReviewCycleRow.stage_id == stage_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, ReviewCycleRow) and r.stage_id == stage_id
        ]

    def add_review_request(self, row: ReviewRequestRow) -> ReviewRequestRow:
        self.session.add(row)
        return row

    def get_review_request(self, request_id: str) -> ReviewRequestRow | None:
        return self.session.get(ReviewRequestRow, request_id)

    def list_review_requests(self, cycle_id: str) -> list[ReviewRequestRow]:
        rows = list(
            self.session.scalars(
                select(ReviewRequestRow).where(ReviewRequestRow.review_cycle_id == cycle_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, ReviewRequestRow) and r.review_cycle_id == cycle_id
        ]

    def add_review_report(self, row: ReviewReportRow) -> ReviewReportRow:
        self.session.add(row)
        return row

    def list_review_reports(self, cycle_id: str) -> list[ReviewReportRow]:
        rows = list(
            self.session.scalars(
                select(ReviewReportRow).where(ReviewReportRow.review_cycle_id == cycle_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, ReviewReportRow) and r.review_cycle_id == cycle_id
        ]

    def add_user_decision(self, row: StageUserDecisionRow) -> StageUserDecisionRow:
        self.session.add(row)
        return row

    def get_user_decision_by_request(self, request_id: str) -> StageUserDecisionRow | None:
        for obj in getattr(self.session, "added", []):
            if isinstance(obj, StageUserDecisionRow) and obj.request_id == request_id:
                return obj
        return self.session.scalar(
            select(StageUserDecisionRow).where(StageUserDecisionRow.request_id == request_id)
        )

    def latest_user_decision_for_cycle(self, review_cycle_id: str) -> StageUserDecisionRow | None:
        matches = [
            obj
            for obj in getattr(self.session, "added", [])
            if isinstance(obj, StageUserDecisionRow) and obj.review_cycle_id == review_cycle_id
        ]
        if matches:
            return matches[-1]
        rows = list(
            self.session.scalars(
                select(StageUserDecisionRow).where(
                    StageUserDecisionRow.review_cycle_id == review_cycle_id
                )
            ).all()
        )
        return rows[-1] if rows else None

    def get_rework(self, rework_order_id: str) -> ReworkOrderRow | None:
        for obj in getattr(self.session, "added", []):
            if isinstance(obj, ReworkOrderRow) and obj.id == rework_order_id:
                return obj
        return self.session.get(ReworkOrderRow, rework_order_id)

    def add_gate_decision(self, row: GateDecisionRow) -> GateDecisionRow:
        self.session.add(row)
        return row

    def list_gate_decisions(self, stage_id: str) -> list[GateDecisionRow]:
        rows = list(
            self.session.scalars(
                select(GateDecisionRow).where(GateDecisionRow.stage_id == stage_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, GateDecisionRow) and r.stage_id == stage_id
        ]

    def add_rework_order(self, row: ReworkOrderRow) -> ReworkOrderRow:
        self.session.add(row)
        return row

    def find_rework(self, *, cycle_id: str, problem_digest: str) -> ReworkOrderRow | None:
        for obj in getattr(self.session, "added", []):
            if (
                isinstance(obj, ReworkOrderRow)
                and obj.source_review_cycle_id == cycle_id
                and obj.problem_digest == problem_digest
            ):
                return obj
        return self.session.scalar(
            select(ReworkOrderRow).where(
                ReworkOrderRow.source_review_cycle_id == cycle_id,
                ReworkOrderRow.problem_digest == problem_digest,
            )
        )

    def list_rework_orders(self, stage_id: str) -> list[ReworkOrderRow]:
        rows = list(
            self.session.scalars(
                select(ReworkOrderRow).where(ReworkOrderRow.stage_id == stage_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, ReworkOrderRow) and r.stage_id == stage_id
        ]

    def add_release(self, row: ReleaseRecordRow) -> ReleaseRecordRow:
        self.session.add(row)
        return row

    def list_releases(self, stage_id: str) -> list[ReleaseRecordRow]:
        rows = list(
            self.session.scalars(
                select(ReleaseRecordRow).where(ReleaseRecordRow.stage_id == stage_id)
            ).all()
        )
        if rows:
            return rows
        return [
            r
            for r in getattr(self.session, "added", [])
            if isinstance(r, ReleaseRecordRow) and r.stage_id == stage_id
        ]
