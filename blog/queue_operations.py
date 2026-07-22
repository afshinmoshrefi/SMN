"""Safe, bounded operator actions for review holds and dead letters.

This module is deliberately client-injected: importing it never creates a Redis
connection.  It uses an atomic ``SET NX`` decision marker to make each source
record single-use, while preserving source lists and append-only audit history.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import uuid
from typing import Any, Callable

_DECIDE_LUA = """
if redis.call('SET', KEYS[1], ARGV[1], 'NX') then
  if ARGV[2] ~= '' then redis.call('RPUSH', KEYS[2], ARGV[2]) end
  redis.call('RPUSH', KEYS[3], ARGV[3])
  return 1
end
return 0
"""

_RECONCILE_LUA = """
-- atomic publication reconciliation transition
if redis.call('SET', KEYS[1], ARGV[1], 'NX') then
  redis.call('RPUSH', KEYS[2], ARGV[2])
  redis.call('RPUSH', KEYS[3], ARGV[3])
  if ARGV[4] ~= '' then redis.call('SET', KEYS[4], ARGV[4]) end
  return 1
end
return 0
"""


class OperatorLifecycleError(RuntimeError):
    pass


class NotFound(OperatorLifecycleError):
    pass


class Conflict(OperatorLifecycleError):
    pass


class HashMismatch(OperatorLifecycleError):
    pass


class InvalidTransition(OperatorLifecycleError):
    pass


class OperatorLifecycle:
    MAX_PAGE = 100
    MAX_SCAN = 1000
    TERMINAL_STATUSES = frozenset({
        "published", "succeeded", "completed", "rejected", "cancelled", "terminal"
    })

    def __init__(self, client: Any, *, prefix: str = "smn_news_queue",
                 clock: Callable[[], str] | None = None,
                 id_factory: Callable[[], str] | None = None,
                 transaction_inspector: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
                 publication_inspector: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        if client is None:
            raise ValueError("a Redis-compatible client must be explicitly supplied")
        self.client = client
        self.prefix = prefix
        self.clock = clock or self._utc_now
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.transaction_inspector = transaction_inspector
        self.publication_inspector = publication_inspector
        self.review_key = f"{prefix}_review_hold"
        self.dead_letter_key = f"{prefix}_dead_letter"
        self.ready_key = prefix
        self.publication_key = f"{prefix}_publication_commands"
        self.publication_dead_letter_key = f"{self.publication_key}_dead_letter"
        self.publication_terminal_key = f"{self.publication_key}_terminal"
        self.publication_idempotency_prefix = f"{self.publication_key}_result:"
        self.approved_key = self.publication_key
        self.audit_key = f"{prefix}_operator_audit"
        self.decision_prefix = f"{prefix}_operator_decision:"

    @staticmethod
    def _utc_now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("operator record must be a JSON object")
        return value

    @staticmethod
    def _require_actor(operator: str, reason: str) -> tuple[str, str]:
        operator = str(operator or "").strip()
        reason = str(reason or "").strip()
        if not operator or not reason:
            raise ValueError("explicit non-empty operator and reason are required")
        return operator, reason

    @classmethod
    def _limit(cls, limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= cls.MAX_PAGE:
            raise ValueError(f"limit must be between 1 and {cls.MAX_PAGE}")
        return limit

    def _records(self, key: str) -> list[dict[str, Any]]:
        records = []
        for raw in self.client.lrange(key, 0, self.MAX_SCAN - 1):
            try:
                records.append(self._decode(raw))
            except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                continue
        return records

    @staticmethod
    def _record_id(record: dict[str, Any]) -> str | None:
        return (record.get("job_id") or record.get("command_id") or
                record.get("_queue", {}).get("job_id"))

    def _show(self, key: str, job_id: str) -> dict[str, Any]:
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        for record in self._records(key):
            if self._record_id(record) == job_id:
                return copy.deepcopy(record)
        raise NotFound(f"record {job_id!r} was not found in bounded operator window")

    def _list(self, key: str, limit: int, *, hide_html: bool = False) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        output = []
        for record in self._records(key)[:limit]:
            item = copy.deepcopy(record)
            if hide_html:
                item.pop("reviewed_html", None)
            output.append(item)
        return output

    def list_review_holds(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._list(self.review_key, limit, hide_html=True)

    def show_review_hold(self, job_id: str) -> dict[str, Any]:
        return self._show(self.review_key, job_id)

    def list_dead_letters(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._list(self.dead_letter_key, limit)

    def show_dead_letter(self, job_id: str) -> dict[str, Any]:
        return self._show(self.dead_letter_key, job_id)

    def _transition(self, source_kind, source_id, action, operator, reason, source,
                    output_key, output, **links):
        decision = {"source_kind": source_kind, "source_id": source_id, "action": action,
                    "operator": operator, "reason": reason, "decided_at": self.clock()}
        event = json.loads(json.dumps({**decision, **links, "source_snapshot": source},
                                      ensure_ascii=False, sort_keys=True))
        key = f"{self.decision_prefix}{source_kind}:{source_id}"
        encoded = "" if output is None else json.dumps(output, ensure_ascii=False, sort_keys=True)
        try:
            transitioned = self.client.eval(
                _DECIDE_LUA, 3, key, output_key, self.audit_key,
                json.dumps(decision, sort_keys=True), encoded,
                json.dumps(event, ensure_ascii=False, sort_keys=True)
            )
        except Exception as exc:
            raise OperatorLifecycleError(
                "atomic Redis Lua transition unavailable; operator action refused"
            ) from exc
        if not transitioned:
            raise Conflict(f"{source_kind} {source_id!r} already has a decision")
        return event

    def _audit(self, decision: dict[str, Any], source: dict[str, Any], **links: Any) -> dict[str, Any]:
        # JSON round-tripping creates an immutable snapshot independent of caller/source mutation.
        event = json.loads(json.dumps({**decision, **links, "source_snapshot": source},
                                      ensure_ascii=False, sort_keys=True))
        self.client.rpush(self.audit_key, json.dumps(event, ensure_ascii=False, sort_keys=True))
        return event

    def approve(self, job_id: str, *, expected_sha256: str,
                operator: str, reason: str) -> dict[str, Any]:
        operator, reason = self._require_actor(operator, reason)
        source = self.show_review_hold(job_id)
        html = source.get("reviewed_html")
        stored_hash = source.get("reviewed_html_sha256")
        actual_hash = hashlib.sha256(str(html).encode("utf-8")).hexdigest() if isinstance(html, str) else None
        expected_sha256 = str(expected_sha256 or "").lower()
        if not expected_sha256 or expected_sha256 != stored_hash or expected_sha256 != actual_hash:
            raise HashMismatch("expected, stored, and recomputed reviewed HTML hashes must match")
        bundle = source.get("publication_bundle")
        if not isinstance(bundle, dict):
            raise HashMismatch("complete immutable publication bundle is required")
        unsigned_bundle = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        actual_bundle_hash = hashlib.sha256(json.dumps(
            unsigned_bundle, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        if (bundle.get("bundle_sha256") != actual_bundle_hash or
                source.get("publication_bundle_sha256") != actual_bundle_hash or
                bundle.get("reviewed_html") != html):
            raise HashMismatch("immutable publication bundle hash or HTML linkage failed")
        manifest = bundle.get("artifact_manifest")
        artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
        if not isinstance(artifacts, list) or manifest.get("version") != 1:
            raise HashMismatch("complete frozen artifact manifest is required")
        manifest_hash = hashlib.sha256(json.dumps(
            artifacts, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        if manifest.get("manifest_sha256") != manifest_hash:
            raise HashMismatch("frozen artifact manifest hash verification failed")
        for index, item in enumerate(artifacts):
            try:
                payload = base64.b64decode(item["content_base64"], validate=True)
            except Exception as exc:
                raise HashMismatch(f"frozen artifact payload {index} is invalid") from exc
            if len(payload) != item.get("size") or hashlib.sha256(payload).hexdigest() != item.get("sha256"):
                raise HashMismatch(f"frozen artifact payload {index} hash verification failed")
        immutable_receipts = source.get("immutable_receipts")
        if immutable_receipts != bundle.get("immutable_receipts") or not isinstance(immutable_receipts, dict):
            raise HashMismatch("complete matching immutable publication receipts are required")
        sources = immutable_receipts.get("publication_sources")
        receipts = immutable_receipts.get("publication_receipts")
        if not isinstance(sources, dict) or not isinstance(receipts, dict):
            raise HashMismatch("source and evidence receipts are required")
        source_payload = {key: sources.get(key) for key in ("version", "article_date", "sources")}
        source_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if (sources.get("canonical_json") != source_json or
                sources.get("contract_sha256") != hashlib.sha256(source_json.encode()).hexdigest() or
                sources.get("article_sha256") != actual_hash):
            raise HashMismatch("source contract or article linkage verification failed")

        def verify_receipt(value, hash_key):
            if not isinstance(value, dict):
                raise HashMismatch(f"missing immutable {hash_key} receipt")
            unsigned = {key: item for key, item in value.items() if key != hash_key}
            digest = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
            if value.get(hash_key) != digest:
                raise HashMismatch(f"immutable {hash_key} receipt verification failed")

        evidence = receipts.get("evidence")
        gates = receipts.get("gates")
        verify_receipt(evidence, "evidence_sha256")
        if receipts.get("version") != 1 or not isinstance(gates, dict):
            raise HashMismatch("immutable gate receipts are incomplete")
        for gate_name, issue_key in (("citations", "violations"), ("integrity", "errors")):
            gate = gates.get(gate_name)
            verify_receipt(gate, "receipt_sha256")
            # Receipt authenticity is verified above unconditionally (tamper
            # detection). Whether a *recorded finding* blocks approval is an
            # enforcement decision owned by gate_policy: gates are detection.
            if not gate.get("ok") or gate.get(issue_key):
                import config
                import gate_policy
                if gate_policy.gate_blocks_publication(gate, issue_key, config):
                    raise HashMismatch(f"immutable {gate_name} gate did not pass")
        artifact_receipt = immutable_receipts.get("artifact")
        verify_receipt(artifact_receipt, "receipt_sha256")
        linked = {"reviewed_html_sha256": actual_hash,
                  "source_contract_sha256": sources["contract_sha256"],
                  "evidence_sha256": evidence["evidence_sha256"]}
        if {key: artifact_receipt.get(key) for key in linked} != linked:
            raise HashMismatch("artifact receipt does not link the exact held inputs")
        decided_at = self.clock()
        immutable_receipts = copy.deepcopy(immutable_receipts)
        artifact = {
            "job_id": self.id_factory(), "attempt_of": job_id,
            "job": copy.deepcopy(source.get("job", {})),
            "reviewed_html": html, "reviewed_html_sha256": actual_hash,
            "approved_at": decided_at, "approved_by": operator,
            "publication_bundle": copy.deepcopy(bundle),
        }
        if isinstance(immutable_receipts, dict):
            artifact["immutable_receipts"] = immutable_receipts
        command = {"action": "publish_reviewed_artifact", "state": "ready_for_publication",
                   "command_id": artifact["job_id"], "artifact": artifact}
        old_clock, self.clock = self.clock, lambda: decided_at
        try:
            self._transition("review", job_id, "approve", operator, reason, source,
                             self.publication_key, command, attempt_id=artifact["job_id"],
                             artifact_sha256=actual_hash)
        finally:
            self.clock = old_clock
        return copy.deepcopy(artifact)

    def reject_review(self, job_id: str, *, operator: str, reason: str) -> dict[str, Any]:
        operator, reason = self._require_actor(operator, reason)
        source = self.show_review_hold(job_id)
        return self._transition("review", job_id, "reject", operator, reason, source,
                                self.audit_key, None)

    def replay_dead_letter(self, job_id: str, *, operator: str, reason: str) -> dict[str, Any]:
        operator, reason = self._require_actor(operator, reason)
        source = self.show_dead_letter(job_id)
        status = str(source.get("status") or source.get("terminal_status") or
                     source.get("_queue", {}).get("terminal_status") or "").lower()
        if status in self.TERMINAL_STATUSES:
            raise InvalidTransition(f"cannot replay terminal/published job with status {status!r}")
        decided_at = self.clock()
        old_attempt = source.get("attempt", source.get("_queue", {}).get("attempt", 0))
        try:
            attempt = int(old_attempt) + 1
        except (TypeError, ValueError):
            attempt = 1
        replay = {
            "payload": copy.deepcopy(source.get("job", source.get("payload", {}))),
            "_queue": {
                "job_id": self.id_factory(), "attempt_of": job_id, "attempt": attempt,
                "replayed_at": decided_at, "replayed_by": operator,
            },
        }
        old_clock, self.clock = self.clock, lambda: decided_at
        try:
            self._transition("dead_letter", job_id, "replay", operator, reason, source,
                             self.ready_key, replay, attempt_id=replay["_queue"]["job_id"])
        finally:
            self.clock = old_clock
        return copy.deepcopy(replay)

    def reconcile_publication(self, command_id: str, *, expected_command_id: str,
                              expected_command_sha256: str, operator: str,
                              reason: str) -> dict[str, Any]:
        """Resolve ambiguous publication only from conclusive canonical evidence."""
        operator, reason = self._require_actor(operator, reason)
        source = self._show(self.publication_dead_letter_key, command_id)
        if (source.get("state") != "ambiguous_reconciliation_required" or
                source.get("reconciliation_required") is not True):
            raise InvalidTransition("publication record is not awaiting reconciliation")
        command = source.get("publication_command")
        if not isinstance(command, dict):
            raise HashMismatch("dead letter lacks the immutable publication command")
        encoded = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual_hash = hashlib.sha256(encoded.encode()).hexdigest()
        if (str(expected_command_id or "").strip() != command_id or
                command.get("command_id") != command_id or
                str(expected_command_sha256 or "").lower() != actual_hash or
                source.get("publication_command_sha256") != actual_hash):
            raise HashMismatch("expected, stored, and recomputed command identity must match")
        if not callable(self.transaction_inspector) or not callable(self.publication_inspector):
            raise InvalidTransition("canonical transaction and publication inspectors are required")
        transaction = self.transaction_inspector(copy.deepcopy(command))
        publication = self.publication_inspector(copy.deepcopy(command))
        if not isinstance(transaction, dict) or not isinstance(publication, dict):
            raise InvalidTransition("canonical inspector state is indeterminate")

        def proven(value, state):
            return (value.get("state") == state and value.get("command_id") == command_id and
                    value.get("command_sha256") == actual_hash and bool(value.get("proof_id")))

        decided_at = self.clock()
        if proven(transaction, "committed") and proven(publication, "published"):
            action, destination = "confirm_publication", self.publication_terminal_key
            output = {"command_id": command_id, "command_sha256": actual_hash,
                      "status": "published", "completed_at": decided_at, "reconciled": True,
                      "transaction_proof": transaction, "publication_proof": publication}
            marker = json.dumps(output, ensure_ascii=False, sort_keys=True)
        elif transaction.get("state") == "absent" and publication.get("state") == "absent":
            action, destination, output, marker = (
                "requeue_publication", self.publication_key, copy.deepcopy(command), "")
        else:
            raise InvalidTransition("canonical state is indeterminate or contradictory; held")
        event = {"source_kind": "publication_dead_letter", "source_id": command_id,
                 "action": action, "operator": operator, "reason": reason,
                 "decided_at": decided_at, "command_sha256": actual_hash,
                 "transaction_state": transaction, "publication_state": publication,
                 "source_snapshot": source}
        decision_key = f"{self.decision_prefix}publication_dead_letter:{command_id}"
        try:
            won = self.client.eval(
                _RECONCILE_LUA, 4, decision_key, destination, self.audit_key,
                self.publication_idempotency_prefix + command_id,
                json.dumps({"action": action, "operator": operator, "reason": reason,
                            "decided_at": decided_at}, sort_keys=True),
                json.dumps(output, ensure_ascii=False, sort_keys=True),
                json.dumps(event, ensure_ascii=False, sort_keys=True), marker)
        except Exception as exc:
            raise OperatorLifecycleError("atomic publication reconciliation unavailable") from exc
        if not won:
            raise Conflict(f"publication {command_id!r} already has a reconciliation decision")
        return copy.deepcopy(event)
