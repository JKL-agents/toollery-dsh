"""Small ToolTalk adapter; upstream execution and per-API checks stay upstream.

The explicitly named strict scorer replaces only the heavyweight sent2vec text
comparator. It is NOT the official semantic ToolTalk score. No benchmark labels
are used when exporting schemas or generating manuals.
"""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import re
import sys
import types


SCORER = 'upstream_api_checks_with_exact_free_text_v1'


def load_runtime(root: Path):
    source = str(root / 'src')
    if source not in sys.path:
        sys.path.insert(0, source)
    # Upstream utils eagerly imports numpy and sent2vec although execution only
    # needs two format validators. Supply an explicit lightweight scoring mode,
    # rather than silently skipping failed semantic comparisons.
    if 'tooltalk.apis.utils' not in sys.modules:
        utils = types.ModuleType('tooltalk.apis.utils')
        utils.verify_phone_format = lambda value: re.match(r'^\d{3}-\d{3}-\d{4}$', value) is not None
        utils.verify_email_format = lambda value: re.match(r'^[\w\-\d\.]+@[\w\-\d\.]+$', value) is not None
        utils.semantic_str_compare = lambda left, right: float(left is not None and left == right)
        utils.TOOLLERY_SCORER = SCORER
        sys.modules[utils.__name__] = utils
    elif getattr(sys.modules['tooltalk.apis.utils'], 'TOOLLERY_SCORER', None) != SCORER:
        raise RuntimeError('ToolTalk scorer already initialized with a different policy')
    apis = importlib.import_module('tooltalk.apis')
    executor = importlib.import_module('tooltalk.evaluation.tool_executor').ToolExecutor

    class SafeExecutor(executor):
        # Failed/invalid model calls sometimes lack fields assumed by upstream
        # custom checkers. Count those as unmatched rather than losing a turn.
        def compare_api_calls(self, prediction, ground_truth):
            try:
                return super().compare_api_calls(prediction, ground_truth)
            except (KeyError, TypeError, IndexError, ValueError):
                self.checker_errors = getattr(self, 'checker_errors', 0) + 1
                return False

    return apis.ALL_APIS, SafeExecutor


def schemas_for(apis):
    result = []
    for api in apis:
        properties, required = {}, []
        for name, attributes in api.parameters.items():
            attributes = copy.deepcopy(attributes)
            if attributes.pop('required', False):
                required.append(name)
            properties[name] = attributes
        result.append({'name': api.__name__, 'description': api.description,
                       'parameters': {'type': 'object', 'properties': properties, 'required': required}})
    return sorted(result, key=lambda row: row['name'])


def conversation_turns(conversation):
    """Gold *prior* context; current gold calls are returned only for scoring."""
    history, api_history = [], []
    current_query = None
    for index, turn in enumerate(conversation['conversation']):
        if turn['role'] == 'user':
            current_query = turn['text']
            continue
        if turn['role'] != 'assistant' or current_query is None:
            raise ValueError('Expected alternating ToolTalk user/assistant turns')
        yield {'turn_index': index, 'query': current_query,
               'history': copy.deepcopy(history), 'api_history': copy.deepcopy(api_history),
               'gold': copy.deepcopy(turn.get('apis', []))}
        history.append({'role': 'user', 'text': current_query})
        for api in turn.get('apis', []):
            api_history.append(copy.deepcopy(api))
            history.append({'role': 'api', **copy.deepcopy(api)})
        history.append({'role': 'assistant', 'text': turn['text']})
        current_query = None


def score_turn(executor, gold, predictions):
    sample = {'conversation': [{'role': 'assistant', 'apis': copy.deepcopy(gold),
                               'predictions': copy.deepcopy(predictions)}]}
    # Upstream divides by zero for clarification-only turns. Keep its matching
    # and bad-action checks; define empty-gold recall as 1 separately.
    if gold:
        result = executor.evaluate_predictions(sample)['metrics']
    else:
        actions = [p for p in predictions if executor.is_action(p['request']['api_name'])]
        bad = sum(p['exception'] is None for p in actions)
        result = {'ground_truths': 0, 'predictions': len(predictions), 'matches': 0,
                  'actions': len(actions), 'valid_actions': 0, 'bad_actions': bad,
                  'precision': 0.0, 'recall': 1.0, 'bad_action_rate': bad / len(actions) if actions else 0,
                  'success': bad == 0}
    return {'scorer': SCORER, **result}
