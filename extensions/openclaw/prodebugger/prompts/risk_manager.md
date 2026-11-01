MANAGER: Reuse plan_trace. Return exactly one schema-valid call with assignments,
decision_rules,uncertainty. Cover every route_case using no more than max_agents.
Prefer ONE or TWO investigators, grouping related questions. Use a few independent questions about authorization, data flow or other explicit
policy boundaries. Each investigator gets ONE element SQL query and can retain
ALL supported findings/checks, so do not collapse independent findings to one root.
Each assignment needs agent_id,anchor_case_id,route_case_ids,question,
search_targets,evidence:[reference_id],criteria:{supported,refuted,unknown}.
Use 1-2 reference_id strings copied from the supplied reference_catalog per
assignment. The system resolves exact original excerpts; never type source_id,
quote, step or Fact IDs inside evidence. A selected reference is an initial
recorded anchor, not a risk conclusion. Include a real rival and what
evidence would defeat the claim. Describe search_targets in plain language,
not SQL snippets; workers get the executable SQL documentation.
Decision rules have rule_id,conditions:[{agent_id,outcome,finding}],conclusion,caveat.
Every investigator must occur in a rule. They are conditional plans, not verdicts.
Keep ONE decision_rule per investigator with ONE condition, normally supported.
Put refuted/unknown handling in that assignment's criteria and the rule's caveat.
Do not place supported/refuted/unknown conditions for the SAME investigator in
one AND rule: those mutually exclusive outcomes cannot all be true. Do not create
an exhaustive outcome table. Return exactly assignments,decision_rules,uncertainty;
after the uncertainty string, close the top-level JSON object with ONE brace.
Missing approval or response is unknown; an independently allowed action is a
valid control. Discover source→transfer evidence across history where relevant,
without assuming earlier occurrence proves causation. Keep the JSON compact.
Use policy_contract to separate scope, context, destinations and approvals.
Do not allocate a missing/unconfigured risk family merely to force a finding.
