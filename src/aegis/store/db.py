"""In-memory knowledge base persisted to SQLite (+FTS5). The corpus is tiny, so objects live in memory and SQLite
is the durable, inspectable, reproducible store (architecture §4 'Store')."""
from __future__ import annotations
import hashlib, json, sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from ..models import Alias, Claim, Document, Element, Entity, Evidence, Relation

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, json TEXT);
CREATE TABLE IF NOT EXISTS elements(id TEXT PRIMARY KEY, document_id TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, document_id TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, kind TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS aliases(entity_id TEXT, norm_key TEXT, status TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS claims(id TEXT PRIMARY KEY, entity_id TEXT, predicate TEXT, lifecycle TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS claim_audit(claim_id TEXT, stage TEXT, result TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS relations(kind TEXT, src TEXT, dst TEXT, json TEXT);
CREATE TABLE IF NOT EXISTS derivations(claim_id TEXT, rule_id TEXT, uses TEXT);
CREATE TABLE IF NOT EXISTS mentions(element_id TEXT, entity_id TEXT, surface TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(evidence_id UNINDEXED, quote);
"""

@dataclass
class KnowledgeBase:
    documents: dict[str, Document] = field(default_factory=dict)
    elements: dict[str, Element] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)
    aliases: list[Alias] = field(default_factory=list)
    claims: dict[str, Claim] = field(default_factory=dict)          # every claim ever proposed (lifecycle recorded)
    audit: list[tuple[str, str, str, str]] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    derivations: list[dict] = field(default_factory=list)
    mentions: list[tuple[str, str, str]] = field(default_factory=list)
    db_path: str = ""

    # --- views
    def admitted(self) -> list[Claim]:
        return [c for c in self.claims.values() if c.lifecycle == "ADMITTED"]

    def visible(self) -> list[Claim]:
        """ADMITTED plus DEMOTED (needs_review): stored and inspectable; DEMOTED is never answer-bearing."""
        return [c for c in self.claims.values() if c.lifecycle in ("ADMITTED", "DEMOTED")]

    def store_hash(self) -> str:
        """Hash of every admitted claim's canonical content, so answers are reproducible against a store version."""
        rows = sorted(json.dumps([c.entity_id, c.predicate, c.vkey(), c.scope.model_dump(), c.condition, c.assertion_kind,
                                  c.doc_id, c.needs_review], sort_keys=True) for c in self.visible())
        return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]

    # --- persistence
    def save(self, path: str) -> None:
        p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists(): p.unlink()
        con = sqlite3.connect(path); con.executescript(SCHEMA)
        j = lambda m: m.model_dump_json()
        con.executemany("INSERT INTO documents VALUES(?,?)", [(d.id, j(d)) for d in self.documents.values()])
        con.executemany("INSERT INTO elements VALUES(?,?,?)", [(e.id, e.document_id, j(e)) for e in self.elements.values()])
        con.executemany("INSERT INTO evidence VALUES(?,?,?)", [(e.id, e.document_id, j(e)) for e in self.evidence.values()])
        con.executemany("INSERT INTO entities VALUES(?,?,?)", [(e.id, e.kind, j(e)) for e in self.entities.values()])
        con.executemany("INSERT INTO aliases VALUES(?,?,?,?)", [(a.entity_id, a.norm_key, a.status, j(a)) for a in self.aliases])
        con.executemany("INSERT INTO claims VALUES(?,?,?,?,?)", [(c.id, c.entity_id, c.predicate, c.lifecycle, j(c)) for c in self.claims.values()])
        con.executemany("INSERT INTO claim_audit VALUES(?,?,?,?)", self.audit)
        con.executemany("INSERT INTO relations VALUES(?,?,?,?)", [(r.kind, r.src, r.dst, j(r)) for r in self.relations])
        con.executemany("INSERT INTO derivations VALUES(?,?,?)", [(d["claim_id"], d["rule_id"], json.dumps(d["uses"])) for d in self.derivations])
        con.executemany("INSERT INTO mentions VALUES(?,?,?)", self.mentions)
        con.executemany("INSERT INTO evidence_fts VALUES(?,?)", [(e.id, e.quote) for e in self.evidence.values()])
        con.execute("INSERT INTO meta VALUES('store_hash',?)", (self.store_hash(),))
        con.commit(); con.close(); self.db_path = path

    @classmethod
    def load(cls, path: str) -> "KnowledgeBase":
        con = sqlite3.connect(path); kb = cls(db_path=path)
        for (s,) in con.execute("SELECT json FROM documents"): d = Document.model_validate_json(s); kb.documents[d.id] = d
        for (s,) in con.execute("SELECT json FROM elements"): e = Element.model_validate_json(s); kb.elements[e.id] = e
        for (s,) in con.execute("SELECT json FROM evidence"): e = Evidence.model_validate_json(s); kb.evidence[e.id] = e
        for (s,) in con.execute("SELECT json FROM entities"): e = Entity.model_validate_json(s); kb.entities[e.id] = e
        for (s,) in con.execute("SELECT json FROM aliases"): kb.aliases.append(Alias.model_validate_json(s))
        for (s,) in con.execute("SELECT json FROM claims"): c = Claim.model_validate_json(s); kb.claims[c.id] = c
        kb.audit = [tuple(r) for r in con.execute("SELECT * FROM claim_audit")]
        for (s,) in con.execute("SELECT json FROM relations"): kb.relations.append(Relation.model_validate_json(s))
        kb.derivations = [{"claim_id": a, "rule_id": b, "uses": json.loads(c)} for a, b, c in con.execute("SELECT * FROM derivations")]
        kb.mentions = [tuple(r) for r in con.execute("SELECT * FROM mentions")]
        con.close(); return kb

    def fts(self, query: str, limit: int = 10) -> list[str]:
        """FTS5 recall net over evidence quotes (typed lookup is primary)."""
        import re
        toks = [t for t in re.findall(r"[A-Za-z0-9]+", query) if len(t) > 1]
        if not toks or not self.db_path: return []
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute("SELECT evidence_id FROM evidence_fts WHERE evidence_fts MATCH ? ORDER BY rank LIMIT ?",
                               (" OR ".join(f'"{t}"' for t in toks), limit)).fetchall()
        finally: con.close()
        return [r[0] for r in rows]
