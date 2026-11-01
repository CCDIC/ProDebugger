"""Regression checks for source-scope errors, including real contradictions."""
import unittest

from prodebugger.record_bridge import build_bundle, plain, compiler, PROFILES
from prodebugger.debug_tools import DebugTools
from prodebugger.query_dsl import execute
from criterion import kappa
from record import Entity


def bundle(mem='', plan='I should look.', action='look', env=None, task_type='alfworld', admissible=None):
    env = env or ['You arrive at cabinet 1.', 'You arrive at cabinet 2.']
    agent = ['<plan>I should look.</plan><action>look</action>'] * (len(env)-1)
    agent += [f'<memory>{mem}</memory><plan>{plan}</plan><action>{action}</action>']
    task = ('put a apple on diningtable.' if task_type == 'alfworld' else
            'Find me shirts with color: red, and price lower than 50 dollars')
    return build_bundle({'trajectory_id': 'scope-fixture', 'task_type': task_type, 'task': task,
                         'steps': plain(compiler.segment(env, agent, admissible or [['look']] * len(env)))})


class SemanticScopeChecks(unittest.TestCase):
    def test_quoted_tags_cannot_capture_the_actual_action_slot(self):
        raw = ("<memory>I recall the text '<action>'.</memory>"
               "<reflection>The old output was 'l. </plan> <action> inventory'.</reflection>"
               "<plan>I should go to toilet 1.</plan>\n<action>go to toilet 1</action>")
        step = compiler.segment(['Nothing happens.'], [raw], [['go to toilet 1']])[0]
        self.assertEqual(step.parts['action'], 'go to toilet 1')
        self.assertIn('</plan> <action> inventory', step.parts['reflection'])
        self.assertEqual(step.parts['plan'], 'I should go to toilet 1.')
        raw = '<memory>Earlier action was /plan> <action> open drawer 2.</memory>\n<action>open drawer 2'
        step = compiler.segment(['Nothing happens.'], [raw], [['open drawer 2']])[0]
        self.assertEqual(step.parts['action'], 'open drawer 2')

    def test_historical_location_is_checked_at_its_event_time(self):
        r = bundle('Step 1: You arrive at cabinet 1.').record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(r.claim_scopes[f.fid]['event_step'], 1)
        self.assertEqual(kappa(r, f).value, 'sat')
        self.assertNotEqual(r.in_force('at', f.args, 2).slot, 'mem')
        # A genuinely false historical claim must still be detected.
        r = bundle('Step 1: You arrive at cabinet 3.').record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(kappa(r, f).value, 'viol')

    def test_current_positive_and_negative_assertions(self):
        for memory, expected, value in [('I am at cabinet 1.', 'viol', True),
                                         ('I am at cabinet 2.', 'sat', True),
                                         ('I am not at cabinet 2.', 'viol', False),
                                         ('I am not at cabinet 1.', 'sat', False)]:
            with self.subTest(memory=memory):
                r = bundle(memory).record
                f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
                self.assertIs(f.value, value)
                self.assertEqual(kappa(r, f).value, expected)

    def test_historical_action_outcome_is_not_forced_to_pre_action_time(self):
        r = bundle('Step 1: I arrived at cabinet 2.').record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(kappa(r, f).value, 'unk')
        r = bundle('Observation 1: I arrived at cabinet 2.').record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(kappa(r, f).value, 'viol')

    def test_attempts_and_modal_states_do_not_become_world_claims(self):
        for text in ['I attempted to go to shelf 2 but did not achieve a successful navigation.',
                     'Step 1: Action: go to cabinet 1.', 'I might be at cabinet 1.',
                     'I tried holding apple 1.', 'I should be carrying apple 1.',
                     'I am looking at cabinet 1.']:
            with self.subTest(text=text):
                self.assertEqual(PROFILES['alfworld'].parse_memory(text), [])

    def test_unknown_time_and_observation_gap_remain_unknown(self):
        r = bundle('I was at cabinet 1.').record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(kappa(r, f).value, 'unk')
        r = bundle('I am at cabinet 2.', env=['You arrive at cabinet 1.', 'It might be elsewhere.']).record
        f = next(f for f in r.F if f.slot == 'mem' and f.rel == 'at')
        self.assertEqual(kappa(r, f).value, 'unk')

    def test_noun_adjective_and_navigation_do_not_invent_actions(self):
        alf = PROFILES['alfworld']
        for text, verb in [
            ('A good place to start would be to go to the countertop or the shelf.', 'go'),
            ('I should move towards the shelves.', 'go'),
            ('The task is to find a cool tomato. The first step is to locate a tomato.', None),
            ("I should try to 'look' again, as the previous attempts to go to specific objects failed.", 'look'),
        ]:
            with self.subTest(text=text):
                self.assertEqual(alf.parse_intention(text, ['look'])['rel'], verb)

    def test_explicit_first_step_overrides_tentative_goal_action(self):
        plan = ('The agent should now take the candle from the countertop. '
                "However, the admissible actions do not include 'take candle 1', "
                'so the agent should first examine the countertop.')
        self.assertEqual(PROFILES['alfworld'].parse_intention(plan, ['look'])['rel'], 'look')

    def test_true_plan_action_disagreement_remains_violation(self):
        r = bundle(plan='I should open cabinet 2.', action='go to cabinet 1').record
        action = next(f for f in r.F if f.t == 2 and f.slot == 'act')
        self.assertEqual(kappa(r, action).value, 'viol')
        r = bundle(plan='I should go to cabinet 2.', action='go to cabinet 1').record
        action = next(f for f in r.F if f.t == 2 and f.slot == 'act')
        self.assertEqual(kappa(r, action).value, 'viol')

    def test_unbound_target_does_not_rule_out_a_navigation_prerequisite(self):
        r = bundle(plan='I should examine the drawers.', action='go to drawer 1').record
        action = next(f for f in r.F if f.t == 2 and f.slot == 'act')
        self.assertEqual(kappa(r, action).value, 'unk')

    def test_named_target_navigation_prerequisite_is_grounded_in_controls(self):
        r = bundle(plan='I should open cabinet 2.', action='go to cabinet 2',
                   admissible=[['look'], ['look', 'go to cabinet 2']]).record
        action = next(f for f in r.F if f.t == 2 and f.slot == 'act')
        self.assertEqual(kappa(r, action).value, 'sat')

    def test_unbound_plans_and_repaired_retries_are_not_proven_failures(self):
        for plan in ['I should explore another area.', 'I should go to cabinet 2.']:
            env = ['You arrive at cabinet 1.', 'Nothing happens.']
            agent = [f'<plan>{plan}</plan><action>\'go to cabinet 2\'</action>',
                     f'<plan>{plan}</plan><action>go to cabinet 2</action>']
            source = {'trajectory_id': 'retry-fixture', 'task_type': 'alfworld',
                      'task': 'put a apple on diningtable.',
                      'steps': plain(compiler.segment(env, agent, [['go to cabinet 2']] * 2))}
            r = build_bundle(source).record
            f = next(f for f in r.F if f.slot == 'plan' and f.t == 2)
            self.assertEqual(kappa(r, f).value, 'unk')
            # The original malformed command is still independently flagged.
            self.assertEqual(next(f.value for f in r.F if f.rel == 'admissibility' and f.t == 1), 'quoted')

    def test_webshop_negation_and_historical_background_do_not_bind_controls(self):
        web = PROFILES['webshop']; adm = ['click[next >]', 'click[< prev]', 'click[back to search]']
        for plan in [
            'Navigating to the next page may not be the most effective approach. A better approach might be to revisit the search query.',
            "- Given the lack of results on previous pages, we need a new query.\n- Clicking 'back to search' will allow me to refine it.",
        ]:
            with self.subTest(plan=plan):
                parsed = web.parse_intention(plan, adm)
                self.assertEqual(parsed['args'], [('page', 'back_to_search')])

    def test_click_target_binds_to_command_not_product_description(self):
        web = PROFILES['webshop']
        plan = ('The Windshield Kit might match. Since the product is a Windshield Kit, '
                'clicking on "description" or "features" could provide more details.')
        p = web.parse_intention(plan, ['click[windshield kit]', 'click[description]', 'click[features]'])
        self.assertEqual(p['args'], [('page', 'description')])
        self.assertIn([('page', 'features')], [x['args'] for x in p['execution_options']])

    def test_quoted_controls_and_search_results_have_precise_navigation(self):
        web = PROFILES['webshop']
        p = web.parse_intention('The next step should be to click on "click[< prev]" to go back to Page 2.',
                                ['click[< prev]', 'click[next >]', 'click[back to search]'])
        self.assertEqual(p['args'], [('page', '<_prev')])
        p = web.parse_intention('It would be helpful to go back to the search results page to review the products.',
                                ['click[< prev]', 'click[description]', 'click[back to search]'])
        self.assertEqual(p['args'], [('page', '<_prev')])
        p = web.parse_intention('I should proceed to Page 2.', ['click[< prev]', 'click[next >]'])
        self.assertIsNone(p['args'])

    def test_webshop_requires_explicit_selection_and_word_boundaries(self):
        web = PROFILES['webshop']; known = {'color': ['red', 'navy'], 'size': ['small']}
        for text in ['None confirm the required materials.', 'They lack the desired attributes.',
                     'The user requires navy and small.', 'Red is available. Selecting it would finish the task.',
                     'I have not selected red.', 'The query was navy small.', 'This is a navy shirt.']:
            with self.subTest(text=text):
                self.assertFalse(any(f['rel'] == 'chosen' for f in web.parse_memory(text, known)))
        fs = web.parse_memory('Currently, the selected color is red.', known)
        self.assertEqual(len(fs), 1)
        self.assertEqual(fs[0]['args'], [('attribute', 'color'), ('value', 'red')])
        self.assertEqual(fs[0]['claim_scope']['temporal'], 'current')

    def test_element_queries_expose_event_time(self):
        b = bundle('Step 1: You arrive at cabinet 1.')
        result = execute(DebugTools(b), "SELECT step,assertion_time,claim_scope FROM TRACK_ENTITY('cabinet:1') WHERE slot='mem'")
        self.assertNotIn('error', result)
        self.assertEqual(result['rows'], [{'step': 2, 'assertion_time': 1, 'claim_scope': 'historical'}])

    def test_lamp_placement_and_power_are_programmatic_and_heat_means_hot(self):
        b = bundle(env=['You arrive at sidetable 1. On the sidetable 1, you see a desklamp 1.',
                        'You turn on the desklamp 1.'])
        self.assertTrue(all(a['status'] == 'template' for a in b.record.parse_audits))
        self.assertTrue(any(f.rel == 'in' and f.args[0] == Entity('desklamp', '1') for f in b.record.F))
        self.assertTrue(any(f.rel == 'has' and Entity('attribute','powered_on') in f.args for f in b.record.F))
        b = bundle(env=['You heat the apple 1 using the microwave 1.'])
        self.assertTrue(any(f.rel == 'has' and Entity('attribute','hot') in f.args for f in b.record.F))
        self.assertFalse(any(Entity('attribute','heat') in f.args for f in b.record.F))


if __name__ == '__main__':
    unittest.main()
