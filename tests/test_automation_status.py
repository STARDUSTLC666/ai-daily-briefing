from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from briefing.automation import automation_status


class AutomationStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / datetime.now().strftime('%Y-%m-%d')

    def write(self, path, value):
        file = self.run / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value), encoding='utf-8')

    def test_new_edition_prepares_with_explicit_source_profile(self):
        state = automation_status(self.run)
        self.assertEqual(state['next_action'], 'prepare-agent')
        self.assertIn('--config', state['arguments'])
        self.assertFalse(self.run.exists())

    def test_partial_run_is_preserved_instead_of_overwritten(self):
        self.write('run-state.json', {'status': 'FAILED'})
        self.assertEqual(automation_status(self.run)['stage'], 'blocked')

    def test_unknown_submission_never_retries(self):
        self.write('bilibili-upload-result.json', {'status': 'submitting', 'dry_run': False})
        self.assertEqual(automation_status(self.run)['stage'], 'submission_unknown')

    def test_successful_submission_is_a_noop(self):
        self.write('bilibili-upload-result.json', {'status': 'submitted', 'dry_run': False})
        self.assertEqual(automation_status(self.run)['next_action'], 'none')

    def test_dry_run_submission_is_not_a_success(self):
        self.write('bilibili-upload-result.json', {'status': 'submitted', 'dry_run': True})
        self.assertNotEqual(automation_status(self.run)['stage'], 'submitted')

    def test_sample_cannot_enter_publication(self):
        self.write('manifest.json', {'qa_fixture': True})
        self.assertEqual(automation_status(self.run)['stage'], 'blocked')

    def test_corrupt_manifest_fails_closed(self):
        self.run.mkdir(parents=True)
        (self.run / 'manifest.json').write_text('{', encoding='utf-8')
        self.assertEqual(automation_status(self.run)['stage'], 'blocked')

    def test_source_contract_is_rechecked_before_render(self):
        self.write('manifest.json', {'agent_policy': {'required': True}})
        self.write('review/agent-audit.json', {})
        self.write('review/agent-contract.json', {})
        with patch('briefing.agent_workflow.verify_agent_edit_contract', return_value=['tampered facts']):
            state = automation_status(self.run)
        self.assertEqual(state['stage'], 'blocked')
        self.assertIn('tampered facts', state['errors'])

    def test_zero_news_is_a_skipped_edition(self):
        self.write('manifest.json', {'agent_policy': {'required': True}, 'package_quality': {'ok': False}})
        self.assertEqual(automation_status(self.run)['stage'], 'no_edition')

    def test_historical_date_cannot_become_today(self):
        self.assertEqual(automation_status(Path(self.temp.name) / '2020-01-01')['stage'], 'blocked')
