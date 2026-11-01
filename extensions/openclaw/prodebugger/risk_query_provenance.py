"""Risk evidence comes from real element output, never SQL-authored strings.

The unchanged SQL engine still returns every legal result column. This adapter
adds provenance only for returned source fragments with an actual source-column
dependency and an exact, same-location match in the materialized element output.
No second SQL, additional source retrieval, new facts or model call is performed.
"""
from __future__ import annotations

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

from .query_dsl import QueryEngine, FACT_SCHEMA, SOURCE_SCHEMA, execute as execute_query
from .record_bridge import digest
from .session_sources import fact_source_module, source_text


FIELDS = {"text", "quote", "span", "value"}


class RiskQueryEngine(QueryEngine):
    def __init__(self, tools):
        self.materialized_sources = []
        self.lineage_cache = {}
        super().__init__(tools)

    def _table(self, name, schema, rows):
        rows = list(rows)
        super()._table(name, schema, rows)
        if not name.startswith("fn_") or schema not in {FACT_SCHEMA, SOURCE_SCHEMA}:
            return
        columns = [part.split()[0] for part in schema.split(",")]
        for values in rows:
            row = dict(zip(columns, values))
            fid, step = row.get("fid"), row.get("step")
            if schema == SOURCE_SCHEMA:
                module, fields = row.get("module"), ("text",)
            else:
                fact = self.tools.index.facts.get(fid)
                if fact is None or fact.t != step:
                    continue
                module = fact_source_module(fact, self.tools.bundle.source)
                fields = ("span", "value")
            if type(step) is not int or not module:
                continue
            original = source_text(self.tools.bundle.source, step, module) or ""
            for field in fields:
                text = row.get(field)
                if not isinstance(text, str) or not text.strip() or text not in original:
                    continue
                start = row.get("start", original.index(text))
                if type(start) is not int or original[start:start + len(text)] != text:
                    continue
                self.materialized_sources.append({"table": name, "field": field, "fid": fid,
                    "step": step, "module": module, "text": text, "start": start,
                    "kind": "source" if schema == SOURCE_SCHEMA else "fact",
                    "source_id": row.get("source_id", f"source:{step}:{module}")})

    @staticmethod
    def _direct_expression(expression, *, text=False):
        if isinstance(expression, (exp.Alias, exp.Paren)):
            return RiskQueryEngine._direct_expression(expression.this, text=text)
        if isinstance(expression, exp.Column):
            return True
        # These can preserve/extract a source fragment. Exact returned-content
        # matching remains mandatory; CASE/COALESCE/concatenation are not proof.
        if text and isinstance(expression, (exp.Cast, exp.Substring)):
            return RiskQueryEngine._direct_expression(expression.this, text=True)
        return False

    def _dependencies(self, field, *, text=False, label=False):
        key = field, text, label
        if key in self.lineage_cache:
            return self.lineage_cache[key]
        schema = {name: {part.split()[0]: (part.split()[1] if len(part.split()) > 1 else "TEXT")
                         for part in fields.split(",")} for name, fields in self.table_schemas.items()}
        try:
            graph = lineage(field, self.executed_tree, schema=schema, dialect="sqlite")
            def walk(node, path=(), root=False):
                if not root and "." in node.name:
                    path += (node.name.rsplit(".", 1)[0],)
                if node.name in {"UNION", "INTERSECT", "EXCEPT"}:
                    return [leaf for index, child in enumerate(node.downstream)
                            for leaf in walk(child, path + (node.name + ":" + str(index),))]
                if isinstance(node.expression, exp.Table):
                    table = node.expression.name
                    column = node.name.rsplit(".", 1)[-1].strip('"')
                    source_column = ((self.table_schemas.get(table) == SOURCE_SCHEMA and column == "text")
                        or (self.table_schemas.get(table) == FACT_SCHEMA and column in {"span", "value"}))
                    if text and (not table.startswith("fn_") or not source_column):
                        raise ValueError("column_has_no_materialized_original_source")
                    return [(table, column, path)]
                expression = node.expression
                while isinstance(expression, (exp.Alias, exp.Paren)):
                    expression = expression.this
                if label and not node.downstream and isinstance(expression, exp.Literal) and expression.is_string:
                    return [(None, expression.this, path)]
                if not self._direct_expression(node.expression, text=text):
                    raise ValueError("literal_or_computed_expression_is_not_a_source_projection")
                if not node.downstream:
                    raise ValueError("no_source_column_dependency")
                return [leaf for child in node.downstream for leaf in walk(child, path)]
            answer = walk(graph, root=True), None
        except (ValueError, TypeError, KeyError, AttributeError, SqlglotError) as exc:
            answer = [], "unresolved_source_column_lineage:" + str(exc)
        self.lineage_cache[key] = answer
        return answer

    def _identity_matches(self, row, source, source_path):
        for output, original in (("step", "step"), ("t", "step"), ("fid", "fid"),
                                 ("fact_id", "fid"), ("module", "module"),
                                 ("source_id", "source_id"), ("start", "start"), ("end", "end")):
            if row.get(output) is None:
                continue
            origins, _ = self._dependencies(output, label=output == "module")
            real = (source["table"], original, source_path) in origins
            # Fact operators have no module column. A matching fixed label is
            # allowed only for Facts; SOURCE rows must retain their real module.
            fixed_fact_label = output == "module" and source["kind"] == "fact" and any(
                table is None and (source.get("module") is None or value == source["module"]) and source_path[:len(path)] == path
                for table, value, path in origins)
            if not real and not fixed_fact_label:
                return False
        return True

    def _certify(self, row, row_index, field, dependencies):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
        step, module = row.get("step", row.get("t")), row.get("module")
        fid = row.get("fid", row.get("fact_id"))
        if fid is not None:
            fact = self.tools.index.facts.get(fid) if type(fid) is int else None
            if fact is None:
                return None
            actual_module = fact_source_module(fact, self.tools.bundle.source)
            if step is not None and step != fact.t or module is not None and module != actual_module:
                return None
            step, module = fact.t, actual_module
        if type(step) is not int or not isinstance(module, str):
            return None
        # A valid but filtered-out UNION arm must not lend its source path to a
        # returned row from an inconsistent arm. Fail closed for the entire
        # projection unless EVERY source branch agrees with its identity fields.
        for table, _, path in dependencies:
            origin = {"table": table, "kind": "source" if self.table_schemas.get(table) == SOURCE_SCHEMA else "fact"}
            if not self._identity_matches(row, origin, path):
                return None
        for source in self.materialized_sources:
            paths = [path for table, field, path in dependencies
                     if (table, field) == (source["table"], source["field"])]
            if (not paths
                    or (source["step"], source["module"]) != (step, module)
                    or fid is not None and source["fid"] != fid
                    or value not in source["text"]):
                continue
            if not any(self._identity_matches(row, source, path) for path in paths):
                continue
            start = source["start"] + source["text"].index(value)
            end = start + len(value)
            if row.get("source_id") not in (None, source["source_id"]):
                continue
            if any(row.get(key) is not None and (type(row[key]) is not int or row[key] != number)
                   for key, number in (("start", start), ("end", end))):
                continue
            return {"row_index": row_index, "field": field, "step": step, "module": module,
                "source_id": source["source_id"], "start": start, "end": end,
                "text_sha256": digest(value), "producer_table": source["table"],
                "producer_field": source["field"], "producer_fid": source["fid"]}
        return None

    def execute(self, sql, **kwargs):
        result = super().execute(sql, **kwargs)
        dependencies = {field: self._dependencies(field, text=True) for field in FIELDS
                        if field in result["columns"]}
        certified, unavailable = [], []
        for index, row in enumerate(result["rows"]):
            for field, (sources, reason) in dependencies.items():
                if not isinstance(row.get(field), str) or not row[field].strip():
                    continue
                proof = self._certify(row, index, field, sources) if sources else None
                if proof:
                    certified.append(proof)
                else:
                    unavailable.append({"row_index": index, "field": field,
                        "reason": reason or "returned_value_or_location_not_in_its_materialized_source"})
        result["verified_source_fragments"] = certified
        result["source_provenance"] = {"schema": "risk-query-source-provenance/v1",
            "verified_fields": len(certified), "unverified_fields": unavailable,
            "meaning": "Only code-verified fields may become original-source references. SQL literals, derived values or unresolved lineage remain query results, not new recorded evidence. No additional text or retrieval is supplied."}
        # The original query engine also prepares mental-statement fragments.
        # Apply the same risk-only boundary before query_trace passes them to
        # ClaimParser; otherwise a rejected literal could enter via that path.
        trusted = {(p["step"], p["module"], p["start"], p["end"], p["text_sha256"]) for p in certified}
        fragments = result.get("claim_fragments", [])
        retained = [f for f in fragments if (f["step"], f["module"], f["start"], f["end"], digest(f["text"])) in trusted]
        result["source_provenance"]["excluded_claim_fragments"] = len(fragments) - len(retained)
        if retained != fragments:
            from .claim_parser import cards, payload
            result["claim_fragments"] = retained
            result["agent_statements"] = payload(cards(self.tools.bundle.source, retained))
        return result


def execute(tools, sql):
    return execute_query(tools, sql, engine_class=RiskQueryEngine)
