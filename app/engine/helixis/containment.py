"""Containment layer: OpenShell CLI wrapper, OCSF denial tail, proposal client.

The boundary, not the agent's judgement, is what makes Helixis safe to hand real
credentials (Requirement 4). This module is the host-side half of that boundary:
it applies policy, ingests the gateway's denial log into the episode store so the
dashboard has a live policy feed, and drives the propose -> prove -> approve
escalation flow.

Everything here degrades instead of failing. OpenShell is alpha and is not
installed on a typical laptop, so every CLI call returns a structured result with
`available=False` rather than raising — the epoch runner must still work offline,
just without containment evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .store import EpisodeStore

HONEYPOT_HOSTS = ("exfil.helixis-demo.net",)
PROPOSAL_BASE_URL = "http://policy.local/v1"

# Fields the gateway's proposal API accepts inside an endpoint. Its JSON mirror
# of the YAML schema is deliberately narrower than the YAML itself, so unknown
# keys are dropped here rather than left for the gateway to reject.
ENDPOINT_FIELDS = frozenset(
    {
        "host",
        "port",
        "ports",
        "protocol",
        "tls",
        "enforcement",
        "access",
        "rules",
        "allowed_ips",
        "deny_rules",
        "allow_encoded_slash",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------- CLI


@dataclass
class ShellResult:
    """Outcome of one `openshell` invocation.

    `available` is false when the binary is missing; callers branch on it to
    decide whether missing containment evidence is a bug or just a laptop
    without OpenShell installed.
    """

    ok: bool
    available: bool = True
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    data: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "available": self.available,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "data": self.data,
            "error": self.error,
        }


class OpenShellClient:
    """Thin subprocess wrapper over the `openshell` CLI."""

    def __init__(
        self,
        *,
        binary: str = "openshell",
        sandbox: str = "helixis",
        timeout_s: float = 120.0,
    ):
        self.binary = binary
        self.sandbox = sandbox
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return self._run(["--version"], timeout_s=10.0).available

    def policy_set(
        self, path: Path | str, *, wait: bool = True, yes: bool = True
    ) -> ShellResult:
        """Replace the sandbox policy wholesale.

        `--wait` blocks until the gateway has admitted the YAML, which is the
        only cheap way to catch a deny_unknown_fields parse failure before an
        epoch starts running against a policy that never applied.
        """
        args = ["policy", "set", self.sandbox, "--policy", str(path)]
        if yes:
            args.append("--yes")
        if wait:
            args.append("--wait")
        return self._run(args)

    def sandbox_create(
        self,
        *,
        name: str | None = None,
        command: Iterable[str] = (),
        policy: Path | str | None = None,
        env: dict[str, str] | None = None,
        approval_mode: str = "manual",
        extra_args: Iterable[str] = (),
    ) -> ShellResult:
        """Stand up the sandbox. `approval_mode=manual` is load-bearing: it is
        what forces the escalation flow through human approval (Req 4.5)."""
        args = ["sandbox", "create", "--name", name or self.sandbox]
        if policy:
            args += ["--policy", str(policy)]
        args += ["--approval-mode", approval_mode]
        for key, value in (env or {}).items():
            args += ["--env", f"{key}={value}"]
        args += list(extra_args)
        command = list(command)
        if command:
            args += ["--", *command]
        return self._run(args)

    def rule_list_pending(self, sandbox: str | None = None) -> ShellResult:
        """Pending policy drafts. The CLI spells this `rule get --status pending`
        and has no JSON output mode, so the table is parsed into `data` on a
        best-effort basis and the raw stdout is always kept."""
        result = self._run(["rule", "get", sandbox or self.sandbox, "--status", "pending"])
        result.data = _parse_pending_table(result.stdout) if result.ok else []
        return result

    def rule_approve(self, sandbox: str, chunk_id: str) -> ShellResult:
        return self._run(["rule", "approve", sandbox, "--chunk-id", chunk_id])

    def rule_reject(self, sandbox: str, chunk_id: str, reason: str) -> ShellResult:
        return self._run(
            ["rule", "reject", sandbox, "--chunk-id", chunk_id, "--reason", reason]
        )

    def _run(
        self,
        args: list[str],
        *,
        timeout_s: float | None = None,
    ) -> ShellResult:
        try:
            proc = subprocess.run(
                [self.binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout_s or self.timeout_s,
            )
        except FileNotFoundError:
            return ShellResult(ok=False, available=False, error=f"{self.binary} not found on PATH")
        except subprocess.TimeoutExpired:
            return ShellResult(ok=False, error=f"timed out after {timeout_s or self.timeout_s}s")
        except OSError as exc:  # permission denied, bad exec format, ...
            return ShellResult(ok=False, available=False, error=f"{type(exc).__name__}: {exc}")

        return ShellResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            error="" if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()[:2000],
        )


_CHUNK_ID_RE = re.compile(r"\b([0-9a-f]{8,}(?:-[0-9a-f]{4,}){0,4})\b", re.IGNORECASE)


def _parse_pending_table(stdout: str) -> list[dict[str, Any]]:
    """Best-effort scrape of `rule get --status pending` table output.

    Deliberately loose: the CLI has no stable machine format, so a layout change
    should cost the dashboard a column, not the whole feed. Callers that need
    certainty read `ShellResult.stdout`.
    """
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or set(stripped) <= set("-+|= "):
            continue
        cells = [c.strip() for c in re.split(r"\s*\|\s*|\s{2,}", stripped) if c.strip()]
        if len(cells) < 2 or cells[0].lower() in ("chunk id", "chunk_id", "id"):
            continue
        match = _CHUNK_ID_RE.search(cells[0]) or _CHUNK_ID_RE.search(stripped)
        if not match:
            continue
        rows.append({"chunk_id": match.group(1), "cells": cells, "line": stripped})
    return rows


# -------------------------------------------------------------- OCSF tailer

# Shorthand denial, as emitted by openshell-ocsf `format_shorthand`:
#   OCSF NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443
#       [policy:- engine:opa] [reason:no matching policy]
# `openshell logs` prefixes the same payload with epoch/stream brackets.
_ACTOR_RE = re.compile(r"(?:DENIED|ALLOWED)\s+(?:\w+\s+)?(\S+?)(?:\((\d+)\))?\s+(?:->|→)")
_DST_RE = re.compile(r"(?:->|→)\s+(\[[0-9a-fA-F:]+\]|[^\s:]+)(?::(\d+))?")
_REASON_RE = re.compile(r"\[(?:[^\]]*\s)?reason:([^\]]*)\]")
_KIND_RE = re.compile(r"\bOCSF\s+(\S+)|\b((?:NET|HTTP|FILE|PROC):\S+)")
_SEVERITY_RE = re.compile(r"\[(INFO|LOW|MED|HIGH|CRIT|FATAL)\]")
_LEADING_TIME_RE = re.compile(r"(\d{2}:\d{2}:\d{2}(?:\.\d+)?)")
# L7 denials carry a URL instead of the `actor(pid) -> host:port` triple.
_URL_RE = re.compile(r"\bhttps?://([^/\s:]+)(?::(\d+))?")
_FILE_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class TailStats:
    files: int = 0
    lines: int = 0
    denials: int = 0
    recorded: int = 0
    honeypot: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "lines": self.lines,
            "denials": self.denials,
            "recorded": self.recorded,
            "honeypot": self.honeypot,
            "errors": self.errors,
        }


class OCSFTailer:
    """Streams gateway denial events from disk into `policy_events`.

    Two formats coexist in the log dir: the one-line shorthand
    (`openshell.<date>.log`) and OCSF JSONL (`openshell-ocsf.<date>.log`).
    Both are handled because which one a given OpenShell build emits is a
    runtime setting (`ocsf_json_enabled`) we do not control.

    Byte offsets are kept per file so repeated `poll()` calls are cheap, and
    every event carries a content fingerprint so a full re-tail — after a
    restart, a rotation, or the same denial landing in both logs — is idempotent
    against the store's UNIQUE constraint.
    """

    LOG_GLOBS = ("openshell-*.log", "openshell.*.log", "openshell-ocsf.*.log")

    def __init__(
        self,
        store: EpisodeStore,
        log_dir: Path,
        *,
        honeypot_hosts: Iterable[str] = HONEYPOT_HOSTS,
    ):
        self.store = store
        self.log_dir = Path(log_dir)
        self.honeypot_hosts = {h.lower() for h in honeypot_hosts}
        self._offsets: dict[str, int] = {}

    def poll(self) -> TailStats:
        stats = TailStats()
        if not self.log_dir.is_dir():
            return stats
        for path in self._log_files():
            stats.files += 1
            jsonl = "ocsf" in path.name
            for line in self._new_lines(path, stats):
                stats.lines += 1
                event = (
                    self._parse_jsonl(line, path) if jsonl else self._parse_shorthand(line, path)
                )
                if event is None:
                    continue
                stats.denials += 1
                if event["is_honeypot"]:
                    stats.honeypot += 1
                if self.store.record_policy_event(event):
                    stats.recorded += 1
        return stats

    def _log_files(self) -> list[Path]:
        seen: dict[str, Path] = {}
        for pattern in self.LOG_GLOBS:
            for path in self.log_dir.glob(pattern):
                seen[path.name] = path
        return [seen[name] for name in sorted(seen)]

    def _new_lines(self, path: Path, stats: TailStats) -> list[str]:
        key = str(path)
        offset = self._offsets.get(key, 0)
        try:
            if path.stat().st_size < offset:  # rotated/truncated: restart, dedup covers it
                offset = 0
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                text = fh.read()
        except OSError as exc:
            stats.errors.append(f"{path.name}: {exc}")
            return []
        # A trailing partial line must not be parsed; leave the offset before it
        # so the next poll sees the completed line.
        cut = text.rfind("\n")
        if cut < 0:
            self._offsets[key] = offset
            return []
        self._offsets[key] = offset + len(text[: cut + 1].encode("utf-8"))
        return [ln for ln in text[:cut].splitlines() if ln.strip()]

    def _parse_shorthand(self, line: str, path: Path) -> dict[str, Any] | None:
        # The agreed denial predicate. Padding lets a line that begins at column
        # zero with "OCSF " still match the space-delimited form.
        padded = f" {line} "
        if " OCSF " not in padded or " DENIED " not in padded:
            return None
        actor, host, port = "", "", None
        if m := _ACTOR_RE.search(line):
            actor = m.group(1)
        if m := _DST_RE.search(line):
            host = m.group(1).strip("[]")
            port = int(m.group(2)) if m.group(2) else None
        elif m := _URL_RE.search(line):
            # HTTP:<METHOD> lines name the destination as a URL, and the scheme
            # here is the gateway's internal view — the real port is the TLS one.
            host = m.group(1)
            port = int(m.group(2)) if m.group(2) else (443 if line.find("https://") >= 0 else None)
        kind = "NET:OPEN"
        if m := _KIND_RE.search(line):
            kind = m.group(1) or m.group(2) or kind
        return self._event(
            ts=self._shorthand_ts(line, path),
            kind=kind,
            severity=_match(_SEVERITY_RE, line) or "MED",
            actor=actor,
            host=host,
            port=port,
            reason=_match(_REASON_RE, line),
            raw={"line": line, "source": path.name, "format": "shorthand"},
        )

    def _shorthand_ts(self, line: str, path: Path) -> str:
        """Shorthand carries only a wall clock; the date lives in the filename.

        Recombining them keeps fingerprints distinct across days — otherwise two
        identical denials 24h apart would collapse into one dashboard row.
        """
        clock = _match(_LEADING_TIME_RE, line[:32])
        date = _match(_FILE_DATE_RE, path.name)
        if clock and date:
            return f"{date}T{clock}"
        return clock or _now()

    def _parse_jsonl(self, line: str, path: Path) -> dict[str, Any] | None:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(rec, dict):
            return None
        verdict = f"{rec.get('action', '')} {rec.get('disposition', '')}".upper()
        if not any(tag in verdict for tag in ("DENIED", "DENY", "BLOCK")):
            return None
        dst = rec.get("dst_endpoint") or {}
        dst = dst if isinstance(dst, dict) else {}
        proc = ((rec.get("actor") or {}).get("process") or {}) if isinstance(rec.get("actor"), dict) else {}
        port = dst.get("port")
        return self._event(
            ts=_iso_time(rec.get("time")),
            kind=str(rec.get("class_name") or "NET:OPEN"),
            severity=str(rec.get("severity") or "MED").upper(),
            actor=str((proc.get("file") or {}).get("path") or proc.get("name") or ""),
            host=str(dst.get("domain") or dst.get("hostname") or dst.get("ip") or ""),
            port=int(port) if str(port).isdigit() else None,
            reason=str(rec.get("status_detail") or rec.get("message") or ""),
            raw={"record": rec, "source": path.name, "format": "ocsf-jsonl"},
        )

    def _event(
        self,
        *,
        ts: str,
        kind: str,
        severity: str,
        actor: str,
        host: str,
        port: int | None,
        reason: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        is_honeypot = host.lower() in self.honeypot_hosts
        return {
            "ts": ts,
            "kind": kind,
            "severity": "HIGH" if is_honeypot else severity.upper(),
            "action": "DENIED",
            "actor": actor,
            "dst_host": host,
            "dst_port": port,
            "reason": reason,
            "is_honeypot": is_honeypot,
            "raw": raw,
            "fingerprint": fingerprint(ts, actor, f"{host}:{port or ''}", reason),
        }


def fingerprint(ts: str, actor: str, dst: str, reason: str) -> str:
    """Hash of the identifying fields, not of the whole line.

    The same denial can reach us twice in different formats (shorthand and
    JSONL) or on a re-tail after restart; hashing only the semantic fields makes
    those collapse to one row.
    """
    payload = "|".join(part.strip() for part in (ts, actor, dst, reason))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _iso_time(value: Any) -> str:
    """OCSF `time` is epoch milliseconds; tolerate seconds and strings too."""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value) if value else _now()


def _match(pattern: re.Pattern[str], line: str) -> str:
    m = pattern.search(line)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------- proposals


@dataclass
class Proposal:
    """One proposed policy chunk as the gateway reports it.

    `requires_human` is derived, not returned: the gateway exposes prover output
    as `validation_result` and leaves flagged chunks pending. Anything the prover
    had something to say about needs a person (Req 4.5).
    """

    chunk_id: str
    rule_name: str = ""
    intent_summary: str = ""
    status: str = "pending"
    prover_findings: list[Any] = field(default_factory=list)
    requires_human: bool = True
    rejection_reason: str | None = None
    created_at: str = field(default_factory=_now)
    decided_at: str | None = None
    timed_out: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in ("approved", "rejected")

    @classmethod
    def from_state(
        cls, body: dict[str, Any], *, rule_name: str = "", intent_summary: str = ""
    ) -> Proposal:
        findings = _findings(body.get("validation_result"))
        status = str(body.get("status") or "pending").lower()
        return cls(
            chunk_id=str(body.get("chunk_id") or ""),
            rule_name=str(body.get("rule_name") or rule_name),
            intent_summary=intent_summary,
            status=status,
            prover_findings=findings,
            requires_human=bool(findings) or status == "pending",
            rejection_reason=body.get("rejection_reason"),
            decided_at=_now() if status in ("approved", "rejected") else None,
            timed_out=bool(body.get("timed_out")),
        )

    def to_store(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "rule_name": self.rule_name,
            "intent_summary": self.intent_summary,
            "status": self.status,
            "prover_findings": self.prover_findings,
            "requires_human": self.requires_human,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


def _findings(validation_result: Any) -> list[Any]:
    """Prover output shape varies by build; normalize to a flat list."""
    if not validation_result:
        return []
    if isinstance(validation_result, list):
        return validation_result
    if isinstance(validation_result, dict):
        for key in ("findings", "flags", "categories", "violations"):
            value = validation_result.get(key)
            if isinstance(value, list):
                return value
        return [validation_result]
    return [validation_result]


def build_add_rule(
    *,
    rule_name: str,
    endpoints: list[dict[str, Any]],
    binaries: list[str] | list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble one `addRule` operation — the only operation kind we emit.

    Endpoint keys outside the accepted set are dropped so a model-authored
    proposal cannot be rejected wholesale over one stray field.
    """
    clean_endpoints = [{k: v for k, v in ep.items() if k in ENDPOINT_FIELDS} for ep in endpoints]
    clean_binaries = [b if isinstance(b, dict) else {"path": b} for b in binaries]
    return {
        "addRule": {
            "ruleName": rule_name,
            "rule": {
                "name": rule_name,
                "endpoints": clean_endpoints,
                "binaries": clean_binaries,
            },
        }
    }


class ProposalClient:
    """Files policy proposals from inside the sandbox and mirrors their fate.

    `policy.local` resolves only within the sandbox network namespace, so on the
    host every method returns a structured failure instead of raising, matching
    OpenShellClient's contract.
    """

    def __init__(
        self,
        store: EpisodeStore,
        *,
        base_url: str = PROPOSAL_BASE_URL,
        timeout_s: float = 30.0,
        wait_timeout_s: float = 300.0,
    ):
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.wait_timeout_s = wait_timeout_s

    async def propose(
        self,
        *,
        intent_summary: str,
        rule_name: str,
        endpoints: list[dict[str, Any]],
        binaries: list[str] | list[dict[str, Any]],
    ) -> tuple[list[Proposal], str]:
        """Submit one addRule proposal. The gateway splits it into chunks and
        returns their ids, so this yields a list even for a single rule."""
        payload = {
            "intent_summary": intent_summary,
            "operations": [
                build_add_rule(rule_name=rule_name, endpoints=endpoints, binaries=binaries)
            ],
        }
        body, error = await self._request("POST", "/proposals", json=payload)
        if body is None:
            return [], error
        chunk_ids = [str(c) for c in (body.get("accepted_chunk_ids") or [])]
        if not chunk_ids:
            reasons = body.get("rejection_reasons") or []
            return [], f"no chunks accepted: {reasons}"
        proposals = [
            Proposal(chunk_id=cid, rule_name=rule_name, intent_summary=intent_summary)
            for cid in chunk_ids
        ]
        for proposal in proposals:
            self.store.upsert_proposal(proposal.to_store())
        return proposals, ""

    async def wait(
        self,
        chunk_id: str,
        *,
        timeout_s: float | None = None,
        rule_name: str = "",
        intent_summary: str = "",
    ) -> tuple[Proposal | None, str]:
        """Long-poll one chunk's decision.

        Approval mode is manual, so this legitimately blocks for as long as a
        human takes; a `timed_out` response is a non-answer, not a rejection.
        """
        timeout = timeout_s or self.wait_timeout_s
        body, error = await self._request(
            "GET",
            f"/proposals/{chunk_id}/wait",
            params={"timeout": int(timeout)},
            # Outlast the server-side long poll so the client is not the one
            # that gives up first.
            timeout_s=timeout + 15,
        )
        if body is None:
            return None, error
        proposal = Proposal.from_state(body, rule_name=rule_name, intent_summary=intent_summary)
        if not proposal.chunk_id:
            proposal.chunk_id = chunk_id
        self.store.upsert_proposal(proposal.to_store())
        return proposal, ""

    async def status(self, chunk_id: str) -> tuple[Proposal | None, str]:
        body, error = await self._request("GET", f"/proposals/{chunk_id}")
        if body is None:
            return None, error
        proposal = Proposal.from_state(body)
        if not proposal.chunk_id:
            proposal.chunk_id = chunk_id
        self.store.upsert_proposal(proposal.to_store())
        return proposal, ""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout_s or self.timeout_s) as client:
                resp = await client.request(method, url, json=json, params=params)
        except httpx.HTTPError as exc:
            # Outside the sandbox this is the normal case, not an incident.
            return None, f"{type(exc).__name__}: {exc}"
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}: {resp.text[:500]}"
        try:
            body = resp.json()
        except ValueError:
            return None, "gateway returned a non-JSON body"
        return (body, "") if isinstance(body, dict) else (None, "unexpected response shape")
