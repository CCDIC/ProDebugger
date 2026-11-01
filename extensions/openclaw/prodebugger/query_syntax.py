"""SQL column discovery and lowering of sparse UNION BY NAME for SQLite.

This is syntax translation only. The query executor still validates every source,
literal, function, provenance alias and SQLite operation after translation.
"""
from functools import reduce
from sqlglot import exp

BATCH_ARGUMENTS = {"SEARCH_TEXT": {0, 1}, "FIND_CLAIMS": {0, 1}, "TRACK_ACTION": {0}}


def schema_columns(schema):
    return [field.strip().split()[0] for field in schema.split(",")]


class Columns:
    def __init__(self, tree, tables, *, allow_unnamed=False):
        self.allow_unnamed = allow_unnamed
        self.tables = {name.lower(): schema_columns(schema) for name, schema in tables.items()}
        self.ctes = {}
        for cte in tree.find_all(exp.CTE):
            name = cte.alias_or_name.lower()
            if name in self.ctes:
                raise ValueError("UNION BY NAME requires unique CTE names; rename the shadowed CTE")
            self.ctes[name] = cte

    def source(self, node, visiting=()):
        if isinstance(node, exp.Table):
            if isinstance(node.this, exp.Anonymous):
                name = node.this.name.lower()
            else:
                name = node.name.lower()
            if name in self.tables:
                names = self.tables[name]
            elif name in self.ctes and name not in visiting:
                cte = self.ctes[name]
                names = cte.alias_column_names or self.query(cte.this, (*visiting, name))
            else:
                raise ValueError(f"cannot resolve columns of {name!r}; use a documented element source")
        elif isinstance(node, exp.Subquery):
            names = self.query(node.this, visiting)
        else:
            raise ValueError("UNION BY NAME requires named element/CTE/subquery columns")
        return node.alias_column_names or names

    def expressions(self, node, visiting=()):
        result = []
        sources = []
        origin = node.args.get("from") or node.args.get("from_")
        if origin:
            sources.append(origin.this)
        sources.extend(join.this for join in node.args.get("joins", []))
        for expression in node.expressions:
            if isinstance(expression, exp.Star) or isinstance(expression, exp.Column) and expression.is_star:
                star = expression.this if isinstance(expression, exp.Column) else expression
                if any(value for value in star.args.values()):
                    raise ValueError("star modifiers are not supported with UNION BY NAME; list fields explicitly")
                qualifier = expression.table if isinstance(expression, exp.Column) else None
                matches = [s for s in sources if not qualifier or s.alias_or_name == qualifier]
                if not matches:
                    raise ValueError("cannot expand star without a known element source")
                for source in matches:
                    result.extend(exp.column(name, table=qualifier, quoted=True)
                                  for name in self.source(source, visiting))
            else:
                result.append(expression.copy())
        return result

    def query(self, node, visiting=()):
        if isinstance(node, exp.Subquery):
            return self.query(node.this, visiting)
        if isinstance(node, exp.SetOperation):
            left = self.query(node.this, visiting)
            if node.args.get("by_name"):
                return left + [name for name in self.query(node.expression, visiting)
                               if name.lower() not in {n.lower() for n in left}]
            return left
        if not isinstance(node, exp.Select):
            raise ValueError("UNION BY NAME supports SELECT branches only")
        result = []
        for item in self.expressions(node, visiting):
            name = item.alias_or_name
            if not name and self.allow_unnamed:
                name = item.sql(dialect="sqlite", comments=False)
            if not name:
                raise ValueError("UNION BY NAME requires AS names for computed fields")
            result.append(name)
        return result


def lower_named_unions(tree, tables):
    """Align by names, retaining branch order, ALL/DISTINCT and compound modifiers."""
    resolver = Columns(tree, tables)
    modifiers = {"order", "limit", "offset", "with", "with_"}

    def lower(node):
        if isinstance(node, exp.Union) and node.args.get("by_name"):
            distinct = node.args.get("distinct")

            def arms(item, root=False):
                if (isinstance(item, exp.Union) and item.args.get("by_name")
                        and item.args.get("distinct") == distinct
                        and (root or not any(item.args.get(k) is not None for k in modifiers))):
                    return arms(item.this) + arms(item.expression)
                return [item]

            branches = arms(node, True)
            names = [resolver.query(branch) for branch in branches]
            target, seen = [], set()
            for columns in names:
                if len({name.lower() for name in columns}) != len(columns):
                    raise ValueError("UNION BY NAME requires unique field names in each branch")
                for name in columns:
                    if name.lower() not in seen:
                        target.append(name)
                        seen.add(name.lower())
            aligned = []
            for branch, columns in zip(branches, names):
                lowered = lower(branch)
                if isinstance(lowered, exp.Select) and not any(lowered.args.get(k) for k in modifiers):
                    available = dict(zip((n.lower() for n in columns), resolver.expressions(lowered)))
                    lowered.set("expressions", [available.get(name.lower(), exp.alias_(exp.Null(), name, quoted=True))
                                                for name in target])
                else:
                    # A branch's own ORDER BY/LIMIT must run before alignment.
                    available = {name.lower(): name for name in columns}
                    lowered = exp.select(*[
                        exp.column(available[name.lower()], quoted=True) if name.lower() in available
                        else exp.alias_(exp.Null(), name, quoted=True) for name in target
                    ]).from_(lowered.subquery())
                aligned.append(lowered)
            result = reduce(lambda a, b: exp.Union(this=a, expression=b, distinct=distinct), aligned)
            for key, value in node.args.items():
                if key not in {"this", "expression", "distinct", "by_name"} and value is not None:
                    result.set(key, lower(value) if isinstance(value, exp.Expression) else value)
            return result
        result = node.copy()
        for key, value in node.args.items():
            if isinstance(value, exp.Expression):
                result.set(key, lower(value))
            elif isinstance(value, list):
                result.set(key, [lower(item) if isinstance(item, exp.Expression) else item for item in value])
        return result

    return lower(tree)
