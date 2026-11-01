"""Bounded evidence retrieval, typed provenance graphs, and shared trace windows."""
from __future__ import annotations

import copy
from collections import defaultdict, deque
from dataclasses import replace

from .record_bridge import digest, plain


class ToolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class BudgetExceeded(ToolError):
    def __init__(self, message: str = "run budget exhausted"):
        super().__init__("budget_exhausted", message)


def integer(value, name, lo=0, hi=None):
    if type(value) is not int or value < lo or (hi is not None and value > hi):
        raise ToolError("invalid_arguments", f"{name} must be an integer in {lo}..{hi}")
    return value


class _VisibleFacts:
    """Iteration is scoped; integer indexing retains original Fact IDs."""
    def __init__(self, facts, count):
        self.facts, self.count = facts, count

    def __iter__(self):
        return iter(self.facts.values())

    def __len__(self):
        return self.count

    def __getitem__(self, fid):
        if type(fid) is not int or fid not in self.facts:
            raise ToolError("out_of_scope", "Fact is outside this read scope")
        return self.facts[fid]


class EvidenceIndex:
    def __init__(self, bundle):
        self.record = bundle.record
        self.source = bundle.source
        self.facts = {f.fid: f for f in bundle.projection.F}
        self.evidence = {e.eid: plain(e) for g in bundle.projection.evidences.values() for e in g.items}
        self.evaluations = bundle.evaluations
        self.length = len(bundle.source["steps"])
        self.fingerprint = bundle.fingerprint
        self.requirements = {str(g.entity()) for g in self.record.goals}
        self.edges = []
        self.neighbours = defaultdict(list)
        for f in self.facts.values():
            for basis in f.basis:
                self._edge(f"fact:{basis}", f"fact:{f.fid}", "basis")
            if f.situation is not None:
                self._edge(f"fact:{f.situation}", f"fact:{f.fid}", "situation")
            if f.purpose:
                self._edge(f"fact:{f.fid}", f"entity:{f.purpose}", "purpose")
        for entity, intention in self.record.intentions.items():
            if intention.purpose:
                self._edge(f"entity:{entity}", f"entity:{intention.purpose}", "purpose")
        chains = defaultdict(list)
        for f in self.facts.values():
            rel = self.record.relations[f.rel]
            chains[(f.rel, tuple(str(f.args[i]) for i in rel.key))].append(f)
        for chain in chains.values():
            for a, b in zip(sorted(chain, key=lambda f: (f.t, f.fid)),
                            sorted(chain, key=lambda f: (f.t, f.fid))[1:]):
                self._edge(f"fact:{a.fid}", f"fact:{b.fid}", "version")

    def _edge(self, source, target, kind):
        edge = {"source": source, "target": target, "type": kind,
                "causal_verdict": False}
        self.edges.append(edge)
        self.neighbours[source].append(edge)
        self.neighbours[target].append(edge)

    def scope(self, scope):
        if not isinstance(scope, dict) or scope.get("mode") not in {"local", "global"}:
            raise ToolError("invalid_arguments", "scope needs mode=local or global")
        if scope["mode"] == "local":
            return {"mode": "local", "step": integer(scope.get("step"), "step", 1, self.length)}
        return {"mode": "global"}

    def fact(self, fid, scope=None):
        integer(fid, "fid")
        if fid not in self.facts:
            raise ToolError("invalid_id", "unknown original Fact ID")
        f = self.facts[fid]
        if scope and scope["mode"] == "local":
            t = scope["step"]
            if f.t > t and not (f.t == t + 1 and f.side == "env" and f.slot == "obs"):
                raise ToolError("out_of_scope", "Fact exceeds local prefix plus next environment feedback")
        return f

    def scoped_record(self, scope):
        scope = self.scope(scope)
        result = copy.copy(self.record)
        result.E = dict(self.record.E)
        visible = {}
        for fid in self.facts:
            try:
                visible[fid] = self.fact(fid, scope)
            except ToolError:
                pass
        result.F = _VisibleFacts(visible, len(self.facts))
        for name in list(result.__dict__):
            if name.startswith("_") and name not in {"_chain", "_by_entity", "_by_step", "_by_rel"}:
                del result.__dict__[name]
        for name in ("_chain", "_by_entity", "_by_step", "_by_rel"):
            setattr(result, name, {key: [fid for fid in ids if fid in visible]
                                  for key, ids in getattr(self.record, name).items()})
        result.process_notes = {}
        if scope["mode"] == "local":
            t = scope["step"]
            result.source_steps = {s: v for s, v in self.record.source_steps.items() if s <= t}
            from .session_sources import message_event
            # The next session event may be an assistant/user message. It is
            # not the environment feedback permitted by the original local
            # prefix scope, so never expose its raw text as next observation.
            if t + 1 in self.record.source_steps and not message_event(self.source, t + 1):
                nxt = self.record.source_steps[t + 1]
                result.source_steps[t + 1] = replace(nxt, agent="", modules={}, admissible=())
            mentioned = {str(e) for f in visible.values() for e in f.args}
            mentioned.update(str(f.purpose) for f in visible.values() if f.purpose)
            result.intentions = {k: v for k, v in self.record.intentions.items() if k in mentioned}
            task_entities = {str(g.entity()) for g in self.record.goals}
            task_entities.update(str(g.target) for g in self.record.goals if g.target)
            result.E = {k: v for k, v in self.record.E.items() if k in mentioned | task_entities}
        return result

    def goal_links(self, fids):
        links = set()
        for fid in fids:
            for entity in self.record.purpose_chain(self.fact(fid).purpose):
                if str(entity) in self.requirements:
                    links.add(str(entity))
            links.update(str(a) for a in self.fact(fid).args if str(a) in self.requirements)
        return sorted(links)

    def view(self, *, scope, eid=None, kind=None, rule=None, status=None, steps=None,
             requirement=None, include_refs=True, cursor=None, limit=20):
        scope = self.scope(scope)
        integer(limit, "limit", 1, 100)
        offset = integer(0 if cursor is None else cursor, "cursor")
        if requirement and requirement not in self.requirements:
            raise ToolError("invalid_requirement", "unknown goal")
        if eid is not None:
            integer(eid, "eid")
            if any(x is not None for x in (kind, rule, status, steps, requirement, cursor)):
                raise ToolError("invalid_arguments", "eid lookup and list filters are mutually exclusive")
            if eid not in self.evidence:
                raise ToolError("invalid_id", "unknown Evidence ID")
            items = [self.evidence[eid]]
        else:
            items = sorted(self.evidence.values(), key=lambda e: e["eid"])
        if steps is not None:
            lo, hi = steps
            integer(lo, "steps.lo", 0, self.length); integer(hi, "steps.hi", lo, self.length)
        rows = []
        for e in items:
            if any(expected is not None and e[key] != expected for key, expected in
                   (("kind", kind), ("rule", rule), ("status", status))):
                continue
            if steps is not None and not lo <= e["t"] <= hi:
                continue
            if requirement and requirement not in self.goal_links(e["refs"]):
                continue
            try:
                facts = [self.fact(fid, scope) for fid in e["refs"]]
            except ToolError:
                if eid is not None:
                    raise
                continue
            rows.append({**e, "evaluation_state": "violated", "ref_steps": sorted({f.t for f in facts}),
                         "requirement_links": self.goal_links(e["refs"]), "link_source": "recorded",
                         "facts": plain(facts) if include_refs else []})
        page = rows[offset:offset + limit]
        # Some rule families have no applicable source step (t=None). Keep those
        # in the global audit, but do not invent a local position for them.
        evaluations = [e for e in self.evaluations
                       if scope["mode"] == "global"
                       or (type(e.get("t")) is int and e["t"] <= scope["step"])]
        return {"items": page, "total": len(rows), "next_cursor": offset + limit
                if offset + limit < len(rows) else None,
                "evaluation_counts": {state: sum(e["state"] == state for e in evaluations)
                                      for state in ("satisfied", "violated", "unknown", "not_applicable")}}

    def trace(self, starts, *, scope, direction="backward", edge_types=None, depth=3,
              max_nodes=80, steps=None):
        scope = self.scope(scope)
        integer(depth, "depth", 0, 8)
        if max_nodes is not None:
            integer(max_nodes, "max_nodes", 1, 500)
        if direction not in {"backward", "forward", "goal"}:
            raise ToolError("invalid_arguments", "invalid trace direction")
        kinds = set(edge_types if edge_types is not None else ["basis", "situation", "purpose"])
        if not kinds or kinds - {"basis", "situation", "purpose", "version", "context"}:
            raise ToolError("invalid_arguments", "invalid edge_types")
        if steps is not None:
            integer(steps[0], "steps.lo", 0, self.length)
            integer(steps[1], "steps.hi", steps[0], self.length)
        for fid in starts:
            f = self.fact(fid, scope)
            if steps and not steps[0] <= f.t <= steps[1]:
                raise ToolError("out_of_scope", "start Fact outside requested steps")
        queue = deque((f"fact:{fid}", 0) for fid in dict.fromkeys(starts))
        nodes, edges, missing = {}, {}, []
        truncated = False
        while queue:
            node, d = queue.popleft()
            if node in nodes:
                continue
            if max_nodes is not None and len(nodes) >= max_nodes:
                truncated = True
                break
            if node.startswith("fact:"):
                try:
                    f = self.fact(int(node[5:]), scope)
                    if steps and not steps[0] <= f.t <= steps[1]:
                        raise ToolError("out_of_scope", "window boundary")
                    nodes[node] = {"id": node, "type": "fact", **plain(f)}
                except ToolError:
                    missing.append({"id": node, "reason": "scope_or_window_boundary"})
                    continue
            else:
                nodes[node] = {"id": node, "type": "entity", "entity": node[7:]}
            if d >= depth:
                if self.neighbours[node]:
                    truncated = True
                continue
            candidates = list(self.neighbours[node])
            if "context" in kinds and node.startswith("fact:"):
                f = self.fact(int(node[5:]))
                candidates += [{"source": node, "target": f"fact:{other.fid}", "type": "context",
                                "causal_verdict": False} for other in self.facts.values()
                               if abs(other.t - f.t) <= 1 and other.fid != f.fid]
            for edge in candidates:
                if edge["type"] not in kinds:
                    continue
                other = edge["target"] if node == edge["source"] else edge["source"]
                if edge["type"] != "context":
                    toward_goal = edge["type"] == "purpose" and node == edge["source"]
                    if direction == "goal" and not toward_goal:
                        continue
                    if direction == "backward" and not (node == edge["target"] or toward_goal):
                        continue
                    if direction == "forward" and node != edge["source"]:
                        continue
                edges[(edge["source"], edge["target"], edge["type"])] = edge
                queue.append((other, d + 1))
        kept = [e for e in edges.values() if e["source"] in nodes and e["target"] in nodes]
        return {"start_refs": starts, "nodes": list(nodes.values()), "edges": kept,
                "retrieved_steps": sorted({n["t"] for n in nodes.values() if n["type"] == "fact"}),
                "goal_links": self.goal_links(starts), "missing_links": missing,
                "truncated": truncated or bool(missing), "reason": "limits_or_scope" if truncated or missing else None,
                "limits": {"depth": depth, "max_nodes": max_nodes}}


class TraceWindows:
    def __init__(self, index):
        self.index, self.version = index, 0
        self.windows = []

    def configure(self, candidates, W):
        integer(W, "W", 1)
        if not isinstance(candidates, list) or not candidates:
            raise ToolError("invalid_arguments", "candidates must be a nonempty list")
        windows, ids = [], set()
        for item in candidates:
            if not isinstance(item, dict):
                raise ToolError("invalid_arguments", "each candidate must be an object")
            cid = item.get("candidate_id")
            if not isinstance(cid, str) or not cid.strip() or cid in ids:
                raise ToolError("invalid_arguments", "candidate_id must be unique and nonempty")
            t = integer(item.get("issue_step"), "issue_step", 1, self.index.length)
            fids = item.get("issue_fids", [])
            if not isinstance(fids, list):
                raise ToolError("invalid_arguments", "issue_fids must be a list")
            for fid in fids:
                if self.index.fact(fid).t != t:
                    raise ToolError("invalid_arguments", "issue_fids must belong to issue_step")
            goals = item.get("goal_links", [])
            if (not isinstance(goals, list) or any(not isinstance(g, str) for g in goals)
                    or set(goals) - self.index.requirements):
                raise ToolError("invalid_requirement", "invalid candidate goals")
            ids.add(cid)
            windows.append({**item, "W": W, "lo": max(1, t - W + 1), "hi": t})
        if digest(windows) != digest(self.windows):
            self.windows, self.version = windows, self.version + 1
        return self.windows

    def members(self, step):
        return [w["candidate_id"] for w in self.windows if w["lo"] <= step <= w["hi"]]
