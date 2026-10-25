import tempfile
import unittest
from pathlib import Path

from toollery.generation import MockGenerator
from toollery.manuals import ManualCache, PreparationError
from toollery.retrieval import build_skill_documents
from toollery.skill_manuals import prepare_skill_manuals, skill_cache_preflight


class SkillManualTests(unittest.TestCase):
    def candidates(self, body='Record decisions, owners, deadlines, and action items.'):
        return [{
            'name': 'meeting-minutes',
            'description': 'Capture decisions and action items',
            'content': body,
            'tags': ['meeting', 'notes'],
        }]

    def test_body_manual_cache_is_provenance_linked_and_invalidates_on_body_change(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ManualCache(Path(directory) / 'skill-cache')
            first = prepare_skill_manuals(self.candidates(), MockGenerator(), cache, count=2)
            self.assertEqual(first.report['generated'], 1)
            record = first.records['meeting-minutes']
            self.assertEqual(record['provenance']['kind'], 'skill')
            self.assertEqual(record['provenance']['skill_name'], 'meeting-minutes')
            self.assertEqual(skill_cache_preflight(self.candidates(), MockGenerator(), cache, count=2)['valid'], 1)

            changed = self.candidates('Record risks, owners, and follow-up tasks.')
            preflight = skill_cache_preflight(changed, MockGenerator(), cache, count=2)
            self.assertEqual(preflight['missing_skill_names'], ['meeting-minutes'])
            refreshed = prepare_skill_manuals(changed, MockGenerator(), cache, count=2)
            self.assertEqual(refreshed.report['generated'], 1)
            self.assertNotEqual(record['skill_body_hash'], refreshed.records['meeting-minutes']['skill_body_hash'])

    def test_changed_or_mismatched_provenance_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ManualCache(Path(directory) / 'skill-cache')
            prepared = prepare_skill_manuals(self.candidates(), MockGenerator(), cache, count=2)
            record = prepared.records['meeting-minutes']
            identity = {key: record[key] for key in (
                'format_version', 'manual_kind', 'skill_name', 'skill_body_hash',
                'skill_metadata_hash', 'evidence_hash', 'normalization_version',
                'manual_prompt_version', 'generator', 'proxy_query_count', 'language_policy')}
            self.assertIsNotNone(cache.read(identity))
            record['provenance']['skill_body_hash'] = 'sha256:stale'
            from toollery.storage import atomic_json
            atomic_json(cache.path(record['manual_key']), record)
            self.assertIsNone(cache.read(identity))

    def test_partial_failure_preserves_completed_manuals_for_retry(self):
        class OneFailureGenerator(MockGenerator):
            def generate(self, evidence, count):
                if evidence.fields['name'] == 'second-skill':
                    raise RuntimeError('fixture failure')
                return super().generate(evidence, count)

        candidates = self.candidates() + [{
            'name': 'second-skill', 'description': 'Handle project follow-ups',
            'content': 'Track work that follows a meeting.',
        }]
        with tempfile.TemporaryDirectory() as directory:
            cache = ManualCache(Path(directory) / 'skill-cache')
            with self.assertRaises(PreparationError) as raised:
                prepare_skill_manuals(candidates, OneFailureGenerator(), cache, count=2, attempts=1)
            self.assertEqual(raised.exception.report['valid'], 1)
            retried = prepare_skill_manuals(candidates, MockGenerator(), cache, count=2)
            self.assertEqual(retried.report['cache_hits'], 1)
            self.assertEqual(retried.report['generated'], 1)
            self.assertEqual(set(retried.records), {'meeting-minutes', 'second-skill'})

    def test_manual_and_spec_manual_documents_require_full_manual_coverage(self):
        candidates = self.candidates()
        with self.assertRaises(ValueError):
            build_skill_documents(candidates, 'manual', {})
        with tempfile.TemporaryDirectory() as directory:
            prepared = prepare_skill_manuals(candidates, MockGenerator(), ManualCache(Path(directory)), count=2)
            manual = build_skill_documents(candidates, 'manual', prepared.records)
            combined = build_skill_documents(candidates, 'spec_manual', prepared.records)
            self.assertIn('Mock intent', manual['meeting-minutes'])
            self.assertIn('Capture decisions', combined['meeting-minutes'])


if __name__ == '__main__':
    unittest.main()
