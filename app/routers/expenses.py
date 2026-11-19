import csv
import hashlib
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, Header, Query, Request, Response, UploadFile, status
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import ValidationError

from app.cursor import decode_cursor, encode_cursor, filters_fingerprint
from app.dependencies import CurrentUser, SessionDep
from app.errors import ApiError
from app.models import Category, Expense, IdempotencyRecord
from app.receipts import store_receipt, validate_receipt
from app.schemas import (
    CsvImportError,
    CsvImportResult,
    ExpenseCreate,
    ExpenseOut,
    ExpensePage,
    ExpensePatch,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])


async def _owned_expense(
    expense_id: uuid.UUID, user_id: uuid.UUID, session: SessionDep
) -> Expense:
    expense = await session.scalar(
        select(Expense).where(Expense.id == expense_id, Expense.user_id == user_id)
    )
    if expense is None:
        raise ApiError(404, "expense_not_found", "Expense not found")
    return expense


async def _owned_category(
    category_id: uuid.UUID, user_id: uuid.UUID, session: SessionDep
) -> bool:
    return (
        await session.scalar(
            select(Category.id).where(
                Category.id == category_id, Category.user_id == user_id
            )
        )
    ) is not None


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreate,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=255)
    ] = None,
) -> ExpenseOut:
    if not await _owned_category(payload.category_id, user.id, session):
        raise ApiError(404, "category_not_found", "Category not found")
    if idempotency_key is not None:
        key = idempotency_key.strip()
        if not key:
            raise ApiError(400, "invalid_idempotency_key", "Idempotency-Key cannot be empty")
        serialized = json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        request_hash = hashlib.sha256(serialized.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        insert_result = await session.execute(
            pg_insert(IdempotencyRecord)
            .values(
                user_id=user.id,
                key=key,
                request_hash=request_hash,
                expires_at=now + timedelta(hours=24),
            )
            .on_conflict_do_update(
                constraint="uq_idempotency_user_key",
                set_={
                    "request_hash": request_hash,
                    "expense_id": None,
                    "response_body": None,
                    "created_at": now,
                    "expires_at": now + timedelta(hours=24),
                },
                where=IdempotencyRecord.expires_at <= now,
            )
            .returning(IdempotencyRecord.id)
        )
        record_id = insert_result.scalar_one_or_none()
        if record_id is None:
            record = await session.scalar(
                select(IdempotencyRecord)
                .where(IdempotencyRecord.user_id == user.id, IdempotencyRecord.key == key)
                .with_for_update()
            )
            if record is None:
                raise ApiError(409, "idempotency_conflict", "The idempotent request could not be resolved")
            if record.request_hash != request_hash:
                raise ApiError(
                    422,
                    "idempotency_key_reused",
                    "Idempotency-Key was already used with a different request",
                )
            if record.response_body is None:
                raise ApiError(
                    409,
                    "idempotency_conflict",
                    "The original idempotent request has no stored response",
                )
            return ExpenseOut.model_validate(record.response_body)

        expense = Expense(user_id=user.id, **payload.model_dump())
        session.add(expense)
        await session.flush()
        record = await session.get(IdempotencyRecord, record_id)
        record.expense_id = expense.id
        await session.refresh(expense)
        record.response_body = ExpenseOut.model_validate(expense).model_dump(mode="json")
        await session.commit()
        return ExpenseOut.model_validate(record.response_body)

    expense = Expense(user_id=user.id, **payload.model_dump())
    session.add(expense)
    await session.commit()
    await session.refresh(expense)
    return expense


@router.get("", response_model=ExpensePage)
async def list_expenses(
    user: CurrentUser,
    session: SessionDep,
    category_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_amount: Decimal | None = Query(default=None, ge=0),
    max_amount: Decimal | None = Query(default=None, ge=0),
    currency: str | None = Query(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ExpensePage:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ApiError(400, "invalid_date_range", "date_from must be on or before date_to")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise ApiError(400, "invalid_amount_range", "min_amount must not exceed max_amount")
    if category_id is not None and not await _owned_category(category_id, user.id, session):
        raise ApiError(404, "category_not_found", "Category not found")

    filter_values = {
        "category_id": category_id,
        "date_from": date_from,
        "date_to": date_to,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "currency": currency,
        "q": q,
    }
    fingerprint = filters_fingerprint(filter_values)
    cursor_data = decode_cursor(cursor, fingerprint) if cursor else None
    snapshot = (
        cursor_data["snapshot"]
        if cursor_data
        else await session.scalar(select(func.now()))
    )
    conditions = [Expense.user_id == user.id, Expense.created_at <= snapshot]
    if category_id is not None:
        conditions.append(Expense.category_id == category_id)
    if date_from is not None:
        conditions.append(Expense.spent_on >= date_from)
    if date_to is not None:
        conditions.append(Expense.spent_on <= date_to)
    if min_amount is not None:
        conditions.append(Expense.amount >= min_amount)
    if max_amount is not None:
        conditions.append(Expense.amount <= max_amount)
    if currency is not None:
        conditions.append(Expense.currency == currency)
    if q is not None:
        conditions.append(Expense.description.ilike(f"%{q}%"))
    if cursor_data:
        conditions.append(
            or_(
                Expense.spent_on < cursor_data["spent_on"],
                and_(
                    Expense.spent_on == cursor_data["spent_on"],
                    Expense.id < cursor_data["id"],
                ),
            )
        )

    rows = (
        await session.scalars(
            select(Expense)
            .where(*conditions)
            .order_by(Expense.spent_on.desc(), Expense.id.desc())
            .limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = (
        encode_cursor(items[-1].spent_on, items[-1].id, snapshot, fingerprint)
        if has_more
        else None
    )
    return ExpensePage(items=items, next_cursor=next_cursor)


@router.get("/{expense_id}", response_model=ExpenseOut)
async def get_expense(
    expense_id: uuid.UUID, user: CurrentUser, session: SessionDep, response: Response
) -> Expense:
    expense = await _owned_expense(expense_id, user.id, session)
    response.headers["ETag"] = f'"{expense.version}"'
    return expense


def _if_match_version(value: str | None) -> int:
    if value is None:
        raise ApiError(428, "if_match_required", "The If-Match header is required")
    normalized = value.strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:]
    normalized = normalized.strip('"')
    if not normalized.isdigit():
        raise ApiError(400, "invalid_if_match", "If-Match must contain an expense version")
    return int(normalized)


@router.patch("/{expense_id}", response_model=ExpenseOut)
async def update_expense(
    expense_id: uuid.UUID,
    payload: ExpensePatch,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Expense:
    expected_version = _if_match_version(if_match)
    changes = payload.model_dump(exclude_unset=True)
    category_id = changes.get("category_id")
    if category_id is not None and not await _owned_category(
        category_id, user.id, session
    ):
        raise ApiError(404, "category_not_found", "Category not found")

    result = await session.execute(
        update(Expense)
        .where(
            Expense.id == expense_id,
            Expense.user_id == user.id,
            Expense.version == expected_version,
        )
        .values(
            **changes,
            version=Expense.version + 1,
            updated_at=func.now(),
        )
        .returning(Expense)
        .execution_options(populate_existing=True)
    )
    expense = result.scalar_one_or_none()
    if expense is None:
        exists = await session.scalar(
            select(Expense.id).where(
                Expense.id == expense_id, Expense.user_id == user.id
            )
        )
        if exists is None:
            raise ApiError(404, "expense_not_found", "Expense not found")
        raise ApiError(412, "version_conflict", "The expense has changed; fetch it and retry")

    await session.commit()
    response.headers["ETag"] = f'"{expense.version}"'
    return expense


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    expense_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> Response:
    expense = await _owned_expense(expense_id, user.id, session)
    await session.delete(expense)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/import.csv", response_model=CsvImportResult)
async def import_expenses_csv(
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
) -> CsvImportResult:
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise ApiError(413, "file_too_large", "CSV import exceeds the 5 MB limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(400, "invalid_csv_encoding", "CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(text))
    required = {"category_id", "amount", "currency", "spent_on"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ApiError(
            400,
            "invalid_csv_headers",
            "CSV must include category_id, amount, currency, and spent_on columns",
        )

    imported: list[Expense] = []
    errors: list[CsvImportError] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            if any(value is None for value in row.values()):
                raise ValueError("Row has more columns than the CSV header")
            payload = ExpenseCreate.model_validate(row)
            if not await _owned_category(payload.category_id, user.id, session):
                raise ValueError("Category not found")
            imported.append(Expense(user_id=user.id, **payload.model_dump()))
        except (ValidationError, ValueError, TypeError) as exc:
            if isinstance(exc, ValidationError):
                message = "; ".join(error["msg"] for error in exc.errors())
            else:
                message = str(exc)
            errors.append(CsvImportError(row=row_number, message=message))

    session.add_all(imported)
    if imported:
        await session.commit()
    return CsvImportResult(
        imported_count=len(imported),
        rejected_count=len(errors),
        errors=errors,
    )


@router.post("/{expense_id}/receipt", response_model=ExpenseOut)
async def upload_receipt(
    expense_id: uuid.UUID,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    file: UploadFile = File(...),
) -> Expense:
    expense = await _owned_expense(expense_id, user.id, session)
    content = await file.read(5 * 1024 * 1024 + 1)
    try:
        content_type, extension = validate_receipt(content, file.filename)
    except ValueError as exc:
        raise ApiError(422, "invalid_receipt", str(exc)) from exc
    key = f"receipts/{user.id}/{uuid.uuid4()}{extension}"
    storage = request.app.state.receipt_storage
    await store_receipt(storage, key, content, content_type)
    expense.receipt_key = key
    await session.commit()
    await session.refresh(expense)
    return expense
