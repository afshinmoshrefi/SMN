"""Bounded, recoverable filesystem publication transactions.

Local artifacts are staged before a canonical publication lock is acquired.  A
manifest and byte-for-byte snapshots make a failed in-process commit reversible.
External effects are represented only as durable outbox records until every local
artifact has committed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]


class TransactionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransactionResult:
    transaction_id: str
    manifest_path: str
    status: str


class PublicationTransaction:
    SCHEMA_VERSION = 1
    SUPPORTED_PLATFORM = os.name == "posix" and fcntl is not None

    def __init__(self, publication_root: Path | str, canonical_id: str, *,
                 fault_injector: Callable[[str], None] | None = None,
                 lock_timeout: float = 10.0):
        self.root = Path(publication_root).resolve()
        if not self.SUPPORTED_PLATFORM:
            raise NotImplementedError(
                "durable publication transactions require POSIX flock and directory fsync"
            )
        self.canonical_id = str(canonical_id).strip()
        if not self.canonical_id:
            raise ValueError("canonical publication id is required")
        self.transaction_id = uuid.uuid4().hex
        self.control_root = self.root / ".publication-transactions"
        self.tx_dir = self.control_root / self.transaction_id
        self.stage_dir = self.tx_dir / "stage"
        self.snapshot_dir = self.tx_dir / "snapshots"
        self.manifest_path = self.tx_dir / "manifest.json"
        # Catalog and search artifacts are shared by every article under this
        # root. An article-specific lock permits lost shared-file updates.
        self.lock_path = self.control_root / "locks" / "publication-slice.lock"
        self._artifacts: list[dict[str, Any]] = []
        self._outbox: list[dict[str, Any]] = []
        self._fault = fault_injector or (lambda boundary: None)
        self.lock_timeout = lock_timeout

    def _bounded(self, path: Path | str) -> Path:
        candidate = Path(path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"publication artifact escapes root: {candidate}") from exc
        if self.control_root == candidate or self.control_root in candidate.parents:
            raise ValueError("publication artifacts cannot target transaction control files")
        return candidate

    def stage_bytes(self, destination: Path | str, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("staged payload must be bytes")
        target = self._bounded(destination)
        if any(item["destination"] == str(target) for item in self._artifacts):
            raise ValueError(f"publication artifact staged more than once: {target}")
        index = len(self._artifacts)
        self._artifacts.append({
            "destination": str(target), "stage": str(self.stage_dir / str(index)),
            "snapshot": str(self.snapshot_dir / str(index)),
            "sha256": hashlib.sha256(payload).hexdigest(), "payload": payload,
            "existed": None,
        })

    def stage_text(self, destination: Path | str, payload: str) -> None:
        if not isinstance(payload, str):
            raise TypeError("staged payload must be text")
        self.stage_bytes(destination, payload.encode("utf-8"))

    def enqueue(self, kind: str, payload: Mapping[str, Any]) -> None:
        if kind.startswith("wordpress"):
            raise ValueError("WordPress transactional draft/promotion is unsupported; failing closed")
        # Round-trip now so invalid/non-durable payloads fail before any mutation.
        detached = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        self._outbox.append({"id": uuid.uuid4().hex, "kind": kind,
                             "payload": detached, "status": "pending", "attempts": 0})

    @staticmethod
    def _fsync_file(path: Path | str) -> None:
        """Persist file contents and metadata, or fail rather than weaken durability."""
        with Path(path).open("rb") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path | str) -> None:
        """Persist directory entries; this transaction protocol is POSIX-only."""
        if not PublicationTransaction.SUPPORTED_PLATFORM:
            raise NotImplementedError(
                "durable publication transactions require POSIX directory fsync"
            )
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(Path(path), flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _atomic_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        PublicationTransaction._fsync_file(path)
        PublicationTransaction._fsync_directory(path.parent)

    @classmethod
    def _restore_snapshot(cls, snapshot: Path, target: Path, transaction_id: str) -> None:
        """Restore without consuming the snapshot, so interrupted rollback is repeatable."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.rollback-{transaction_id}")
        with snapshot.open("rb") as source, temporary.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
        cls._fsync_file(target)
        cls._fsync_directory(target.parent)

    def _manifest(self, status: str, error: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION, "transaction_id": self.transaction_id,
            "canonical_id": self.canonical_id, "status": status,
            "artifacts": [{k: v for k, v in item.items() if k != "payload"}
                          for item in self._artifacts],
            "outbox": self._outbox, "error": error,
        }

    def _acquire(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(f"timed out acquiring publication lock for {self.canonical_id}")
                time.sleep(0.01)

    def commit(self) -> TransactionResult:
        if not self._artifacts:
            raise ValueError("publication transaction has no local artifacts")
        # Stage all bytes before lock acquisition or destination mutation.
        self.stage_dir.mkdir(parents=True, exist_ok=False)
        self.snapshot_dir.mkdir(parents=True, exist_ok=False)
        # Persist the newly-created transaction directory chain before a
        # ``prepared`` manifest can make it authoritative and discoverable.
        self._fsync_directory(self.tx_dir)
        self._fsync_directory(self.control_root)
        self._fsync_directory(self.root)
        for item in self._artifacts:
            Path(item["stage"]).write_bytes(item.pop("payload"))
            self._fsync_file(item["stage"])
        self._fsync_directory(self.stage_dir)
        lock = self._acquire()
        replaced: list[dict[str, Any]] = []
        try:
            for item in self._artifacts:
                target, snapshot = Path(item["destination"]), Path(item["snapshot"])
                item["existed"] = target.exists()
                if item["existed"]:
                    snapshot.write_bytes(target.read_bytes())
                    self._fsync_file(snapshot)
            self._fsync_directory(self.snapshot_dir)
            self._atomic_json(self.manifest_path, self._manifest("prepared"))
            for index, item in enumerate(self._artifacts):
                self._fault(f"before_replace:{index}")
                target = Path(item["destination"])
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item["stage"], target)
                self._fsync_file(target)
                # A rename spanning directories is not durable until both the
                # destination insertion and staged-name removal are persisted.
                self._fsync_directory(target.parent)
                self._fsync_directory(self.stage_dir)
                replaced.append(item)
                self._fault(f"after_replace:{index}")
            self._fault("before_local_commit_marker")
            self._atomic_json(self.manifest_path, self._manifest("local_committed"))
            return TransactionResult(self.transaction_id, str(self.manifest_path), "local_committed")
        except Exception as exc:
            rollback_errors = []
            for item in reversed(replaced):
                try:
                    target = Path(item["destination"])
                    if item["existed"]:
                        self._restore_snapshot(Path(item["snapshot"]), target,
                                               self.transaction_id)
                    elif target.exists():
                        target.unlink()
                        self._fsync_directory(target.parent)
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            status = "rollback_failed" if rollback_errors else "rolled_back"
            self._atomic_json(self.manifest_path, self._manifest(status, repr(exc)))
            raise TransactionError(f"publication transaction {status}: {exc}") from exc
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    @classmethod
    def recover(cls, manifest_path: Path | str, *,
                fault_injector: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Roll a stranded ``prepared`` transaction back from durable snapshots."""
        path = Path(manifest_path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("status") != "prepared":
            return manifest
        errors = []
        fault = fault_injector or (lambda boundary: None)
        for index, item in enumerate(reversed(manifest.get("artifacts", []))):
            try:
                target, snapshot = Path(item["destination"]), Path(item["snapshot"])
                if item.get("existed"):
                    if not snapshot.exists():
                        raise FileNotFoundError(f"missing transaction snapshot {snapshot}")
                    cls._restore_snapshot(snapshot, target, manifest["transaction_id"])
                elif target.exists():
                    target.unlink()
                    cls._fsync_directory(target.parent)
                fault(f"after_restore:{index}")
            except Exception as exc:
                errors.append(repr(exc))
        manifest["status"] = "rollback_failed" if errors else "rolled_back"
        manifest["recovery_errors"] = errors
        cls._atomic_json(path, manifest)
        if errors:
            raise TransactionError("publication recovery could not restore every artifact")
        return manifest

    @classmethod
    def reconcile(cls, manifest_path: Path | str,
                  handlers: Mapping[str, Callable[[dict[str, Any]], None]]) -> dict[str, Any]:
        path = Path(manifest_path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("status") not in ("local_committed", "outbox_pending", "completed"):
            raise TransactionError("outbox dispatch requires a locally committed transaction")
        for item in manifest.get("outbox", []):
            if item.get("status") == "delivered":
                continue
            handler = handlers.get(item["kind"])
            if handler is None:
                manifest["status"] = "outbox_pending"
                cls._atomic_json(path, manifest)
                continue
            item["attempts"] = int(item.get("attempts", 0)) + 1
            try:
                context = {
                    "transaction_id": manifest["transaction_id"],
                    "outbox_id": item["id"],
                    "idempotency_key": f'{manifest["transaction_id"]}:{item["id"]}',
                    "attempt": item["attempts"],
                }
                # Preserve one-argument handlers while allowing receivers to
                # deduplicate retries with the durable outbox identity.
                try:
                    inspect.signature(handler).bind(item["payload"], context)
                except (TypeError, ValueError):
                    handler(item["payload"])
                else:
                    handler(item["payload"], context)
            except Exception as exc:
                item["last_error"] = repr(exc)
                manifest["status"] = "outbox_pending"
                cls._atomic_json(path, manifest)
                continue
            item["status"] = "delivered"
            item.pop("last_error", None)
            cls._atomic_json(path, manifest)
        manifest["status"] = ("completed" if all(i.get("status") == "delivered"
                                                  for i in manifest.get("outbox", []))
                              else "outbox_pending")
        cls._atomic_json(path, manifest)
        return manifest
