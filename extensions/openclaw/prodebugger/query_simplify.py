"""Conservative AST conversion of archived SQL to batches and sparse named unions.

No query is executed here. The migration runner must verify converted results on
the original input before publishing them. Multiplicity and literal call order
are preserved when adjacent branches are batched.
"""
from itertools import product
import re

import sqlglot
from sqlglot import exp

from .query_syntax import BATCH_ARGUMENTS, Columns

MODIFIERS = {"order", "limit", "offset", "with", "with_"}


def _sql(node):
    return node.sql(dialect="sqlite", comments=False)


def _null(node):
    while isinstance(node, (exp.Alias, exp.Cast, exp.Paren)):
        node = node.this
    return isinstance(node, exp.Null)


def _function(select):
    if not isinstance(select, exp.Select) or any(select.args.get(key) for key in (
            "distinct", "joins", "group", "having", "qualify", *MODIFIERS)):
        return None
    if select.find(exp.AggFunc) or select.find(exp.Window):
        return None
    origin = select.args.get("from") or select.args.get("from_")
    table = origin.this if origin else None
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Anonymous):
        return None
    return table.this if table.this.name.upper() in BATCH_ARGUMENTS else None


def _calls(function):
    return list(product(*[
        tuple(_sql(item) for item in arg.expressions) if isinstance(arg, exp.Tuple) else (_sql(arg),)
        for arg in function.expressions]))


def _merge(left, right):
    a, b = _function(left), _function(right)
    if a is None or b is None or a.name.upper() != b.name.upper() or len(a.expressions) != len(b.expressions):
        return None
    # Everything besides the literal function arguments must be identical.
    templates = []
    for select in (left, right):
        template = select.copy()
        _function(template).set("expressions", [])
        templates.append(_sql(template))
    if templates[0] != templates[1]:
        return None
    for position in sorted(BATCH_ARGUMENTS[a.name.upper()]):
        if position >= len(a.expressions) or any(_sql(x) != _sql(y) for i, (x, y) in
                enumerate(zip(a.expressions, b.expressions)) if i != position):
            continue
        items = []
        for function in (a, b):
            arg = function.expressions[position]
            items.extend(arg.expressions if isinstance(arg, exp.Tuple) else [arg])
        if not all(isinstance(item, exp.Literal) and item.is_string for item in items):
            continue
        merged = left.copy()
        fn = _function(merged)
        args = [arg.copy() for arg in fn.expressions]
        args[position] = exp.Tuple(expressions=[item.copy() for item in items])
        fn.set("expressions", args)
        if _calls(fn) != _calls(a) + _calls(b):
            continue
        for node in right.walk():
            if node.comments:
                merged.add_comments([c for c in node.comments if c not in (merged.comments or [])])
        return merged
    return None


def _batch(node):
    if not isinstance(node, exp.Union) or node.args.get("distinct") is not False:
        result = node.copy()
        for key, value in node.args.items():
            if isinstance(value, exp.Expression):
                result.set(key, _batch(value))
            elif isinstance(value, list):
                result.set(key, [_batch(v) if isinstance(v, exp.Expression) else v for v in value])
        return result

    def arms(item, root=False):
        if (isinstance(item, exp.Union) and item.args.get("distinct") is False
                and (root or not any(item.args.get(k) for k in MODIFIERS))):
            return arms(item.this) + arms(item.expression)
        return [item]

    grouped = []
    for item in arms(node, True):
        item = _batch(item)
        merged = _merge(grouped[-1], item) if grouped else None
        if merged is None:
            grouped.append(item)
        else:
            grouped[-1] = merged
        # Completing a module list may make the previous term group mergeable.
        # Repeat only across adjacent groups and only when literal call order is
        # exactly preserved (term-major and module-major batches are different).
        while len(grouped) > 1:
            merged = _merge(grouped[-2], grouped[-1])
            if merged is None:
                break
            grouped[-2:] = [merged]
    result = grouped[0]
    for item in grouped[1:]:
        result = exp.Union(this=result, expression=item, distinct=False,
                           by_name=node.args.get("by_name"))
    for key, value in node.args.items():
        if key not in {"this", "expression", "distinct", "by_name"} and value is not None:
            result.set(key, _batch(value) if isinstance(value, exp.Expression) else value)
    result.add_comments(node.comments or [])
    return result


def _prune_cte_padding(tree, schemas):
    """Drop all-NULL CTE fields only consumed by name-aligned star branches.

    Keep expressions used by filters and positional clauses. Verify every affected
    union's public shape before accepting a removal; other branches must supply
    the missing field in the same position.
    """
    for cte in tree.find_all(exp.CTE):
        select = cte.this
        if not isinstance(select, exp.Select) or any(select.args.get(k) for k in (
                "joins", "distinct", "group", "having", "qualify", *MODIFIERS)):
            continue
        nulls = {item.alias_or_name.lower() for item in select.expressions if _null(item)}
        if not nulls or any(c.name.lower() in nulls for c in select.find_all(exp.Column)):
            continue
        references = [t for t in tree.find_all(exp.Table) if t.name.lower() == cte.alias_or_name.lower()]
        unions = []
        for table in references:
            consumer = table.parent.parent if isinstance(table.parent, exp.From) else None
            if (not isinstance(consumer, exp.Select) or consumer.args.get("joins")
                    or len(consumer.expressions) != 1 or not isinstance(consumer.expressions[0], exp.Star)
                    or any(consumer.args.get(k) for k in MODIFIERS | {"group", "having", "qualify"})
                    or any(c.name.lower() in nulls for c in consumer.find_all(exp.Column))
                    or not isinstance(consumer.parent, exp.Union) or not consumer.parent.args.get("by_name")):
                break
            union = consumer.parent
            while isinstance(union.parent, exp.Union) and union.parent.args.get("by_name"):
                union = union.parent
            if union not in unions:
                unions.append(union)
        else:
            if not references:
                continue
            resolver = Columns(tree, schemas, allow_unnamed=True)
            before = [resolver.query(u) for u in unions]
            original = select.expressions
            kept = [e for e in original if e.alias_or_name.lower() not in nulls]
            if not kept:
                continue
            select.set("expressions", kept)
            resolver = Columns(tree, schemas, allow_unnamed=True)
            if [resolver.query(u) for u in unions] != before:
                select.set("expressions", original)


def simplify_sql(sql):
    from .query_dsl import RecordSQLite, FUNCTION_SCHEMAS, SCHEMAS

    trees = sqlglot.parse(sql, read=RecordSQLite)
    if len(trees) != 1 or not isinstance(trees[0], (exp.Select, exp.SetOperation)):
        raise ValueError("simplification requires one SELECT query")
    tree = trees[0]
    schemas = {**SCHEMAS, **FUNCTION_SCHEMAS}

    def columns():
        return Columns(tree, schemas, allow_unnamed=True)

    # Give positional UNION branches their actual inherited output names before
    # dropping placeholders. Aliases such as excerpt and literal zero offsets stay.
    def rename(node, names):
        if isinstance(node, exp.Union):
            rename(node.this, names)
            rename(node.expression, names)
        elif isinstance(node, exp.Select):
            expressions = columns().expressions(node)
            if len(expressions) != len(names):
                raise ValueError("cannot simplify an invalid positional UNION")
            replacements = []
            for item, name in zip(expressions, names):
                if item.alias_or_name != name:
                    item = exp.alias_(item.this if isinstance(item, exp.Alias) else item, name, quoted=True)
                replacements.append(item)
            node.set("expressions", replacements)
        else:
            current = columns().query(node)
            if current != names:
                raise ValueError("explicit aliases required in parenthesized positional UNION branch")

    for union in reversed(list(tree.find_all(exp.Union))):
        if not union.args.get("by_name"):
            names = columns().query(union.this)
            rename(union.this, names)
            rename(union.expression, names)

    roots = [n for n in tree.find_all(exp.Union) if not isinstance(n.parent, exp.Union)]
    for root in reversed(roots):
        original_names = columns().query(root)

        def leaves(node):
            return leaves(node.this) + leaves(node.expression) if isinstance(node, exp.Union) else [node]

        leaf_nodes = leaves(root)
        available = set()
        for leaf in leaf_nodes:
            if isinstance(leaf, exp.Select):
                available.update(item.alias_or_name.lower() for item in columns().expressions(leaf) if not _null(item))
        for leaf in leaf_nodes:
            if isinstance(leaf, exp.Select):
                expressions = columns().expressions(leaf)
                kept = [item for item in expressions if not _null(item) or item.alias_or_name.lower() not in available]
                if kept:
                    leaf.set("expressions", kept)
        # Only this set-operation tree: nested CTEs/subqueries have their own roots.
        def mark(node):
            if isinstance(node, exp.Union):
                node.set("by_name", True)
                mark(node.this)
                mark(node.expression)
        mark(root)
        if columns().query(root) != original_names:
            # Restore public column order, including positional ORDER BY meaning.
            inner = root.copy()
            moved = {}
            for key in MODIFIERS:
                if root.args.get(key) is not None:
                    moved[key] = root.args[key].copy()
                    inner.set(key, None)
            wrapper = exp.select(*[exp.column(name, quoted=True) for name in original_names]).from_(inner.subquery())
            for key, value in moved.items():
                wrapper.set(key, value)
            if root is tree:
                tree = wrapper
            else:
                root.replace(wrapper)

    tree = _batch(tree)
    # CTE output lists already define their columns. Avoid restating them at every
    # consumer, but never turn a narrower element selection into SELECT *.
    resolver = columns()
    for select in tree.find_all(exp.Select):
        origin = select.args.get("from") or select.args.get("from_")
        if not origin or select.args.get("joins"):
            continue
        if not all(isinstance(item, exp.Column) and not item.table and not item.is_star for item in select.expressions):
            continue
        if [item.name for item in select.expressions] == resolver.source(origin.this):
            select.set("expressions", [exp.Star()])

    _prune_cte_padding(tree, schemas)

    # Preserve the original purpose as an English line comment; sqlglot retains
    # other CTE/branch annotations next to their corresponding expressions.
    purpose = re.match(r"\s*(--\s*Purpose:[^\n]*)", sql, flags=re.I)
    heading = purpose.group(1) if purpose else "-- Purpose: retrieve the same targeted evidence using compact SQL."
    for node in tree.walk():
        if node.comments:
            node.comments[:] = [c for c in node.comments if c.strip() != heading[2:].strip()]
    class CompactGenerator(RecordSQLite.Generator):
        def expressions(self, expression=None, *args, **kwargs):
            if isinstance(expression, exp.Select) and kwargs.get("key") in (None, "expressions"):
                kwargs["flat"] = True
            return super().expressions(expression, *args, **kwargs)

    rendered = CompactGenerator(dialect=RecordSQLite, pretty=True, comments=True, max_text_width=120).generate(tree)
    return heading + "\n" + rendered + ";\n"
