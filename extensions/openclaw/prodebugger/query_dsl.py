"""Element-scoped SQL; no SQL-accessible complete steps, modules or fact dumps."""
from __future__ import annotations
import json
import re
import sqlite3
import time
from itertools import product
from pathlib import Path
import sqlglot
from sqlglot import exp
from sqlglot.dialects.sqlite import SQLite
from .record_bridge import plain, digest
from .evidence_quality import RAW_MODULE_RELATIONS, evidence_quality, fact_quality
from .query_syntax import BATCH_ARGUMENTS, lower_named_unions
from .session_sources import source_text as exact_source_text, fact_source_module, is_session, MESSAGE_FACTS

VERSION = "record-sql/v6-sparse-evidence"
FACT_SCHEMA = "fid INTEGER, step INTEGER, rel TEXT, args TEXT, slot TEXT, side TEXT, value, value_type TEXT, span TEXT, purpose TEXT, source_kind TEXT, parse_status TEXT, value_clipped INTEGER, assertion_time INTEGER, claim_scope TEXT, claim_polarity TEXT"
SOURCE_SCHEMA = "source_id TEXT, fid INTEGER, step INTEGER, module TEXT, start INTEGER, end INTEGER, text TEXT, matched TEXT, source_kind TEXT, parse_status TEXT"
SCHEMAS = {
    "goals": "goal_id TEXT, name TEXT, count INTEGER, of_kind TEXT, rel TEXT, target TEXT, status_mode TEXT",
    "constraints": "goal_id TEXT, property TEXT, operator TEXT, expected, value_type TEXT, unit TEXT, mode TEXT, span TEXT",
    "route_evidence": "candidate_id TEXT, issue_step INTEGER, fid INTEGER, hypothesis TEXT, goal_links TEXT",
    "parse_audits": "source_id TEXT, step INTEGER, kind TEXT, status TEXT, template TEXT, reasons TEXT, unresolved TEXT",
    "relations": "rel TEXT, arity INTEGER, functional_key TEXT",
}
FUNCTIONS = {"FIND_ENTITY", "ABOUT", "TRACK_ENTITY", "TRACK_STATE", "TRACK_ACTION", "TRACE_FACT",
             "VERSIONS", "STATE_AT", "SOURCE", "SEARCH_TEXT", "FIND_CLAIMS", "GOAL_STATUS", "COVERAGE"}
SQL_FUNCTIONS = {"count", "min", "max", "sum", "avg", "total", "lower", "upper", "length", "substr", "substring",
                 "trim", "ltrim", "rtrim", "coalesce", "ifnull", "nullif", "abs", "round", "instr", "like",
                 "glob", "replace", "json_extract", "json_array_length", "typeof", "contains"}
MODULES = {"mem": "memory", "refl": "reflection", "plan": "plan", "act": "action", "obs": "observation"}
SOURCE_FIELDS = {"observation", "memory", "reflection", "plan", "action", "message"}
EXCERPT_CHARS = 360
TEXT_ARGUMENTS = {"FIND_ENTITY": {0}, "ABOUT": {0}, "TRACK_ENTITY": {0},
    "TRACK_STATE": {0, 1}, "STATE_AT": {0, 1}, "TRACK_ACTION": {0},
    "TRACE_FACT": {1}, "SOURCE": {1}, "SEARCH_TEXT": {0, 1}, "FIND_CLAIMS": {0, 1}, "GOAL_STATUS": {0}}
FUNCTION_SCHEMAS = {**{name: FACT_SCHEMA for name in FUNCTIONS},
    "SEARCH_TEXT": SOURCE_SCHEMA, "SOURCE": SOURCE_SCHEMA, "FIND_CLAIMS": SOURCE_SCHEMA,
    "FIND_ENTITY": "entity_id TEXT, kind TEXT, entity_index TEXT",
    "GOAL_STATUS": "goal_id TEXT, step INTEGER, state TEXT, details TEXT",
    "COVERAGE": "step INTEGER, details TEXT"}


def short_code(value):
    return bool(re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d]{2}", value))


def match_position(text, needle):
    if short_code(needle):
        match = re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", text, flags=re.I)
        return match.start() if match else -1
    return text.lower().find(needle.lower())


class RecordSQLite(SQLite):
    """Preserve SQLite json_extract's scalar/null semantics on round-trip.

    sqlglot's generic JSONExtract is rendered as -> for one path, which returns
    quoted JSON strings instead of SQLite scalar text (and is not authorized).
    Keep the native function instead of weakening the SQL function allowlist.
    """
    class Parser(SQLite.Parser):
        FUNCTIONS = {**SQLite.Parser.FUNCTIONS,
                     "JSON_EXTRACT": lambda args: exp.Anonymous(this="JSON_EXTRACT", expressions=args)}


def documentation():
    return Path(__file__).with_name("prompts").joinpath("query_dsl.md").read_text(encoding="utf-8")


def _cell(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True)


def _clip(value, size=240):
    return value[:size] if isinstance(value, str) else value


class QueryEngine:
    def __init__(self, tools):
        self.tools, self.record = tools, tools.bundle.record
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.function_scopes = []
        self.function_errors, self.repairs, self.skipped_branches = [], [], []
        self.source_catalog = []
        self.table_schemas = {}
        self._populate()

    def close(self):
        self.conn.close()

    def _table(self, name, schema, rows):
        self.table_schemas[name] = schema
        self.conn.execute(f'CREATE TABLE "{name}" ({schema})')
        rows = list(rows)
        if rows:
            self.conn.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for _ in rows[0])})',
                                  [tuple(_cell(v) for v in row) for row in rows])

    def _fact_row(self, fact):
        quality = fact_quality(self.record, fact)
        raw = fact.rel in RAW_MODULE_RELATIONS
        value = None if raw else _cell(fact.value)
        return (fact.fid, fact.t, fact.rel, [str(a) for a in fact.args], fact.slot, fact.side,
                _clip(value), fact.value_type, None if raw else _clip(fact.span),
                str(fact.purpose) if fact.purpose else None, quality["source_kind"], quality["parse_status"],
                int(isinstance(value, str) and len(value) > 240), quality['assertion_time'],
                quality['claim_scope'], quality['claim_polarity'])

    def _populate(self):
        r = self.record
        self._table("goals", SCHEMAS["goals"], [(str(g.entity()), g.name, g.count, g.of_kind, g.rel,
                      str(g.target) if g.target else None, g.status_mode) for g in r.goals])
        self._table("constraints", SCHEMAS["constraints"], [(str(g.entity()), str(c.property), c.operator, _clip(_cell(c.expected)),
                      c.value_type, c.unit, c.mode, _clip(c.span)) for g in r.goals for c in g.constraints])
        self._table("route_evidence", SCHEMAS["route_evidence"], [(c["candidate_id"], c["issue_step"], fid,
                    _clip(c.get("hypothesis")), c.get("goal_links", []))
                    for c in self.tools.windows.windows for fid in c.get("issue_fids", [])])
        self._table("parse_audits", SCHEMAS["parse_audits"], [(a["source_id"], a["step"], a["kind"], a["status"],
                    a["template"], [str(x)[:180] for x in a["reasons"][:4]],
                    [{"quote": g["quote"][:180], "reason": g["reason"][:180]} for g in a["unresolved"][:3]])
                    for a in getattr(r, "parse_audits", [])])
        self._table("relations", SCHEMAS["relations"], [(name, rel.arity, list(rel.key)) for name, rel in r.relations.declared().items()])
        self.conn.create_function("contains", 2, lambda a, b: None if a is None or b is None else int(str(b) in str(a)))

    def _literal(self, node, name, position, function_index):
        if isinstance(node, exp.Tuple):
            if position not in BATCH_ARGUMENTS.get(name, set()) or not node.expressions:
                raise ValueError("literal lists are supported only for SEARCH_TEXT/FIND_CLAIMS terms/modules and TRACK_ACTION relations")
            if any(not isinstance(item, exp.Literal) or not item.is_string for item in node.expressions):
                raise ValueError("batch items must be single-quoted literal strings; no expressions, columns or nested lists")
            return [item.this for item in node.expressions]
        if isinstance(node, exp.Literal):
            return node.this if node.is_string else int(node.this)
        if (position in TEXT_ARGUMENTS.get(name, set()) and isinstance(node, exp.Column)
                and not node.table and isinstance(node.this, exp.Identifier) and node.this.args.get("quoted")):
            self.repairs.append({"kind": "double_quoted_string_literal", "function": name,
                "function_index": function_index, "argument_index": position, "text": node.name,
                "instruction": "Use single-quoted SQL strings; double quotes normally name identifiers."})
            return node.name
        raise ValueError("table function arguments must be literal IDs/integers/single-quoted strings; columns, expressions and subqueries are unsupported")

    def _entity(self, value):
        if not isinstance(value, str) or value not in self.record.E:
            raise ValueError("use an existing entity_id; FIND_ENTITY('specific noun') can locate it")
        return self.record.E[value]

    def _relation(self, value):
        if not isinstance(value, str) or value not in self.record.relations:
            raise ValueError("use an existing relation from relations")
        return value

    def _bounds(self, args):
        if len(args) not in (0, 2):
            raise ValueError("optional bounds require both from_step,to_step")
        lo, hi = args if args else (0, self.tools.index.length)
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= self.tools.index.length:
            raise ValueError("bounds must be valid original steps")
        return lo, hi

    def _needle(self, value, *, exact_ui=False):
        if isinstance(value, str):
            value = value.strip()
            if short_code(value):
                return value
            if exact_ui and value.lower() == "search":
                return value
        if (not isinstance(value, str) or not 3 <= len(value) <= 120
                or len(re.sub(r"\W", "", value)) < 3 or re.search(r"[%_*]", value)
                or value.lower() in {"the", "and", "you", "step", "plan", "memory", "reflection", "action", "task", "search"}):
            raise ValueError("use a specific literal claim/entity (3..120 characters, or a two-character letter+digit code such as 4T); "
                "no wildcards or generic labels. For actions use TRACK_ACTION('search'); for plans use the disputed phrase, not 'search'.")
        return value

    def _source(self, step, field, text, needle, fid=None):
        pos = match_position(text, needle)
        if pos < 0:
            return None
        start = max(0, pos - 90); end = min(len(text), start + EXCERPT_CHARS)
        return self._source_interval(step, field, text, start, end, needle, fid)

    def _source_interval(self, step, field, text, start, end, needle, fid=None):
        audit = next((a for a in getattr(self.record, "parse_audits", [])
                      if a["step"] == step and a["kind"] == "observation"), {})
        row = (f"{field}:{step}:{start}:{end}", fid, step, field, start, end, text[start:end], needle,
                "original_session_message" if field == "message" else "original_observation" if field == "observation" else "original_agent_module",
                "recorded_public_text_not_verified_truth" if field == "message" else audit.get("status", "not_audited") if field == "observation" else "raw_agent_text_not_verified_truth")
        self.source_catalog.append(row)
        return row

    def _visible_claim_fragments(self, rows):
        """Require exact provenance AND returned text, never an intermediate scan."""
        from .claim_parser import MENTAL, fragment, source_text
        catalog = {}
        for r in self.source_catalog:
            catalog.setdefault((r[2], r[3], r[6]), {})[r[0]] = r
        found = {}
        for row in rows:
            if (type(row.get("step")) is int and isinstance(row.get("module"), str)
                    and isinstance(row.get("text"), str)):
                matches = list(catalog.get((row["step"], row["module"], row["text"]), {}).values())
                if row.get("source_id") is not None:
                    matches = [r for r in matches if r[0] == row["source_id"]]
                for key, index in (("start", 4), ("end", 5)):
                    if row.get(key) is not None:
                        matches = [r for r in matches if type(row[key]) is int and r[index] == row[key]]
                original = matches[0] if len(matches) == 1 else None
                if original and original[3] in MENTAL:
                    f = fragment(self.tools.bundle.source, original[2], original[3], original[4], original[5])
                    found[(f["step"], f["module"], f["start"], f["end"])] = f
            if type(row.get("fid")) is int and row["fid"] in self.tools.index.facts:
                fact = self.tools.index.fact(row["fid"])
                module = MODULES.get(fact.slot)
                span = row.get("span")
                if module not in MENTAL or fact.rel in RAW_MODULE_RELATIONS or not isinstance(span, str) or not span:
                    continue
                original = source_text(self.tools.bundle.source, fact.t, module)
                if span == _clip(fact.span) and original.count(span) == 1:
                    start = original.index(span)
                    f = fragment(self.tools.bundle.source, fact.t, module, start, start + len(span))
                    found[(f["step"], f["module"], f["start"], f["end"])] = f
        return list(found.values())

    def _function(self, name, args):
        if any(isinstance(arg, list) for arg in args):
            # A batch is atomic on failure. Successful subcalls are not silently
            # presented as a complete result if any requested element is invalid.
            collected, schema = [], None
            for values in product(*[arg if isinstance(arg, list) else [arg] for arg in args]):
                current, rows = self._function(name, list(values))
                if schema is not None and current != schema:
                    raise ValueError("batch source schemas disagree")
                schema = current
                collected.extend(rows)
            return schema, collected
        r, idx = self.record, self.tools.index
        fids, schema, rows, limits, truncated = set(), FACT_SCHEMA, None, {}, False
        if name in {"ABOUT", "TRACK_ENTITY"}:
            if len(args) not in (1, 3):
                raise ValueError("TRACK_ENTITY(entity_id[,from_step,to_step])")
            entity = self._entity(args[0]); lo, hi = self._bounds(args[1:])
            if entity.kind in {"agent", "int"}:
                raise ValueError("agent/int entities are too broad; use TRACK_STATE(entity,relation) or SOURCE(fid,needle)")
            fids = {f.fid for f in r.F if lo <= f.t <= hi and (entity in f.args or f.purpose == entity)}
        elif name in {"TRACK_STATE", "STATE_AT"}:
            if (name == "STATE_AT" and len(args) != 3) or (name == "TRACK_STATE" and len(args) not in (2, 4)):
                raise ValueError("TRACK_STATE(entity,relation[,from,to]) or STATE_AT(entity,relation,step)")
            entity, relation = self._entity(args[0]), self._relation(args[1])
            lo, hi = self._bounds([0, args[2]] if name == "STATE_AT" else args[2:])
            pool = [f for f in r.F if lo <= f.t <= hi and f.rel == relation and entity in f.args and
                    (f.side in {"env", "task"} or (is_session(self.tools.bundle.source) and f.side == "mind" and
                     f.rel in MESSAGE_FACTS | {"session_read_target", "message_content", "message_role"}))]
            if name == "STATE_AT":
                latest = {}
                for f in pool:
                    latest[(f.rel, tuple(f.args[i] for i in r.relations[f.rel].key)) if r.relations[f.rel].is_functional() else f.key()] = f
                pool = list(latest.values())
            fids = {f.fid for f in pool}
        elif name == "TRACK_ACTION":
            if len(args) not in (1, 3):
                raise ValueError("TRACK_ACTION(relation[,from_step,to_step])")
            relation = self._relation(args[0]); lo, hi = self._bounds(args[1:])
            fids = {f.fid for f in r.F if f.slot == "act" and f.rel == relation and lo <= f.t <= hi}
        elif name == "TRACE_FACT":
            if not 1 <= len(args) <= 3:
                raise ValueError("TRACE_FACT(fid,direction='backward',depth=2)")
            graph = idx.trace([args[0]], scope={"mode": "global"}, direction=args[1] if len(args) > 1 else "backward",
                              depth=args[2] if len(args) > 2 else 2, max_nodes=None)
            fids = {n["fid"] for n in graph["nodes"] if n["type"] == "fact"}
            limits, truncated = graph["limits"], graph["truncated"]
        elif name == "VERSIONS":
            if len(args) != 1:
                raise ValueError("VERSIONS(fid)")
            f = r.fact(idx.fact(args[0]).fid); relation = r.relations[f.rel]
            key = tuple(f.args[i] for i in relation.key)
            fids = {v.fid for v in r.by_rel(f.rel) if tuple(v.args[i] for i in relation.key) == key}
        elif name == "FIND_ENTITY":
            if len(args) != 1:
                raise ValueError("FIND_ENTITY(specific_literal)")
            needle = self._needle(args[0]).lower(); schema = "entity_id TEXT, kind TEXT, entity_index TEXT"
            rows = [(str(e), e.kind, e.index) for e in r.E.values() if needle in str(e).lower() and len(str(e)) <= 160]
        elif name == "SOURCE":
            if len(args) != 2:
                raise ValueError("SOURCE(fid,specific_literal) returns an excerpt, never a full module")
            fact = idx.fact(args[0]); needle = self._needle(args[1]); field = fact_source_module(fact, self.tools.bundle.source) or "action"
            step = self.tools.bundle.source["steps"][fact.t - 1] if fact.t else None
            text = exact_source_text(self.tools.bundle.source, fact.t, field) if step else ""
            schema = SOURCE_SCHEMA; row = self._source(fact.t, field, text, needle, fact.fid)
            rows = [row] if row else []; truncated = row is None and match_position(text, needle) >= 0
        elif name == "SEARCH_TEXT":
            if len(args) not in (2, 4) or args[1] not in SOURCE_FIELDS:
                raise ValueError("SEARCH_TEXT(specific_literal,one_module_or_observation[,from_step,to_step])")
            if args[1] == "message" and not is_session(self.tools.bundle.source):
                raise ValueError("message source is available only for recorded OpenClaw sessions")
            lo, hi = self._bounds(args[2:])
            # The one-word WebShop home page is an exact UI-state observation,
            # not permission to use a generic word to retrieve arbitrary logs.
            exact_ui = (args[1] == "observation" and lo == hi and lo > 0
                and self.tools.bundle.source["task_type"] == "webshop"
                and self.tools.bundle.source["steps"][lo - 1]["env_text"].strip().strip("'\"").lower() == "search")
            needle = self._needle(args[0], exact_ui=exact_ui); schema, rows, matches = SOURCE_SCHEMA, [], 0
            for step in self.tools.bundle.source["steps"]:
                if not lo <= step["t"] <= hi:
                    continue
                text = exact_source_text(self.tools.bundle.source, step["t"], args[1])
                if match_position(text, needle) >= 0:
                    matches += 1
                    row = self._source(step["t"], args[1], text, needle)
                    if row:
                        rows.append(row)
            limits = {"matched_sources": matches, "returned_sources": len(rows), "max_sources": None,
                      "excerpt_chars": EXCERPT_CHARS, "shared_source_chars": None}
            truncated = matches > len(rows)
        elif name == "FIND_CLAIMS":
            from .claim_parser import TOPICS, MENTAL, source_text
            if len(args) not in (2, 4) or args[0] not in TOPICS or args[1] not in MENTAL:
                raise ValueError("FIND_CLAIMS(topic,one_mental_module[,from_step,to_step]); use a documented topic")
            lo, hi = self._bounds(args[2:]); schema, rows = SOURCE_SCHEMA, []
            for card in getattr(r, "agent_statements", []):
                if lo <= card["step"] <= hi and card["module"] == args[1] and args[0] in card["topics"]:
                    text = source_text(self.tools.bundle.source, card["step"], card["module"])
                    rows.append(self._source_interval(card["step"], card["module"], text, card["start"],
                        min(card["end"], card["start"] + EXCERPT_CHARS), args[0]))
            limits = {"discovery": "lexical_topic_hints_not_complete_semantic_search", "excerpt_chars": EXCERPT_CHARS}
        elif name == "GOAL_STATUS":
            if len(args) != 2 or args[0] not in idx.requirements:
                raise ValueError("GOAL_STATUS(goal_id,step)")
            self._bounds([0, args[1]])
            from process_notes import goal_state
            state = goal_state(r, next(g for g in r.goals if str(g.entity()) == args[0]), args[1])
            schema = "goal_id TEXT, step INTEGER, state TEXT, details TEXT"
            rows = [(args[0], args[1], state["state"], state)]
        elif name == "COVERAGE":
            if len(args) != 1:
                raise ValueError("COVERAGE(through_step)")
            self._bounds([0, args[0]])
            schema, rows = "step INTEGER, details TEXT", [(args[0], evidence_quality(r, args[0]))]
        else:
            raise ValueError("unknown element function " + name)
        rows = rows if rows is not None else [self._fact_row(r.fact(fid)) for fid in sorted(fids)]
        self.function_scopes.append({"function": name, "arguments": args, "matched_rows": len(rows),
                                     "truncated": truncated, "limits": {**limits, "rows": None}})
        return schema, rows

    def _isolate_failed_branches(self, tree, failed):
        """Suppress entire dependent UNION ALL arms, never treat failure as empty truth.

        Joins, negation and aggregates in a dependent arm are suppressed together.
        A standalone/compound dependency with no independent arm remains an error.
        """
        if not failed:
            return
        if len(list(tree.find_all(exp.With))) > 1:
            raise ValueError("failed function in nested WITH; independent recovery cannot be established")
        ctes = {c.alias_or_name.lower(): c.this for c in tree.find_all(exp.CTE)}

        def dependencies(node, visiting=()):
            # The outer WITH declares all arms; only referenced CTEs are dependencies.
            scoped = node.copy()
            for key in ("with", "with_"):
                if key in scoped.args:
                    scoped.set(key, None)
            errors = set()
            for table in scoped.find_all(exp.Table):
                name = table.name.lower()
                if name in failed:
                    errors.add(failed[name])
                elif name in ctes and name not in visiting:
                    errors.update(dependencies(ctes[name], (*visiting, name)))
            return errors

        def arms(node):
            if isinstance(node, exp.Union) and node.args.get("distinct") is False:
                return arms(node.this) + arms(node.expression)
            return [node]

        # A failed scalar subquery in a compound LIMIT/ORDER BY can suppress or
        # reorder even otherwise independent arms. Do not evaluate it as empty.
        for compound in tree.find_all(exp.SetOperation):
            for key, value in compound.args.items():
                if key not in {"this", "expression", "with", "with_"} and isinstance(value, exp.Expression) and dependencies(value):
                    raise ValueError("compound query modifier depends on failed evidence; cannot recover independent rows")
        branches = arms(tree)
        affected = [(node, dependencies(node)) for node in branches]
        if all(errors for _, errors in affected):
            raise ValueError("all output branches depend on failed functions; inspect function_errors")
        for number, (node, errors) in enumerate(affected, 1):
            if not errors:
                continue
            # Keep the arm's output names (including SELECT *) but suppress its
            # rows even for COUNT or NOT EXISTS over an unavailable source.
            wrapper = exp.select("*").from_(node.copy().subquery()).where(exp.false())
            node.replace(wrapper)
            self.skipped_branches.append({"branch": number, "function_errors": sorted(errors),
                "reason": "Unavailable evidence; dependent arm omitted, not a negative finding."})

    def execute(self, sql, *, max_rows=None, max_bytes=None):
        if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
            raise ValueError("query must be nonempty SQL, at most 20000 characters")
        trees = sqlglot.parse(sql, read=RecordSQLite)
        if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError("exactly one SELECT query (optionally WITH/set operations) is permitted")
        tree = trees[0]
        sparse = any(n.args.get("by_name") for n in tree.find_all(exp.Union)) or any(
            isinstance(arg, exp.Tuple) for t in tree.find_all(exp.Table)
            if isinstance(t.this, exp.Anonymous) for arg in t.this.expressions)
        if any(w.args.get("recursive") for w in tree.find_all(exp.With)) or tree.find(exp.Into):
            raise ValueError("recursive CTEs and SELECT INTO are not supported")
        tables, ctes = set(SCHEMAS), {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        function_count = 0
        failed = {}
        for number, table in enumerate(list(tree.find_all(exp.Table))):
            if table.db or table.catalog:
                raise ValueError("qualified databases are not available")
            function = table.this
            if isinstance(function, exp.Anonymous):
                name = function.name.upper(); function_count += 1
                if name not in FUNCTIONS:
                    raise ValueError("use documented element functions; RAW_STEP is forbidden")
                args = None
                target = f"fn_{number}"
                try:
                    args = [self._literal(a, name, i, number) for i, a in enumerate(function.expressions)]
                    schema, rows = self._function(name, args)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    error = {"function_index": number, "function": name,
                        "arguments": args if args is not None else [a.sql(dialect="sqlite") for a in function.expressions],
                        "code": "invalid_function_arguments", "message": str(exc)}
                    self.function_errors.append(error)
                    self.function_scopes.append({"function": name, "arguments": error["arguments"],
                        "matched_rows": None, "truncated": False, "limits": {}, "error": error})
                    failed[target] = number
                    schema, rows = FUNCTION_SCHEMAS[name], []
                target = f"fn_{number}"; self._table(target, schema, rows); tables.add(target)
                table.set("this", exp.to_identifier(target))
            elif table.name.lower() not in tables | ctes:
                raise ValueError("full-log access forbidden: steps/modules/facts are not SQL tables. Use TRACK_ENTITY, TRACK_STATE, TRACK_ACTION, SOURCE or SEARCH_TEXT")
        if not function_count:
            raise ValueError("query must investigate an element using at least one element function")
        if any(n.args.get("by_name") for n in tree.find_all(exp.SetOperation)):
            if any(not isinstance(n, exp.Union) and n.args.get("by_name") for n in tree.find_all(exp.SetOperation)):
                raise ValueError("BY NAME is supported for UNION/UNION ALL only")
            tree = lower_named_unions(tree, self.table_schemas)
        self._isolate_failed_branches(tree, failed)
        for alias in tree.find_all(exp.Alias):
            if alias.alias.lower() in {"fid", "step"} and not isinstance(alias.this, exp.Null):
                if not isinstance(alias.this, exp.Column) or alias.this.name.lower() != alias.alias.lower():
                    raise ValueError("fid/step aliases must preserve their original meaning")
        rewritten = tree.sql(dialect="sqlite", unsupported_level=sqlglot.ErrorLevel.RAISE)
        self.executed_tree = tree.copy()
        def authorize(action, one, two, db, trigger):
            if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_READ and one in tables:
                return sqlite3.SQLITE_OK
            # SQLite may report a zero-column COUNT(*)/EXISTS read against a
            # CTE alias rather than its underlying materialized element table.
            # The AST above has already validated every CTE source. This is a
            # synthetic read, with no database or column; real table/column
            # reads still require the existing materialized-table whitelist.
            if (action == sqlite3.SQLITE_READ and db is None and two == ""
                    and isinstance(one, str) and one.lower() in ctes):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (two or one or "").lower() in SQL_FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        self.conn.commit(); self.conn.set_authorizer(authorize)
        started = time.monotonic(); operations = 0
        def progress():
            nonlocal operations
            operations += 1000
            return int(operations > 2000000 or time.monotonic() - started > 3)
        self.conn.set_progress_handler(progress, 1000)
        cursor = self.conn.execute(rewritten); columns = [d[0] for d in cursor.description]
        if len(columns) != len(set(columns)):
            raise ValueError("duplicate output columns; use explicit aliases")
        rows, size, cap = [], 0, False
        for row in cursor:
            item = {key: value for key, value in dict(row).items() if value is not None or not sparse}
            cost = len(json.dumps(item, ensure_ascii=False).encode())
            if ((max_rows is not None and len(rows) >= max_rows)
                    or (max_bytes is not None and size + cost > max_bytes)):
                cap = True; break
            rows.append(item); size += cost
        fids = {r["fid"] for r in rows if type(r.get("fid")) is int and r["fid"] in self.tools.index.facts}
        steps = {r["step"] for r in rows if type(r.get("step")) is int and 1 <= r["step"] <= self.tools.index.length}
        sql_limits = [node.sql(dialect="sqlite") for node in tree.find_all(exp.Limit)]
        from .claim_parser import cards, payload
        fragments = self._visible_claim_fragments(rows)
        return {"schema": VERSION, "query": sql, "query_digest": digest(sql), "columns": columns, "rows": rows,
                "row_format": "sparse/v1" if sparse else "dense/v1",
                "claim_fragments": fragments,
                "agent_statements": payload(cards(self.tools.bundle.source, fragments)),
                "returned_rows": len(rows), "truncated": cap or any(f["truncated"] for f in self.function_scopes),
                "query_has_limit": bool(sql_limits), "sql_limits": sql_limits,
                "coverage": "partial_failed_branches" if self.function_errors else "partial_SQL_LIMIT" if sql_limits else "element_scope_only",
                "partial": bool(self.function_errors), "function_errors": self.function_errors,
                "repairs": self.repairs, "skipped_branches": self.skipped_branches,
                "engine_truncated": cap, "function_scopes": self.function_scopes,
                "returned_fids": sorted(fids), "returned_steps": sorted(steps),
                "limits": {"rows": max_rows, "bytes": max_bytes, "vm_operations": 2000000,
                           "raw_source_chars": None, "function_calls": None, "excerpt_chars": EXCERPT_CHARS},
                "evidence_quality": evidence_quality(self.record, self.tools.index.length),
                "limitations": "Element results only, never complete steps. In sparse rows, absent fields mean SQL NULL/unknown, not false; columns lists the union of available fields. Failed function branches are UNAVAILABLE, not zero matches or counterevidence. "
                    "Inspect partial/function_errors/skipped_branches and repairs. No aggregate row/byte/source/function budget is imposed. "
                    "Explicit SQL LIMIT, filters, graph depth, phrase-centered excerpts and column selection can omit evidence. "
                    "Empty results and missing facts mean unknown, not an agent error. Inspect original source before accepting a parsed premise."}


def execute(tools, sql, *, engine_class=QueryEngine):
    engine = engine_class(tools)
    try:
        return engine.execute(sql)
    except (ValueError, TypeError, KeyError, sqlite3.Error, sqlglot.errors.SqlglotError) as exc:
        return {"schema": VERSION, "query": sql, "error": {"code": "invalid_query", "message": str(exc)},
                "rows": [], "returned_steps": [], "returned_fids": [], "truncated": False,
                "partial": False, "function_errors": engine.function_errors, "repairs": engine.repairs,
                "skipped_branches": engine.skipped_branches, "function_scopes": engine.function_scopes,
                "evidence_quality": evidence_quality(tools.bundle.record, tools.index.length)}
    finally:
        engine.close()
