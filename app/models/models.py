from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Boolean, Text, ForeignKey, Enum as SAEnum, JSON,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    timezone = Column(String(64), default="UTC")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    accounts = relationship("BankAccount", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    account_type = Column(String(32), default="bank")  # bank/cash/credit
    currency = Column(String(8), default="INR")
    balance = Column(Numeric(14, 2), default=0)
    balance_source = Column(String(32), default="manual")
    balance_updated_at = Column(DateTime(timezone=True), default=utcnow)
    user = relationship("User", back_populates="accounts")


class IncomeRecord(Base):
    __tablename__ = "income_records"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source = Column(String(128), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    frequency = Column(String(32), default="monthly")
    expected_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(64), nullable=False)
    kind = Column(String(16), default="expense")  # expense/income


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), default="INR")
    txn_type = Column(String(16), default="expense")  # income/expense/transfer/refund
    description = Column(Text)
    txn_date = Column(DateTime(timezone=True), default=utcnow, index=True)
    status = Column(String(16), default="confirmed")  # pending/confirmed
    source = Column(String(32), default="manual")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    user = relationship("User", back_populates="transactions")


class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    period = Column(String(16), default="monthly")
    limit_amount = Column(Numeric(14, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class FixedCommitment(Base):
    __tablename__ = "fixed_commitments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=False)
    recurrence = Column(String(32), default="monthly")
    status = Column(String(16), default="unpaid")  # unpaid/paid/cancelled
    paid_txn_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class SavingsRule(Base):
    __tablename__ = "savings_rules"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    protected_monthly = Column(Numeric(14, 2), default=0)
    savings_percent = Column(Numeric(5, 2), default=0)
    side_savings_percent = Column(Numeric(5, 2), default=10)
    fixed_monthly_expense = Column(Numeric(14, 2), default=0)
    emergency_target = Column(Numeric(14, 2), default=0)
    emergency_current = Column(Numeric(14, 2), default=0)
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class Goal(Base):
    __tablename__ = "goals"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    goal_type = Column(String(32), default="trip")
    target_amount = Column(Numeric(14, 2), nullable=False)
    current_amount = Column(Numeric(14, 2), default=0)
    target_date = Column(DateTime(timezone=True))
    priority = Column(String(16), default="medium")
    status = Column(String(16), default="active")  # active/completed/cancelled/archived
    created_at = Column(DateTime(timezone=True), default=utcnow)
    user = relationship("User", back_populates="goals")


class GoalContribution(Base):
    __tablename__ = "goal_contributions"
    id = Column(Integer, primary_key=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    direction = Column(String(8), default="in")  # in/out
    created_at = Column(DateTime(timezone=True), default=utcnow)


class UploadedDocument(Base):
    __tablename__ = "uploaded_documents"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(128))
    size_bytes = Column(Integer, default=0)
    storage_path = Column(String(512))
    extraction_status = Column(String(32), default="pending")
    created_at = Column(DateTime(timezone=True), default=utcnow)


class PlanExtraction(Base):
    __tablename__ = "plan_extractions"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("uploaded_documents.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=True)
    extracted_json = Column(JSON)
    confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class VectorDocument(Base):
    __tablename__ = "vector_documents"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(32))
    source_id = Column(Integer)
    vector_id = Column(String(128))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    ntype = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(64), nullable=False)
    entity = Column(String(64))
    entity_id = Column(Integer)
    details = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utcnow)
