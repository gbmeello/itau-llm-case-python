"""CRUD versionado de skills (prompts, políticas, exemplos).

- Versões são imutáveis: "update" = nova versão.
- Uma versão ACTIVE por skill; ativar outra deprecia a anterior (rollback = reativar versão antiga).
- Delete é lógico (DELETED) para manter rastreabilidade das decisões já tomadas.
- Skills essenciais ao agente não podem ser removidas, apenas versionadas.
- Fonte inicial: arquivos em resources/skills/<tipo>/<skill>/<versão>.<ext> (versionados em git).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from purchase_agent import resources
from purchase_agent.db import SkillVersion, utcnow

ANALYST = "analyst"
REPAIR = "repair"
COMPLIANCE = "compliance-reviewer"
CASE_SUMMARIZER = "case-summarizer"
EXAMPLES = "analyst-examples"
POLICIES = "purchase-policies"
PROTECTED = {ANALYST, REPAIR, COMPLIANCE, CASE_SUMMARIZER, POLICIES}

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_TYPE_DIRS = {"prompts": "PROMPT", "policies": "POLICY", "examples": "EXAMPLES"}
_CHANGELOG = re.compile(r"(?m)^changelog:\s*(.+)$")

log = logging.getLogger(__name__)


class SkillErrorReason(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class SkillError(Exception):
    def __init__(self, reason: SkillErrorReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ActiveSkill:
    skill_id: str
    version: str
    content: str


def semver_key(v: str) -> tuple[int, int, int]:
    if not v or not _SEMVER.match(v):
        raise SkillError(SkillErrorReason.INVALID, f"Versão deve ser semver MAJOR.MINOR.PATCH: {v}")
    major, minor, patch = (int(x) for x in v.split("."))
    return major, minor, patch


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SkillRegistry:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sessions = session_factory

    def active(self, skill_id: str) -> ActiveSkill | None:
        with self._sessions() as s:
            v = s.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id,
                                                    SkillVersion.status == "ACTIVE"))
            return ActiveSkill(v.skill_id, v.version, v.content) if v else None

    def required(self, skill_id: str) -> ActiveSkill:
        skill = self.active(skill_id)
        if skill is None:
            raise SkillError(SkillErrorReason.NOT_FOUND, f"Skill obrigatória sem versão ativa: {skill_id}")
        return skill

    def list_all(self) -> dict[str, list[SkillVersion]]:
        with self._sessions() as s:
            out: dict[str, list[SkillVersion]] = {}
            for v in s.scalars(select(SkillVersion).order_by(SkillVersion.skill_id, SkillVersion.id)):
                out.setdefault(v.skill_id, []).append(v)
            return out

    def versions(self, skill_id: str) -> list[SkillVersion]:
        with self._sessions() as s:
            rows = list(s.scalars(select(SkillVersion).where(SkillVersion.skill_id == skill_id)
                                  .order_by(SkillVersion.id)))
        if not rows:
            raise SkillError(SkillErrorReason.NOT_FOUND, f"Skill não encontrada: {skill_id}")
        return rows

    def create(self, skill_id: str, type_: str, version: str, content: str, changelog: str | None,
               author: str) -> SkillVersion:
        semver_key(version)
        with self._sessions.begin() as s:
            if s.scalar(select(SkillVersion.id).where(SkillVersion.skill_id == skill_id).limit(1)):
                raise SkillError(SkillErrorReason.CONFLICT, f"Skill já existe: {skill_id}")
            v = SkillVersion(skill_id=skill_id, type=type_, version=version, status="ACTIVE", content=content,
                             checksum=sha256(content), changelog=changelog, created_by=author)
            s.add(v)
        log.info("skill_created skill=%s version=%s author=%s", skill_id, version, author)
        return v

    def add_version(self, skill_id: str, version: str, content: str, changelog: str | None, author: str,
                    activate: bool) -> SkillVersion:
        existing = self.versions(skill_id)
        if all(v.status == "DELETED" for v in existing):
            raise SkillError(SkillErrorReason.CONFLICT, f"Skill removida: {skill_id}")
        if any(v.version == version for v in existing):
            raise SkillError(SkillErrorReason.CONFLICT,
                             f"Versão já existe (versões são imutáveis): {skill_id}@{version}")
        latest = max((v.version for v in existing), key=semver_key)
        if semver_key(version) <= semver_key(latest):
            raise SkillError(SkillErrorReason.INVALID, f"Nova versão deve ser maior que {latest}")
        with self._sessions.begin() as s:
            s.add(SkillVersion(skill_id=skill_id, type=existing[-1].type, version=version, status="DRAFT",
                               content=content, checksum=sha256(content), changelog=changelog, created_by=author))
        log.info("skill_version_added skill=%s version=%s author=%s", skill_id, version, author)
        if activate:
            return self.activate(skill_id, version, author)
        return self._get(skill_id, version)

    def activate(self, skill_id: str, version: str, author: str) -> SkillVersion:
        """Ativa uma versão (também usado para rollback)."""
        with self._sessions.begin() as s:
            target = s.scalar(select(SkillVersion).where(SkillVersion.skill_id == skill_id,
                                                         SkillVersion.version == version))
            if target is None:
                raise SkillError(SkillErrorReason.NOT_FOUND, f"Versão não encontrada: {skill_id}@{version}")
            if target.status == "DELETED":
                raise SkillError(SkillErrorReason.CONFLICT, "Versão removida não pode ser ativada")
            for cur in s.scalars(select(SkillVersion).where(SkillVersion.skill_id == skill_id,
                                                            SkillVersion.status == "ACTIVE")):
                if cur.id != target.id:
                    cur.status, cur.status_changed_at = "DEPRECATED", utcnow()
            target.status, target.status_changed_at = "ACTIVE", utcnow()
        log.info("skill_activated skill=%s version=%s author=%s", skill_id, version, author)
        return target

    def delete(self, skill_id: str, author: str) -> None:
        if skill_id in PROTECTED:
            raise SkillError(SkillErrorReason.CONFLICT,
                             f"Skill essencial ao agente não pode ser removida (crie uma nova versão): {skill_id}")
        self.versions(skill_id)
        with self._sessions.begin() as s:
            for v in s.scalars(select(SkillVersion).where(SkillVersion.skill_id == skill_id)):
                v.status, v.status_changed_at = "DELETED", utcnow()
        log.info("skill_deleted skill=%s author=%s", skill_id, author)

    def seed_from_resources(self) -> None:
        """Idempotente por (skill, versão). A versão mais alta de cada skill fica ativa."""
        found: dict[str, list[tuple[str, str, str, str]]] = {}
        for type_dir, type_ in _TYPE_DIRS.items():
            base = resources.path("skills", type_dir)
            if not base.exists():
                continue
            for f in base.glob("*/*.*"):
                if not _SEMVER.match(f.stem):
                    continue
                content = f.read_text(encoding="utf-8")
                m = _CHANGELOG.search(content)
                found.setdefault(f.parent.name, []).append((type_, f.stem, content, m.group(1).strip() if m else "seed"))
        for skill_id, versions in found.items():
            versions.sort(key=lambda v: semver_key(v[1]))
            for i, (type_, version, content, changelog) in enumerate(versions):
                if self._exists(skill_id, version):
                    continue
                with self._sessions.begin() as s:
                    s.add(SkillVersion(skill_id=skill_id, type=type_, version=version, status="DEPRECATED",
                                       content=content, checksum=sha256(content), changelog=changelog,
                                       created_by="seed"))
                if i == len(versions) - 1:
                    self.activate(skill_id, version, "seed")

    def _exists(self, skill_id: str, version: str) -> bool:
        with self._sessions() as s:
            return s.scalar(select(SkillVersion.id).where(SkillVersion.skill_id == skill_id,
                                                          SkillVersion.version == version)) is not None

    def _get(self, skill_id: str, version: str) -> SkillVersion:
        with self._sessions() as s:
            return s.scalars(select(SkillVersion).where(SkillVersion.skill_id == skill_id,
                                                        SkillVersion.version == version)).one()
