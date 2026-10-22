from pathlib import Path
import copy
import json
import unittest

from toollery.tooltalk import conversation_turns, load_runtime, schemas_for, score_turn

SOURCE = Path(__file__).resolve().parents[1] / 'upstream/ToolTalk'


@unittest.skipUnless((SOURCE / 'src').exists(), 'Optional ToolTalk source not downloaded')
class ToolTalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apis, executor = load_runtime(SOURCE)
        cls.Executor = staticmethod(executor)

    def test_schema_export_has_only_tool_information(self):
        schemas = schemas_for(self.apis)
        self.assertEqual(len(schemas), 28)
        for schema in schemas:
            self.assertTrue(set(schema['parameters']['required']) <= set(schema['parameters']['properties']))
            self.assertNotIn('session_token', schema['parameters']['properties'])

    def test_no_current_gold_leaks_into_history_and_gold_self_scoring(self):
        count = 0
        for split in ('easy', 'tooltalk'):
            for path in (SOURCE / 'data' / split).glob('*.json'):
                conversation = json.loads(path.read_text(encoding='utf-8'))
                expected_history, expected_apis = [], []
                for turn in conversation_turns(conversation):
                    self.assertEqual(turn['history'], expected_history)
                    self.assertEqual(turn['api_history'], expected_apis)
                    executor = self.Executor(init_database_dir=str(SOURCE / 'data/databases'))
                    executor.init_conversation_state(conversation['metadata'], copy.deepcopy(expected_apis), conversation.get('user', {}))
                    metrics = score_turn(executor, turn['gold'], [{'role': 'api', **g} for g in turn['gold']])
                    self.assertTrue(metrics['success'], path.name)
                    expected_history.append({'role': 'user', 'text': turn['query']})
                    for api in turn['gold']:
                        expected_apis.append(copy.deepcopy(api))
                        expected_history.append({'role': 'api', **copy.deepcopy(api)})
                    original = conversation['conversation'][turn['turn_index']]
                    expected_history.append({'role': 'assistant', 'text': original['text']})
                    count += 1
        self.assertGreater(count, 100)

    def test_invalid_action_not_ignored(self):
        executor = self.Executor(init_database_dir=str(SOURCE / 'data/databases'))
        prediction = {'role': 'api', 'request': {'api_name': 'SendEmail', 'parameters': {}},
                      'response': None, 'exception': 'invalid_arguments'}
        gold = {'request': {'api_name': 'SendEmail', 'parameters': {}}, 'response': None, 'exception': None}
        score = score_turn(executor, [gold], [prediction])
        self.assertEqual(score['predictions'], 1)
        self.assertEqual(score['matches'], 0)
        self.assertFalse(score['success'])
        self.assertFalse(score_turn(executor, [], [{**prediction, 'exception': None}])['success'])


if __name__ == '__main__':
    unittest.main()
