#!/usr/bin/env python3
"""Corpus generation and seeder script for DocuQuery.

Generates realistic enterprise PDFs in sample_data and ingests them into the
local database for instant demo readiness.
"""

import logging
from pathlib import Path
import shutil
import sys
import uuid as uuidlib

import pymupdf as fitz
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

backend_dir = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.config import get_settings
from app.db.models import Document, DocumentStatus
from app.workers.tasks import process_document_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_corpus")

SAMPLE_DOCS_DIR = backend_dir / "sample_data"
SAMPLE_DOCS_DIR.mkdir(parents=True, exist_ok=True)


def create_employee_handbook(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4

    text = """ACME ENTERPRISES - EMPLOYEE HANDBOOK (2026 EDITION)

Section 1: Working Hours and Remote Work Policy
Standard business operations require full-time employees to align during core collaboration
hours between 10:00 AM and 3:00 PM in their respective local time zones. Remote and hybrid
arrangements are permitted for eligible roles with departmental lead approval.

Section 2: Paid Time Off (PTO) and Leave Policy
Full-time employees accrue 20 days of paid time off per calendar year for the first 3 years of service.
Beginning year 4, annual accrual increases to 25 days. Up to 5 unused PTO days may roll over
into the subsequent calendar year, expiring on March 31st if unspent.
Primary caregivers receive 16 weeks of fully paid parental leave; secondary caregivers receive 8 weeks.

Section 3: Retirement and Benefits
Acme offers a 401(k) retirement savings program with an immediate 100% company match on the first
4% of eligible employee salary contributions. Vesting is immediate upon initial enrollment.
An annual home office and wellness stipend of up to $1,200 is available for approved equipment and memberships.

Section 4: Information Security and Incident Reporting
All company devices must enforce full-disk encryption (BitLocker or FileVault) and multi-factor
authentication (MFA). Any suspected data breach, lost equipment, or phishing attempt must be
reported immediately to security@company.internal or escalated to the on-call security officer within 1 hour.
"""
    page.insert_textbox(fitz.Rect(40, 60, 555, 800), text, fontsize=10.5, fontname="helv")
    path.write_bytes(doc.tobytes())
    logger.info("Generated %s", path.name)


def create_it_security_policy(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    text = """GLOBAL CLOUD SERVICES - IT SECURITY & COMPLIANCE STANDARD (SOC2 / ISO 27001)

1. Access Control and Least Privilege Principle
Access to production database infrastructure and pgvector clusters requires hardware security keys (FIDO2)
and just-in-time (JIT) role elevation via Okta. All credentials must be rotated at minimum every 90 days.
Root-level access to cloud containers is strictly prohibited in customer-facing production VPCs.

2. Encryption Standards
Data at rest must be encrypted using AES-256 with AWS KMS or HashiCorp Vault managed keys.
All data in transit must utilize TLS 1.3 with cipher suites enforcing forward secrecy.
Database backups are automatically encrypted and retained for 30 rolling calendar days.

3. Vulnerability Management and Penetration Testing
Static application security testing (SAST) and container vulnerability scanning run on every Git commit.
Third-party penetration tests are commissioned semi-annually. Critical vulnerabilities (CVSS > 9.0)
must be remediated or mitigated within 48 hours of public disclosure.
"""
    page.insert_textbox(fitz.Rect(40, 60, 555, 800), text, fontsize=10.5, fontname="helv")
    path.write_bytes(doc.tobytes())
    logger.info("Generated %s", path.name)


def create_financial_report(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)

    text = """Q3 2026 FINANCIAL AND OPERATIONAL REVIEW

Executive Summary:
Total revenue for the third quarter reached $48.2 million, reflecting a 22% year-over-year increase.
Subscription software revenue accounted for 78% of aggregate receipts ($37.6 million).
Adjusted EBITDA reached $12.4 million with an operational margin of 25.7%.

Expense Allocation:
- Research & Development (R&D): $16.8 million (AI agent inference pipelines and retrieval infrastructure)
- Sales and Marketing: $11.2 million
- General and Administrative: $5.8 million
- Capital Expenditures: $2.0 million dedicated to on-premise GPU clusters for local LLM fine-tuning.

Cash Position and Runway:
Cash and cash equivalents ended the quarter at $84.5 million with zero long-term debt obligations,
yielding approximately 28 months of operational runway under conservative baseline projections.
"""
    page.insert_textbox(fitz.Rect(40, 60, 555, 800), text, fontsize=10.5, fontname="helv")
    path.write_bytes(doc.tobytes())
    logger.info("Generated %s", path.name)


def generate_all_sample_files() -> list[Path]:
    handbook_path = SAMPLE_DOCS_DIR / "employee_handbook_2026.pdf"
    if not handbook_path.exists():
        create_employee_handbook(handbook_path)

    security_path = SAMPLE_DOCS_DIR / "it_security_compliance.pdf"
    if not security_path.exists():
        create_it_security_policy(security_path)

    finance_path = SAMPLE_DOCS_DIR / "quarterly_financial_report.pdf"
    if not finance_path.exists():
        create_financial_report(finance_path)

    # List all available sample files
    sample_files = list(SAMPLE_DOCS_DIR.glob("*.pdf"))
    logger.info("Found %d sample PDF documents in %s", len(sample_files), SAMPLE_DOCS_DIR)
    return sample_files


def seed_database(sample_files: list[Path]) -> None:
    settings = get_settings()
    engine = create_engine(settings.sync_database_url)
    session_factory = sessionmaker(engine, expire_on_commit=False)

    with session_factory() as db:
        for file_path in sample_files:
            # Check if document already seeded by original name
            existing = db.execute(
                select(Document).where(Document.original_name == file_path.name)
            ).scalar_one_or_none()

            if existing and existing.status == DocumentStatus.ready:
                logger.info("Document '%s' already indexed (status: %s); skipping", file_path.name, existing.status)
                continue

            doc_id = existing.id if existing else uuidlib.uuid4()
            stored_path = settings.upload_dir / f"{doc_id}.pdf"
            shutil.copy2(file_path, stored_path)

            if not existing:
                doc = Document(
                    id=doc_id,
                    original_name=file_path.name,
                    stored_path=str(stored_path),
                    size_bytes=file_path.stat().st_size,
                    status=DocumentStatus.processing,
                )
                db.add(doc)
                db.commit()
            else:
                existing.status = DocumentStatus.processing
                existing.stored_path = str(stored_path)
                db.commit()

            logger.info("Processing document '%s' (%s)...", file_path.name, doc_id)
            try:
                process_document_core(str(doc_id))
                logger.info("Successfully ingested '%s'", file_path.name)
            except Exception as exc:
                logger.exception("Failed to ingest '%s': %s", file_path.name, exc)


if __name__ == "__main__":
    files = generate_all_sample_files()
    try:
        seed_database(files)
        logger.info("Corpus seeding completed successfully.")
    except Exception as exc:
        logger.warning("Could not connect to database during CLI seed run: %s", exc)
        logger.info("Sample PDFs have been generated in %s and are ready to be uploaded via UI.", SAMPLE_DOCS_DIR)
