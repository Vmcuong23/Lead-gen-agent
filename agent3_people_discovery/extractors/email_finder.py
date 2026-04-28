"""
Email pattern inference + verification.

Two-step process for any (full_name, company) pair:

1. PATTERN INFERENCE
   Find 2-3 known emails at the company's domain (from About pages,
   press releases, contact pages, MX records). Infer the most likely
   pattern: {first}.{last}@, {f}{last}@, {first}{l}@, etc.

2. VERIFICATION
   - MX lookup: does the domain have mail servers?
   - SMTP handshake: connect to MX, MAIL FROM + RCPT TO without DATA.
     If RCPT TO is accepted, the address probably exists.
     If the server accepts ALL addresses (catch-all), mark as such.

Caveats:
  - SMTP probing from cloud IPs gets blocked. Use a small VPS with
    clean reputation, throttled to <1 req/sec per domain.
  - Many corporate domains use Microsoft 365 or Google Workspace which
    silently accept all RCPT TO and only reject at DATA — those are
    catch-all and we mark the result accordingly.
  - This module ONLY produces candidates. It never sends mail.
"""
from __future__ import annotations

import logging
import re
import smtplib
import socket
import unicodedata
from dataclasses import dataclass
from typing import Optional

import dns.resolver

logger = logging.getLogger(__name__)

PATTERNS = [
    "{first}.{last}",      # tran.van.a
    "{first}{last}",       # tranvana
    "{f}{last}",           # tvana
    "{first}{l}",          # tranvana -> tranva
    "{first}_{last}",      # tran_va
    "{last}.{first}",      # va.tran
    "{first}",             # tran
    "{f}.{last}",          # t.va
]


def _norm(s: str) -> str:
    """Strip diacritics, lowercase, alpha-only."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z]", "", s.lower())


def candidate_locals(full_name: str) -> list[str]:
    """Generate local-parts (the 'first.last' bit) for a name."""
    parts = [_norm(p) for p in full_name.split() if _norm(p)]
    if len(parts) < 2:
        return []
    # Vietnamese names are usually [family][middle][given]; given name is last
    # Western names are usually [given][family]; family is last
    # We try both interpretations.
    interpretations = [
        {"first": parts[0], "last": parts[-1], "f": parts[0][0], "l": parts[-1][0]},
        {"first": parts[-1], "last": parts[0], "f": parts[-1][0], "l": parts[0][0]},
    ]
    seen: set[str] = set()
    out: list[str] = []
    for fields in interpretations:
        for pat in PATTERNS:
            try:
                local = pat.format(**fields)
            except (KeyError, IndexError):
                continue
            if local and local not in seen:
                seen.add(local)
                out.append(local)
    return out


@dataclass
class VerificationResult:
    email: str
    status: str   # 'mx_valid'|'smtp_verified'|'invalid'|'catch_all'|'mx_invalid'
    confidence: int
    notes: str = ""


class EmailVerifier:
    def __init__(self, sender_addr: str = "verify@example.com", timeout: int = 8):
        self.sender = sender_addr
        self.timeout = timeout

    def verify(self, email: str) -> VerificationResult:
        domain = email.split("@", 1)[1]

        # 1. MX lookup
        try:
            mx_records = sorted(
                dns.resolver.resolve(domain, "MX"),
                key=lambda r: r.preference,
            )
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            return VerificationResult(email, "mx_invalid", 5, "No MX records")
        except Exception as e:
            return VerificationResult(email, "mx_invalid", 5, f"MX error: {e}")
        if not mx_records:
            return VerificationResult(email, "mx_invalid", 5, "No MX records")

        mx_host = str(mx_records[0].exchange).rstrip(".")

        # 2. Detect catch-all by testing a known-fake address
        catch_all = self._is_catch_all(domain, mx_host)

        # 3. SMTP probe the real address
        rcpt_ok = self._smtp_probe(mx_host, email)
        if rcpt_ok and catch_all:
            return VerificationResult(
                email, "catch_all", 50, "Domain accepts everything — cannot confirm"
            )
        if rcpt_ok:
            return VerificationResult(email, "smtp_verified", 90)
        return VerificationResult(email, "invalid", 10, "RCPT rejected")

    def _is_catch_all(self, domain: str, mx_host: str) -> bool:
        random_local = "x" + str(abs(hash(domain)))[-10:] + "nonsense"
        return self._smtp_probe(mx_host, f"{random_local}@{domain}")

    def _smtp_probe(self, mx_host: str, recipient: str) -> bool:
        try:
            with smtplib.SMTP(mx_host, 25, timeout=self.timeout) as smtp:
                smtp.helo("verify.local")
                smtp.mail(self.sender)
                code, _ = smtp.rcpt(recipient)
                return 200 <= code < 300
        except (smtplib.SMTPException, socket.error, OSError) as e:
            logger.debug("SMTP probe failed for %s: %s", recipient, e)
            return False


def best_email(
    full_name: str,
    domain: str,
    verifier: EmailVerifier,
    known_pattern: Optional[str] = None,
) -> Optional[VerificationResult]:
    """
    Try patterns in order; return the first SMTP-verified address, else
    the first MX-valid pattern-inferred address, else None.
    """
    if known_pattern:
        # If we already know the company's pattern, just use that.
        local = known_pattern.format(
            **_pattern_fields(full_name)
        )
        if local:
            return verifier.verify(f"{local}@{domain}")

    fallback: Optional[VerificationResult] = None
    for local in candidate_locals(full_name):
        result = verifier.verify(f"{local}@{domain}")
        if result.status == "smtp_verified":
            return result
        if result.status == "catch_all" and fallback is None:
            fallback = result
    return fallback


def _pattern_fields(full_name: str) -> dict:
    parts = [_norm(p) for p in full_name.split() if _norm(p)]
    if len(parts) < 2:
        return {}
    return {"first": parts[0], "last": parts[-1], "f": parts[0][0], "l": parts[-1][0]}
